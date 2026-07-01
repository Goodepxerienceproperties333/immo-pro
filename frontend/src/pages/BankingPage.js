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
import { Plus, Trash2, Upload, Link2, Unlink, Search, Landmark, PlusCircle, Save, Pencil, X, CheckCircle2, AlertTriangle, Eye, Tag } from 'lucide-react';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';
import { useAuth } from '@/contexts/AuthContext';
import CounterpartySearchSelect from '@/components/CounterpartySearchSelect';
import CodaImportDialog from '@/components/CodaImportDialog';
import { fmtDate } from '@/lib/dateFmt';

export default function BankingPage() {
  const { selectedCopro } = useAuth();
  const navigate = useNavigate();
  const fyParams = useFiscalYearParams();
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
  const [codaPreview, setCodaPreview] = useState(null);
  const [codaDialogOpen, setCodaDialogOpen] = useState(false);
  const [stmtForm, setStmtForm] = useState({ number: '', date: '', account_number: '', opening_balance: 0, closing_balance: 0 });
  const [bankAccounts, setBankAccounts] = useState([]);
  const [inlineLines, setInlineLines] = useState([]);
  const [editForm, setEditForm] = useState({});
  // ----- Multi-selection lettrage state -----
  const [selectedTxnIds, setSelectedTxnIds] = useState(new Set());
  const [batchLettrageDialog, setBatchLettrageDialog] = useState(false);
  const [batchInvoiceSearch, setBatchInvoiceSearch] = useState('');
  // 1 txn -> N invoices (multi-selection des factures dans le dialog lettrage de transaction)
  const [selectedInvoiceIds, setSelectedInvoiceIds] = useState(new Set());
  // ----- iter90k : Categorisation par nature de depense/revenu -----
  const [expenseCategories, setExpenseCategories] = useState([]);
  const [distributionKeys, setDistributionKeys] = useState([]);
  const [categorizeDialog, setCategorizeDialog] = useState(false);
  const [categorizeTarget, setCategorizeTarget] = useState(null);
  const [categorizeSplits, setCategorizeSplits] = useState([]);

  const load = useCallback(async () => {
    const promises = [
      api.get('/banking/statements', { params: fyParams }),
      api.get('/banking/transactions', { params: fyParams }),
      api.get('/owners'),
      api.get('/invoices', { params: fyParams }),
      api.get('/suppliers'),
      api.get('/expense-categories').catch(() => ({ data: [] })),
      api.get('/distribution-keys').catch(() => ({ data: [] })),
    ];
    if (selectedCopro) promises.push(api.get(`/coproprietes/${selectedCopro}`));
    const [s, t, o, inv, sup, cats, dks, c] = await Promise.all(promises);
    setStatements(s.data); setTransactions(t.data); setOwners(o.data); setInvoices(inv.data); setSuppliers(sup.data);
    setExpenseCategories(cats.data || []);
    setDistributionKeys(dks.data || []);
    setBankAccounts(c?.data?.bank_accounts || []);
  }, [selectedCopro, fyParams.date_from, fyParams.date_to]);
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
      const coproId = selectedCopro || '';
      if (coproId) fd.append('copropriete_id', coproId);
      // Step 1 : preview only - no DB write yet
      const { data } = await api.post('/banking/coda/preview', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      setCodaPreview(data);
      setCodaDialogOpen(true);
      if (data.duplicate_warning) {
        toast.warning('Fichier deja importe', {
          description: data.duplicate_warning.message,
          duration: 8000,
        });
      } else {
        toast.success(`${data.movements?.length || 0} mouvement(s) detecte(s) - reviewez et confirmez`);
      }
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
  const openLettrage = (txn) => { setLettrageTarget(txn); setLettrageDialog(true); setLookupQuery(''); setSelectedInvoiceIds(new Set()); };
  const doLettrage = async (id, type) => { try { await api.post('/banking/lettrage', { transaction_id: lettrageTarget.id, match_to_id: id, match_type: type }); toast.success('Lettre'); setLettrageDialog(false); if (selectedStmt) loadStmtTxns(selectedStmt); else load(); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const unlettrage = async (id) => { await api.post(`/banking/unlettrage/${id}`); toast.success('Delettrage'); if (selectedStmt) loadStmtTxns(selectedStmt); else load(); load(); };
  const unlettrageByInvoice = async (invId) => { try { await api.post(`/banking/unlettrage-by-invoice/${invId}`); toast.success('Facture delettree'); if (selectedStmt) loadStmtTxns(selectedStmt); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };

  // ----- iter90k : CATEGORISATION -----
  const openCategorize = (txn) => {
    setCategorizeTarget(txn);
    setCategorizeSplits([{ expense_category_id: '', distribution_key_id: '', amount: Math.abs(Number(txn.amount) || 0), description: '' }]);
    setCategorizeDialog(true);
  };
  const addCatSplit = () => setCategorizeSplits([...categorizeSplits, { expense_category_id: '', distribution_key_id: '', amount: 0, description: '' }]);
  const removeCatSplit = (i) => setCategorizeSplits(categorizeSplits.filter((_, idx) => idx !== i));
  const updateCatSplit = (i, f, v) => { const s = [...categorizeSplits]; s[i] = { ...s[i], [f]: v }; setCategorizeSplits(s); };
  const doCategorize = async () => {
    if (!categorizeTarget) return;
    const payload = { splits: categorizeSplits.map(s => ({ ...s, amount: Number(s.amount) })) };
    try {
      await api.post(`/banking/transactions/${categorizeTarget.id}/categorize`, payload);
      toast.success(`Categorisation OK (${payload.splits.length} nature${payload.splits.length > 1 ? 's' : ''})`);
      setCategorizeDialog(false);
      if (selectedStmt) loadStmtTxns(selectedStmt); else load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const uncategorize = async (id) => {
    if (!window.confirm('Retirer la nature de cette transaction ?')) return;
    try {
      await api.delete(`/banking/transactions/${id}/categorize`);
      toast.success('Categorisation retiree');
      if (selectedStmt) loadStmtTxns(selectedStmt); else load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // 1 txn -> N factures : multi-selection dans le dialog Lettrage
  const toggleInvoiceSelected = (id) => {
    setSelectedInvoiceIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const selectedInvoicesTotal = (lettrageTarget ? invoices.filter(i => selectedInvoiceIds.has(i.id)) : [])
    .reduce((s, i) => s + Number(i.total_amount || i.amount_ttc || i.amount || 0), 0);
  const doLettrageMultiInvoices = async () => {
    if (!lettrageTarget || selectedInvoiceIds.size === 0) return;
    try {
      const r = await api.post('/banking/lettrage-multi-invoices', {
        transaction_id: lettrageTarget.id,
        invoice_ids: Array.from(selectedInvoiceIds),
      });
      const { transaction_amount, invoice_total, remaining, is_exact } = r.data;
      const msg = is_exact
        ? `Lettrage OK : ${selectedInvoiceIds.size} factures = ${invoice_total.toFixed(2)} EUR (exact)`
        : `Lettrage OK : ${selectedInvoiceIds.size} factures pour ${invoice_total.toFixed(2)} EUR / txn ${transaction_amount.toFixed(2)} EUR (ecart ${Math.abs(remaining).toFixed(2)} EUR sur compte tiers)`;
      toast.success(msg);
      setLettrageDialog(false);
      setSelectedInvoiceIds(new Set());
      if (selectedStmt) loadStmtTxns(selectedStmt); else load();
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lettrage multi-factures');
    }
  };

  // ---- MULTI-SELECTION LETTRAGE (N transactions -> 1 facture) ----
  const toggleTxnSelected = (id) => {
    setSelectedTxnIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const clearSelection = () => setSelectedTxnIds(new Set());
  // Multi-selection : ne s'applique qu'aux txns de l'extrait actuellement ouvert
  const selectedTxns = transactions.filter(t => selectedTxnIds.has(t.id));
  const selectedTotal = selectedTxns.reduce((s, t) => s + Math.abs(Number(t.amount) || 0), 0);
  const doBatchLettrage = async (invoiceId) => {
    try {
      const ids = Array.from(selectedTxnIds);
      const r = await api.post('/banking/lettrage-batch', {
        transaction_ids: ids,
        match_to_id: invoiceId,
        match_type: 'invoice',
      });
      const { total_paid, invoice_amount, status, remaining } = r.data;
      const msg = status === 'paid'
        ? `Lettrage OK : ${ids.length} transactions = ${total_paid.toFixed(2)} EUR / ${invoice_amount.toFixed(2)} EUR (solde)`
        : `Lettrage partiel : ${total_paid.toFixed(2)} EUR / ${invoice_amount.toFixed(2)} EUR (reste ${remaining.toFixed(2)} EUR)`;
      toast.success(msg);
      setBatchLettrageDialog(false);
      clearSelection();
      if (selectedStmt) loadStmtTxns(selectedStmt); else load();
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lettrage en lot');
    }
  };
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

                {/* Toolbar selection multi-lettrage */}
                {selectedTxnIds.size > 0 && (
                  <div className="bg-[#0055FF]/10 border border-[#0055FF]/30 rounded-md px-3 py-2 mb-2 flex items-center justify-between text-sm" data-testid="batch-lettrage-toolbar">
                    <div className="flex items-center gap-4">
                      <strong className="text-[#0055FF]">{selectedTxnIds.size} transaction(s) selectionnee(s)</strong>
                      <span className="font-mono text-slate-700">Total : <strong>{selectedTotal.toFixed(2)} EUR</strong></span>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => { setBatchInvoiceSearch(''); setBatchLettrageDialog(true); }} className="bg-[#0055FF] hover:bg-[#0040CC] text-white" data-testid="batch-lettrage-open-btn">
                        <Link2 size={13} className="mr-1.5" /> Lettrer la selection vers une facture
                      </Button>
                      <Button size="sm" variant="outline" onClick={clearSelection} data-testid="batch-lettrage-clear-btn">Annuler</Button>
                    </div>
                  </div>
                )}

                {/* Transaction table */}
                <Table>
                  <TableHeader><TableRow>
                    <TableHead className="w-8 px-1">
                      <input
                        type="checkbox"
                        title="Tout selectionner (txns non lettrees)"
                        checked={transactions.length > 0 && transactions.filter(t => !t.matched).every(t => selectedTxnIds.has(t.id))}
                        onChange={(e) => {
                          if (e.target.checked) {
                            const next = new Set(selectedTxnIds);
                            transactions.filter(t => !t.matched).forEach(t => next.add(t.id));
                            setSelectedTxnIds(next);
                          } else {
                            clearSelection();
                          }
                        }}
                        data-testid="batch-select-all-checkbox"
                      />
                    </TableHead>
                    <TableHead className="w-24">Date</TableHead><TableHead className="min-w-[200px]">Contrepartie</TableHead><TableHead className="min-w-[200px]">Communication</TableHead>
                    <TableHead className="text-right w-28">Montant</TableHead><TableHead className="w-24">Lettrage</TableHead><TableHead className="w-24"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {transactions.length === 0 ? (
                      <TableRow><TableCell colSpan={7} className="text-center py-6 text-slate-400 text-sm">Cliquez &laquo;Ajouter lignes&raquo; pour encoder</TableCell></TableRow>
                    ) : transactions.map(txn => editingTxn === txn.id ? (
                      <TableRow key={txn.id} className="bg-yellow-50/50">
                        <TableCell></TableCell>
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
                      <TableRow key={txn.id} className={`hover:bg-slate-50/50 ${selectedTxnIds.has(txn.id) ? 'bg-blue-50/40' : ''}`}>
                        <TableCell className="px-1">
                          {!txn.matched && (
                            <input
                              type="checkbox"
                              checked={selectedTxnIds.has(txn.id)}
                              onChange={() => toggleTxnSelected(txn.id)}
                              data-testid={`select-txn-${txn.id}`}
                            />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{fmtDate(txn.date)}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.counterparty_name}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.communication}</TableCell>
                        <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>{txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}</TableCell>
                        <TableCell>{txn.matched ? <Badge className={
                            txn.match_type === 'expense_category'
                              ? "bg-purple-50 text-purple-700 border-purple-200 text-[10px]"
                              : "bg-green-50 text-green-700 border-green-200 text-[10px]"
                          } variant="outline">{
                            txn.match_type === 'owner_payment' ? 'Proprio' :
                            txn.match_type === 'supplier_payment' ? 'Fourn.' :
                            txn.match_type === 'expense_category' ? (
                              (txn.category_splits && txn.category_splits.length > 1)
                                ? `Nature (${txn.category_splits.length})`
                                : 'Nature'
                            ) :
                            'Fact.'
                          }</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">-</Badge>}</TableCell>
                        <TableCell>
                          <div className="flex gap-0">
                            <Button variant="ghost" size="sm" onClick={() => startEdit(txn)} className="h-6 w-6 p-0 text-slate-400" title="Editer" data-testid={`edit-txn-${txn.id}`}><Pencil size={11} /></Button>
                            {txn.matched ? (
                              txn.match_type === 'expense_category'
                                ? <Button variant="ghost" size="sm" onClick={() => uncategorize(txn.id)} className="text-purple-600 h-6 w-6 p-0" title="Retirer la nature" data-testid={`uncategorize-${txn.id}`}><Unlink size={11} /></Button>
                                : <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500 h-6 w-6 p-0" title="Delettrer"><Unlink size={11} /></Button>
                            ) : (
                              <>
                                <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#0055FF] h-6 w-6 p-0" title="Lettrer" data-testid={`lettrage-${txn.id}`}><Link2 size={11} /></Button>
                                <Button variant="ghost" size="sm" onClick={() => openCategorize(txn)} className="text-purple-600 h-6 w-6 p-0" title="Categoriser (nature de depense/revenu)" data-testid={`categorize-${txn.id}`}><Tag size={11} /></Button>
                              </>
                            )}
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
                  <span className="ml-auto text-slate-600"><b>Astuce :</b> cochez plusieurs factures pour les lettrer ensemble a cette transaction.</span>
                </div>
                {/* Barre de selection multi-factures */}
                {selectedInvoiceIds.size > 0 && (
                  <div className="bg-[#0055FF]/10 border border-[#0055FF]/30 rounded px-3 py-2 mb-3 flex items-center justify-between text-xs" data-testid="multi-invoice-toolbar">
                    <div>
                      <strong className="text-[#0055FF]">{selectedInvoiceIds.size} factures selectionnees</strong>
                      <span className="ml-3 font-mono">Total : <strong>{selectedInvoicesTotal.toFixed(2)} EUR</strong></span>
                      {(() => {
                        const txnAmt = Math.abs(Number(lettrageTarget?.amount || 0));
                        const diff = txnAmt - selectedInvoicesTotal;
                        if (Math.abs(diff) < 0.01) return <span className="ml-2 text-green-700 font-semibold">= SOLDE EXACT</span>;
                        if (diff > 0) return <span className="ml-2 text-amber-700 font-semibold">- partiel (txn reste {diff.toFixed(2)} EUR)</span>;
                        return <span className="ml-2 text-amber-700 font-semibold">- sur-paiement ({(-diff).toFixed(2)} EUR)</span>;
                      })()}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={doLettrageMultiInvoices} className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-7 text-xs" data-testid="multi-invoice-confirm-btn">
                        <Link2 size={11} className="mr-1" /> Lettrer ces {selectedInvoiceIds.size} factures
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setSelectedInvoiceIds(new Set())} className="h-7 text-xs">Annuler</Button>
                    </div>
                  </div>
                )}
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
                      const isSelected = selectedInvoiceIds.has(inv.id);
                      const borderClr = isPaid ? 'border-l-green-400 bg-green-50/40' : isSelected ? 'border-l-[#0055FF] bg-blue-50/50' : 'border-l-red-400 bg-red-50/30';
                      return (
                        <div
                          key={inv.id}
                          className={`border border-slate-200 border-l-4 ${borderClr} rounded-md px-3 py-2.5 transition-shadow hover:shadow-sm`}
                          data-testid={`lettrage-invoice-row-${inv.id}`}
                        >
                          {/* Ligne 1 : checkbox + numero + fournisseur + badge statut + montant aligned right */}
                          <div className="flex items-start justify-between gap-3 mb-1">
                            <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                              {!isPaid && (
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => toggleInvoiceSelected(inv.id)}
                                  className="shrink-0"
                                  data-testid={`lettrage-invoice-check-${inv.id}`}
                                  title="Cocher pour lettrer plusieurs factures avec cette transaction"
                                />
                              )}
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

      {/* ----- Dialog lettrage en lot (N transactions -> 1 facture) ----- */}
      <Dialog open={batchLettrageDialog} onOpenChange={setBatchLettrageDialog}>
        <DialogContent className="max-w-3xl p-0 overflow-hidden" data-testid="batch-lettrage-dialog">
          <div className="bg-gradient-to-r from-[#0055FF] to-[#0040CC] text-white px-5 py-4">
            <DialogTitle className="text-base font-semibold m-0">Lettrer {selectedTxnIds.size} transaction(s) vers une facture</DialogTitle>
            <div className="mt-1 text-xs opacity-90">
              Total selectionne : <strong className="font-mono">{selectedTotal.toFixed(2)} EUR</strong>
              {' '} - choisissez UNE facture a solder (totalement ou partiellement).
            </div>
          </div>
          <div className="p-5 space-y-3">
            <Input
              placeholder="Rechercher par numero, fournisseur ou description..."
              value={batchInvoiceSearch}
              onChange={e => setBatchInvoiceSearch(e.target.value)}
              data-testid="batch-lettrage-search"
            />
            <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
              {(() => {
                const q = batchInvoiceSearch.trim().toLowerCase();
                const list = invoices.filter(inv => {
                  if (!q) return true;
                  return (inv.number || '').toLowerCase().includes(q)
                      || (inv.supplier || '').toLowerCase().includes(q)
                      || (inv.description || '').toLowerCase().includes(q);
                });
                if (list.length === 0) {
                  return <div className="text-center py-6 text-slate-400 text-sm">Aucune facture correspondante</div>;
                }
                return list.map(inv => {
                  const amount = Number(inv.total_amount || inv.amount_ttc || inv.amount || 0);
                  const diff = amount - selectedTotal;
                  const isExact = Math.abs(diff) < 0.01;
                  const isOver = diff < -0.01;
                  return (
                    <div key={inv.id} className={`border rounded-md px-3 py-2.5 ${isExact ? 'border-green-400 bg-green-50/40' : isOver ? 'border-amber-300 bg-amber-50/30' : 'border-slate-200'}`} data-testid={`batch-lettrage-invoice-${inv.id}`}>
                      <div className="flex items-start justify-between gap-3 mb-1">
                        <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                          <span className="font-mono text-xs font-semibold text-slate-700">{inv.number || '—'}</span>
                          <span className="text-sm font-medium text-slate-900 truncate">{inv.supplier || ''}</span>
                          {isExact && <span className="text-[10px] bg-green-600 text-white px-2 py-0.5 rounded-full font-semibold">SOLDE EXACT</span>}
                          {isOver && <span className="text-[10px] bg-amber-500 text-white px-2 py-0.5 rounded-full font-semibold" title="L'excedent sera porte au compte tiers du proprietaire lors de la comptabilisation">SUR-PAIEMENT (+{(selectedTotal - amount).toFixed(2)} EUR)</span>}
                          {!isExact && !isOver && diff > 0.01 && (
                            <span className="text-[10px] bg-amber-500 text-white px-2 py-0.5 rounded-full font-semibold">PARTIEL ({(amount - selectedTotal).toFixed(2)} EUR)</span>
                          )}
                        </div>
                        <div className="text-right shrink-0">
                          <div className="font-mono font-semibold text-slate-900 text-sm leading-tight">{amount.toFixed(2)} <span className="text-[10px] text-slate-500">EUR</span></div>
                          <div className="text-[10px] text-slate-500 mt-0.5">{fmtDate(inv.date)}</div>
                        </div>
                      </div>
                      <div className="flex items-end justify-between gap-3">
                        <p className="text-[11px] text-slate-600 line-clamp-2 leading-snug flex-1 min-w-0">
                          {inv.description || <span className="text-slate-400 italic">Aucune description</span>}
                        </p>
                        <Button
                          size="sm"
                          onClick={() => doBatchLettrage(inv.id)}
                          className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-7 text-xs"
                          data-testid={`batch-lettrage-confirm-${inv.id}`}
                        ><Link2 size={11} className="mr-1" /> Lettrer ici</Button>
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* CODA Import Mapping Dialog */}
      <CodaImportDialog
        open={codaDialogOpen}
        onClose={() => setCodaDialogOpen(false)}
        preview={codaPreview}
        copropriete_id={selectedCopro || ''}
        owners={owners}
        suppliers={suppliers}
        invoices={invoices}
        onSuccess={() => { setCodaPreview(null); load(); }}
      />

      {/* iter90k : Categorize dialog (nature de depense / revenu) */}
      <Dialog open={categorizeDialog} onOpenChange={setCategorizeDialog}>
        <DialogContent className="max-w-2xl" data-testid="categorize-dialog">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold m-0">Categoriser la transaction</DialogTitle>
          </DialogHeader>
          {categorizeTarget && (() => {
            const txnAmt = Math.abs(Number(categorizeTarget.amount) || 0);
            const isCredit = Number(categorizeTarget.amount) > 0;
            const sumSplits = categorizeSplits.reduce((a, s) => a + Number(s.amount || 0), 0);
            const diff = Math.round((sumSplits - txnAmt) * 100) / 100;
            const filteredCats = expenseCategories.filter(c => {
              // Si credit -> proposer produits (classe 7) en priorite; sinon charges (6).
              // On laisse tout visible pour flexibilite mais on tri.
              return true;
            }).sort((a, b) => {
              const isProdA = (a.kind === 'produit') || (a.account_number || '').startsWith('7');
              const isProdB = (b.kind === 'produit') || (b.account_number || '').startsWith('7');
              if (isCredit) return (isProdB ? 1 : 0) - (isProdA ? 1 : 0);
              return (isProdA ? 1 : 0) - (isProdB ? 1 : 0);
            });
            return (
              <div className="space-y-3">
                <div className="text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded px-3 py-2">
                  <div className="flex justify-between items-center">
                    <span><b>{fmtDate(categorizeTarget.date)}</b> — {categorizeTarget.counterparty_name || <em className="text-slate-400">Sans contrepartie</em>}</span>
                    <span className={`font-mono font-semibold ${isCredit ? 'text-green-700' : 'text-red-700'}`}>
                      {isCredit ? '+' : '−'}{txnAmt.toFixed(2)} EUR
                    </span>
                  </div>
                  {categorizeTarget.communication && (
                    <div className="mt-1 text-slate-500 truncate">{categorizeTarget.communication}</div>
                  )}
                </div>

                <div className="space-y-2 max-h-[50vh] overflow-y-auto pr-1">
                  {categorizeSplits.map((split, i) => (
                    <div key={i} className="border border-slate-200 rounded p-2 grid grid-cols-12 gap-2 items-end" data-testid={`cat-split-${i}`}>
                      <div className="col-span-5">
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">Nature {isCredit ? '(produit/charge)' : '(charge/produit)'}</label>
                        <Select value={split.expense_category_id} onValueChange={(v) => updateCatSplit(i, 'expense_category_id', v)}>
                          <SelectTrigger className="h-8 text-xs" data-testid={`cat-split-nature-${i}`}><SelectValue placeholder="Choisir..." /></SelectTrigger>
                          <SelectContent>
                            {filteredCats.length === 0 && <div className="px-3 py-2 text-xs text-slate-400">Aucune nature configuree — creez-en dans Configuration</div>}
                            {filteredCats.map(c => {
                              const isProd = (c.kind === 'produit') || (c.account_number || '').startsWith('7');
                              return (
                                <SelectItem key={c.id} value={c.id}>
                                  <span className={isProd ? 'text-emerald-700' : ''}>
                                    {c.name} <span className="text-slate-400 text-[10px]">({c.account_number}{isProd ? ' • produit' : ''})</span>
                                  </span>
                                </SelectItem>
                              );
                            })}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="col-span-3">
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">Cle</label>
                        <Select value={split.distribution_key_id} onValueChange={(v) => updateCatSplit(i, 'distribution_key_id', v)}>
                          <SelectTrigger className="h-8 text-xs" data-testid={`cat-split-key-${i}`}><SelectValue placeholder="Cle..." /></SelectTrigger>
                          <SelectContent>
                            {distributionKeys.map(k => (
                              <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="col-span-3">
                        <label className="text-[10px] text-slate-500 uppercase tracking-wide">Montant</label>
                        <Input type="number" step="0.01" className="h-8 text-xs font-mono" value={split.amount}
                          onChange={(e) => updateCatSplit(i, 'amount', e.target.value)}
                          data-testid={`cat-split-amount-${i}`} />
                      </div>
                      <div className="col-span-1 flex justify-end">
                        {categorizeSplits.length > 1 && (
                          <Button variant="ghost" size="sm" onClick={() => removeCatSplit(i)}
                            className="h-8 w-8 p-0 text-red-400 hover:text-red-600"
                            data-testid={`cat-split-remove-${i}`}
                            title="Supprimer ce split"><X size={13} /></Button>
                        )}
                      </div>
                      <div className="col-span-12">
                        <Input placeholder="Description (facultatif)" className="h-7 text-xs"
                          value={split.description || ''}
                          onChange={(e) => updateCatSplit(i, 'description', e.target.value)}
                          data-testid={`cat-split-desc-${i}`} />
                      </div>
                    </div>
                  ))}
                </div>

                <Button variant="outline" size="sm" onClick={addCatSplit} className="h-7 text-xs" data-testid="cat-split-add">
                  <PlusCircle size={13} className="mr-1" /> Ajouter un split (multi-natures)
                </Button>

                <div className="flex justify-between items-center border-t border-slate-200 pt-2 text-xs">
                  <div className="text-slate-600">
                    Somme des splits : <span className="font-mono font-semibold">{sumSplits.toFixed(2)}</span> / <span className="font-mono">{txnAmt.toFixed(2)} EUR</span>
                  </div>
                  <div>
                    {Math.abs(diff) < 0.01 ? (
                      <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline"><CheckCircle2 size={11} className="mr-1" /> Equilibre</Badge>
                    ) : (
                      <Badge className="bg-orange-50 text-orange-700 border-orange-200 text-[10px]" variant="outline"><AlertTriangle size={11} className="mr-1" /> Ecart {diff > 0 ? '+' : ''}{diff.toFixed(2)}</Badge>
                    )}
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" size="sm" onClick={() => setCategorizeDialog(false)} className="h-8 text-xs">Annuler</Button>
                  <Button size="sm" onClick={doCategorize}
                    disabled={Math.abs(diff) >= 0.01 || categorizeSplits.some(s => !s.expense_category_id || !s.distribution_key_id || Number(s.amount) <= 0)}
                    className="bg-purple-600 hover:bg-purple-700 text-white h-8 text-xs"
                    data-testid="cat-confirm-btn">
                    <Tag size={12} className="mr-1" /> Categoriser
                  </Button>
                </div>
              </div>
            );
          })()}
        </DialogContent>
      </Dialog>
    </div>
  );
}
