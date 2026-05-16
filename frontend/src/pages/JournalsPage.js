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
import { Plus, Trash2, Eye } from 'lucide-react';

const JOURNAL_TYPES = [
  { value: 'OD', label: 'Operations Diverses' },
  { value: 'AV', label: 'Avances' },
  { value: 'AP', label: 'Appels' },
];

export default function JournalsPage() {
  const [entries, setEntries] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [journalType, setJournalType] = useState('OD');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [viewEntry, setViewEntry] = useState(null);
  const [form, setForm] = useState({ journal_type: 'OD', date: '', reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });

  const load = useCallback(async () => {
    const [e, a] = await Promise.all([api.get('/accounting/entries', { params: { journal_type: journalType } }), api.get('/accounting/pcmn')]);
    setEntries(e.data);
    setAccounts(a.data);
  }, [journalType]);

  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setForm({ journal_type: journalType, date: new Date().toISOString().split('T')[0], reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
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
      await api.post('/accounting/entries', payload);
      toast.success('Ecriture creee');
      setDialogOpen(false);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm("Supprimer cette ecriture ?")) return;
    await api.delete(`/accounting/entries/${id}`);
    toast.success('Ecriture supprimee');
    load();
  };

  return (
    <div data-testid="journals-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Journaux Comptables</h1><p className="page-subtitle">Ecritures comptables par journal</p></div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-entry-btn"><Plus size={16} className="mr-2" /> Nouvelle ecriture</Button>
      </div>

      <Tabs value={journalType} onValueChange={setJournalType}>
        <TabsList className="mb-4" data-testid="journal-type-tabs">
          {JOURNAL_TYPES.map(j => <TabsTrigger key={j.value} value={j.value}>{j.label}</TabsTrigger>)}
        </TabsList>
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
                  <TableRow key={e.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-mono text-sm">{e.date}</TableCell>
                    <TableCell>{e.reference}</TableCell>
                    <TableCell className="font-medium">{e.description}</TableCell>
                    <TableCell className="text-right font-mono">{e.total_debit?.toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{e.total_credit?.toFixed(2)}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setViewEntry(e)}><Eye size={14} /></Button>
                        <Button variant="ghost" size="sm" onClick={() => handleDelete(e.id)} className="text-red-500"><Trash2 size={14} /></Button>
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
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl" data-testid="entry-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvelle ecriture comptable</DialogTitle></DialogHeader>
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
                    <th className="p-2 text-right">Debit</th><th className="p-2 text-right">Credit</th><th className="p-2 w-10"></th>
                  </tr></thead>
                  <tbody>
                    {form.lines.map((line, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="p-1">
                          <select className="w-full border rounded px-2 py-1 text-sm" value={line.account_number} onChange={e => updateLine(i, 'account_number', e.target.value)}>
                            <option value="">Choisir...</option>
                            {accounts.map(a => <option key={a.number} value={a.number}>{a.number} - {a.name}</option>)}
                          </select>
                        </td>
                        <td className="p-1 text-xs text-slate-500">{line.account_name}</td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8" value={line.debit} onChange={e => updateLine(i, 'debit', e.target.value)} /></td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8" value={line.credit} onChange={e => updateLine(i, 'credit', e.target.value)} /></td>
                        <td className="p-1">{form.lines.length > 2 && <button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button>}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot><tr className="border-t-2 border-slate-200 bg-slate-50 font-semibold text-sm">
                    <td colSpan={2} className="p-2">
                      <Button variant="ghost" size="sm" onClick={addLine} className="text-xs"><Plus size={12} className="mr-1" /> Ajouter ligne</Button>
                    </td>
                    <td className="p-2 text-right font-mono">{totalDebit.toFixed(2)}</td>
                    <td className="p-2 text-right font-mono">{totalCredit.toFixed(2)}</td>
                    <td></td>
                  </tr></tfoot>
                </table>
              </div>
              {!isBalanced && <p className="text-red-500 text-xs mt-1">Ecart: {Math.abs(totalDebit - totalCredit).toFixed(2)} EUR</p>}
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
                <div><span className="text-slate-500">Date:</span> <span className="font-medium">{viewEntry.date}</span></div>
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
    </div>
  );
}
