import { useState, useEffect, useCallback, useRef } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { Plus, Trash2, Upload, Link2, Unlink, Search, Landmark, ArrowUpDown, PlusCircle, Save } from 'lucide-react';

export default function BankingPage() {
  const [tab, setTab] = useState('statements');
  const [statements, setStatements] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [selectedStmt, setSelectedStmt] = useState(null);
  const [stmtDialog, setStmtDialog] = useState(false);
  const [codaUploading, setCodaUploading] = useState(false);
  const [lettrageDialog, setLettrageDialog] = useState(false);
  const [lettrageTarget, setLettrageTarget] = useState(null);
  const [owners, setOwners] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [ownerSearch, setOwnerSearch] = useState('');
  const [vcsLookupResult, setVcsLookupResult] = useState(null);
  const codaRef = useRef(null);
  const [stmtForm, setStmtForm] = useState({ number: '', date: '', account_number: '', opening_balance: 0, closing_balance: 0 });

  // Inline lines for adding to statement
  const [inlineLines, setInlineLines] = useState([]);

  const load = useCallback(async () => {
    const [s, t, o, inv, sup] = await Promise.all([
      api.get('/banking/statements'), api.get('/banking/transactions'),
      api.get('/owners'), api.get('/invoices'), api.get('/suppliers')
    ]);
    setStatements(s.data); setTransactions(t.data); setOwners(o.data); setInvoices(inv.data); setSuppliers(sup.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const loadStmtTransactions = async (stmt) => {
    setSelectedStmt(stmt);
    const { data } = await api.get(`/banking/statements/${stmt.id}`);
    setTransactions(data.transactions || []);
    setInlineLines([]);
  };

  // CODA Import
  const handleCodaImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setCodaUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await api.post('/banking/coda/import', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success(data.message); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur import CODA'); }
    finally { setCodaUploading(false); if (codaRef.current) codaRef.current.value = ''; }
  };

  // Statement CRUD
  const saveStmt = async () => {
    try {
      await api.post('/banking/statements', { ...stmtForm, opening_balance: Number(stmtForm.opening_balance), closing_balance: Number(stmtForm.closing_balance) });
      toast.success('Extrait cree'); setStmtDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteStmt = async (id) => {
    if (!window.confirm('Supprimer cet extrait et ses transactions ?')) return;
    await api.delete(`/banking/statements/${id}`); toast.success('Supprime'); load();
    if (selectedStmt?.id === id) { setSelectedStmt(null); setTransactions([]); }
  };

  // INLINE LINE ENTRY
  const addInlineLine = () => {
    setInlineLines([...inlineLines, { date: new Date().toISOString().split('T')[0], amount: 0, counterparty_name: '', communication: '', transaction_type: 'credit' }]);
  };
  const updateInlineLine = (i, field, value) => {
    const lines = [...inlineLines]; lines[i] = { ...lines[i], [field]: value }; setInlineLines(lines);
  };
  const removeInlineLine = (i) => setInlineLines(inlineLines.filter((_, idx) => idx !== i));

  const saveInlineLines = async () => {
    if (!selectedStmt || inlineLines.length === 0) return;
    const validLines = inlineLines.filter(l => Math.abs(l.amount) > 0.001);
    if (validLines.length === 0) { toast.error('Aucune ligne valide'); return; }
    try {
      const { data } = await api.post(`/banking/statements/${selectedStmt.id}/add-lines`, { lines: validLines.map(l => ({ ...l, amount: Number(l.amount) })) });
      toast.success(data.message); setInlineLines([]); loadStmtTransactions(selectedStmt);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // VCS Lookup on communication change
  const handleVcsLookup = async (value) => {
    if (value.length >= 3) {
      try { const { data } = await api.get('/banking/vcs-lookup', { params: { communication: value } }); setVcsLookupResult(data.owner); } catch { setVcsLookupResult(null); }
    } else { setVcsLookupResult(null); }
  };

  // Lettrage
  const openLettrage = (txn) => { setLettrageTarget(txn); setLettrageDialog(true); setOwnerSearch(''); };
  const doLettrage = async (matchToId, matchType) => {
    try {
      await api.post('/banking/lettrage', { transaction_id: lettrageTarget.id, match_to_id: matchToId, match_type: matchType });
      toast.success('Lettrage effectue'); setLettrageDialog(false);
      if (selectedStmt) loadStmtTransactions(selectedStmt); else load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const unlettrage = async (txnId) => {
    await api.post(`/banking/unlettrage/${txnId}`); toast.success('Lettrage annule');
    if (selectedStmt) loadStmtTransactions(selectedStmt); else load();
  };

  const filteredOwners = owners.filter(o => o.name.toLowerCase().includes(ownerSearch.toLowerCase()) || (o.vcs_code || '').includes(ownerSearch));
  const unpaidInvoices = invoices.filter(i => i.status === 'unpaid');

  return (
    <div data-testid="banking-page">
      <div className="page-header flex items-center justify-between flex-wrap gap-3">
        <div><h1 className="page-title">Interface Bancaire</h1><p className="page-subtitle">Extraits, saisie en ligne, lettrage et import CODA</p></div>
        <div className="flex gap-2">
          <input type="file" ref={codaRef} accept=".cod,.coda,.txt" onChange={handleCodaImport} className="hidden" />
          <Button onClick={() => codaRef.current?.click()} variant="outline" disabled={codaUploading} data-testid="coda-import-btn">
            <Upload size={16} className="mr-2" /> {codaUploading ? 'Import...' : 'Import CODA'}
          </Button>
          <Button onClick={() => { setStmtForm({ number: '', date: new Date().toISOString().split('T')[0], account_number: '', opening_balance: 0, closing_balance: 0 }); setStmtDialog(true); }} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-stmt-btn">
            <Plus size={16} className="mr-2" /> Nouvel extrait
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Statement list */}
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold px-1">Extraits de compte</div>
          {statements.length === 0 ? <p className="text-sm text-slate-400 text-center py-4">Aucun extrait</p> : statements.map(s => (
            <Card key={s.id} className={`cursor-pointer transition-all border ${selectedStmt?.id === s.id ? 'border-[#0055FF] shadow-md' : 'border-slate-200 hover:border-slate-300'}`}
              onClick={() => loadStmtTransactions(s)} data-testid={`stmt-card-${s.id}`}>
              <CardContent className="p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-sm font-semibold">N {s.number}</span>
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); deleteStmt(s.id); }} className="text-red-400 h-5 w-5 p-0"><Trash2 size={10} /></Button>
                </div>
                <div className="text-xs text-slate-500">{s.date}</div>
                <div className="flex justify-between mt-1 text-[10px] font-mono">
                  <span>O: {s.opening_balance?.toFixed(2)}</span>
                  <span>F: {s.closing_balance?.toFixed(2)}</span>
                </div>
                {s.source === 'CODA' && <Badge className="mt-1 text-[9px]" variant="outline">CODA</Badge>}
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Transactions + inline entry */}
        <div className="lg:col-span-3">
          {selectedStmt ? (
            <Card className="border-slate-200">
              <CardHeader className="pb-2 flex flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Extrait N {selectedStmt.number} - {selectedStmt.date}</CardTitle>
                  <p className="text-xs text-slate-500">Solde ouverture: {selectedStmt.opening_balance?.toFixed(2)} EUR - Fermeture: {selectedStmt.closing_balance?.toFixed(2)} EUR</p>
                </div>
                <Button size="sm" variant="outline" onClick={addInlineLine} data-testid="add-inline-line">
                  <PlusCircle size={14} className="mr-1" /> Ajouter lignes
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                {/* Inline line entry */}
                {inlineLines.length > 0 && (
                  <div className="border-b-2 border-[#0055FF] bg-blue-50/30 p-3">
                    <div className="text-xs font-semibold text-[#0055FF] mb-2 uppercase tracking-wider">Nouvelles lignes</div>
                    <table className="w-full text-sm">
                      <thead><tr className="text-[10px] text-slate-500 uppercase">
                        <th className="p-1 text-left w-28">Date</th><th className="p-1 text-right w-28">Montant</th><th className="p-1 text-left w-20">Type</th>
                        <th className="p-1 text-left">Contrepartie</th><th className="p-1 text-left">Communication</th><th className="p-1 w-8"></th>
                      </tr></thead>
                      <tbody>
                        {inlineLines.map((line, i) => (
                          <tr key={i} className="border-t border-blue-100">
                            <td className="p-1"><Input type="date" className="h-7 text-xs" value={line.date} onChange={e => updateInlineLine(i, 'date', e.target.value)} /></td>
                            <td className="p-1"><Input type="number" step="0.01" className="h-7 text-xs text-right" value={line.amount} onChange={e => updateInlineLine(i, 'amount', e.target.value)} /></td>
                            <td className="p-1">
                              <select className="h-7 text-xs border rounded px-1 w-full" value={line.transaction_type} onChange={e => updateInlineLine(i, 'transaction_type', e.target.value)}>
                                <option value="credit">+</option><option value="debit">-</option>
                              </select>
                            </td>
                            <td className="p-1"><Input className="h-7 text-xs" value={line.counterparty_name} onChange={e => updateInlineLine(i, 'counterparty_name', e.target.value)} placeholder="Nom" /></td>
                            <td className="p-1 relative">
                              <Input className="h-7 text-xs" value={line.communication} onChange={e => { updateInlineLine(i, 'communication', e.target.value); handleVcsLookup(e.target.value); }} placeholder="Communication / VCS" />
                              {vcsLookupResult && i === inlineLines.length - 1 && (
                                <div className="absolute top-8 left-0 z-10 bg-white border border-blue-200 shadow-lg rounded p-2 text-xs">
                                  <span className="text-[#0055FF] font-medium">VCS:</span> {vcsLookupResult.name} <span className="font-mono">{vcsLookupResult.vcs_code}</span>
                                </div>
                              )}
                            </td>
                            <td className="p-1"><button onClick={() => removeInlineLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="flex gap-2 mt-2 justify-end">
                      <Button size="sm" variant="ghost" onClick={addInlineLine}><Plus size={12} className="mr-1" /> Ligne</Button>
                      <Button size="sm" onClick={saveInlineLines} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="save-inline-lines"><Save size={12} className="mr-1" /> Enregistrer</Button>
                    </div>
                  </div>
                )}

                {/* Existing transactions */}
                <Table>
                  <TableHeader><TableRow>
                    <TableHead className="w-24">Date</TableHead><TableHead>Contrepartie</TableHead><TableHead>Communication</TableHead>
                    <TableHead className="text-right w-28">Montant</TableHead><TableHead className="w-28">Lettrage</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {transactions.length === 0 ? (
                      <TableRow><TableCell colSpan={6} className="text-center py-6 text-slate-400 text-sm">Aucune transaction - Cliquez "Ajouter lignes" pour saisir</TableCell></TableRow>
                    ) : transactions.map(txn => (
                      <TableRow key={txn.id} className="hover:bg-slate-50/50">
                        <TableCell className="font-mono text-xs">{txn.date}</TableCell>
                        <TableCell className="text-sm">{txn.counterparty_name}</TableCell>
                        <TableCell className="text-sm max-w-[180px] truncate">{txn.communication}</TableCell>
                        <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                          {txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}
                        </TableCell>
                        <TableCell>
                          {txn.matched ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline">{txn.match_type === 'owner_payment' ? 'Proprio' : txn.match_type === 'supplier_payment' ? 'Fourniss.' : 'Facture'}</Badge>
                            : <Badge variant="outline" className="text-slate-400 text-[10px]">Non lettre</Badge>}
                        </TableCell>
                        <TableCell>
                          {txn.matched ? <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500 h-6 w-6 p-0"><Unlink size={12} /></Button>
                            : <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#0055FF] h-6 w-6 p-0" data-testid={`lettrage-${txn.id}`}><Link2 size={12} /></Button>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un extrait pour voir et saisir des transactions</div>
          )}
        </div>
      </div>

      {/* Statement Dialog */}
      <Dialog open={stmtDialog} onOpenChange={setStmtDialog}>
        <DialogContent data-testid="stmt-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvel extrait de compte</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Numero *</label><Input value={stmtForm.number} onChange={e => setStmtForm({...stmtForm, number: e.target.value})} /></div>
              <div><label className="form-label">Date *</label><Input type="date" value={stmtForm.date} onChange={e => setStmtForm({...stmtForm, date: e.target.value})} /></div>
            </div>
            <div><label className="form-label">Compte bancaire</label><Input value={stmtForm.account_number} onChange={e => setStmtForm({...stmtForm, account_number: e.target.value})} placeholder="BE00 0000 0000 0000" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Solde ouverture</label><Input type="number" step="0.01" value={stmtForm.opening_balance} onChange={e => setStmtForm({...stmtForm, opening_balance: e.target.value})} /></div>
              <div><label className="form-label">Solde fermeture</label><Input type="number" step="0.01" value={stmtForm.closing_balance} onChange={e => setStmtForm({...stmtForm, closing_balance: e.target.value})} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setStmtDialog(false)}>Annuler</Button>
              <Button onClick={saveStmt} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="stmt-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Lettrage Dialog */}
      <Dialog open={lettrageDialog} onOpenChange={setLettrageDialog}>
        <DialogContent className="max-w-2xl" data-testid="lettrage-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Lettrage - {lettrageTarget?.amount?.toFixed(2)} EUR</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <p className="text-sm text-slate-600">Contrepartie: <strong>{lettrageTarget?.counterparty_name}</strong> | Communication: {lettrageTarget?.communication}</p>
            <Tabs defaultValue="owners">
              <TabsList><TabsTrigger value="owners">Proprietaires</TabsTrigger><TabsTrigger value="invoices">Factures impayees</TabsTrigger><TabsTrigger value="suppliers">Fournisseurs</TabsTrigger></TabsList>
              <TabsContent value="owners" className="mt-3">
                <div className="relative mb-3"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input placeholder="Rechercher par nom ou VCS..." value={ownerSearch} onChange={e => setOwnerSearch(e.target.value)} className="pl-9" /></div>
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {filteredOwners.map(o => (
                    <div key={o.id} className="flex items-center justify-between border rounded p-2 hover:bg-slate-50 text-sm">
                      <div><span className="font-medium">{o.name}</span>{o.vcs_code && <span className="ml-2 font-mono text-xs text-[#0055FF]">{o.vcs_code}</span>}</div>
                      <Button size="sm" variant="outline" onClick={() => doLettrage(o.id, 'owner_payment')}>Lettrer</Button>
                    </div>
                  ))}
                </div>
              </TabsContent>
              <TabsContent value="invoices" className="mt-3">
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {unpaidInvoices.length === 0 ? <p className="text-sm text-slate-400">Aucune facture impayee</p> : unpaidInvoices.map(inv => (
                    <div key={inv.id} className="flex items-center justify-between border rounded p-2 hover:bg-slate-50 text-sm">
                      <div><span className="font-mono">{inv.number}</span> - {inv.supplier} <span className="text-slate-500">{inv.total_amount?.toFixed(2)} EUR</span></div>
                      <Button size="sm" variant="outline" onClick={() => doLettrage(inv.id, 'invoice')}>Lettrer</Button>
                    </div>
                  ))}
                </div>
              </TabsContent>
              <TabsContent value="suppliers" className="mt-3">
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {suppliers.map(s => (
                    <div key={s.id} className="flex items-center justify-between border rounded p-2 hover:bg-slate-50 text-sm">
                      <div><span className="font-medium">{s.name}</span>{s.vat_number && <span className="ml-2 text-xs text-slate-400">{s.vat_number}</span>}</div>
                      <Button size="sm" variant="outline" onClick={() => doLettrage(s.id, 'supplier_payment')}>Lettrer</Button>
                    </div>
                  ))}
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
