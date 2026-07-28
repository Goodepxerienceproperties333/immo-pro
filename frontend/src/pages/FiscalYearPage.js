import { useState, useEffect, useCallback, useMemo, Fragment } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Trash2, Lock, Unlock, Calendar, CheckCircle2, RotateCcw, Sparkles, Send, Calculator, FileDown, Pencil } from 'lucide-react';
import BudgetWizard from '@/components/BudgetWizard';
import RegularizationDialog from '@/components/RegularizationDialog';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import { fmtDate } from '@/lib/dateFmt';

export default function FiscalYearPage() {
  const [tab, setTab] = useState('years');
  const [years, setYears] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [distKeys, setDistKeys] = useState([]);
  const [yearDialog, setYearDialog] = useState(false);
  // iter90ei : edition d'un exercice existant (null = mode creation)
  const [editingYear, setEditingYear] = useState(null);  const [budgetDialog, setBudgetDialog] = useState(false);
  const [editingBudget, setEditingBudget] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [yearForm, setYearForm] = useState({ name: '', start_date: '', end_date: '', invoice_number_prefix: '' });
  const [budgetForm, setBudgetForm] = useState({ fiscal_year_id: '', name: '', lines: [] });
  const [prevExp, setPrevExp] = useState(null);
  const [wizardBudget, setWizardBudget] = useState(null);
  const [wizardMode, setWizardMode] = useState('create');
  const [regulFy, setRegulFy] = useState(null);

  const load = useCallback(async () => {
    // iter93y : autoriser les comptes classe 7 (produits venant diminuer les
    // charges dans le budget). Chargement des classes 6 ET 7 en parallele.
    const [y, b, a6, a7, dk] = await Promise.all([
      api.get('/fiscal/years'),
      api.get('/fiscal/budgets'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }),
      api.get('/accounting/pcmn', { params: { class_num: 7 } }),
      api.get('/distribution-keys'),
    ]);
    setYears(y.data); setBudgets(b.data);
    setAccounts([...(a6.data || []), ...(a7.data || [])]);
    setDistKeys(dk.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  // Cle de repartition par defaut (marquee is_default=true, sinon premiere cle)
  const defaultKeyId = useMemo(() => {
    if (!distKeys.length) return '';
    return (distKeys.find(k => k.is_default) || distKeys[0]).id;
  }, [distKeys]);

  const openCreateYear = () => {
    const now = new Date().getFullYear();
    setEditingYear(null);
    setYearForm({ name: `Exercice ${now}`, start_date: `${now}-01-01`, end_date: `${now}-12-31`, invoice_number_prefix: `FA-${now}-` });
    setYearDialog(true);
  };
  // iter90ei : ouverture du dialog en mode edition
  const openEditYear = (y) => {
    setEditingYear(y);
    setYearForm({
      name: y.name || '',
      start_date: (y.start_date || '').slice(0, 10),
      end_date: (y.end_date || '').slice(0, 10),
      invoice_number_prefix: y.invoice_number_prefix || '',
    });
    setYearDialog(true);
  };
  const saveYear = async () => {
    try {
      if (editingYear) {
        await api.put(`/fiscal/years/${editingYear.id}`, yearForm);
        toast.success('Exercice modifie');
      } else {
        await api.post('/fiscal/years', yearForm);
        toast.success('Exercice cree');
      }
      setYearDialog(false);
      setEditingYear(null);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const closeYear = async (id) => {
    if (!window.confirm(
      'Cloturer cet exercice ?\n\n' +
      'Cette action irreversible va :\n' +
      '  1. Generer une OD "Annulation provisions" (Dr 7400 / Cr 4000XX par quotites)\n' +
      '  2. Generer une OD "Imputation charges" (Dr 4000XX par quotites / Cr 6XXX)\n' +
      '  3. Reporter les soldes (AN) au 01/01 de l\'exercice suivant\n' +
      '  4. Verrouiller les ecritures de cet exercice\n\n' +
      'Continuer ?'
    )) return;
    try {
      const { data } = await api.post(`/fiscal/years/${id}/close`);
      toast.success(`${data.message} - Resultat: ${data.result_net} EUR`);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur de cloture');
    }
  };
  const reopenYear = async (id) => {
    try { await api.post(`/fiscal/years/${id}/reopen`); toast.success('Exercice reouvert'); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const openCreateBudget = async () => {
    const openFy = years.find(y => y.status === 'open');
    setEditingBudget(null);
    setBudgetForm({
      fiscal_year_id: openFy?.id || '',
      name: 'Budget previsionnel ' + (openFy?.name || ''),
      lines: [],
      // iter90cj : engagement AG reserve/roulement des la creation du budget.
      // Permet a _compute_mutation_breakdown de connaitre l'engagement meme si
      // le wizard n'a pas encore genere les appels a la date de mutation.
      reserve_fund_amount: 0,
      reserve_fund_key_id: '',
      roulement_fund_amount: 0,
      roulement_fund_key_id: '',
    });
    // Fetch previous year aggregation for hint
    try {
      if (openFy?.id) {
        const { data } = await api.get('/fiscal/previous-year-expenses', { params: { fiscal_year_id: openFy.id } });
        setPrevExp(data);
      } else setPrevExp(null);
    } catch { setPrevExp(null); }
    setBudgetDialog(true);
  };

  const openEditBudget = async (b) => {
    if (b.status === 'approved') { toast.error('Budget approuve - non modifiable'); return; }
    setEditingBudget(b);
    setBudgetForm({
      fiscal_year_id: b.fiscal_year_id,
      name: b.name,
      lines: (b.lines || []).map(l => ({ ...l })),
      // iter90cj : rehydrate engagement reserve/roulement pour edition
      reserve_fund_amount: Number(b.reserve_fund_amount || 0),
      reserve_fund_key_id: b.reserve_fund_key_id || '',
      roulement_fund_amount: Number(b.roulement_fund_amount || 0),
      roulement_fund_key_id: b.roulement_fund_key_id || '',
    });
    try {
      const { data } = await api.get('/fiscal/previous-year-expenses', { params: { fiscal_year_id: b.fiscal_year_id } });
      setPrevExp(data);
    } catch { setPrevExp(null); }
    setBudgetDialog(true);
  };

  const prefillFromPrevious = () => {
    if (!prevExp || !prevExp.lines?.length) { toast.info('Aucune donnee N-1 disponible'); return; }
    const lines = prevExp.lines.map(l => ({
      account_number: l.account_number,
      account_name: l.account_name,
      distribution_key_id: l.distribution_key_id || defaultKeyId,
      amount: l.amount_total,
    }));
    setBudgetForm(f => ({ ...f, lines }));
    toast.success(`${lines.length} ligne(s) pre-remplies depuis N-1`);
  };

  const addBudgetLine = () => setBudgetForm(f => ({
    ...f,
    lines: [...f.lines, { account_number: '', account_name: '', distribution_key_id: defaultKeyId, amount: 0 }],
  }));
  const removeBudgetLine = (i) => setBudgetForm(f => ({ ...f, lines: f.lines.filter((_, j) => j !== i) }));
  const updateBudgetLine = (i, field, value) => {
    const lines = [...budgetForm.lines];
    lines[i] = { ...lines[i], [field]: field === 'amount' ? Number(value) : value };
    // iter93y : detection classe pour bascule automatique du signe.
    const accNumber = lines[i].account_number || '';
    const acc = accounts.find(a => a.number === accNumber);
    const isClass7 = (acc?.class_num === 7) || String(accNumber).startsWith('7');
    if (field === 'account_number' && acc) {
      lines[i].account_name = acc.name;
    }
    // iter93y : force le signe correct selon la classe (comptable de produits
    // stocke en negatif pour venir diminuer les charges dans sum(amount)).
    const cur = Number(lines[i].amount || 0);
    if (isClass7 && cur > 0) lines[i].amount = -cur;
    else if (!isClass7 && cur < 0) lines[i].amount = Math.abs(cur);
    setBudgetForm({ ...budgetForm, lines });
  };

  const saveBudget = async () => {
    try {
      // Filter : keep lines with account_number AND non-zero amount (allow negatives,
      // e.g. boni/mali like compte 61210 with -605 EUR in section 0011).
      const payload = { ...budgetForm, lines: budgetForm.lines.filter(l => l.account_number && Number(l.amount) !== 0) };
      if (!payload.lines.length) { toast.error('Ajoutez au moins une ligne'); return; }
      if (editingBudget) await api.put(`/fiscal/budgets/${editingBudget.id}`, payload);
      else await api.post('/fiscal/budgets', payload);
      toast.success('Budget enregistre');
      setBudgetDialog(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const approveBudget = async (b) => {
    if (!window.confirm(`Approuver definitivement "${b.name}" ?\n\nApres approbation, le budget devient non modifiable et l'assistant d'appels de fonds s'ouvre automatiquement.`)) return;
    try {
      const { data } = await api.post(`/fiscal/budgets/${b.id}/approve`);
      toast.success('Budget approuve');
      load();
      setWizardMode('create');
      setWizardBudget(data);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const downloadBudgetPdf = async (b) => {
    try {
      const r = await api.get(`/fiscal/budgets/${b.id}/pdf`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([r.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      const fy = years.find(y => y.id === b.fiscal_year_id);
      const safeName = (b.name || 'budget').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 40);
      const fyName = (fy?.name || '').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 20);
      a.download = `budget_${safeName}_${fyName}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success('PDF telecharge');
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur telechargement PDF'); }
  };

  const revokeBudget = async (b, force = false) => {
    const msg = force
      ? `FORCER la devalidation ? TOUS les appels lies a "${b.name}" (provisions, reserve, roulement, special) seront contrepasses, MEME ceux deja payes. Les paiements bancaires seront delettres.`
      : `Devalider le budget "${b.name}" ? TOUS les appels de fonds lies (provisions, reserve, roulement) seront contrepasses ainsi que leurs ecritures comptables.`;
    if (!window.confirm(msg)) return;
    try {
      const url = `/fiscal/budgets/${b.id}/revoke${force ? '?force=true' : ''}`;
      const { data } = await api.post(url);
      const cnt = data.deleted_fund_calls || 0;
      toast.success(data.message || `Budget devalide (${cnt} appel(s) contrepasse(s))`);
      load();
    } catch (err) {
      const detail = err.response?.data?.detail || 'Erreur';
      // Si le backend bloque a cause d'appels payes : proposer le force
      if (typeof detail === 'string' && detail.includes('paiement')) {
        if (window.confirm(`${detail}\n\nVoulez-vous FORCER la contrepassation malgre les paiements ?`)) {
          return revokeBudget(b, true);
        }
      } else {
        toast.error(detail);
      }
    }
  };

  const [deleteConfirm, setDeleteConfirm] = useState(null);

  const prepareDeleteBudget = async (b) => {
    // iter90af : preview d'impact avant suppression cascade (fund_calls + VE + balances)
    try {
      const { data: calls } = await api.get(`/fund-calls?budget_id=${b.id}`);
      const ownerIds = new Set();
      let totalAmount = 0;
      (calls || []).forEach(c => {
        totalAmount += c.total_amount || 0;
        (c.distribution || []).forEach(d => { if (d.owner_id) ownerIds.add(d.owner_id); });
      });
      setDeleteConfirm({
        budget: b,
        callCount: (calls || []).length,
        totalAmount,
        ownerCount: ownerIds.size,
      });
    } catch {
      setDeleteConfirm({ budget: b, callCount: 0, totalAmount: 0, ownerCount: 0 });
    }
  };

  const confirmDeleteBudget = async () => {
    if (!deleteConfirm) return;
    try {
      await api.delete(`/fiscal/budgets/${deleteConfirm.budget.id}?force=true`);
      toast.success(`Budget supprime avec ${deleteConfirm.callCount} appel(s)`);
      setDeleteConfirm(null);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const loadComparison = async (yearId) => {
    try { const { data } = await api.get(`/fiscal/budget-comparison/${yearId}`); setComparison(data); }
    catch { toast.error('Erreur chargement'); }
  };

  const totalBudget = budgetForm.lines.reduce((s, l) => s + Number(l.amount || 0), 0);
  // iter93y : ventilation charges (classe 6) vs produits (classe 7) par
  // classe de compte (le signe stocke est deja negatif pour classe 7).
  const totalCharges = budgetForm.lines.reduce((s, l) => {
    const isCl7 = String(l.account_number || '').startsWith('7');
    if (isCl7) return s;
    return s + Math.abs(Number(l.amount || 0));
  }, 0);
  const totalProduits = budgetForm.lines.reduce((s, l) => {
    const isCl7 = String(l.account_number || '').startsWith('7');
    if (!isCl7) return s;
    return s + Math.abs(Number(l.amount || 0));
  }, 0);
  const hasClass7 = budgetForm.lines.some(l => String(l.account_number || '').startsWith('7'));

  return (
    <div data-testid="fiscal-page">
      <div className="page-header">
        <h1 className="page-title"><Calendar size={24} className="inline mr-2" />Exercices Comptables</h1>
        <p className="page-subtitle">Exercices, budgets et cloture</p>
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex items-center justify-between mb-4">
          <TabsList>
            <TabsTrigger value="years">Exercices</TabsTrigger>
            <TabsTrigger value="budgets">Budgets</TabsTrigger>
            <TabsTrigger value="comparison">Budget vs Reel</TabsTrigger>
          </TabsList>
          <div>
            {tab === 'years' && (
              <Button onClick={openCreateYear} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-year-btn"><Plus size={16} className="mr-2" />Nouvel exercice</Button>
            )}
            {tab === 'budgets' && (
              <Button onClick={openCreateBudget} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-budget-btn"><Plus size={16} className="mr-2" />Nouveau budget</Button>
            )}
          </div>
        </div>

        <TabsContent value="years" className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow><TableHead>Nom</TableHead><TableHead>Debut</TableHead><TableHead>Fin</TableHead><TableHead>Statut</TableHead><TableHead>Resultat</TableHead><TableHead className="w-32">Actions</TableHead></TableRow></TableHeader>
              <TableBody>
                {years.length === 0 ? <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucun exercice</TableCell></TableRow> : years.map(y => (
                  <TableRow key={y.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-medium">{y.name}</TableCell>
                    <TableCell className="font-mono text-sm">{fmtDate(y.start_date)}</TableCell>
                    <TableCell className="font-mono text-sm">{fmtDate(y.end_date)}</TableCell>
                    <TableCell><Badge variant="outline" className={y.status === 'open' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-100 text-slate-600'}>{y.status === 'open' ? 'Ouvert' : 'Cloture'}</Badge></TableCell>
                    <TableCell className="font-mono">{y.result_net !== undefined ? `${y.result_net} EUR` : '-'}</TableCell>
                    <TableCell><div className="flex gap-1">
                      {y.status === 'open'
                        ? <>
                            <Button variant="ghost" size="sm" onClick={() => openEditYear(y)} title="Modifier l'exercice" data-testid={`edit-year-${y.id}`}><Pencil size={14} /></Button>
                            <Button variant="ghost" size="sm" onClick={() => setRegulFy(y)} className="text-orange-600" title="Regulariser cloture" data-testid={`regularize-${y.id}`}><Calculator size={14} /></Button>
                            <Button variant="ghost" size="sm" onClick={() => closeYear(y.id)} className="text-orange-600" title="Cloturer"><Lock size={14} /></Button>
                          </>
                        : <Button variant="ghost" size="sm" onClick={() => reopenYear(y.id)} title="Reouvrir"><Unlock size={14} /></Button>}
                    </div></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="budgets" className="mt-0">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {budgets.length === 0 ? <p className="text-slate-400 col-span-2 text-center py-8">Aucun budget</p> : budgets.map(b => {
              const fy = years.find(y => y.id === b.fiscal_year_id);
              const approved = b.status === 'approved';
              return (
                <Card key={b.id} className={`border-2 ${approved ? 'border-green-300' : 'border-slate-200'}`}>
                  <CardContent className="p-4">
                    <div className="flex justify-between items-start mb-2">
                      <div>
                        <div className="font-semibold text-sm">{b.name}</div>
                        <div className="text-xs text-slate-500">{fy?.name || 'Exercice inconnu'}</div>
                      </div>
                      <div className="text-right">
                        <Badge className={approved ? 'bg-green-600 text-white' : 'bg-slate-200 text-slate-700'}>{approved ? 'Approuve' : 'Brouillon'}</Badge>
                        <div className="font-mono text-sm font-bold text-[#022D52] mt-1">{b.total?.toFixed(2)} EUR</div>
                      </div>
                    </div>
                    <div className="text-xs text-slate-500 mb-3">{b.lines?.length || 0} postes budgetaires{approved && b.approved_at ? ` - approuve le ${b.approved_at.slice(0, 10)}` : ''}</div>
                    <div className="flex gap-2 flex-wrap">
                      {!approved && <Button size="sm" variant="outline" onClick={() => openEditBudget(b)} data-testid={`edit-budget-${b.id}`}>Modifier</Button>}
                      {!approved && b.lines?.length > 0 && <Button size="sm" className="bg-green-600 hover:bg-green-700 text-white" onClick={() => approveBudget(b)} data-testid={`approve-budget-${b.id}`}><CheckCircle2 size={14} className="mr-1" />Approuver</Button>}
                      {approved && <Button size="sm" variant="outline" onClick={() => { setWizardMode('create'); setWizardBudget(b); }} data-testid={`wizard-budget-${b.id}`}><Send size={14} className="mr-1" />Lancer appels</Button>}
                      {approved && <Button size="sm" variant="outline" className="border-amber-300 text-amber-700 hover:bg-amber-50" onClick={() => { setWizardMode('regenerate'); setWizardBudget(b); }} data-testid={`regenerate-budget-${b.id}`}><RotateCcw size={14} className="mr-1" />Regenerer non-echus</Button>}
                      <Button size="sm" variant="outline" onClick={() => downloadBudgetPdf(b)} data-testid={`download-budget-pdf-${b.id}`} title="Exporter le budget en PDF"><FileDown size={14} className="mr-1" />PDF</Button>
                      {approved && <Button size="sm" variant="ghost" onClick={() => revokeBudget(b)} title="Revoquer approbation"><Unlock size={14} /></Button>}
                      {!approved && <Button size="sm" variant="ghost" className="text-red-500" onClick={() => prepareDeleteBudget(b)} data-testid={`budget-delete-btn-${b.id}`}><Trash2 size={14} /></Button>}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        <TabsContent value="comparison" className="mt-0">
          <div className="mb-4">
            <Select onValueChange={loadComparison}>
              <SelectTrigger className="w-[300px]" data-testid="comparison-year-select"><SelectValue placeholder="Selectionner un exercice" /></SelectTrigger>
              <SelectContent>{years.map(y => <SelectItem key={y.id} value={y.id}>{y.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {comparison && (
            <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
              <Table>
                <TableHeader><TableRow><TableHead>Compte</TableHead><TableHead>Libelle</TableHead><TableHead className="text-right">Budget</TableHead><TableHead className="text-right">Reel</TableHead><TableHead className="text-right">Ecart</TableHead></TableRow></TableHeader>
                <TableBody>
                  {comparison.comparison.map((c, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-sm">{c.account_number}</TableCell>
                      <TableCell>{c.account_name}</TableCell>
                      <TableCell className="text-right font-mono">{c.budgeted.toFixed(2)}</TableCell>
                      <TableCell className="text-right font-mono">{c.actual.toFixed(2)}</TableCell>
                      <TableCell className={`text-right font-mono font-semibold ${c.difference >= 0 ? 'text-green-700' : 'text-red-700'}`}>{c.difference.toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="bg-slate-50 font-bold">
                    <TableCell colSpan={2}>TOTAL</TableCell>
                    <TableCell className="text-right font-mono">{comparison.total_budgeted.toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{comparison.total_actual.toFixed(2)}</TableCell>
                    <TableCell className={`text-right font-mono ${(comparison.total_budgeted - comparison.total_actual) >= 0 ? 'text-green-700' : 'text-red-700'}`}>{(comparison.total_budgeted - comparison.total_actual).toFixed(2)}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Year dialog */}
      <Dialog open={yearDialog} onOpenChange={(open) => { setYearDialog(open); if (!open) setEditingYear(null); }}>
        <DialogContent>
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingYear ? "Modifier l'exercice" : 'Nouvel exercice'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={yearForm.name} onChange={e => setYearForm({...yearForm, name: e.target.value})} data-testid="year-name" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Debut *</label><Input type="date" value={yearForm.start_date} onChange={e => setYearForm({...yearForm, start_date: e.target.value})} /></div>
              <div><label className="form-label">Fin *</label><Input type="date" value={yearForm.end_date} onChange={e => setYearForm({...yearForm, end_date: e.target.value})} /></div>
            </div>
            {/* iter90dq : prefixe libre pour l'auto-numerotation des factures */}
            <div>
              <label className="form-label">Reference interne (prefixe des factures)</label>
              <Input
                value={yearForm.invoice_number_prefix || ''}
                onChange={e => setYearForm({...yearForm, invoice_number_prefix: e.target.value})}
                placeholder="Ex : FA-2025- ou ACACIA-25-"
                data-testid="year-invoice-prefix"
              />
              <p className="text-xs text-slate-500 mt-1">
                Chaque nouvelle facture creee pendant cet exercice sera numerotee
                automatiquement : <b>{yearForm.invoice_number_prefix || 'FA-{annee}-'}0001</b>,
                {' '}<b>{yearForm.invoice_number_prefix || 'FA-{annee}-'}0002</b>, etc.
                Cette reference interne permet au commissaire aux comptes de lier
                chaque facture a la depense comptabilisee.
              </p>
              {editingYear && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5 mt-2">
                  <b>Attention :</b> les factures deja creees NE seront PAS renumerotees.
                  Seules les prochaines factures utiliseront le nouveau prefixe.
                </p>
              )}
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => { setYearDialog(false); setEditingYear(null); }}>Annuler</Button>
              <Button onClick={saveYear} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="year-save-btn">{editingYear ? 'Enregistrer' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Budget dialog */}
      <Dialog open={budgetDialog} onOpenChange={setBudgetDialog}>
        <DialogContent className="w-[95vw] max-w-5xl max-h-[92vh] overflow-y-auto">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingBudget ? 'Modifier le budget' : 'Nouveau budget previsionnel'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Exercice</label>
                <Select value={budgetForm.fiscal_year_id} onValueChange={v => setBudgetForm({...budgetForm, fiscal_year_id: v})}>
                  <SelectTrigger><SelectValue placeholder="Choisir" /></SelectTrigger>
                  <SelectContent>{years.filter(y=>y.status==='open').map(y=><SelectItem key={y.id} value={y.id}>{y.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div><label className="form-label">Nom</label><Input value={budgetForm.name} onChange={e => setBudgetForm({...budgetForm, name: e.target.value})} /></div>
            </div>

            {prevExp && prevExp.fiscal_year && (
              <Card className="border-purple-200 bg-purple-50/40">
                <CardContent className="p-3 flex items-center justify-between">
                  <div className="text-sm">
                    <span className="font-semibold">Realise N-1: </span>
                    <span className="text-slate-700">{prevExp.fiscal_year.name}</span> -
                    <span className="font-mono ml-1">{prevExp.total.toFixed(2)} EUR</span>
                    <span className="text-slate-500 ml-2">({prevExp.lines.length} postes)</span>
                  </div>
                  <Button size="sm" variant="outline" className="border-purple-300 text-purple-700 hover:bg-purple-100" onClick={prefillFromPrevious} data-testid="prefill-prev-year-btn">
                    <Sparkles size={14} className="mr-1" /> Pre-remplir depuis N-1
                  </Button>
                </CardContent>
              </Card>
            )}

            <div className="border border-slate-200 rounded-md bg-white">
              <div className="grid grid-cols-12 gap-3 bg-slate-50 px-3 py-2 text-[11px] uppercase tracking-wider text-slate-600 font-semibold border-b border-slate-200">
                <div className="col-span-6">Compte (classe 6 charges / classe 7 produits)</div>
                <div className="col-span-3">Cle de repartition</div>
                <div className="col-span-2 text-right">Montant (EUR)</div>
                <div className="col-span-1"></div>
              </div>
              {budgetForm.lines.length === 0 && (
                <div className="p-6 text-center text-slate-400 text-sm">Aucune ligne. Ajoutez-en ou pre-remplissez depuis N-1.</div>
              )}
              <div className="divide-y divide-slate-100">
                {budgetForm.lines.map((l, i) => {
                  // iter93y : detection classe 7 pour affichage badge produit
                  const isClass7 = String(l.account_number || '').startsWith('7');
                  return (
                  <div key={i} className={`grid grid-cols-12 gap-3 px-3 py-2 items-center ${isClass7 ? 'bg-emerald-50/40' : ''}`}>
                    <div className="col-span-6">
                      <div className="flex items-center gap-2">
                        <div className="flex-1">
                          <AccountSearchSelect
                            accounts={accounts}
                            value={l.account_number}
                            onChange={(num) => updateBudgetLine(i, 'account_number', num)}
                            placeholder="Rechercher par numero ou libelle..."
                            testId={`budget-line-acc-${i}`}
                          />
                        </div>
                        {isClass7 && (
                          <span
                            className="shrink-0 text-[10px] font-semibold uppercase tracking-wider px-2 py-1 rounded bg-emerald-100 text-emerald-800 border border-emerald-300"
                            title="Compte de produits - reduit le total des charges"
                            data-testid={`budget-line-class7-badge-${i}`}
                          >
                            Produit
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="col-span-3">
                      <select
                        className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm bg-white hover:border-slate-300 transition"
                        value={l.distribution_key_id || ''}
                        onChange={e => updateBudgetLine(i, 'distribution_key_id', e.target.value)}
                        data-testid={`budget-line-key-${i}`}
                      >
                        {distKeys.length === 0 && (
                          <option value="" disabled>Aucune cle - creez-en une</option>
                        )}
                        {distKeys.map(k => (
                          <option key={k.id} value={k.id}>
                            {k.name}{k.is_default ? ' (defaut)' : ''}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="col-span-2">
                      <Input
                        type="number"
                        step="0.01"
                        className="text-right text-sm h-10 bg-white"
                        value={l.amount}
                        onChange={e => updateBudgetLine(i, 'amount', e.target.value)}
                        data-testid={`budget-line-amount-${i}`}
                      />
                    </div>
                    <div className="col-span-1 text-center">
                      <button
                        onClick={() => removeBudgetLine(i)}
                        className="text-red-400 hover:text-red-600 p-1"
                        title="Supprimer la ligne"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                  );
                })}
              </div>
              <div className="border-t-2 bg-slate-50 px-3 py-2 flex items-center justify-between">
                <Button variant="ghost" size="sm" onClick={addBudgetLine} className="text-xs"><Plus size={12} className="mr-1" />Ajouter ligne</Button>
                <div className="flex items-center gap-4">
                  {hasClass7 && (
                    <div className="flex items-center gap-3 text-xs" data-testid="budget-breakdown">
                      <span className="text-slate-600">Charges (cl. 6) : <span className="font-mono font-semibold text-slate-800">{totalCharges.toFixed(2)}</span></span>
                      <span className="text-emerald-700">- Produits (cl. 7) : <span className="font-mono font-semibold">{totalProduits.toFixed(2)}</span></span>
                      <span className="text-slate-400">=</span>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-slate-500 uppercase tracking-wider">Total net</span>
                    <span className="font-mono font-bold text-base text-slate-900" data-testid="budget-total">{totalBudget.toFixed(2)} EUR</span>
                  </div>
                </div>
              </div>
            </div>

            {/* iter90cj : Engagement AG Fonds de reserve + Fonds de roulement */}
            <div className="border border-slate-200 rounded-md bg-white p-4">
              <div className="text-[11px] uppercase tracking-wider text-slate-600 font-semibold mb-3">
                Engagement AG - Fonds permanents (facultatif)
              </div>
              <div className="text-xs text-slate-500 mb-3">
                Montants engages par l&apos;AG des le vote du budget. Ces montants seront utilises pour calculer le transfert
                du capital roulement lors des mutations, meme avant que l&apos;assistant d&apos;appels de fonds ne soit lance.
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="form-label">Fonds de reserve (EUR)</label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={budgetForm.reserve_fund_amount || 0}
                    onChange={e => setBudgetForm({...budgetForm, reserve_fund_amount: Number(e.target.value) || 0})}
                    data-testid="budget-reserve-fund-amount"
                    placeholder="0.00"
                  />
                  <select
                    className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm bg-white hover:border-slate-300 transition"
                    value={budgetForm.reserve_fund_key_id || ''}
                    onChange={e => setBudgetForm({...budgetForm, reserve_fund_key_id: e.target.value})}
                    data-testid="budget-reserve-fund-key"
                  >
                    <option value="">Cle de repartition (defaut = generale)</option>
                    {distKeys.map(k => (
                      <option key={k.id} value={k.id}>{k.name}{k.is_default ? ' (defaut)' : ''}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <label className="form-label">Fonds de roulement (EUR)</label>
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={budgetForm.roulement_fund_amount || 0}
                    onChange={e => setBudgetForm({...budgetForm, roulement_fund_amount: Number(e.target.value) || 0})}
                    data-testid="budget-roulement-fund-amount"
                    placeholder="0.00"
                  />
                  <select
                    className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm bg-white hover:border-slate-300 transition"
                    value={budgetForm.roulement_fund_key_id || ''}
                    onChange={e => setBudgetForm({...budgetForm, roulement_fund_key_id: e.target.value})}
                    data-testid="budget-roulement-fund-key"
                  >
                    <option value="">Cle de repartition (defaut = generale)</option>
                    {distKeys.map(k => (
                      <option key={k.id} value={k.id}>{k.name}{k.is_default ? ' (defaut)' : ''}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setBudgetDialog(false)}>Annuler</Button>
              <Button onClick={saveBudget} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="budget-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Fund call wizard */}
      {wizardBudget && (
        <BudgetWizard
          budget={wizardBudget}
          fiscalYear={years.find(y => y.id === wizardBudget.fiscal_year_id) || null}
          distKeys={distKeys}
          mode={wizardMode}
          onClose={() => setWizardBudget(null)}
          onDone={() => { setWizardBudget(null); load(); }}
        />
      )}

      <RegularizationDialog
        fiscalYearId={regulFy?.id}
        open={!!regulFy}
        onClose={() => setRegulFy(null)}
        onDone={() => { setRegulFy(null); load(); }}
      />

      {/* iter90af : Confirmation cascade suppression budget */}
      {deleteConfirm && (
        <Dialog open onOpenChange={() => setDeleteConfirm(null)}>
          <DialogContent data-testid="budget-delete-dialog">
            <DialogHeader><DialogTitle>Supprimer le budget ?</DialogTitle></DialogHeader>
            <div className="space-y-2 text-sm">
              <p><strong>{deleteConfirm.budget.name}</strong></p>
              {deleteConfirm.callCount > 0 ? (
                <>
                  <p><span className="font-mono">{deleteConfirm.callCount}</span> appel(s) de fonds seront supprimes</p>
                  <p>Montant total : <span className="font-mono">{deleteConfirm.totalAmount.toFixed(2)} EUR</span></p>
                  <p><span className="font-mono">{deleteConfirm.ownerCount}</span> proprietaire(s) impacte(s)</p>
                  <p className="text-red-600 font-medium">Les ecritures comptables associees seront supprimees definitivement (balances de tiers recalculees).</p>
                </>
              ) : (
                <p className="text-slate-600 italic">Aucun appel de fonds lie a ce budget. Suppression sans impact comptable.</p>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)} data-testid="budget-delete-cancel">Annuler</Button>
              <Button variant="destructive" onClick={confirmDeleteBudget} data-testid="budget-delete-confirm">Confirmer la suppression</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
