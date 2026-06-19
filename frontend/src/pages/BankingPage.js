import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
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
import { Plus, Trash2, Upload, Link2, Unlink, Search, Landmark, PlusCircle, Save, Pencil, X, CheckCircle2, AlertTriangle, Eye } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import CounterpartySearchSelect from '@/components/CounterpartySearchSelect';
import { fmtDate } from '@/lib/dateFmt';

export default function BankingPage() {
  const { selectedCopro } = useAuth();
  const navigate = useNavigate();
  const [statements, setStatements] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [selectedStmt, setSelectedStmt] = useState(null);
  const [stmtDialog, setStmtDialog] = useState(false);
  const [editingStmtId, setEditingStmtId] = useState(null);
  const [codaUploading, setCodaUploading] = useState(false);
  const [lettrageDialog, setLettrageDialog] = useState(false);
  const [lettrageTarget, setLettrageTarget] = useState(null);
  const [editingTxn, setEditingTxn] = useState(null);
  const [owners, setOwners] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [lookupResults, setLookupResults] = useState(null);
  const [lookupQuery, setLookupQuery] = useState('');
  const codaRef = useRef(null);
  const [stmtForm, setStmtForm] = useState({ number: '', date: '', account_number: '', opening_balance: 0, closing_balance: 0 });
  const [bankAccounts, setBankAccounts] = useState([]);
  const [inlineLines, setInlineLines] = useState([]);
  const [editForm, setEditForm] = useState({});

  const load = useCallback(async () => {
    const promises = [
      api.get('/banking/statements'), api.get('/banking/transactions'),
      api.get('/owners'), api.get('/invoices'), api.get('/suppliers')
    ];
    if (selectedCopro) promises.push(api.get(`/coproprietes/${selectedCopro}`));
    const [s, t, o, inv, sup, c] = await Promise.all(promises);
    setStatements(s.data); setTransactions(t.data); setOwners(o.data); setInvoices(inv.data); setSuppliers(sup.data);
    setBankAccounts(c?.data?.bank_accounts || []);
  }, [selectedCopro]);
  useEffect(() => { load(); }, [load]);

  const loadStmtTxns = async (stmt) => {
    setSelectedStmt(stmt);
    const { data } = await api.get(`/banking/statements/${stmt.id}`);
    setTransactions(data.transactions || []);
    setInlineLines([]); setEditingTxn(null);
  };

  const handleCodaImport = async (e) => {
    const file = e.target.files[0]; if (!file) return; setCodaUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const coproId = localStorage.getItem('selectedCopro');
      if (coproId) fd.append('copropriete_id', coproId);
      const { data } = await api.post('/banking/coda/import', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success(data.message); load();
    }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur CODA'); }
    finally { setCodaUploading(false); if (codaRef.current) codaRef.current.value = ''; }
  };

  const saveStmt = async () => {
    try {
      const payload = { ...stmtForm, opening_balance: Number(stmtForm.opening_balance), closing_balance: Number(stmtForm.closing_balance) };
      if (editingStmtId) {
        await api.put(`/banking/statements/${editingStmtId}`, payload);
        toast.success('Extrait modifie');
        // Recharge le selected pour mettre a jour le bandeau equilibre
        const refreshed = await api.get(`/banking/statements/${editingStmtId}`);
        if (refreshed.data && selectedStmt?.id === editingStmtId) {
          setSelectedStmt({ ...selectedStmt, ...refreshed.data });
        }
      } else {
        await api.post('/banking/statements', payload);
        toast.success('Extrait cree');
      }
      setEditingStmtId(null);
      setStmtDialog(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const deleteStmt = async (id) => { if (!window.confirm('Supprimer cet extrait ?')) return; await api.delete(`/banking/statements/${id}`); toast.success('Supprime'); load(); if (selectedStmt?.id === id) { setSelectedStmt(null); setTransactions([]); } };

  // INLINE LINES
  const addInlineLine = () => setInlineLines([...inlineLines, { date: new Date().toISOString().split('T')[0], amount: 0, counterparty_name: '', counterparty_account: '', communication: '', transaction_type: 'credit', counterparty_id: '', counterparty_type: '' }]);
  const updateLine = (i, f, v) => { const l = [...inlineLines]; l[i] = { ...l[i], [f]: v }; setInlineLines(l); };
  const removeLine = (i) => setInlineLines(inlineLines.filter((_, idx) => idx !== i));

  const saveLines = async () => {
    if (!selectedStmt || inlineLines.length === 0) return;
    const valid = inlineLines.filter(l => Math.abs(l.amount) > 0.001);
    if (!valid.length) { toast.error('Aucune ligne valide'); return; }
    try { const { data } = await api.post(`/banking/statements/${selectedStmt.id}/add-lines`, { lines: valid.map(l => ({ ...l, amount: Number(l.amount) })) }); toast.success(data.message); setInlineLines([]); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // GLOBAL LOOKUP
  const doLookup = async (q, lineIdx) => {
    if (q.length < 2) { setLookupResults(null); return; }
    try { const { data } = await api.get('/banking/lookup', { params: { q } }); setLookupResults({ ...data, lineIdx, query: q }); } catch { setLookupResults(null); }
  };

  // EDIT EXISTING TXN
  const startEdit = (txn) => {
    setEditingTxn(txn.id);
    setEditForm({
      date: txn.date,
      amount: txn.amount,
      counterparty_name: txn.counterparty_name || '',
      counterparty_account: txn.counterparty_account || '',
      communication: txn.communication || '',
      transaction_type: txn.transaction_type || 'credit',
      counterparty_id: txn.counterparty_id || '',
      counterparty_type: txn.counterparty_type || '',
    });
  };
  const cancelEdit = () => { setEditingTxn(null); };
  const saveEdit = async () => {
    try { await api.put(`/banking/transactions/${editingTxn}`, { ...editForm, amount: Number(editForm.amount) }); toast.success('Transaction modifiee'); setEditingTxn(null); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteTxn = async (id) => {
    if (!window.confirm('Supprimer cette transaction ? Cette action est irreversible.')) return;
    try { await api.delete(`/banking/transactions/${id}`); toast.success('Transaction supprimee'); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // LETTRAGE
  const openLettrage = (txn) => { setLettrageTarget(txn); setLettrageDialog(true); setLookupQuery(''); };
  const doLettrage = async (id, type) => { try { await api.post('/banking/lettrage', { transaction_id: lettrageTarget.id, match_to_id: id, match_type: type }); toast.success('Lettre'); setLettrageDialog(false); if (selectedStmt) loadStmtTxns(selectedStmt); else load(); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const unlettrage = async (id) => { await api.post(`/banking/unlettrage/${id}`); toast.success('Delettrage'); if (selectedStmt) loadStmtTxns(selectedStmt); else load(); load(); };
  const unlettrageByInvoice = async (invId) => { try { await api.post(`/banking/unlettrage-by-invoice/${invId}`); toast.success('Facture delettree'); if (selectedStmt) loadStmtTxns(selectedStmt); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  // unpaidInv (legacy) supprime - l'onglet factures affiche maintenant toutes les factures avec coloration.

  // unpaidInv (legacy) supprime - l'onglet factures affiche maintenant toutes les factures avec coloration.

  return (
    <div data-testid="banking-page">
      <div className="page-header flex items-center justify-between flex-wrap gap-3">
        <div><h1 className="page-title">Interface Bancaire</h1><p className="page-subtitle">Extraits de compte, encodage et lettrage</p></div>
        <div className="flex gap-2">
          <Button
            onClick={() => navigate('/reports?tab=bilan')}
            variant="outline"
            className="text-[#0055FF] border-[#0055FF]/30 hover:bg-[#0055FF]/10"
            data-testid="view-bilan-btn"
            title="Ouvrir le bilan de la copropriete"
          >
            <Eye size={16} className="mr-2" /> Voir le bilan
          </Button>
          <input type="file" ref={codaRef} accept=".cod,.coda,.txt" onChange={handleCodaImport} className="hidden" />
          <Button onClick={() => codaRef.current?.click()} variant="outline" disabled={codaUploading} data-testid="coda-import-btn"><Upload size={16} className="mr-2" /> {codaUploading ? 'Import...' : 'Import CODA'}</Button>
          <Button onClick={() => {
            const def = bankAccounts.find(b => b.is_default) || bankAccounts[0];
            setStmtForm({ number: '', date: new Date().toISOString().split('T')[0], account_number: def?.iban || '', opening_balance: 0, closing_balance: 0 });
            setStmtDialog(true);
          }} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-stmt-btn"><Plus size={16} className="mr-2" /> Nouvel extrait</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Statements sidebar */}
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold px-1">Extraits</div>
          {statements.length === 0 ? <p className="text-sm text-slate-400 text-center py-4">Aucun extrait</p> : statements.map(s => (
            <Card key={s.id} className={`cursor-pointer transition-all border text-sm ${selectedStmt?.id === s.id ? 'border-[#0055FF] shadow-md' : 'border-slate-200 hover:border-slate-300'}`} onClick={() => loadStmtTxns(s)} data-testid={`stmt-card-${s.id}`}>
              <CardContent className="p-3">
                <div className="flex items-center justify-between"><span className="font-mono font-semibold">N {s.number}</span><Button variant="ghost" size="sm" onClick={e => { e.stopPropagation(); deleteStmt(s.id); }} className="text-red-400 h-5 w-5 p-0"><Trash2 size={10} /></Button></div>
                <div className="text-xs text-slate-500">{fmtDate(s.date)}</div>
                <div className="flex justify-between mt-1 text-[10px] font-mono"><span>O:{s.opening_balance?.toFixed(2)}</span><span>F:{s.closing_balance?.toFixed(2)}</span></div>
                <div className="flex gap-1 mt-1 flex-wrap">
                  {s.status === 'posted' && <Badge className="text-[9px] bg-green-100 text-green-700 border-green-300">Comptabilise</Badge>}
                  {s.source === 'CODA' && <Badge className="text-[9px]" variant="outline">CODA</Badge>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Main panel */}
        <div className="lg:col-span-3">
          {selectedStmt ? (
            <Card className="border-slate-200">
              <CardHeader className="pb-2">
                <div className="flex flex-row items-start justify-between gap-3">
                  <div className="flex-1">
                    <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
                      Extrait N {selectedStmt.number} - {fmtDate(selectedStmt.date)}
                      {selectedStmt.status === 'posted' ? (
                        <Badge className="bg-green-100 text-green-700 border-green-300"><CheckCircle2 size={11} className="mr-1" />Comptabilise</Badge>
                      ) : (
                        <Badge variant="outline" className="bg-slate-50 text-slate-500">Brouillon</Badge>
                      )}
                    </CardTitle>
                    <p className="text-xs text-slate-500 mt-1 font-mono">{selectedStmt.account_number}</p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {selectedStmt.status !== 'posted' && (
                      <>
                        <Button size="sm" variant="outline" onClick={() => {
                          setStmtForm({
                            number: selectedStmt.number || '',
                            date: selectedStmt.date || '',
                            account_number: selectedStmt.account_number || '',
                            opening_balance: selectedStmt.opening_balance || 0,
                            closing_balance: selectedStmt.closing_balance || 0,
                          });
                          setEditingStmtId(selectedStmt.id);
                          setStmtDialog(true);
                        }} data-testid="edit-stmt-btn"><Pencil size={14} className="mr-1" /> Modifier</Button>
                        <Button size="sm" variant="outline" onClick={addInlineLine} data-testid="add-inline-line"><PlusCircle size={14} className="mr-1" /> Ajouter lignes</Button>
                      </>
                    )}
                    {(() => {
                      // Compute balance check inline
                      const opening = Number(selectedStmt.opening_balance || 0);
                      const closing = Number(selectedStmt.closing_balance || 0);
                      const mvts = transactions.reduce((s, t) => s + (t.transaction_type === 'credit' ? 1 : -1) * Math.abs(Number(t.amount || 0)), 0);
                      const computed = Math.round((opening + mvts) * 100) / 100;
                      const diff = Math.round((computed - closing) * 100) / 100;
                      const balanced = Math.abs(diff) < 0.01;
                      if (selectedStmt.status === 'posted') return (
                        <Button size="sm" variant="outline" onClick={async () => {
                          if (!window.confirm('Repasser cet extrait en brouillon ?')) return;
                          try {
                            await api.post(`/banking/statements/${selectedStmt.id}/unpost`);
                            toast.success('Extrait repasse en brouillon');
                            loadStmtTxns({ ...selectedStmt, status: 'draft' });
                            load();
                          } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
                        }} data-testid="unpost-stmt-btn">Repasser brouillon</Button>
                      );
                      return (
                        <Button
                          size="sm"
                          onClick={async () => {
                            try {
                              await api.post(`/banking/statements/${selectedStmt.id}/post`);
                              toast.success('Extrait comptabilise');
                              loadStmtTxns({ ...selectedStmt, status: 'posted' });
                              load();
                            } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
                          }}
                          disabled={!balanced || transactions.length === 0}
                          className={balanced && transactions.length > 0 ? "bg-green-600 hover:bg-green-700 text-white" : ""}
                          data-testid="post-stmt-btn"
                          title={!balanced ? `Difference: ${diff.toFixed(2)} EUR. Ajustez les mouvements ou le solde de fermeture.` : "Comptabiliser l'extrait"}
                        >
                          <CheckCircle2 size={14} className="mr-1" />
                          Comptabiliser
                        </Button>
                      );
                    })()}
                  </div>
                </div>
                {/* Bandeau equilibre */}
                {(() => {
                  const opening = Number(selectedStmt.opening_balance || 0);
                  const closing = Number(selectedStmt.closing_balance || 0);
                  const mvts = transactions.reduce((s, t) => s + (t.transaction_type === 'credit' ? 1 : -1) * Math.abs(Number(t.amount || 0)), 0);
                  const computed = Math.round((opening + mvts) * 100) / 100;
                  const diff = Math.round((computed - closing) * 100) / 100;
                  const balanced = Math.abs(diff) < 0.01;
                  return (
                    <div className={`mt-3 p-2 rounded text-xs flex items-center justify-between gap-4 ${balanced ? 'bg-green-50 border border-green-200' : 'bg-amber-50 border border-amber-200'}`} data-testid="balance-check-banner">
                      <div className="flex gap-4">
                        <span>Solde ouverture : <b className="font-mono">{opening.toFixed(2)}</b></span>
                        <span>+ Mouvements : <b className={`font-mono ${mvts >= 0 ? 'text-green-700' : 'text-red-700'}`}>{mvts >= 0 ? '+' : ''}{mvts.toFixed(2)}</b></span>
                        <span>= Solde calcule : <b className="font-mono">{computed.toFixed(2)}</b></span>
                        <span>vs. saisi : <b className="font-mono">{closing.toFixed(2)}</b></span>
                      </div>
                      {balanced ? (
                        <span className="text-green-700 flex items-center gap-1"><CheckCircle2 size={12} /> Equilibre</span>
                      ) : (
                        <span className="text-amber-700 flex items-center gap-1"><AlertTriangle size={12} /> Difference : <b className="font-mono">{diff > 0 ? '+' : ''}{diff.toFixed(2)}</b></span>
                      )}
                    </div>
                  );
                })()}
              </CardHeader>
              <CardContent className="p-0">
                {/* Inline entry */}
                {inlineLines.length > 0 && (
                  <div className="border-b-2 border-[#0055FF] bg-blue-50/30 p-3">
                    <div className="text-xs font-semibold text-[#0055FF] mb-2 uppercase tracking-wider">Nouvelles lignes</div>
                    <table className="w-full text-xs">
                      <thead><tr className="text-[10px] text-slate-500 uppercase"><th className="p-1 text-left w-24">Date</th><th className="p-1 text-right w-24">Montant</th><th className="p-1 w-12">+/-</th><th className="p-1 text-left">Contrepartie</th><th className="p-1 text-left">Communication</th><th className="p-1 w-6"></th></tr></thead>
                      <tbody>
                        {inlineLines.map((line, i) => (
                          <tr key={i} className="border-t border-blue-100">
                            <td className="p-1"><Input type="date" className="h-7 text-xs" value={line.date} onChange={e => updateLine(i, 'date', e.target.value)} /></td>
                            <td className="p-1"><Input type="number" step="0.01" className="h-7 text-xs text-right" value={line.amount} onChange={e => updateLine(i, 'amount', e.target.value)} /></td>
                            <td className="p-1"><select className="h-7 text-xs border rounded px-1 w-full" value={line.transaction_type} onChange={e => updateLine(i, 'transaction_type', e.target.value)}><option value="credit">+</option><option value="debit">-</option></select></td>
                            <td className="p-1 relative">
                              <CounterpartySearchSelect
                                owners={owners}
                                suppliers={suppliers}
                                value={line.counterparty_name}
                                onChange={(v) => updateLine(i, 'counterparty_name', v)}
                                onSelect={({ item, type }) => {
                                  // Enregistre l'ID + type pour que le backend utilise
                                  // cette contrepartie EXPLICITE en priorite sur le VCS.
                                  updateLine(i, 'counterparty_id', item.id);
                                  updateLine(i, 'counterparty_type', type);
                                  updateLine(i, 'counterparty_name', item.name);
                                  // Pour un encaissement proprietaire, auto-pre-remplir la communication
                                  // (VCS si vide + mention "Votre paiement au JJ/MM/AAAA")
                                  if (type === 'owner') {
                                    const dateLabel = line.date ? new Date(line.date).toLocaleDateString('fr-BE') : '';
                                    const paymentLabel = dateLabel ? `Votre paiement au ${dateLabel}` : 'Votre paiement';
                                    const vcs = item.vcs_code || '';
                                    const newComm = (line.communication || '').trim()
                                      ? line.communication
                                      : (vcs ? `${vcs} - ${paymentLabel}` : paymentLabel);
                                    updateLine(i, 'communication', newComm);
                                  }
                                }}
                                placeholder="Nom contrepartie"
                                testId={`counterparty-${i}`}
                              />
                            </td>
                            <td className="p-1 relative">
                              <Input className="h-7 text-xs" value={line.communication} onChange={e => { updateLine(i, 'communication', e.target.value); doLookup(e.target.value, `cm-${i}`); }} placeholder="Communication libre ou VCS" />
                              {lookupResults && lookupResults.lineIdx === `cm-${i}` && lookupResults.owners.length > 0 && (
                                <div className="absolute top-8 left-0 z-20 bg-white border border-[#0055FF] shadow-lg rounded-md p-2 text-xs w-56">
                                  {lookupResults.owners.map(o => <div key={o.id} className="p-1 text-[#0055FF]"><strong>{o.name}</strong> <span className="font-mono text-[10px]">{o.vcs_code}</span><div className="text-[10px] text-green-600">Auto-lettrage VCS</div></div>)}
                                </div>
                              )}
                            </td>
                            <td className="p-1"><button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="flex gap-2 mt-2 justify-end">
                      <Button size="sm" variant="ghost" onClick={addInlineLine}><Plus size={12} className="mr-1" /> Ligne</Button>
                      <Button size="sm" onClick={saveLines} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="save-inline-lines"><Save size={12} className="mr-1" /> Enregistrer</Button>
                    </div>
                  </div>
                )}

                {/* Transaction table */}
                <Table>
                  <TableHeader><TableRow>
                    <TableHead className="w-24">Date</TableHead><TableHead className="min-w-[200px]">Contrepartie</TableHead><TableHead className="min-w-[200px]">Communication</TableHead>
                    <TableHead className="text-right w-28">Montant</TableHead><TableHead className="w-24">Lettrage</TableHead><TableHead className="w-24"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {transactions.length === 0 ? (
                      <TableRow><TableCell colSpan={6} className="text-center py-6 text-slate-400 text-sm">Cliquez "Ajouter lignes" pour encoder</TableCell></TableRow>
                    ) : transactions.map(txn => editingTxn === txn.id ? (
                      <TableRow key={txn.id} className="bg-yellow-50/50">
                        <TableCell><Input type="date" className="h-7 text-xs" value={editForm.date} onChange={e => setEditForm({...editForm, date: e.target.value})} /></TableCell>
                        <TableCell>
                          <CounterpartySearchSelect
                            owners={owners}
                            suppliers={suppliers}
                            value={editForm.counterparty_name}
                            onChange={(v) => setEditForm({...editForm, counterparty_name: v})}
                            onSelect={({ item, type }) => {
                              setEditForm(f => ({
                                ...f,
                                counterparty_id: item.id,
                                counterparty_type: type,
                                counterparty_name: item.name,
                                communication: (f.communication && f.communication.trim()) ? f.communication : (item.vcs_code || f.communication),
                              }));
                            }}
                            placeholder="Nom contrepartie"
                            testId={`edit-counterparty-${txn.id}`}
                          />
                        </TableCell>
                        <TableCell><Input className="h-7 text-xs" value={editForm.communication} onChange={e => setEditForm({...editForm, communication: e.target.value})} /></TableCell>
                        <TableCell><Input type="number" step="0.01" className="h-7 text-xs text-right" value={editForm.amount} onChange={e => setEditForm({...editForm, amount: e.target.value})} /></TableCell>
                        <TableCell colSpan={2}>
                          <div className="flex gap-1"><Button size="sm" variant="ghost" onClick={saveEdit} className="text-green-600 h-6 px-2" data-testid={`save-edit-${txn.id}`}><Save size={12} /></Button><Button size="sm" variant="ghost" onClick={cancelEdit} className="text-slate-400 h-6 px-2"><X size={12} /></Button></div>
                        </TableCell>
                      </TableRow>
                    ) : (
                      <TableRow key={txn.id} className="hover:bg-slate-50/50">
                        <TableCell className="font-mono text-xs">{fmtDate(txn.date)}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.counterparty_name}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.communication}</TableCell>
                        <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>{txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}</TableCell>
                        <TableCell>{txn.matched ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline">{txn.match_type === 'owner_payment' ? 'Proprio' : txn.match_type === 'supplier_payment' ? 'Fourn.' : 'Fact.'}</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">-</Badge>}</TableCell>
                        <TableCell>
                          <div className="flex gap-0">
                            <Button variant="ghost" size="sm" onClick={() => startEdit(txn)} className="h-6 w-6 p-0 text-slate-400" title="Editer" data-testid={`edit-txn-${txn.id}`}><Pencil size={11} /></Button>
                            {txn.matched ? <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500 h-6 w-6 p-0" title="Delettrer"><Unlink size={11} /></Button>
                              : <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#0055FF] h-6 w-6 p-0" title="Lettrer" data-testid={`lettrage-${txn.id}`}><Link2 size={11} /></Button>}
                            <Button variant="ghost" size="sm" onClick={() => deleteTxn(txn.id)} className="h-6 w-6 p-0 text-red-400" title="Supprimer" data-testid={`delete-txn-${txn.id}`}><Trash2 size={11} /></Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un extrait</div>
          )}
        </div>
      </div>

      {/* Statement Dialog */}
      <Dialog open={stmtDialog} onOpenChange={(o) => { setStmtDialog(o); if (!o) setEditingStmtId(null); }}><DialogContent data-testid="stmt-dialog"><DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingStmtId ? 'Modifier l\'extrait' : 'Nouvel extrait'}</DialogTitle></DialogHeader>
        <div className="space-y-4 mt-2">
          <div className="grid grid-cols-2 gap-4"><div><label className="form-label">Numero *</label><Input value={stmtForm.number} onChange={e => setStmtForm({...stmtForm, number: e.target.value})} /></div><div><label className="form-label">Date *</label><Input type="date" value={stmtForm.date} onChange={e => setStmtForm({...stmtForm, date: e.target.value})} /></div></div>
          <div><label className="form-label">Compte bancaire *</label>
            {bankAccounts.length > 0 ? (
              <Select value={stmtForm.account_number} onValueChange={async (v) => {
                setStmtForm(f => ({...f, account_number: v}));
                // Auto-fill opening balance via API (gere posted+draft+computed)
                try {
                  const { data } = await api.get('/banking/statements/previous-closing', {
                    params: { account_number: v },
                  });
                  setStmtForm(f => ({
                    ...f,
                    account_number: v,
                    opening_balance: Number(data.balance || 0),
                    _opening_source: data.source,
                    _previous_stmt: data.previous_statement_number || data.previous_statement_id || '',
                    _previous_date: data.previous_statement_date || '',
                  }));
                } catch {
                  // Fallback : 0
                  setStmtForm(f => ({...f, account_number: v, opening_balance: 0}));
                }
              }}>
                <SelectTrigger data-testid="stmt-account-select"><SelectValue placeholder="Selectionner un compte bancaire" /></SelectTrigger>
                <SelectContent>
                  {bankAccounts.map(b => (
                    <SelectItem key={b.iban} value={b.iban} data-testid={`stmt-account-${b.iban}`}>
                      <div className="flex items-center justify-between gap-3 w-full">
                        <div>
                          <span className="font-mono text-xs">{b.iban}</span>
                          {b.label && <span className="ml-2 text-slate-700">{b.label}</span>}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {b.pcmn_number && <span className="text-[10px] font-mono bg-green-100 text-green-800 px-1.5 py-0.5 rounded">PCMN {b.pcmn_number}</span>}
                          {b.is_default && <span className="text-[10px] bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">defaut</span>}
                        </div>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <>
                <Input value={stmtForm.account_number} onChange={e => setStmtForm({...stmtForm, account_number: e.target.value})} placeholder="BE00 0000 0000 0000" />
                <p className="text-[11px] text-amber-600 mt-1">Aucun compte bancaire configure sur cette ACP. Configurez-en un dans Coproprietes.</p>
              </>
            )}
            {/* Mention du compte PCMN selectionne + dernier solde */}
            {stmtForm.account_number && (() => {
              const ba = bankAccounts.find(b => b.iban === stmtForm.account_number);
              const src = stmtForm._opening_source;
              const prevNum = stmtForm._previous_stmt;
              const prevDate = stmtForm._previous_date;
              return (
                <div className="text-[11px] text-slate-500 mt-1.5 space-x-3 flex flex-wrap gap-x-3">
                  {ba?.pcmn_number && <span>Compte PCMN : <b className="font-mono text-slate-700">{ba.pcmn_number}</b></span>}
                  {src === 'posted' && prevDate && (
                    <span className="text-green-700">
                      Solde repris de l&apos;extrait <b className="font-mono">{prevNum}</b> du {fmtDate(prevDate)} (comptabilise)
                    </span>
                  )}
                  {src === 'draft_computed' && prevDate && (
                    <span className="text-amber-600">
                      Solde calcule depuis l&apos;extrait brouillon du {fmtDate(prevDate)} (mouvements non figes)
                    </span>
                  )}
                  {src === 'none' && (
                    <span className="text-blue-600">Aucun extrait precedent - solde d&apos;ouverture initialise a 0</span>
                  )}
                </div>
              );
            })()}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="form-label">Solde ouverture</label>
              <Input type="number" step="0.01" value={stmtForm.opening_balance} onChange={e => setStmtForm({...stmtForm, opening_balance: e.target.value})} data-testid="stmt-opening-balance" />
            </div>
            <div>
              <label className="form-label">Solde fermeture</label>
              <Input type="number" step="0.01" value={stmtForm.closing_balance} onChange={e => setStmtForm({...stmtForm, closing_balance: e.target.value})} data-testid="stmt-closing-balance" />
            </div>
          </div>
          <div className="flex gap-3 justify-end"><Button variant="outline" onClick={() => { setStmtDialog(false); setEditingStmtId(null); }}>Annuler</Button><Button onClick={saveStmt} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="stmt-save-btn">{editingStmtId ? 'Enregistrer' : 'Creer'}</Button></div>
        </div>
      </DialogContent></Dialog>

      {/* Lettrage Dialog - layout pro avec hierarchie claire */}
      <Dialog open={lettrageDialog} onOpenChange={setLettrageDialog}>
        <DialogContent className="max-w-3xl p-0 overflow-hidden" data-testid="lettrage-dialog">
          {/* Header transaction */}
          <div className="bg-gradient-to-r from-[#0055FF] to-[#0040CC] px-6 py-4 text-white">
            <DialogHeader className="space-y-1">
              <DialogTitle className="text-white text-base flex items-center justify-between gap-3" style={{fontFamily:'Chivo,sans-serif'}}>
                <span>Lettrage de la transaction</span>
                <span className="font-mono text-xl">{Number(lettrageTarget?.amount || 0).toFixed(2)} EUR</span>
              </DialogTitle>
            </DialogHeader>
            {lettrageTarget && (
              <div className="text-[12px] text-white/85 mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                {lettrageTarget.date && <span><b>{fmtDate(lettrageTarget.date)}</b></span>}
                {lettrageTarget.counterparty_name && <span>{lettrageTarget.counterparty_name}</span>}
                {lettrageTarget.communication && (
                  <span className="font-mono text-[11px] bg-white/15 px-2 py-0.5 rounded">{lettrageTarget.communication}</span>
                )}
              </div>
            )}
          </div>

          <div className="px-6 py-4">
            <Tabs defaultValue="owners">
              <TabsList className="grid grid-cols-3 mb-4">
                <TabsTrigger value="owners" data-testid="lettrage-tab-owners">Proprietaires</TabsTrigger>
                <TabsTrigger value="invoices" data-testid="lettrage-tab-invoices">Factures</TabsTrigger>
                <TabsTrigger value="suppliers" data-testid="lettrage-tab-suppliers">Fournisseurs</TabsTrigger>
              </TabsList>

              <TabsContent value="owners" className="mt-0">
                <Input placeholder="Rechercher par nom ou VCS..." value={lookupQuery} onChange={e => setLookupQuery(e.target.value)} className="mb-3" />
                <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                  {owners.filter(o => !lookupQuery || (o.name || '').toLowerCase().includes(lookupQuery.toLowerCase()) || (o.vcs_code || '').includes(lookupQuery)).map(o => (
                    <div key={o.id} className="flex items-center justify-between gap-3 border border-slate-200 rounded-md px-3 py-2.5 hover:border-[#0055FF]/40 hover:bg-slate-50 transition-colors">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-slate-900 truncate">{o.name}</div>
                        {o.vcs_code && <div className="text-[11px] font-mono text-[#0055FF] mt-0.5">{o.vcs_code}</div>}
                      </div>
                      <Button size="sm" onClick={() => doLettrage(o.id, 'owner_payment')} className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-7 text-xs shrink-0">
                        <Link2 size={11} className="mr-1" /> Lettrer
                      </Button>
                    </div>
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="invoices" className="mt-0">
                <Input
                  placeholder="Filtrer par fournisseur, numero ou description..."
                  value={lookupQuery}
                  onChange={e => setLookupQuery(e.target.value)}
                  className="mb-3"
                  data-testid="lettrage-invoice-search"
                />
                <div className="text-[11px] text-slate-500 mb-3 flex items-center gap-4">
                  <span className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-full bg-red-400" /> A lettrer</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-full bg-green-400" /> Deja lettree</span>
                </div>
                <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                  {(() => {
                    const cpName = (lettrageTarget?.counterparty_name || '').toLowerCase().trim();
                    const q = (lookupQuery || '').toLowerCase().trim();
                    const list = invoices.filter(inv => {
                      if (q) {
                        return (inv.supplier || '').toLowerCase().includes(q)
                            || (inv.number || '').toLowerCase().includes(q)
                            || (inv.description || '').toLowerCase().includes(q);
                      }
                      if (cpName) {
                        return (inv.supplier || '').toLowerCase().includes(cpName)
                            || cpName.includes((inv.supplier || '').toLowerCase());
                      }
                      return true;
                    }).sort((a, b) => {
                      const aPaid = a.status === 'paid' ? 1 : 0;
                      const bPaid = b.status === 'paid' ? 1 : 0;
                      if (aPaid !== bPaid) return aPaid - bPaid;
                      return (b.date || '').localeCompare(a.date || '');
                    });
                    if (list.length === 0) return <p className="text-sm text-slate-400 text-center py-8">Aucune facture trouvee</p>;
                    return list.map(inv => {
                      const isPaid = inv.status === 'paid';
                      const borderClr = isPaid ? 'border-l-green-400 bg-green-50/40' : 'border-l-red-400 bg-red-50/30';
                      return (
                        <div
                          key={inv.id}
                          className={`border border-slate-200 border-l-4 ${borderClr} rounded-md px-3 py-2.5 transition-shadow hover:shadow-sm`}
                          data-testid={`lettrage-invoice-row-${inv.id}`}
                        >
                          {/* Ligne 1 : numero + fournisseur + badge statut + montant aligned right */}
                          <div className="flex items-start justify-between gap-3 mb-1">
                            <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-xs font-semibold text-slate-700">{inv.number || '—'}</span>
                              <span className="text-sm font-medium text-slate-900 truncate">{inv.supplier || ''}</span>
                              {isPaid
                                ? <span className="text-[10px] bg-green-100 text-green-800 px-2 py-0.5 rounded-full font-semibold border border-green-300">PAYE</span>
                                : <span className="text-[10px] bg-red-100 text-red-800 px-2 py-0.5 rounded-full font-semibold border border-red-300">A PAYER</span>}
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-mono font-semibold text-slate-900 text-sm leading-tight">{Number(inv.total_amount || 0).toFixed(2)} <span className="text-[10px] text-slate-500">EUR</span></div>
                              <div className="text-[10px] text-slate-500 mt-0.5">{fmtDate(inv.date)}</div>
                            </div>
                          </div>
                          {/* Ligne 2 : description + bouton aligned right */}
                          <div className="flex items-end justify-between gap-3">
                            <p className="text-[11px] text-slate-600 line-clamp-2 leading-snug flex-1 min-w-0">
                              {inv.description || <span className="text-slate-400 italic">Aucune description</span>}
                            </p>
                            <div className="shrink-0">
                              {isPaid ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => unlettrageByInvoice(inv.id)}
                                  className="text-orange-600 border-orange-300 hover:bg-orange-50 h-7 text-xs"
                                  data-testid={`unlettrage-invoice-${inv.id}`}
                                ><Unlink size={11} className="mr-1" /> Delettrer</Button>
                              ) : (
                                <Button
                                  size="sm"
                                  onClick={() => doLettrage(inv.id, 'invoice')}
                                  className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-7 text-xs"
                                  data-testid={`lettrage-invoice-${inv.id}`}
                                ><Link2 size={11} className="mr-1" /> Lettrer</Button>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    });
                  })()}
                </div>
              </TabsContent>

              <TabsContent value="suppliers" className="mt-0">
                <Input placeholder="Rechercher par nom ou TVA..." value={lookupQuery} onChange={e => setLookupQuery(e.target.value)} className="mb-3" />
                <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                  {suppliers.filter(s => !lookupQuery || (s.name || '').toLowerCase().includes(lookupQuery.toLowerCase()) || (s.vat_number || '').includes(lookupQuery)).map(s => (
                    <div key={s.id} className="flex items-center justify-between gap-3 border border-slate-200 rounded-md px-3 py-2.5 hover:border-[#0055FF]/40 hover:bg-slate-50 transition-colors">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-slate-900 truncate">{s.name}</div>
                        {s.vat_number && <div className="text-[11px] font-mono text-slate-500 mt-0.5">{s.vat_number}</div>}
                      </div>
                      <Button size="sm" onClick={() => doLettrage(s.id, 'supplier_payment')} className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-7 text-xs shrink-0">
                        <Link2 size={11} className="mr-1" /> Lettrer
                      </Button>
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
