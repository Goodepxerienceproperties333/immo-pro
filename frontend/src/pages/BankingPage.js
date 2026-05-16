import { useState, useEffect, useCallback, useRef } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { Plus, Trash2, Upload, Link2, Unlink, Search, Landmark, ArrowUpDown } from 'lucide-react';

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
  const [ownerSearch, setOwnerSearch] = useState('');
  const codaRef = useRef(null);
  const [stmtForm, setStmtForm] = useState({ number: '', date: '', account_number: '', opening_balance: 0, closing_balance: 0 });

  const load = useCallback(async () => {
    const [s, t, o, inv] = await Promise.all([
      api.get('/banking/statements'), api.get('/banking/transactions'),
      api.get('/owners'), api.get('/invoices')
    ]);
    setStatements(s.data); setTransactions(t.data); setOwners(o.data); setInvoices(inv.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const loadStmtTransactions = async (stmt) => {
    setSelectedStmt(stmt);
    const { data } = await api.get(`/banking/statements/${stmt.id}`);
    setTransactions(data.transactions || []);
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
      toast.success(data.message);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur import CODA');
    } finally {
      setCodaUploading(false);
      if (codaRef.current) codaRef.current.value = '';
    }
  };

  // Statement CRUD
  const saveStmt = async () => {
    try {
      await api.post('/banking/statements', { ...stmtForm, opening_balance: Number(stmtForm.opening_balance), closing_balance: Number(stmtForm.closing_balance) });
      toast.success('Extrait cree'); setStmtDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteStmt = async (id) => {
    if (!window.confirm('Supprimer cet extrait ?')) return;
    await api.delete(`/banking/statements/${id}`); toast.success('Extrait supprime'); load();
    if (selectedStmt?.id === id) setSelectedStmt(null);
  };

  // Lettrage
  const openLettrage = (txn) => { setLettrageTarget(txn); setLettrageDialog(true); };

  const doLettrage = async (matchToId, matchType) => {
    try {
      await api.post('/banking/lettrage', { transaction_id: lettrageTarget.id, match_to_id: matchToId, match_type: matchType });
      toast.success('Lettrage effectue'); setLettrageDialog(false);
      if (selectedStmt) loadStmtTransactions(selectedStmt); else load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const unlettrage = async (txnId) => {
    await api.post(`/banking/unlettrage/${txnId}`);
    toast.success('Lettrage annule');
    if (selectedStmt) loadStmtTransactions(selectedStmt); else load();
  };

  const filteredOwners = owners.filter(o => o.name.toLowerCase().includes(ownerSearch.toLowerCase()));
  const unpaidInvoices = invoices.filter(i => i.status === 'unpaid');

  return (
    <div data-testid="banking-page">
      <div className="page-header flex items-center justify-between flex-wrap gap-3">
        <div><h1 className="page-title">Interface Bancaire</h1><p className="page-subtitle">Extraits, transactions, lettrage et import CODA</p></div>
        <div className="flex gap-2">
          <input type="file" ref={codaRef} accept=".cod,.coda,.txt" onChange={handleCodaImport} className="hidden" />
          <Button onClick={() => codaRef.current?.click()} variant="outline" disabled={codaUploading} data-testid="coda-import-btn">
            <Upload size={16} className="mr-2" /> {codaUploading ? 'Import en cours...' : 'Import CODA'}
          </Button>
          <Button onClick={() => { setStmtForm({ number: '', date: new Date().toISOString().split('T')[0], account_number: '', opening_balance: 0, closing_balance: 0 }); setStmtDialog(true); }} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-stmt-btn">
            <Plus size={16} className="mr-2" /> Nouvel extrait
          </Button>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4" data-testid="banking-tabs">
          <TabsTrigger value="statements"><Landmark size={14} className="mr-2" /> Extraits</TabsTrigger>
          <TabsTrigger value="transactions"><ArrowUpDown size={14} className="mr-2" /> Transactions</TabsTrigger>
        </TabsList>

        <TabsContent value="statements" className="mt-0">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="space-y-3">
              {statements.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-8">Aucun extrait</p>
              ) : statements.map(s => (
                <Card
                  key={s.id}
                  className={`cursor-pointer transition-all border ${selectedStmt?.id === s.id ? 'border-[#0055FF] shadow-md' : 'border-slate-200 hover:border-slate-300'}`}
                  onClick={() => loadStmtTransactions(s)}
                  data-testid={`stmt-card-${s.id}`}
                >
                  <CardContent className="p-4">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono text-sm font-semibold">Extrait {s.number}</span>
                      <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); deleteStmt(s.id); }} className="text-red-400 h-6 w-6 p-0"><Trash2 size={12} /></Button>
                    </div>
                    <div className="text-xs text-slate-500">{s.date} - {s.account_number}</div>
                    <div className="flex justify-between mt-2 text-xs">
                      <span>Ouv: <strong className="font-mono">{s.opening_balance?.toFixed(2)}</strong></span>
                      <span>Ferm: <strong className="font-mono">{s.closing_balance?.toFixed(2)}</strong></span>
                    </div>
                    {s.source === 'CODA' && <Badge className="mt-2 bg-purple-50 text-purple-700 border-purple-200 text-[10px]" variant="outline">CODA</Badge>}
                  </CardContent>
                </Card>
              ))}
            </div>

            <div className="lg:col-span-2">
              {selectedStmt ? (
                <Card className="border-slate-200">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-lg" style={{fontFamily:'Chivo,sans-serif'}}>Transactions - Extrait {selectedStmt.number}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableHeader><TableRow>
                        <TableHead>Date</TableHead><TableHead>Contrepartie</TableHead><TableHead>Communication</TableHead>
                        <TableHead className="text-right">Montant</TableHead><TableHead>Lettrage</TableHead><TableHead className="w-20"></TableHead>
                      </TableRow></TableHeader>
                      <TableBody>
                        {transactions.length === 0 ? (
                          <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucune transaction</TableCell></TableRow>
                        ) : transactions.map(txn => (
                          <TableRow key={txn.id} className="hover:bg-slate-50/50">
                            <TableCell className="font-mono text-sm">{txn.date}</TableCell>
                            <TableCell className="text-sm">{txn.counterparty_name}</TableCell>
                            <TableCell className="text-sm max-w-[200px] truncate">{txn.communication}</TableCell>
                            <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                              {txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}
                            </TableCell>
                            <TableCell>
                              {txn.matched ? (
                                <Badge className="bg-green-50 text-green-700 border-green-200" variant="outline">{txn.match_type}</Badge>
                              ) : (
                                <Badge className="bg-slate-50 text-slate-500" variant="outline">Non lettre</Badge>
                              )}
                            </TableCell>
                            <TableCell>
                              {txn.matched ? (
                                <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500" title="Annuler lettrage"><Unlink size={14} /></Button>
                              ) : (
                                <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#0055FF]" title="Lettrer" data-testid={`lettrage-${txn.id}`}><Link2 size={14} /></Button>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              ) : (
                <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un extrait pour voir les transactions</div>
              )}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="transactions" className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Date</TableHead><TableHead>Contrepartie</TableHead><TableHead>Communication</TableHead>
                <TableHead className="text-right">Montant</TableHead><TableHead>Lettrage</TableHead><TableHead className="w-20"></TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {transactions.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucune transaction</TableCell></TableRow>
                ) : transactions.map(txn => (
                  <TableRow key={txn.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-mono text-sm">{txn.date}</TableCell>
                    <TableCell>{txn.counterparty_name}</TableCell>
                    <TableCell className="max-w-[200px] truncate">{txn.communication}</TableCell>
                    <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                      {txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}
                    </TableCell>
                    <TableCell>
                      {txn.matched ? <Badge className="bg-green-50 text-green-700" variant="outline">{txn.match_type}</Badge> : <Badge variant="outline" className="text-slate-400">Non lettre</Badge>}
                    </TableCell>
                    <TableCell>
                      {txn.matched ? (
                        <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500"><Unlink size={14} /></Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#0055FF]"><Link2 size={14} /></Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

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
            <p className="text-sm text-slate-600">Contrepartie: <strong>{lettrageTarget?.counterparty_name}</strong></p>
            <p className="text-sm text-slate-600">Communication: {lettrageTarget?.communication}</p>

            <Tabs defaultValue="invoices">
              <TabsList><TabsTrigger value="invoices">Factures impayees</TabsTrigger><TabsTrigger value="owners">Proprietaires</TabsTrigger></TabsList>
              <TabsContent value="invoices" className="mt-3">
                {unpaidInvoices.length === 0 ? <p className="text-sm text-slate-400">Aucune facture impayee</p> : (
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    {unpaidInvoices.map(inv => (
                      <div key={inv.id} className="flex items-center justify-between border rounded-md p-3 hover:bg-slate-50">
                        <div>
                          <span className="font-mono text-sm">{inv.number}</span> - <span className="text-sm">{inv.supplier}</span>
                          <div className="text-xs text-slate-500">{inv.date} - {inv.total_amount?.toFixed(2)} EUR</div>
                        </div>
                        <Button size="sm" variant="outline" onClick={() => doLettrage(inv.id, 'invoice')}>Lettrer</Button>
                      </div>
                    ))}
                  </div>
                )}
              </TabsContent>
              <TabsContent value="owners" className="mt-3">
                <div className="relative mb-3">
                  <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input placeholder="Rechercher un proprietaire..." value={ownerSearch} onChange={e => setOwnerSearch(e.target.value)} className="pl-9" />
                </div>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {filteredOwners.map(o => (
                    <div key={o.id} className="flex items-center justify-between border rounded-md p-3 hover:bg-slate-50">
                      <div>
                        <span className="text-sm font-medium">{o.name}</span>
                        <div className="text-xs text-slate-500">{o.email}</div>
                      </div>
                      <Button size="sm" variant="outline" onClick={() => doLettrage(o.id, 'owner_payment')}>Lettrer</Button>
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
