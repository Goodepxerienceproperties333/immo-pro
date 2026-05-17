import { useState, useEffect, useCallback, Fragment } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Trash2, Lock, Unlock, Calendar, CheckCircle2, RotateCcw, Sparkles, Send } from 'lucide-react';
import BudgetWizard from '@/components/BudgetWizard';

export default function FiscalYearPage() {
  const [tab, setTab] = useState('years');
  const [years, setYears] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [distKeys, setDistKeys] = useState([]);
  const [yearDialog, setYearDialog] = useState(false);
  const [budgetDialog, setBudgetDialog] = useState(false);
  const [editingBudget, setEditingBudget] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [yearForm, setYearForm] = useState({ name: '', start_date: '', end_date: '' });
  const [budgetForm, setBudgetForm] = useState({ fiscal_year_id: '', name: '', lines: [] });
  const [prevExp, setPrevExp] = useState(null);
  const [wizardBudget, setWizardBudget] = useState(null);

  const load = useCallback(async () => {
    const [y, b, a, dk] = await Promise.all([
      api.get('/fiscal/years'),
      api.get('/fiscal/budgets'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }),
      api.get('/distribution-keys'),
    ]);
    setYears(y.data); setBudgets(b.data); setAccounts(a.data); setDistKeys(dk.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const openCreateYear = () => {
    const now = new Date().getFullYear();
    setYearForm({ name: `Exercice ${now}`, start_date: `${now}-01-01`, end_date: `${now}-12-31` });
    setYearDialog(true);
  };
  const saveYear = async () => {
    try { await api.post('/fiscal/years', yearForm); toast.success('Exercice cree'); setYearDialog(false); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const closeYear = async (id) => {
    if (!window.confirm('Cloturer cet exercice ? Les ecritures seront verrouillees.')) return;
    try { const { data } = await api.post(`/fiscal/years/${id}/close`); toast.success(`${data.message} - Resultat: ${data.result_net} EUR`); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
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
      distribution_key_id: l.distribution_key_id || '',
      amount: l.amount_total,
    }));
    setBudgetForm(f => ({ ...f, lines }));
    toast.success(`${lines.length} ligne(s) pre-remplies depuis N-1`);
  };

  const addBudgetLine = () => setBudgetForm(f => ({
    ...f,
    lines: [...f.lines, { account_number: '', account_name: '', distribution_key_id: '', amount: 0 }],
  }));
  const removeBudgetLine = (i) => setBudgetForm(f => ({ ...f, lines: f.lines.filter((_, j) => j !== i) }));
  const updateBudgetLine = (i, field, value) => {
    const lines = [...budgetForm.lines];
    lines[i] = { ...lines[i], [field]: field === 'amount' ? Number(value) : value };
    if (field === 'account_number') {
      const acc = accounts.find(a => a.number === value);
      if (acc) lines[i].account_name = acc.name;
    }
    setBudgetForm({ ...budgetForm, lines });
  };

  const saveBudget = async () => {
    try {
      const payload = { ...budgetForm, lines: budgetForm.lines.filter(l => l.account_number && l.amount > 0) };
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
      setWizardBudget(data);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const revokeBudget = async (b) => {
    if (!window.confirm("Revoquer l'approbation ? Le budget redevient modifiable mais les appels deja generes restent.")) return;
    try { await api.post(`/fiscal/budgets/${b.id}/revoke`); toast.success('Approbation revoquee'); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteBudget = async (b) => {
    if (!window.confirm('Supprimer ce budget ?')) return;
    try { await api.delete(`/fiscal/budgets/${b.id}`); toast.success('Budget supprime'); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const loadComparison = async (yearId) => {
    try { const { data } = await api.get(`/fiscal/budget-comparison/${yearId}`); setComparison(data); }
    catch { toast.error('Erreur chargement'); }
  };

  const totalBudget = budgetForm.lines.reduce((s, l) => s + Number(l.amount || 0), 0);

  return (
    <div data-testid="fiscal-page">
      <div className="page-header">
        <h1 className="page-title"><Calendar size={24} className="inline mr-2" />Exercices Comptables</h1>
        <p className="page-subtitle">Exercices, budgets et cloture</p>
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4">
          <TabsTrigger value="years">Exercices</TabsTrigger>
          <TabsTrigger value="budgets">Budgets</TabsTrigger>
          <TabsTrigger value="comparison">Budget vs Reel</TabsTrigger>
        </TabsList>

        <TabsContent value="years" className="mt-0">
          <div className="flex justify-end mb-4">
            <Button onClick={openCreateYear} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-year-btn"><Plus size={16} className="mr-2" />Nouvel exercice</Button>
          </div>
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow><TableHead>Nom</TableHead><TableHead>Debut</TableHead><TableHead>Fin</TableHead><TableHead>Statut</TableHead><TableHead>Resultat</TableHead><TableHead className="w-32">Actions</TableHead></TableRow></TableHeader>
              <TableBody>
                {years.length === 0 ? <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucun exercice</TableCell></TableRow> : years.map(y => (
                  <TableRow key={y.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-medium">{y.name}</TableCell>
                    <TableCell className="font-mono text-sm">{y.start_date}</TableCell>
                    <TableCell className="font-mono text-sm">{y.end_date}</TableCell>
                    <TableCell><Badge variant="outline" className={y.status === 'open' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-100 text-slate-600'}>{y.status === 'open' ? 'Ouvert' : 'Cloture'}</Badge></TableCell>
                    <TableCell className="font-mono">{y.result_net !== undefined ? `${y.result_net} EUR` : '-'}</TableCell>
                    <TableCell><div className="flex gap-1">
                      {y.status === 'open'
                        ? <Button variant="ghost" size="sm" onClick={() => closeYear(y.id)} className="text-orange-600" title="Cloturer"><Lock size={14} /></Button>
                        : <Button variant="ghost" size="sm" onClick={() => reopenYear(y.id)} title="Reouvrir"><Unlock size={14} /></Button>}
                    </div></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>

        <TabsContent value="budgets" className="mt-0">
          <div className="flex justify-end mb-4">
            <Button onClick={openCreateBudget} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-budget-btn"><Plus size={16} className="mr-2" />Nouveau budget</Button>
          </div>
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
                        <div className="font-mono text-sm font-bold text-[#0055FF] mt-1">{b.total?.toFixed(2)} EUR</div>
                      </div>
                    </div>
                    <div className="text-xs text-slate-500 mb-3">{b.lines?.length || 0} postes budgetaires{approved && b.approved_at ? ` - approuve le ${b.approved_at.slice(0, 10)}` : ''}</div>
                    <div className="flex gap-2 flex-wrap">
                      {!approved && <Button size="sm" variant="outline" onClick={() => openEditBudget(b)} data-testid={`edit-budget-${b.id}`}>Modifier</Button>}
                      {!approved && b.lines?.length > 0 && <Button size="sm" className="bg-green-600 hover:bg-green-700 text-white" onClick={() => approveBudget(b)} data-testid={`approve-budget-${b.id}`}><CheckCircle2 size={14} className="mr-1" />Approuver</Button>}
                      {approved && <Button size="sm" variant="outline" onClick={() => setWizardBudget(b)} data-testid={`wizard-budget-${b.id}`}><Send size={14} className="mr-1" />Lancer appels</Button>}
                      {approved && <Button size="sm" variant="outline" onClick={() => revokeBudget(b)} title="Revoquer"><RotateCcw size={14} /></Button>}
                      {!approved && <Button size="sm" variant="ghost" className="text-red-500" onClick={() => deleteBudget(b)}><Trash2 size={14} /></Button>}
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
      <Dialog open={yearDialog} onOpenChange={setYearDialog}>
        <DialogContent>
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvel exercice</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={yearForm.name} onChange={e => setYearForm({...yearForm, name: e.target.value})} data-testid="year-name" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Debut *</label><Input type="date" value={yearForm.start_date} onChange={e => setYearForm({...yearForm, start_date: e.target.value})} /></div>
              <div><label className="form-label">Fin *</label><Input type="date" value={yearForm.end_date} onChange={e => setYearForm({...yearForm, end_date: e.target.value})} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setYearDialog(false)}>Annuler</Button>
              <Button onClick={saveYear} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="year-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Budget dialog */}
      <Dialog open={budgetDialog} onOpenChange={setBudgetDialog}>
        <DialogContent className="max-w-4xl">
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

            <div className="border rounded-md overflow-hidden">
              <table className="w-full text-sm">
                <thead><tr className="bg-slate-50 text-xs text-slate-600 uppercase">
                  <th className="p-2 text-left w-44">Compte (nature)</th>
                  <th className="p-2 text-left">Libelle</th>
                  <th className="p-2 text-left w-48">Cle de repartition</th>
                  <th className="p-2 text-right w-32">Montant annuel (EUR)</th>
                  <th className="p-2 w-10"></th>
                </tr></thead>
                <tbody>
                  {budgetForm.lines.length === 0 && (
                    <tr><td colSpan={5} className="p-4 text-center text-slate-400">Aucune ligne. Ajoutez-en ou pre-remplissez depuis N-1.</td></tr>
                  )}
                  {budgetForm.lines.map((l, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="p-1">
                        <select className="w-full border rounded px-2 py-1 text-xs font-mono" value={l.account_number} onChange={e => updateBudgetLine(i, 'account_number', e.target.value)} data-testid={`budget-line-acc-${i}`}>
                          <option value="">--</option>
                          {accounts.map(a => <option key={a.number} value={a.number}>{a.number} - {a.name}</option>)}
                        </select>
                      </td>
                      <td className="p-1 text-xs text-slate-500">{l.account_name}</td>
                      <td className="p-1">
                        <select className="w-full border rounded px-2 py-1 text-xs" value={l.distribution_key_id || ''} onChange={e => updateBudgetLine(i, 'distribution_key_id', e.target.value)} data-testid={`budget-line-key-${i}`}>
                          <option value="">Tantiemes (defaut)</option>
                          {distKeys.map(k => <option key={k.id} value={k.id}>{k.name}</option>)}
                        </select>
                      </td>
                      <td className="p-1">
                        <Input type="number" step="0.01" className="text-right text-sm h-8 w-28 ml-auto" value={l.amount} onChange={e => updateBudgetLine(i, 'amount', e.target.value)} data-testid={`budget-line-amount-${i}`} />
                      </td>
                      <td className="p-1 text-center"><button onClick={() => removeBudgetLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button></td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 bg-slate-50 font-bold">
                    <td colSpan={3} className="p-2">
                      <Button variant="ghost" size="sm" onClick={addBudgetLine} className="text-xs"><Plus size={12} className="mr-1" />Ajouter ligne</Button>
                    </td>
                    <td className="p-2 text-right font-mono">{totalBudget.toFixed(2)}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setBudgetDialog(false)}>Annuler</Button>
              <Button onClick={saveBudget} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="budget-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Fund call wizard */}
      {wizardBudget && (
        <BudgetWizard
          budget={wizardBudget}
          distKeys={distKeys}
          onClose={() => setWizardBudget(null)}
          onDone={() => { setWizardBudget(null); load(); }}
        />
      )}
    </div>
  );
}
