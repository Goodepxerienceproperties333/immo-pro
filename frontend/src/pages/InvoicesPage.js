import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Key, Receipt } from 'lucide-react';

export default function InvoicesPage() {
  const [tab, setTab] = useState('invoices');
  const [invoices, setInvoices] = useState([]);
  const [distKeys, setDistKeys] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [lots, setLots] = useState([]);
  const [invoiceDialog, setInvoiceDialog] = useState(false);
  const [keyDialog, setKeyDialog] = useState(false);
  const [invForm, setInvForm] = useState({ number: '', date: '', due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', distribution_key_id: '', status: 'unpaid' });
  const [keyForm, setKeyForm] = useState({ name: '', description: '', key_type: 'quotity', lots: [] });

  const load = useCallback(async () => {
    const [inv, dk, acc, lt] = await Promise.all([
      api.get('/invoices'), api.get('/distribution-keys'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }), api.get('/lots')
    ]);
    setInvoices(inv.data); setDistKeys(dk.data); setAccounts(acc.data); setLots(lt.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Invoice handlers
  const openCreateInvoice = () => {
    setInvForm({ number: `F-${Date.now().toString().slice(-6)}`, date: new Date().toISOString().split('T')[0], due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', distribution_key_id: '', status: 'unpaid' });
    setInvoiceDialog(true);
  };

  const saveInvoice = async () => {
    try {
      await api.post('/invoices', { ...invForm, total_amount: Number(invForm.total_amount), vat_amount: Number(invForm.vat_amount) });
      toast.success('Facture creee');
      setInvoiceDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteInvoice = async (id) => {
    if (!window.confirm('Supprimer cette facture ?')) return;
    await api.delete(`/invoices/${id}`); toast.success('Facture supprimee'); load();
  };

  // Distribution key handlers
  const openCreateKey = () => {
    setKeyForm({ name: '', description: '', key_type: 'quotity', lots: lots.map(l => ({ lot_id: l.id, lot_number: l.number, share: l.quotity || 0 })) });
    setKeyDialog(true);
  };

  const updateKeyLot = (i, field, value) => {
    const newLots = [...keyForm.lots];
    newLots[i] = { ...newLots[i], [field]: Number(value) };
    setKeyForm({ ...keyForm, lots: newLots });
  };

  const saveKey = async () => {
    try {
      await api.post('/distribution-keys', keyForm);
      toast.success('Cle creee'); setKeyDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
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
          <div className="flex justify-end mb-4">
            <Button onClick={openCreateInvoice} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-invoice-btn"><Plus size={16} className="mr-2" /> Nouvelle facture</Button>
          </div>
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>N</TableHead><TableHead>Date</TableHead><TableHead>Fournisseur</TableHead>
                <TableHead>Description</TableHead><TableHead className="text-right">Montant</TableHead>
                <TableHead>Cle</TableHead><TableHead>Statut</TableHead><TableHead className="w-20">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {invoices.length === 0 ? (
                  <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Aucune facture</TableCell></TableRow>
                ) : invoices.map(inv => (
                  <TableRow key={inv.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-mono text-sm">{inv.number}</TableCell>
                    <TableCell>{inv.date}</TableCell>
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
                      <Button variant="ghost" size="sm" onClick={() => deleteInvoice(inv.id)} className="text-red-500"><Trash2 size={14} /></Button>
                    </TableCell>
                  </TableRow>
                ))}
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
                <TableHead>Lots</TableHead><TableHead className="w-20">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {distKeys.length === 0 ? (
                  <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucune cle</TableCell></TableRow>
                ) : distKeys.map(k => (
                  <TableRow key={k.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-medium">{k.name}</TableCell>
                    <TableCell>{k.description}</TableCell>
                    <TableCell><Badge variant="outline">{k.key_type}</Badge></TableCell>
                    <TableCell className="text-sm">{k.lots?.length || 0} lots</TableCell>
                    <TableCell><Button variant="ghost" size="sm" onClick={() => deleteKey(k.id)} className="text-red-500"><Trash2 size={14} /></Button></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      {/* Invoice Dialog */}
      <Dialog open={invoiceDialog} onOpenChange={setInvoiceDialog}>
        <DialogContent className="max-w-2xl" data-testid="invoice-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvelle facture</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Numero *</label><Input value={invForm.number} onChange={e => setInvForm({...invForm, number: e.target.value})} data-testid="inv-number" /></div>
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
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Compte PCMN</label>
                <Select value={invForm.account_number} onValueChange={v => setInvForm({...invForm, account_number: v})}>
                  <SelectTrigger><SelectValue placeholder="Selectionner un compte" /></SelectTrigger>
                  <SelectContent>{accounts.map(a => <SelectItem key={a.number} value={a.number}>{a.number} - {a.name}</SelectItem>)}</SelectContent>
                </Select>
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
      <Dialog open={keyDialog} onOpenChange={setKeyDialog}>
        <DialogContent className="max-w-2xl" data-testid="key-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvelle cle de repartition</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
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
            {keyForm.lots.length > 0 && (
              <div>
                <label className="form-label mb-2">Repartition par lot</label>
                <div className="border rounded-md overflow-hidden max-h-60 overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead><tr className="bg-slate-50 text-xs text-slate-600"><th className="p-2 text-left">Lot</th><th className="p-2 text-right">Quote-part</th></tr></thead>
                    <tbody>
                      {keyForm.lots.map((l, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="p-2">Lot {l.lot_number}</td>
                          <td className="p-2"><Input type="number" step="0.01" className="w-24 ml-auto text-right h-7 text-sm" value={l.share} onChange={e => updateKeyLot(i, 'share', e.target.value)} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setKeyDialog(false)}>Annuler</Button>
              <Button onClick={saveKey} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="key-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
