import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Key, Receipt, Sparkles, Paperclip, Download, X, Pencil, AlertTriangle, Filter } from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import { fmtDate } from '@/lib/dateFmt';

const API = process.env.REACT_APP_BACKEND_URL;

export default function InvoicesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState('invoices');
  const [invoices, setInvoices] = useState([]);
  const [invFilters, setInvFilters] = useState({ startDate: '', endDate: '', supplier: '', reference: '', status: '' });
  const [distKeys, setDistKeys] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [lots, setLots] = useState([]);
  const [invoiceDialog, setInvoiceDialog] = useState(false);
  const [keyDialog, setKeyDialog] = useState(false);
  const [invForm, setInvForm] = useState({ number: '', date: '', due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', expense_category_id: '', distribution_key_id: '', status: 'unpaid', is_private_fee: false, private_fee_owner_id: '' });
  const [owners, setOwners] = useState([]);
  const [ownerSearch, setOwnerSearch] = useState('');
  const [suggestCreateSupplier, setSuggestCreateSupplier] = useState(null); // {name, vat, bce, iban}
  const [keyForm, setKeyForm] = useState({ name: '', description: '', key_type: 'quotity', lots: [] });
  const [aiExtracting, setAiExtracting] = useState(false);
  const [aiHint, setAiHint] = useState('');
  const [pendingPdf, setPendingPdf] = useState(null); // {file, filename} captured for later attach
  const [attachDialogInv, setAttachDialogInv] = useState(null); // invoice being managed
  const [newCatDialog, setNewCatDialog] = useState(false);
  const [newCatForm, setNewCatForm] = useState({ name: '', account_number: '', description: '' });

  const load = useCallback(async () => {
    const [inv, dk, acc, lt, cat, ow] = await Promise.all([
      api.get('/invoices'), api.get('/distribution-keys'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }), api.get('/lots'),
      api.get('/expense-categories'), api.get('/owners'),
    ]);
    setInvoices(inv.data); setDistKeys(dk.data); setAccounts(acc.data); setLots(lt.data);
    setCategories(cat.data); setOwners(ow.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Open edit dialog if ?edit=<invoice_id> in URL (deep-link from Expenses page)
  useEffect(() => {
    const editId = searchParams.get('edit');
    if (editId && invoices.length > 0) {
      const inv = invoices.find(i => i.id === editId);
      if (inv) {
        openEditInvoice(inv);
        searchParams.delete('edit');
        setSearchParams(searchParams, { replace: true });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [invoices, searchParams]);

  const [editingInvoice, setEditingInvoice] = useState(null);

  // Invoice handlers
  const openCreateInvoice = () => {
    setEditingInvoice(null);
    setInvForm({ number: `F-${Date.now().toString().slice(-6)}`, date: new Date().toISOString().split('T')[0], due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', expense_category_id: '', distribution_key_id: '', status: 'unpaid', is_private_fee: false, private_fee_owner_id: '' });
    setAiHint(''); setPendingPdf(null); setOwnerSearch('');
    setInvoiceDialog(true);
  };

  const openEditInvoice = (inv) => {
    setEditingInvoice(inv);
    setInvForm({
      number: inv.number || '', date: inv.date || '', due_date: inv.due_date || '',
      supplier: inv.supplier || '', description: inv.description || '',
      total_amount: inv.total_amount || 0, vat_amount: inv.vat_amount || 0,
      account_number: inv.account_number || '',
      expense_category_id: inv.expense_category_id || '',
      distribution_key_id: inv.distribution_key_id || '',
      status: inv.status || 'unpaid',
      is_private_fee: !!inv.is_private_fee,
      private_fee_owner_id: inv.private_fee_owner_id || '',
    });
    setAiHint(''); setPendingPdf(null); setOwnerSearch('');
    setInvoiceDialog(true);
  };

  const aiExtractFromPdf = async (file) => {
    setAiExtracting(true);
    setAiHint('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const copro = localStorage.getItem('copropriete_id') || '';
      if (copro) fd.append('copropriete_id', copro);
      const { data } = await api.post('/invoices-ai/extract', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      const ext = data.extracted || {};
      if (!ext || Object.keys(ext).length === 0) {
        setAiHint('Aucune donnee extraite, completez manuellement.');
      } else {
        setInvForm(f => ({
          ...f,
          number: ext.number || f.number,
          date: ext.date || f.date,
          due_date: ext.due_date || f.due_date,
          supplier: ext.supplier_name || f.supplier,
          description: ext.description || f.description,
          total_amount: ext.total_amount || f.total_amount,
          vat_amount: ext.vat_amount || f.vat_amount,
          account_number: ext.suggested_pcmn_account || f.account_number,
        }));
        const parts = [];
        if (ext.vat_number) parts.push(`TVA fourn.: ${ext.vat_number}`);
        if (ext.bce_number) parts.push(`BCE: ${ext.bce_number}`);
        if (ext.iban) parts.push(`IBAN: ${ext.iban}`);
        if (ext.vat_rate) parts.push(`Taux TVA: ${ext.vat_rate}%`);
        if (data.supplier_match) {
          const m = data.supplier_match_method === 'bce' ? 'par BCE' : 'par nom';
          parts.push(`Fournisseur reconnu (${m}): ${data.supplier_match.name}`);
          setSuggestCreateSupplier(null);
        } else if (data.supplier_suggest_create) {
          setSuggestCreateSupplier({
            name: ext.supplier_name || '',
            vat_number: ext.vat_number || '',
            bce_number: ext.bce_number || '',
            iban: ext.iban || '',
            bic: ext.bic || '',
          });
          parts.push(`Nouveau fournisseur a creer: ${ext.supplier_name}`);
        }
        setAiHint(parts.join(' - '));
        toast.success('Donnees extraites par IA - verifiez avant enregistrement.');
      }
      setPendingPdf({ file, filename: file.name });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec extraction IA');
    } finally { setAiExtracting(false); }
  };

  const saveInvoice = async () => {
    try {
      const payload = { ...invForm, total_amount: Number(invForm.total_amount), vat_amount: Number(invForm.vat_amount) };
      let invoiceId;
      if (editingInvoice) {
        await api.put(`/invoices/${editingInvoice.id}`, payload);
        invoiceId = editingInvoice.id;
        toast.success('Facture modifiee');
      } else {
        const { data: created } = await api.post('/invoices', payload);
        invoiceId = created?.id;
        toast.success('Facture creee');
      }
      // If we have a pending PDF, attach it to the invoice
      if (pendingPdf && pendingPdf.file && invoiceId) {
        try {
          const fd = new FormData();
          fd.append('file', pendingPdf.file);
          await api.post(`/invoices/${invoiceId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
        } catch (e) { console.warn('Attachment failed', e); }
      }
      setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteInvoice = async (id) => {
    if (!window.confirm('Supprimer cette facture ?')) return;
    await api.delete(`/invoices/${id}`); toast.success('Facture supprimee'); load();
  };

  const uploadInvoiceAttachment = async (invoiceId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/invoices/${invoiceId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success('Piece jointe ajoutee');
      const { data } = await api.get(`/invoices/${invoiceId}`);
      setAttachDialogInv(data); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Echec upload'); }
  };

  const deleteInvoiceAttachment = async (invoiceId, attachmentId) => {
    if (!window.confirm('Supprimer cette piece jointe ?')) return;
    try {
      await api.delete(`/invoices/${invoiceId}/attachments/${attachmentId}`);
      const { data } = await api.get(`/invoices/${invoiceId}`);
      setAttachDialogInv(data); load();
    } catch { toast.error('Erreur'); }
  };

  // Distribution key handlers
  const updateKeyLot = (i, field, value) => {
    const newLots = [...keyForm.lots];
    newLots[i] = { ...newLots[i], [field]: Number(value) };
    setKeyForm({ ...keyForm, lots: newLots });
  };

  const [editingKey, setEditingKey] = useState(null);
  const [keyUsage, setKeyUsage] = useState(null);

  const openCreateKey = () => {
    setEditingKey(null); setKeyUsage(null);
    setKeyForm({ name: '', description: '', key_type: 'quotity', lots: lots.map(l => ({ lot_id: l.id, lot_number: l.number, share: l.quotity || 0 })) });
    setKeyDialog(true);
  };
  const openEditKey = async (k) => {
    setEditingKey(k);
    setKeyForm({ name: k.name, description: k.description || '', key_type: k.key_type, lots: (k.lots || []).map(l => ({ ...l })) });
    try {
      const { data } = await api.get(`/distribution-keys/${k.id}/usage`);
      setKeyUsage(data);
    } catch { setKeyUsage(null); }
    setKeyDialog(true);
  };
  const saveKey = async (force = false) => {
    try {
      if (editingKey) {
        await api.put(`/distribution-keys/${editingKey.id}`, keyForm, { params: force ? { force: true } : {} });
        toast.success(force && keyUsage?.invoices?.length > 0 ? `Cle modifiee - ${keyUsage.invoices.length} facture(s) detachee(s)` : 'Cle modifiee');
      } else {
        await api.post('/distribution-keys', keyForm);
        toast.success('Cle creee');
      }
      setKeyDialog(false); setEditingKey(null); setKeyUsage(null); load();
    } catch (err) {
      if (err.response?.status === 409) {
        if (window.confirm(`${err.response.data.detail}\n\nDETACHER les factures et continuer ?`)) {
          await saveKey(true);
        }
      } else {
        toast.error(err.response?.data?.detail || 'Erreur');
      }
    }
  };

  const deleteKey = async (id) => {
    if (!window.confirm('Supprimer cette cle ?')) return;
    await api.delete(`/distribution-keys/${id}`); toast.success('Cle supprimee'); load();
  };

  return (
    <div data-testid="invoices-page">
      <div className="page-header"><h1 className="page-title">Facturation</h1><p className="page-subtitle">Factures et cles de repartition</p></div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4" data-testid="invoices-tabs">
          <TabsTrigger value="invoices"><Receipt size={14} className="mr-2" /> Factures</TabsTrigger>
          <TabsTrigger value="keys"><Key size={14} className="mr-2" /> Cles de repartition</TabsTrigger>
        </TabsList>

        <TabsContent value="invoices" className="mt-0">
          <div className="flex justify-end gap-2 mb-4">
            <label className="inline-flex">
              <Button type="button" variant="outline" className="border-purple-300 text-purple-700 hover:bg-purple-50" data-testid="ai-extract-btn"
                onClick={() => document.getElementById('ai-pdf-input').click()} disabled={aiExtracting}>
                <Sparkles size={16} className="mr-2" /> {aiExtracting ? 'Extraction IA...' : 'Importer facture PDF (IA)'}
              </Button>
              <input id="ai-pdf-input" type="file" accept="application/pdf" className="hidden" onChange={(e) => {
                const f = e.target.files?.[0]; if (!f) return;
                openCreateInvoice();
                aiExtractFromPdf(f);
                e.target.value = '';
              }} />
            </label>
            <Button onClick={openCreateInvoice} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-invoice-btn"><Plus size={16} className="mr-2" /> Nouvelle facture</Button>
          </div>

          {/* ---- Filter bar invoices ---- */}
          <div className="mb-3 p-3 bg-slate-50 border border-slate-200 rounded-md flex flex-wrap items-end gap-2" data-testid="invoices-filter-bar">
            <div className="flex items-center gap-1.5">
              <Filter size={14} className="text-slate-500" />
              <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Filtres</span>
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Du</label>
              <Input type="date" value={invFilters.startDate} onChange={e => setInvFilters({...invFilters, startDate: e.target.value})} className="h-8 text-xs w-36" data-testid="inv-filter-start" />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Au</label>
              <Input type="date" value={invFilters.endDate} onChange={e => setInvFilters({...invFilters, endDate: e.target.value})} className="h-8 text-xs w-36" data-testid="inv-filter-end" />
            </div>
            <div className="flex-1 min-w-[140px]">
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Fournisseur</label>
              <Select value={invFilters.supplier || '__all__'} onValueChange={v => setInvFilters({...invFilters, supplier: v === '__all__' ? '' : v})}>
                <SelectTrigger className="h-8 text-xs" data-testid="inv-filter-supplier"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">Tous</SelectItem>
                  {[...new Set(invoices.map(i => (i.supplier || '').trim()).filter(Boolean))].sort().map(s => (
                    <SelectItem key={s} value={s}>{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex-1 min-w-[140px]">
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Reference / description</label>
              <Input value={invFilters.reference} onChange={e => setInvFilters({...invFilters, reference: e.target.value})} placeholder="Numero..." className="h-8 text-xs" data-testid="inv-filter-ref" />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Statut</label>
              <Select value={invFilters.status || '__all__'} onValueChange={v => setInvFilters({...invFilters, status: v === '__all__' ? '' : v})}>
                <SelectTrigger className="h-8 text-xs w-28" data-testid="inv-filter-status"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">Tous</SelectItem>
                  <SelectItem value="unpaid">Impayee</SelectItem>
                  <SelectItem value="paid">Payee</SelectItem>
                  <SelectItem value="draft">Brouillon</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {(invFilters.startDate || invFilters.endDate || invFilters.supplier || invFilters.reference || invFilters.status) && (
              <Button size="sm" variant="ghost" className="h-8 text-red-600" onClick={() => setInvFilters({ startDate: '', endDate: '', supplier: '', reference: '', status: '' })} data-testid="inv-filter-reset">
                Reset
              </Button>
            )}
          </div>

          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Ref. interne</TableHead><TableHead>N fournisseur</TableHead><TableHead>Date</TableHead><TableHead>Fournisseur</TableHead>
                <TableHead>Description</TableHead><TableHead className="text-right">Montant</TableHead>
                <TableHead>Cle</TableHead><TableHead>Statut</TableHead><TableHead className="w-20">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {(() => {
                  const filtered = invoices.filter(inv => {
                    if (invFilters.startDate && (inv.date || '') < invFilters.startDate) return false;
                    if (invFilters.endDate && (inv.date || '') > invFilters.endDate) return false;
                    if (invFilters.supplier && inv.supplier !== invFilters.supplier) return false;
                    if (invFilters.reference && !(inv.number || '').toLowerCase().includes(invFilters.reference.toLowerCase()) && !(inv.description || '').toLowerCase().includes(invFilters.reference.toLowerCase())) return false;
                    if (invFilters.status && inv.status !== invFilters.status) return false;
                    return true;
                  });
                  if (filtered.length === 0) return <TableRow><TableCell colSpan={9} className="text-center py-8 text-slate-400">Aucune facture</TableCell></TableRow>;
                  return filtered.map(inv => (
                  <TableRow key={inv.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-mono text-xs text-[#0055FF] font-semibold">{inv.internal_reference || '-'}</TableCell>
                    <TableCell className="font-mono text-sm">{inv.number}</TableCell>
                    <TableCell>{fmtDate(inv.date)}</TableCell>
                    <TableCell className="font-medium">{inv.supplier}</TableCell>
                    <TableCell className="max-w-[200px] truncate">{inv.description}</TableCell>
                    <TableCell className="text-right font-mono">{inv.total_amount?.toFixed(2)} EUR</TableCell>
                    <TableCell className="text-xs">{distKeys.find(k => k.id === inv.distribution_key_id)?.name || '-'}</TableCell>
                    <TableCell>
                      <Badge className={inv.status === 'paid' ? 'bg-green-50 text-green-700 border-green-200' : inv.status === 'unpaid' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-slate-50 text-slate-600'} variant="outline">
                        {inv.status === 'paid' ? 'Payee' : inv.status === 'unpaid' ? 'Impayee' : 'Brouillon'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1 items-center">
                        <Button variant="ghost" size="sm" onClick={() => openEditInvoice(inv)} data-testid={`edit-invoice-${inv.id}`} title="Modifier"><Pencil size={14} /></Button>
                        <Button variant="ghost" size="sm" onClick={() => setAttachDialogInv(inv)} data-testid={`inv-attach-${inv.id}`} title="Pieces jointes">
                          <Paperclip size={14} />{(inv.attachments?.length || 0) > 0 && <span className="ml-1 text-xs">{inv.attachments.length}</span>}
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => deleteInvoice(inv.id)} className="text-red-500"><Trash2 size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ));
                })()}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="keys" className="mt-0">
          <div className="flex justify-end mb-4">
            <Button onClick={openCreateKey} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-key-btn"><Plus size={16} className="mr-2" /> Nouvelle cle</Button>
          </div>
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Nom</TableHead><TableHead>Description</TableHead><TableHead>Type</TableHead>
                <TableHead>Lots</TableHead>
                <TableHead className="text-right">Total quotites</TableHead>
                <TableHead>Coherence</TableHead>
                <TableHead className="w-20">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {distKeys.length === 0 ? (
                  <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucune cle</TableCell></TableRow>
                ) : distKeys.map(k => {
                  const total = (k.lots || []).reduce((s, l) => s + (Number(l.share) || 0), 0);
                  const hasZero = (k.lots || []).some(l => !Number(l.share));
                  // "ronds" frequents en copro belge : 1000 / 10000 / 100 / 1
                  const isRound = [1, 100, 1000, 10000].some(t => Math.abs(total - t) < 0.005);
                  let coherenceColor = 'bg-slate-50 text-slate-500 border-slate-200';
                  let coherenceLabel = `${total.toFixed(2)}`;
                  if (hasZero) { coherenceColor = 'bg-amber-50 text-amber-700 border-amber-200'; coherenceLabel = 'Lots a 0'; }
                  else if (isRound) { coherenceColor = 'bg-green-50 text-green-700 border-green-200'; coherenceLabel = 'OK'; }
                  else if (total > 0) { coherenceColor = 'bg-blue-50 text-blue-700 border-blue-200'; coherenceLabel = 'Custom'; }
                  return (
                    <TableRow key={k.id} className="hover:bg-slate-50/50">
                      <TableCell className="font-medium">{k.name}</TableCell>
                      <TableCell>{k.description}</TableCell>
                      <TableCell><Badge variant="outline">{k.key_type}</Badge></TableCell>
                      <TableCell className="text-sm">{k.lots?.length || 0} lots</TableCell>
                      <TableCell className="text-right font-mono text-sm" data-testid={`key-total-${k.id}`}>{total.toFixed(2)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={coherenceColor}>{coherenceLabel}</Badge>
                      </TableCell>
                      <TableCell><div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openEditKey(k)} data-testid={`edit-key-${k.id}`}><Pencil size={14} /></Button>
                        <Button variant="ghost" size="sm" onClick={() => deleteKey(k.id)} className="text-red-500"><Trash2 size={14} /></Button>
                      </div></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      {/* Invoice Dialog */}
      <Dialog open={invoiceDialog} onOpenChange={(open) => { if (!open) { setEditingInvoice(null); setPendingPdf(null); } setInvoiceDialog(open); }}>
        <DialogContent className="max-w-2xl" data-testid="invoice-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingInvoice ? 'Modifier la facture' : 'Nouvelle facture'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            {aiHint && (
              <div className="text-xs px-3 py-2 rounded bg-purple-50 border border-purple-200 text-purple-800" data-testid="ai-hint">
                <Sparkles size={12} className="inline mr-1" /> {aiHint}
              </div>
            )}
            {suggestCreateSupplier && (
              <div className="text-xs px-3 py-2 rounded bg-amber-50 border border-amber-200 text-amber-900 flex items-center justify-between gap-3" data-testid="suggest-create-supplier">
                <div>
                  <Sparkles size={12} className="inline mr-1 text-amber-700" />
                  <b>Fournisseur introuvable dans la base globale du syndic.</b>
                  <span className="ml-1">Voulez-vous creer la fiche pour <b>{suggestCreateSupplier.name}</b>
                  {suggestCreateSupplier.bce_number && ` (BCE ${suggestCreateSupplier.bce_number})`} ?</span>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button type="button" size="sm" variant="outline" onClick={() => setSuggestCreateSupplier(null)} data-testid="suggest-supplier-dismiss">Ignorer</Button>
                  <Button type="button" size="sm" className="bg-amber-600 hover:bg-amber-700 text-white" onClick={async () => {
                    try {
                      const copro = localStorage.getItem('copropriete_id') || '';
                      const { data } = await api.post('/suppliers', { ...suggestCreateSupplier, copropriete_id: copro });
                      toast.success(`Fiche fournisseur creee : ${data.name}`);
                      setSuggestCreateSupplier(null);
                    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur creation'); }
                  }} data-testid="suggest-supplier-create">Creer la fiche</Button>
                </div>
              </div>
            )}
            {pendingPdf && (
              <div className="text-xs px-3 py-2 rounded bg-blue-50 border border-blue-200 text-blue-800 flex items-center justify-between">
                <span><Paperclip size={12} className="inline mr-1" /> PDF a attacher: <b>{pendingPdf.filename}</b></span>
                <button type="button" onClick={() => setPendingPdf(null)} className="text-blue-600 hover:text-blue-800"><X size={14} /></button>
              </div>
            )}
            {!pendingPdf && (
              <div className="text-xs">
                <input id="manual-pdf-input" type="file" accept="application/pdf,image/*" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) setPendingPdf({ file: f, filename: f.name }); e.target.value = ''; }} />
                <button type="button" className="text-slate-500 hover:text-[#0055FF] underline" onClick={() => document.getElementById('manual-pdf-input').click()} data-testid="manual-attach-btn">
                  <Paperclip size={11} className="inline mr-1" /> Joindre la facture PDF / image (optionnel)
                </button>
              </div>
            )}
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="form-label">N facture fournisseur *</label>
                <Input value={invForm.number} onChange={e => setInvForm({...invForm, number: e.target.value})} placeholder="Ex: V-260114" data-testid="inv-number" />
                {editingInvoice?.internal_reference && (
                  <p className="text-[10px] text-slate-500 mt-1">Ref. interne : <span className="font-mono text-[#0055FF] font-semibold">{editingInvoice.internal_reference}</span></p>
                )}
                {!editingInvoice && (
                  <p className="text-[10px] text-slate-400 mt-1">Une reference interne <span className="font-mono">FA-AAAA-NNNN</span> sera auto-generee a la creation.</p>
                )}
              </div>
              <div><label className="form-label">Date *</label><Input type="date" value={invForm.date} onChange={e => setInvForm({...invForm, date: e.target.value})} /></div>
              <div><label className="form-label">Echeance</label><Input type="date" value={invForm.due_date} onChange={e => setInvForm({...invForm, due_date: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Fournisseur *</label><Input value={invForm.supplier} onChange={e => setInvForm({...invForm, supplier: e.target.value})} data-testid="inv-supplier" /></div>
              <div><label className="form-label">Description</label><Input value={invForm.description} onChange={e => setInvForm({...invForm, description: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Montant TTC *</label><Input type="number" step="0.01" value={invForm.total_amount} onChange={e => setInvForm({...invForm, total_amount: e.target.value})} data-testid="inv-amount" /></div>
              <div><label className="form-label">TVA</label><Input type="number" step="0.01" value={invForm.vat_amount} onChange={e => setInvForm({...invForm, vat_amount: e.target.value})} /></div>
              <div><label className="form-label">Statut</label>
                <Select value={invForm.status} onValueChange={v => setInvForm({...invForm, status: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Brouillon</SelectItem>
                    <SelectItem value="unpaid">Impayee</SelectItem>
                    <SelectItem value="paid">Payee</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="rounded-md border border-amber-200 bg-amber-50/50 p-3">
              <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-amber-900">
                <input
                  type="checkbox"
                  checked={!!invForm.is_private_fee}
                  onChange={e => setInvForm(f => ({ ...f, is_private_fee: e.target.checked, distribution_key_id: e.target.checked ? '' : f.distribution_key_id }))}
                  className="rounded border-amber-400 text-amber-600 focus:ring-amber-500"
                  data-testid="inv-private-fee"
                />
                Frais privatif (a charge d'un seul proprietaire)
              </label>
              {invForm.is_private_fee && (
                <div className="mt-3 space-y-1">
                  <label className="form-label text-xs">Proprietaire concerne *</label>
                  <Input
                    placeholder="Rechercher par nom, prenom ou email..."
                    value={ownerSearch}
                    onChange={e => setOwnerSearch(e.target.value)}
                    data-testid="private-fee-owner-search"
                  />
                  <div className="max-h-40 overflow-auto border border-amber-200 rounded bg-white">
                    {owners
                      .filter(o => {
                        const q = ownerSearch.toLowerCase();
                        if (!q) return true;
                        return (o.name||'').toLowerCase().includes(q) ||
                               (o.email||'').toLowerCase().includes(q) ||
                               (o.vcs_code||'').toLowerCase().includes(q);
                      })
                      .slice(0, 12)
                      .map(o => {
                        const isSel = o.id === invForm.private_fee_owner_id;
                        return (
                          <button
                            type="button"
                            key={o.id}
                            onClick={() => setInvForm(f => ({ ...f, private_fee_owner_id: o.id }))}
                            className={`w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-100 hover:bg-amber-50 ${isSel ? 'bg-amber-100 font-medium' : ''}`}
                            data-testid={`private-fee-owner-${o.id}`}
                          >
                            <div className="flex items-center justify-between">
                              <span>{o.name}</span>
                              <span className="text-slate-400 font-mono text-[10px]">{o.vcs_code || ''}</span>
                            </div>
                            {o.email && <div className="text-slate-400 text-[10px]">{o.email}</div>}
                          </button>
                        );
                      })}
                    {owners.length === 0 && (
                      <div className="px-3 py-2 text-xs text-slate-400">Aucun proprietaire en base</div>
                    )}
                  </div>
                  {invForm.private_fee_owner_id && (
                    <p className="text-[10px] text-amber-700">
                      Selectionne : <b>{owners.find(o => o.id === invForm.private_fee_owner_id)?.name || '-'}</b>.
                      Comptabilisation auto : Dr 643 / Cr Fournisseur + Dr 40000XXX (owner) / Cr 643.
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className={`grid grid-cols-3 gap-4 ${invForm.is_private_fee ? 'opacity-50 pointer-events-none' : ''}`}>
              <div><label className="form-label">Nature de depense</label>
                <Select
                  value={invForm.expense_category_id || 'none'}
                  onValueChange={v => {
                    if (v === '__create__') { setNewCatDialog(true); return; }
                    if (v === 'none') { setInvForm(f => ({...f, expense_category_id: ''})); return; }
                    const cat = categories.find(c => c.id === v);
                    setInvForm(f => ({...f, expense_category_id: v, account_number: cat?.account_number || f.account_number}));
                  }}
                >
                  <SelectTrigger data-testid="invoice-category-select"><SelectValue placeholder="Aucune" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Aucune —</SelectItem>
                    {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name} <span className="text-slate-400 ml-2 font-mono text-xs">({c.account_number})</span></SelectItem>)}
                    <SelectItem value="__create__" className="text-[#0055FF] font-semibold">+ Creer une nature de depense...</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-slate-400 mt-1">Pre-rempli le compte PCMN</p>
              </div>
              <div><label className="form-label">Compte PCMN</label>
                <AccountSearchSelect
                  accounts={accounts}
                  value={invForm.account_number}
                  onChange={v => setInvForm({...invForm, account_number: v})}
                  placeholder="Rechercher un compte..."
                  classFilter={6}
                  allowClear
                  testId="inv-account-search"
                />
              </div>
              <div><label className="form-label">Cle de repartition</label>
                <Select value={invForm.distribution_key_id} onValueChange={v => setInvForm({...invForm, distribution_key_id: v})}>
                  <SelectTrigger><SelectValue placeholder="Selectionner une cle" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Aucune</SelectItem>
                    {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setInvoiceDialog(false)}>Annuler</Button>
              <Button onClick={saveInvoice} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="inv-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Distribution Key Dialog */}
      <Dialog open={keyDialog} onOpenChange={(open) => { if (!open) { setEditingKey(null); setKeyUsage(null); } setKeyDialog(open); }}>
        <DialogContent className="max-w-2xl" data-testid="key-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingKey ? 'Modifier la cle' : 'Nouvelle cle de repartition'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            {editingKey && keyUsage && keyUsage.total > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs" data-testid="key-usage-warning">
                <div className="flex items-start gap-2 mb-1 text-amber-900 font-semibold">
                  <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
                  Cle utilisee par {keyUsage.invoices.length} facture(s), {keyUsage.budgets.length} budget(s), {keyUsage.fund_calls.length} appel(s)
                </div>
                <p className="text-amber-800 ml-6">
                  La modification entrainera leur <b>desindexation</b> de la cle. Pensez a reaffecter une cle apres sauvegarde.
                </p>
                {keyUsage.invoices.length > 0 && (
                  <details className="ml-6 mt-2 text-amber-900">
                    <summary className="cursor-pointer">Voir les factures liees ({keyUsage.invoices.length})</summary>
                    <ul className="mt-1 space-y-0.5 text-[11px]">
                      {keyUsage.invoices.slice(0, 8).map(i => (
                        <li key={i.id} className="font-mono">{fmtDate(i.date)} - {i.number} - {i.supplier} ({i.total_amount} EUR)</li>
                      ))}
                      {keyUsage.invoices.length > 8 && <li>... et {keyUsage.invoices.length - 8} autres</li>}
                    </ul>
                  </details>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Nom *</label><Input value={keyForm.name} onChange={e => setKeyForm({...keyForm, name: e.target.value})} data-testid="key-name" /></div>
              <div><label className="form-label">Type</label>
                <Select value={keyForm.key_type} onValueChange={v => setKeyForm({...keyForm, key_type: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="quotity">Tantiemes</SelectItem>
                    <SelectItem value="equal">Egal</SelectItem>
                    <SelectItem value="custom">Personnalise</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div><label className="form-label">Description</label><Input value={keyForm.description} onChange={e => setKeyForm({...keyForm, description: e.target.value})} /></div>
            {keyForm.lots.length > 0 && (() => {
              const totalShare = keyForm.lots.reduce((s, l) => s + (Number(l.share) || 0), 0);
              const lotsAtZero = keyForm.lots.filter(l => !Number(l.share)).length;
              const isRound = [1, 100, 1000, 10000].some(t => Math.abs(totalShare - t) < 0.005);
              let badgeColor = 'bg-slate-100 text-slate-700 border-slate-300';
              let badgeLabel = `Total : ${totalShare.toFixed(2)}`;
              if (lotsAtZero > 0) { badgeColor = 'bg-amber-50 text-amber-700 border-amber-300'; badgeLabel = `${lotsAtZero} lot(s) a 0 - Total ${totalShare.toFixed(2)}`; }
              else if (isRound) { badgeColor = 'bg-green-50 text-green-700 border-green-300'; badgeLabel = `Total : ${totalShare.toFixed(2)} - coherent`; }
              else if (totalShare > 0) { badgeColor = 'bg-blue-50 text-blue-700 border-blue-300'; badgeLabel = `Total : ${totalShare.toFixed(2)}`; }

              const fillEqual = () => {
                const n = keyForm.lots.length || 1;
                const share = +(1000 / n).toFixed(4);
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => ({ ...l, share }))});
              };
              const fillFromQuotities = () => {
                const byNumber = Object.fromEntries((lots || []).map(x => [x.number, x.quotity || 0]));
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => ({ ...l, share: byNumber[l.lot_number] || 0 }))});
              };
              const normalize1000 = () => {
                if (totalShare <= 0) return;
                const factor = 1000 / totalShare;
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => ({ ...l, share: +((Number(l.share) || 0) * factor).toFixed(4) }))});
              };

              return (
                <div>
                  <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                    <label className="form-label mb-0">Repartition par lot</label>
                    <div className="flex gap-1.5">
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={fillFromQuotities} data-testid="key-fill-from-quotities">Reprendre tantiemes lots</Button>
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={fillEqual} data-testid="key-fill-equal">Repartir egalement (=1000)</Button>
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={normalize1000} disabled={totalShare <= 0} data-testid="key-normalize-1000">Normaliser /1000</Button>
                    </div>
                  </div>
                  <div className="border rounded-md overflow-hidden max-h-60 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-50 text-xs text-slate-600 sticky top-0">
                        <tr><th className="p-2 text-left">Lot</th><th className="p-2 text-right">Quote-part</th><th className="p-2 text-right w-20">% du total</th></tr>
                      </thead>
                      <tbody>
                        {keyForm.lots.map((l, i) => {
                          const share = Number(l.share) || 0;
                          const pct = totalShare > 0 ? (share / totalShare * 100) : 0;
                          return (
                            <tr key={i} className={`border-t border-slate-100 ${!share ? 'bg-amber-50/40' : ''}`}>
                              <td className="p-2">Lot {l.lot_number}</td>
                              <td className="p-2"><Input type="number" step="0.01" min="0" className={`w-24 ml-auto text-right h-7 text-sm ${!share ? 'border-amber-300' : ''}`} value={l.share} onChange={e => updateKeyLot(i, 'share', e.target.value)} data-testid={`key-lot-share-${i}`} /></td>
                              <td className="p-2 text-right font-mono text-xs text-slate-500">{pct.toFixed(2)}%</td>
                            </tr>
                          );
                        })}
                      </tbody>
                      <tfoot>
                        <tr className="bg-slate-50 border-t-2 border-slate-300 text-xs font-semibold">
                          <td className="p-2">Total</td>
                          <td className="p-2 text-right font-mono" data-testid="key-form-total">{totalShare.toFixed(2)}</td>
                          <td className="p-2 text-right font-mono">{totalShare > 0 ? '100.00%' : '0%'}</td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                    <Badge variant="outline" className={badgeColor} data-testid="key-form-coherence">{badgeLabel}</Badge>
                    {lotsAtZero === 0 && !isRound && totalShare > 0 && (
                      <span className="text-slate-400 italic text-[11px]">Total libre : OK pour releves conso. Pour tantiemes, utilisez le bouton Normaliser /1000.</span>
                    )}
                  </div>
                </div>
              );
            })()}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setKeyDialog(false)}>Annuler</Button>
              <Button onClick={() => saveKey(false)} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="key-save-btn">{editingKey ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {/* Attachments Dialog */}
      <Dialog open={!!attachDialogInv} onOpenChange={() => setAttachDialogInv(null)}>
        <DialogContent className="max-w-lg" data-testid="invoice-attach-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Pieces jointes - {attachDialogInv?.number}</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="border-2 border-dashed rounded p-4 text-center">
              <input type="file" accept="application/pdf,image/*" className="hidden" id="att-input-inv"
                onChange={(e) => { const f = e.target.files?.[0]; if (f && attachDialogInv) uploadInvoiceAttachment(attachDialogInv.id, f); e.target.value=''; }} />
              <Button variant="outline" onClick={() => document.getElementById('att-input-inv').click()} data-testid="upload-attachment-inv-btn">
                <Paperclip size={14} className="mr-2" /> Ajouter un PDF / Image
              </Button>
              <div className="text-xs text-slate-500 mt-1">PDF, PNG ou JPG</div>
            </div>
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {(attachDialogInv?.attachments || []).length === 0 ? (
                <div className="text-sm text-slate-400 text-center py-3">Aucune piece jointe</div>
              ) : attachDialogInv.attachments.map((a) => (
                <div key={a.id} className="flex items-center justify-between border rounded px-2 py-1.5 text-sm">
                  <span className="truncate flex items-center gap-2"><Paperclip size={12} />{a.filename}</span>
                  <div className="flex gap-1">
                    <a href={`${API}/api/invoices/${attachDialogInv.id}/attachments/${a.id}/download`} target="_blank" rel="noreferrer">
                      <Button variant="ghost" size="sm"><Download size={14} /></Button>
                    </a>
                    <Button variant="ghost" size="sm" className="text-red-500" onClick={() => deleteInvoiceAttachment(attachDialogInv.id, a.id)}><Trash2 size={14} /></Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Create Expense Category inline dialog */}
      <Dialog open={newCatDialog} onOpenChange={setNewCatDialog}>
        <DialogContent className="max-w-md" data-testid="new-category-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvelle nature de depense</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <label className="form-label">Nom *</label>
              <Input
                value={newCatForm.name}
                onChange={e => setNewCatForm({...newCatForm, name: e.target.value})}
                placeholder="Ex: Entretien chaudiere"
                data-testid="new-cat-name"
              />
            </div>
            <div>
              <label className="form-label">Compte PCMN (classe 6) *</label>
              <AccountSearchSelect
                accounts={accounts.filter(a => (a.number || '').startsWith('6'))}
                value={newCatForm.account_number}
                onChange={v => setNewCatForm({...newCatForm, account_number: v})}
                placeholder="6XXX..."
                testId="new-cat-account"
              />
              <p className="text-[10px] text-slate-400 mt-1">Un compte PCMN ne peut etre lie qu&apos;a une seule nature.</p>
            </div>
            <div>
              <label className="form-label">Description</label>
              <Input
                value={newCatForm.description}
                onChange={e => setNewCatForm({...newCatForm, description: e.target.value})}
                placeholder="Optionnel"
                data-testid="new-cat-description"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setNewCatDialog(false)}>Annuler</Button>
              <Button
                className="bg-[#0055FF] hover:bg-[#0040CC]"
                disabled={!newCatForm.name.trim() || !newCatForm.account_number.trim()}
                onClick={async () => {
                  try {
                    const { data } = await api.post('/expense-categories', {
                      name: newCatForm.name.trim(),
                      account_number: newCatForm.account_number.trim(),
                      description: newCatForm.description.trim(),
                    });
                    toast.success('Nature de depense creee');
                    setCategories(prev => [...prev, data].sort((a, b) => (a.name || '').localeCompare(b.name || '')));
                    setInvForm(f => ({...f, expense_category_id: data.id, account_number: data.account_number}));
                    setNewCatForm({ name: '', account_number: '', description: '' });
                    setNewCatDialog(false);
                  } catch (err) {
                    toast.error(err.response?.data?.detail || 'Erreur creation');
                  }
                }}
                data-testid="new-cat-submit"
              >
                Creer et selectionner
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
