import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Trash2, Lock, Unlock, Calendar, TrendingUp } from 'lucide-react';

export default function FiscalYearPage() {
  const [tab, setTab] = useState('years');
  const [years, setYears] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [yearDialog, setYearDialog] = useState(false);
  const [budgetDialog, setBudgetDialog] = useState(false);
  const [comparison, setComparison] = useState(null);
  const [yearForm, setYearForm] = useState({ name: '', start_date: '', end_date: '' });
  const [budgetForm, setBudgetForm] = useState({ fiscal_year_id: '', name: '', lines: [] });

  const load = useCallback(async () => {
    const [y, b, a] = await Promise.all([api.get('/fiscal/years'), api.get('/fiscal/budgets'), api.get('/accounting/pcmn', { params: { class_num: 6 } })]);
    setYears(y.data); setBudgets(b.data); setAccounts(a.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const openCreateYear = () => { const now = new Date().getFullYear(); setYearForm({ name: `Exercice ${now}`, start_date: `${now}-01-01`, end_date: `${now}-12-31` }); setYearDialog(true); };
  const saveYear = async () => { try { await api.post('/fiscal/years', yearForm); toast.success('Exercice cree'); setYearDialog(false); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const closeYear = async (id) => { if (!window.confirm('Cloturer cet exercice ? Les ecritures seront verrouilees.')) return; try { const { data } = await api.post(`/fiscal/years/${id}/close`); toast.success(`${data.message} - Resultat: ${data.result_net} EUR`); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const reopenYear = async (id) => { try { await api.post(`/fiscal/years/${id}/reopen`); toast.success('Exercice reouvert'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };

  const openCreateBudget = () => {
    const fy = years.find(y => y.status === 'open');
    setBudgetForm({ fiscal_year_id: fy?.id || '', name: 'Budget previsionnel', lines: accounts.map(a => ({ account_number: a.number, account_name: a.name, amount: 0 })) });
    setBudgetDialog(true);
  };
  const updateBudgetLine = (i, amount) => { const lines = [...budgetForm.lines]; lines[i] = { ...lines[i], amount: Number(amount) }; setBudgetForm({ ...budgetForm, lines }); };
  const saveBudget = async () => { try { const payload = { ...budgetForm, lines: budgetForm.lines.filter(l => l.amount > 0) }; await api.post('/fiscal/budgets', payload); toast.success('Budget enregistre'); setBudgetDialog(false); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };

  const loadComparison = async (yearId) => { try { const { data } = await api.get(`/fiscal/budget-comparison/${yearId}`); setComparison(data); } catch { toast.error('Erreur chargement'); } };

  return (
    <div data-testid="fiscal-page">
      <div className="page-header"><h1 className="page-title"><Calendar size={24} className="inline mr-2" />Exercices Comptables</h1><p className="page-subtitle">Exercices, budgets et cloture</p></div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4"><TabsTrigger value="years">Exercices</TabsTrigger><TabsTrigger value="budgets">Budgets</TabsTrigger><TabsTrigger value="comparison">Budget vs Reel</TabsTrigger></TabsList>
        <TabsContent value="years" className="mt-0">
          <div className="flex justify-end mb-4"><Button onClick={openCreateYear} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-year-btn"><Plus size={16} className="mr-2" /> Nouvel exercice</Button></div>
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table><TableHeader><TableRow><TableHead>Nom</TableHead><TableHead>Debut</TableHead><TableHead>Fin</TableHead><TableHead>Statut</TableHead><TableHead>Resultat</TableHead><TableHead className="w-32">Actions</TableHead></TableRow></TableHeader>
              <TableBody>
                {years.length === 0 ? <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucun exercice</TableCell></TableRow> : years.map(y => (
                  <TableRow key={y.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-medium">{y.name}</TableCell>
                    <TableCell className="font-mono text-sm">{y.start_date}</TableCell>
                    <TableCell className="font-mono text-sm">{y.end_date}</TableCell>
                    <TableCell><Badge variant="outline" className={y.status === 'open' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-100 text-slate-600'}>{y.status === 'open' ? 'Ouvert' : 'Cloture'}</Badge></TableCell>
                    <TableCell className="font-mono">{y.result_net !== undefined ? `${y.result_net} EUR` : '-'}</TableCell>
                    <TableCell><div className="flex gap-1">
                      {y.status === 'open' ? <Button variant="ghost" size="sm" onClick={() => closeYear(y.id)} className="text-orange-600" title="Cloturer"><Lock size={14} /></Button> : <Button variant="ghost" size="sm" onClick={() => reopenYear(y.id)} title="Reouvrir"><Unlock size={14} /></Button>}
                    </div></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
        <TabsContent value="budgets" className="mt-0">
          <div className="flex justify-end mb-4"><Button onClick={openCreateBudget} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-budget-btn"><Plus size={16} className="mr-2" /> Nouveau budget</Button></div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {budgets.length === 0 ? <p className="text-slate-400 col-span-2 text-center py-8">Aucun budget</p> : budgets.map(b => (
              <Card key={b.id} className="border-slate-200"><CardContent className="p-4">
                <div className="flex justify-between items-start mb-2"><span className="font-semibold text-sm">{b.name}</span><span className="font-mono text-sm font-bold text-[#0055FF]">{b.total?.toFixed(2)} EUR</span></div>
                <div className="text-xs text-slate-500">{b.lines?.length || 0} postes budgetaires</div>
              </CardContent></Card>
            ))}
          </div>
        </TabsContent>
        <TabsContent value="comparison" className="mt-0">
          <div className="mb-4">
            <Select onValueChange={loadComparison}><SelectTrigger className="w-[300px]" data-testid="comparison-year-select"><SelectValue placeholder="Selectionner un exercice" /></SelectTrigger>
              <SelectContent>{years.map(y => <SelectItem key={y.id} value={y.id}>{y.name}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          {comparison && (
            <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
              <Table><TableHeader><TableRow><TableHead>Compte</TableHead><TableHead>Libelle</TableHead><TableHead className="text-right">Budget</TableHead><TableHead className="text-right">Reel</TableHead><TableHead className="text-right">Ecart</TableHead></TableRow></TableHeader>
                <TableBody>
                  {comparison.comparison.map((c, i) => (
                    <TableRow key={i}><TableCell className="font-mono text-sm">{c.account_number}</TableCell><TableCell>{c.account_name}</TableCell>
                      <TableCell className="text-right font-mono">{c.budgeted.toFixed(2)}</TableCell>
                      <TableCell className="text-right font-mono">{c.actual.toFixed(2)}</TableCell>
                      <TableCell className={`text-right font-mono font-semibold ${c.difference >= 0 ? 'text-green-700' : 'text-red-700'}`}>{c.difference.toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                  <TableRow className="bg-slate-50 font-bold"><TableCell colSpan={2}>TOTAL</TableCell>
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
      <Dialog open={yearDialog} onOpenChange={setYearDialog}><DialogContent><DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvel exercice</DialogTitle></DialogHeader>
        <div className="space-y-4 mt-2">
          <div><label className="form-label">Nom *</label><Input value={yearForm.name} onChange={e => setYearForm({...yearForm, name: e.target.value})} data-testid="year-name" /></div>
          <div className="grid grid-cols-2 gap-4"><div><label className="form-label">Debut *</label><Input type="date" value={yearForm.start_date} onChange={e => setYearForm({...yearForm, start_date: e.target.value})} /></div><div><label className="form-label">Fin *</label><Input type="date" value={yearForm.end_date} onChange={e => setYearForm({...yearForm, end_date: e.target.value})} /></div></div>
          <div className="flex gap-3 justify-end"><Button variant="outline" onClick={() => setYearDialog(false)}>Annuler</Button><Button onClick={saveYear} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="year-save-btn">Creer</Button></div>
        </div>
      </DialogContent></Dialog>
      <Dialog open={budgetDialog} onOpenChange={setBudgetDialog}><DialogContent className="max-w-2xl"><DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Budget previsionnel</DialogTitle></DialogHeader>
        <div className="space-y-4 mt-2">
          <div className="grid grid-cols-2 gap-4">
            <div><label className="form-label">Exercice</label><Select value={budgetForm.fiscal_year_id} onValueChange={v => setBudgetForm({...budgetForm, fiscal_year_id: v})}><SelectTrigger><SelectValue placeholder="Choisir" /></SelectTrigger><SelectContent>{years.filter(y=>y.status==='open').map(y=><SelectItem key={y.id} value={y.id}>{y.name}</SelectItem>)}</SelectContent></Select></div>
            <div><label className="form-label">Nom</label><Input value={budgetForm.name} onChange={e => setBudgetForm({...budgetForm, name: e.target.value})} /></div>
          </div>
          <div className="border rounded-md max-h-64 overflow-y-auto">
            <table className="w-full text-sm"><thead><tr className="bg-slate-50 text-xs text-slate-600 sticky top-0"><th className="p-2 text-left">Compte</th><th className="p-2 text-left">Libelle</th><th className="p-2 text-right">Montant (EUR)</th></tr></thead>
              <tbody>{budgetForm.lines.map((l, i) => (
                <tr key={i} className="border-t border-slate-100"><td className="p-2 font-mono">{l.account_number}</td><td className="p-2 text-xs">{l.account_name}</td><td className="p-1"><Input type="number" step="0.01" className="w-28 ml-auto text-right h-7 text-sm" value={l.amount} onChange={e => updateBudgetLine(i, e.target.value)} /></td></tr>
              ))}</tbody>
              <tfoot><tr className="border-t-2 bg-slate-50 font-bold"><td colSpan={2} className="p-2">TOTAL</td><td className="p-2 text-right font-mono">{budgetForm.lines.reduce((s, l) => s + Number(l.amount), 0).toFixed(2)}</td></tr></tfoot>
            </table>
          </div>
          <div className="flex gap-3 justify-end"><Button variant="outline" onClick={() => setBudgetDialog(false)}>Annuler</Button><Button onClick={saveBudget} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="budget-save-btn">Enregistrer</Button></div>
        </div>
      </DialogContent></Dialog>
    </div>
  );
}
