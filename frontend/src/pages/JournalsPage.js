import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Eye, Paperclip, Download, Pencil, Unlink } from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import { fmtDate } from '@/lib/dateFmt';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';

const API = process.env.REACT_APP_BACKEND_URL;

const JOURNAL_TYPES = [
  { value: 'OD', label: 'Operations Diverses', desc: 'Ecritures manuelles' },
  { value: 'AC', label: 'Achats', desc: 'Factures fournisseurs' },
  { value: 'VE', label: 'Ventes', desc: 'Appels de fonds proprietaires' },
  { value: 'FI', label: 'Financier', desc: 'Mouvements bancaires' },
  { value: 'AN', label: 'A-Nouveau', desc: 'Ouverture exercice' },
];

export default function JournalsPage() {
  const [entries, setEntries] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [journalType, setJournalType] = useState('OD');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [viewEntry, setViewEntry] = useState(null);
  const [attachDialogEntry, setAttachDialogEntry] = useState(null);
  const [form, setForm] = useState({ journal_type: 'OD', date: '', reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
  const [pendingAttachment, setPendingAttachment] = useState(null);
  const [includeReversals, setIncludeReversals] = useState(false);
  const fyParams = useFiscalYearParams();

  const load = useCallback(async () => {
    const [e, a, c] = await Promise.all([
      api.get('/accounting/entries', { params: { journal_type: journalType, include_reversals: includeReversals, ...fyParams } }),
      api.get('/accounting/pcmn'),
      api.get('/expense-categories').catch(() => ({ data: [] })),
    ]);
    setEntries(e.data);
    setAccounts(a.data);
    setCategories(c.data);
  }, [journalType, includeReversals, fyParams.date_from, fyParams.date_to]);

  useEffect(() => { load(); }, [load]);

  const [editingEntry, setEditingEntry] = useState(null);

  const openCreate = () => {
    setEditingEntry(null);
    setForm({ journal_type: journalType, date: new Date().toISOString().split('T')[0], reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
    setPendingAttachment(null);
    setDialogOpen(true);
  };

  const openEdit = (entry) => {
    setEditingEntry(entry);
    setForm({
      journal_type: entry.journal_type, date: entry.date, reference: entry.reference || '',
      description: entry.description || '',
      lines: (entry.lines || []).map(l => ({
        account_number: l.account_number,
        account_name: l.account_name,
        debit: l.debit,
        credit: l.credit,
        occupant_pct: l.occupant_pct,
        proprietaire_pct: l.proprietaire_pct,
      })),
    });
    setPendingAttachment(null);
    setDialogOpen(true);
  };

  const addLine = () => setForm({ ...form, lines: [...form.lines, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
  const removeLine = (i) => setForm({ ...form, lines: form.lines.filter((_, idx) => idx !== i) });
  const updateLine = (i, field, value) => {
    const lines = [...form.lines];
    lines[i] = { ...lines[i], [field]: value };
    if (field === 'account_number') {
      const acc = accounts.find(a => a.number === value);
      if (acc) lines[i].account_name = acc.name;
      // Auto-pre-rempli les % occupant/proprio depuis la categorie de depense du compte
      const cat = (categories || []).find(c => c.account_number === value);
      if (cat && cat.default_occupant_pct != null) {
        lines[i].occupant_pct = Number(cat.default_occupant_pct);
        lines[i].proprietaire_pct = +(100 - Number(cat.default_occupant_pct)).toFixed(2);
      } else if (value && (value.startsWith('6') || value.startsWith('7'))) {
        // Compte de charge sans categorie -> 0% occupant par defaut
        if (lines[i].occupant_pct == null) {
          lines[i].occupant_pct = 0;
          lines[i].proprietaire_pct = 100;
        }
      } else {
        // Compte non-charge : pas de repartition
        lines[i].occupant_pct = null;
        lines[i].proprietaire_pct = null;
      }
    }
    if (field === 'occupant_pct') {
      const v = Math.max(0, Math.min(100, parseFloat(value) || 0));
      lines[i].occupant_pct = v;
      lines[i].proprietaire_pct = +(100 - v).toFixed(2);
    }
    if (field === 'proprietaire_pct') {
      const v = Math.max(0, Math.min(100, parseFloat(value) || 0));
      lines[i].proprietaire_pct = v;
      lines[i].occupant_pct = +(100 - v).toFixed(2);
    }
    setForm({ ...form, lines });
  };

  const totalDebit = form.lines.reduce((s, l) => s + Number(l.debit || 0), 0);
  const totalCredit = form.lines.reduce((s, l) => s + Number(l.credit || 0), 0);
  const isBalanced = Math.abs(totalDebit - totalCredit) < 0.01;

  const handleSave = async () => {
    if (!isBalanced) { toast.error('Ecriture non equilibree'); return; }
    try {
      const payload = { ...form, lines: form.lines.map(l => ({ ...l, debit: Number(l.debit), credit: Number(l.credit) })) };
      let entryId;
      if (editingEntry) {
        await api.put(`/accounting/entries/${editingEntry.id}`, payload);
        entryId = editingEntry.id;
        toast.success(editingEntry.auto_generated ? 'Ecriture modifiee (marquee manuel)' : 'Ecriture modifiee');
      } else {
        const { data: created } = await api.post('/accounting/entries', payload);
        entryId = created?.id;
        toast.success('Ecriture creee');
      }
      if (pendingAttachment && entryId) {
        try {
          const fd = new FormData();
          fd.append('file', pendingAttachment);
          await api.post(`/accounting/entries/${entryId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
        } catch (e) { console.warn(e); }
      }
      setDialogOpen(false); setEditingEntry(null);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const uploadEntryAttachment = async (entryId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/accounting/entries/${entryId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success('Piece jointe ajoutee');
      const { data } = await api.get(`/accounting/entries/${entryId}`);
      setAttachDialogEntry(data); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Echec'); }
  };

  const deleteEntryAttachment = async (entryId, attachmentId) => {
    if (!window.confirm('Supprimer cette piece jointe ?')) return;
    await api.delete(`/accounting/entries/${entryId}/attachments/${attachmentId}`);
    const { data } = await api.get(`/accounting/entries/${entryId}`);
    setAttachDialogEntry(data); load();
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Supprimer cette ecriture ?")) return;
    await api.delete(`/accounting/entries/${id}`);
    toast.success('Ecriture supprimee');
    load();
  };

  const handleUnlettrage = async (entry) => {
    // Delettre la transaction bancaire liee a cette ecriture FI
    const inv = entry.linked_invoice;
    const msg = inv
      ? `Delettrer cette transaction de la facture "${inv.invoice_number || inv.supplier_name}" (${inv.amount_ttc?.toFixed(2)} EUR) ?\n\n`
        + 'La transaction bancaire redeviendra "a lettrer". Vous pourrez ensuite la relier a la bonne facture.'
      : 'Delettrer cette transaction bancaire ?\n\n'
        + 'La transaction redeviendra disponible pour un nouveau lettrage.';
    if (!window.confirm(msg)) return;
    try {
      await api.post(`/banking/unlettrage/${entry.source_id}`);
      toast.success(inv
        ? `Delettrage effectue. La facture "${inv.invoice_number || inv.supplier_name}" est de nouveau en attente de paiement.`
        : 'Lettrage annule.');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur delettrage');
    }
  };

  return (
    <div data-testid="journals-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Journaux Comptables</h1><p className="page-subtitle">Ecritures comptables par journal</p></div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-entry-btn"><Plus size={16} className="mr-2" /> Nouvelle ecriture</Button>
      </div>

      <Tabs value={journalType} onValueChange={setJournalType}>
        <div className="flex items-center justify-between mb-4">
          <TabsList data-testid="journal-type-tabs">
            {JOURNAL_TYPES.map(j => <TabsTrigger key={j.value} value={j.value}>{j.label}</TabsTrigger>)}
          </TabsList>
          <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none" data-testid="include-reversals-toggle">
            <input
              type="checkbox"
              checked={includeReversals}
              onChange={e => setIncludeReversals(e.target.checked)}
              className="h-4 w-4 accent-[#0055FF]"
            />
            <span>Inclure les contre-passations</span>
          </label>
        </div>
        <TabsContent value={journalType} className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Date</TableHead><TableHead>Reference</TableHead><TableHead>Description</TableHead>
                <TableHead className="text-right">Debit</TableHead><TableHead className="text-right">Credit</TableHead><TableHead className="w-24">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {entries.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucune ecriture</TableCell></TableRow>
                ) : entries.map(e => (
                  <TableRow key={e.id} className={`hover:bg-slate-50/50 ${e.reversed ? 'bg-red-50/30 line-through opacity-70' : ''} ${e.is_reversal ? 'bg-amber-50/40' : ''}`}>
                    <TableCell className="font-mono text-sm">{fmtDate(e.date)}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.reference}
                      {e.auto_generated && !e.manually_edited && <Badge variant="outline" className="ml-2 text-[10px] bg-blue-50 border-blue-200 text-blue-700" data-testid={`auto-badge-${e.id}`}>Auto</Badge>}
                      {e.manually_edited && <Badge variant="outline" className="ml-2 text-[10px] bg-orange-50 border-orange-200 text-orange-700" data-testid={`manual-edit-badge-${e.id}`}>Modifie</Badge>}
                      {e.is_reversal && <Badge variant="outline" className="ml-2 text-[10px] bg-amber-100 border-amber-300 text-amber-800" data-testid={`reversal-badge-${e.id}`}>Contre-passation</Badge>}
                      {e.reversed && <Badge variant="outline" className="ml-2 text-[10px] bg-red-100 border-red-300 text-red-700" data-testid={`reversed-badge-${e.id}`}>Extournee</Badge>}
                    </TableCell>
                    <TableCell className="font-medium">
                      {e.description}
                      {e.linked_invoice && (
                        <Badge variant="outline" className="ml-2 text-[10px] bg-emerald-50 border-emerald-300 text-emerald-800" data-testid={`linked-invoice-${e.id}`} title={`Facture liee : ${e.linked_invoice.invoice_number || ''} - ${e.linked_invoice.supplier_name || ''} (${(e.linked_invoice.amount_ttc || 0).toFixed(2)} EUR)`}>
                          Facture {e.linked_invoice.invoice_number || e.linked_invoice.supplier_name || ''}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono">{e.total_debit?.toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{e.total_credit?.toFixed(2)}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setViewEntry(e)}><Eye size={14} /></Button>
                        {!e.is_reversal && !e.reversed && <Button variant="ghost" size="sm" onClick={() => openEdit(e)} data-testid={`edit-entry-${e.id}`} title="Modifier"><Pencil size={14} /></Button>}
                        <Button variant="ghost" size="sm" onClick={() => setAttachDialogEntry(e)} data-testid={`entry-attach-${e.id}`} title="Pieces jointes">
                          <Paperclip size={14} />{(e.attachments?.length || 0) > 0 && <span className="ml-1 text-xs">{e.attachments.length}</span>}
                        </Button>
                        {e.source_type === 'bank_txn' && e.bank_txn_matched && !e.is_reversal && !e.reversed && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleUnlettrage(e)}
                            className="text-amber-600 hover:bg-amber-50"
                            data-testid={`unlettrage-btn-${e.id}`}
                            title={e.linked_invoice
                              ? `Delettrer de la facture "${e.linked_invoice.invoice_number || e.linked_invoice.supplier_name}"`
                              : 'Delettrer la transaction bancaire'}
                          >
                            <Unlink size={14} />
                          </Button>
                        )}
                        {(!e.auto_generated || e.manually_edited) && !e.is_reversal && !e.reversed && <Button variant="ghost" size="sm" onClick={() => handleDelete(e.id)} className="text-red-500"><Trash2 size={14} /></Button>}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      {/* Create Dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) setEditingEntry(null); setDialogOpen(open); }}>
        <DialogContent className="max-w-3xl" data-testid="entry-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            {editingEntry ? `Modifier ecriture ${editingEntry.reference || ''}` : 'Nouvelle ecriture comptable'}
            {editingEntry?.auto_generated && <Badge variant="outline" className="ml-2 text-[10px] bg-orange-50 border-orange-200 text-orange-700">Auto -&gt; sera marquee comme modifiee</Badge>}
          </DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Date *</label><Input type="date" value={form.date} onChange={e => setForm({...form, date: e.target.value})} data-testid="entry-date" /></div>
              <div><label className="form-label">Reference</label><Input value={form.reference} onChange={e => setForm({...form, reference: e.target.value})} /></div>
              <div><label className="form-label">Journal</label>
                <Select value={form.journal_type} onValueChange={v => setForm({...form, journal_type: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{JOURNAL_TYPES.map(j => <SelectItem key={j.value} value={j.value}>{j.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div><label className="form-label">Description</label><Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} data-testid="entry-desc" /></div>

            <div>
              <label className="form-label mb-2">Lignes d'ecriture</label>
              <div className="border rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead><tr className="bg-slate-50 text-xs text-slate-600 uppercase">
                    <th className="p-2 text-left">Compte</th><th className="p-2 text-left">Libelle</th>
                    <th className="p-2 text-right">Debit</th><th className="p-2 text-right">Credit</th>
                    <th className="p-2 text-right" title="Pourcentage occupant (decompte locataire)">%Occ.</th>
                    <th className="p-2 text-right" title="Pourcentage proprietaire">%Prop.</th>
                    <th className="p-2 w-10"></th>
                  </tr></thead>
                  <tbody>
                    {form.lines.map((line, i) => {
                      const isCharge = line.account_number && (line.account_number.startsWith('6') || line.account_number.startsWith('7'));
                      return (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="p-1 min-w-[280px]">
                          <AccountSearchSelect
                            accounts={accounts}
                            value={line.account_number}
                            onChange={v => updateLine(i, 'account_number', v)}
                            placeholder="Choisir un compte..."
                            testId={`journal-line-${i}-account`}
                          />
                        </td>
                        <td className="p-1 text-xs text-slate-500">{line.account_name}</td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8" value={line.debit} onChange={e => updateLine(i, 'debit', e.target.value)} /></td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8" value={line.credit} onChange={e => updateLine(i, 'credit', e.target.value)} /></td>
                        <td className="p-1 w-20">
                          {isCharge ? (
                            <Input type="number" min={0} max={100} step={1} className="text-right text-sm h-8 bg-amber-50/40"
                              value={line.occupant_pct ?? 0}
                              onChange={e => updateLine(i, 'occupant_pct', e.target.value)}
                              data-testid={`journal-line-${i}-occupant`}
                            />
                          ) : <span className="text-slate-300 text-xs">—</span>}
                        </td>
                        <td className="p-1 w-20">
                          {isCharge ? (
                            <Input type="number" min={0} max={100} step={1} className="text-right text-sm h-8 bg-blue-50/40"
                              value={line.proprietaire_pct ?? 100}
                              onChange={e => updateLine(i, 'proprietaire_pct', e.target.value)}
                              data-testid={`journal-line-${i}-proprio`}
                            />
                          ) : <span className="text-slate-300 text-xs">—</span>}
                        </td>
                        <td className="p-1">{form.lines.length > 2 && <button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button>}</td>
                      </tr>
                    );})}
                  </tbody>
                  <tfoot><tr className="border-t-2 border-slate-200 bg-slate-50 font-semibold text-sm">
                    <td colSpan={2} className="p-2">
                      <Button variant="ghost" size="sm" onClick={addLine} className="text-xs"><Plus size={12} className="mr-1" /> Ajouter ligne</Button>
                    </td>
                    <td className="p-2 text-right font-mono">{totalDebit.toFixed(2)}</td>
                    <td className="p-2 text-right font-mono">{totalCredit.toFixed(2)}</td>
                    <td colSpan={3}></td>
                  </tr></tfoot>
                </table>
              </div>
              {!isBalanced && <p className="text-red-500 text-xs mt-1">Ecart: {Math.abs(totalDebit - totalCredit).toFixed(2)} EUR</p>}
            </div>

            <div className="border-t pt-3">
              <label className="form-label mb-1">Piece justificative (PDF / Image)</label>
              <div className="flex items-center gap-3">
                <input id="entry-attach-input" type="file" accept="application/pdf,image/*" className="hidden"
                  onChange={(e) => setPendingAttachment(e.target.files?.[0] || null)} />
                <Button type="button" variant="outline" size="sm" onClick={() => document.getElementById('entry-attach-input').click()} data-testid="entry-attach-pick">
                  <Paperclip size={14} className="mr-2" /> Choisir un fichier
                </Button>
                {pendingAttachment && (
                  <span className="text-xs text-slate-600 flex items-center gap-2">
                    {pendingAttachment.name}
                    <button type="button" className="text-red-500" onClick={() => setPendingAttachment(null)}><Trash2 size={12} /></button>
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-400 mt-1">Optionnel - documente l'ecriture (justificatif, contrat, devis...)</p>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} disabled={!isBalanced} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="entry-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={!!viewEntry} onOpenChange={() => setViewEntry(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Detail de l'ecriture</DialogTitle></DialogHeader>
          {viewEntry && (
            <div className="space-y-4 mt-2">
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div><span className="text-slate-500">Date:</span> <span className="font-medium">{fmtDate(viewEntry.date)}</span></div>
                <div><span className="text-slate-500">Ref:</span> <span className="font-medium">{viewEntry.reference}</span></div>
                <div><span className="text-slate-500">Journal:</span> <Badge variant="outline">{viewEntry.journal_type}</Badge></div>
              </div>
              <div className="text-sm"><span className="text-slate-500">Description:</span> {viewEntry.description}</div>
              <div className="border rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead><tr className="bg-slate-50"><th className="p-2 text-left text-xs text-slate-600">Compte</th><th className="p-2 text-left text-xs text-slate-600">Libelle</th><th className="p-2 text-right text-xs text-slate-600">Debit</th><th className="p-2 text-right text-xs text-slate-600">Credit</th></tr></thead>
                  <tbody>
                    {viewEntry.lines?.map((l, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="p-2 font-mono">{l.account_number}</td>
                        <td className="p-2">{l.account_name}</td>
                        <td className="p-2 text-right font-mono">{l.debit > 0 ? l.debit.toFixed(2) : ''}</td>
                        <td className="p-2 text-right font-mono">{l.credit > 0 ? l.credit.toFixed(2) : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
      {/* Attachments Dialog (per existing entry) */}
      <Dialog open={!!attachDialogEntry} onOpenChange={() => setAttachDialogEntry(null)}>
        <DialogContent className="max-w-lg" data-testid="entry-attach-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Pieces jointes - {attachDialogEntry?.reference || attachDialogEntry?.description}</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="border-2 border-dashed rounded p-4 text-center">
              <input type="file" accept="application/pdf,image/*" className="hidden" id="att-input-entry"
                onChange={(e) => { const f = e.target.files?.[0]; if (f && attachDialogEntry) uploadEntryAttachment(attachDialogEntry.id, f); e.target.value=''; }} />
              <Button variant="outline" onClick={() => document.getElementById('att-input-entry').click()} data-testid="upload-attachment-entry-btn">
                <Paperclip size={14} className="mr-2" /> Ajouter un PDF / Image
              </Button>
            </div>
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {(attachDialogEntry?.attachments || []).length === 0 ? (
                <div className="text-sm text-slate-400 text-center py-3">Aucune piece jointe</div>
              ) : attachDialogEntry.attachments.map((a) => (
                <div key={a.id} className="flex items-center justify-between border rounded px-2 py-1.5 text-sm">
                  <span className="truncate flex items-center gap-2"><Paperclip size={12} />{a.filename}</span>
                  <div className="flex gap-1">
                    <a href={`${API}/api/accounting/entries/${attachDialogEntry.id}/attachments/${a.id}/download`} target="_blank" rel="noreferrer">
                      <Button variant="ghost" size="sm"><Download size={14} /></Button>
                    </a>
                    <Button variant="ghost" size="sm" className="text-red-500" onClick={() => deleteEntryAttachment(attachDialogEntry.id, a.id)}><Trash2 size={14} /></Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
