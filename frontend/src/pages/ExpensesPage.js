import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { useNavigate } from 'react-router-dom';
import { Receipt, X, Paperclip, Filter, Pencil, Download, Save } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/contexts/AuthContext';
import { fmtDate } from '@/lib/dateFmt';

const ALL = '__all__';

export default function ExpensesPage() {
  const navigate = useNavigate();
  const { selectedCopro } = useAuth();
  const [years, setYears] = useState([]);
  const [data, setData] = useState(null);
  const [filters, setFilters] = useState({ fiscal_year_id: '', account_number: '', distribution_key_id: '', bank_account: '', date_from: '', date_to: '' });
  const [loading, setLoading] = useState(false);
  const [distKeys, setDistKeys] = useState([]);
  const [bankAccounts, setBankAccounts] = useState([]);
  // Inline edit dialog state : modifier cle de repartition + % occupant/proprio
  const [quickEdit, setQuickEdit] = useState(null);
  const [savingEdit, setSavingEdit] = useState(false);

  useEffect(() => {
    Promise.all([
      api.get('/fiscal/years'),
      api.get('/distribution-keys'),
      api.get('/accounting/pcmn', { params: { class_num: 5 } }),
    ]).then(([y, dk, bk]) => {
      setYears(y.data); setDistKeys(dk.data);
      setBankAccounts(bk.data.filter(a => a.number.startsWith('55')));
    }).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      Object.entries(filters).forEach(([k, v]) => { if (v) params[k] = v; });
      const { data } = await api.get('/fiscal/expenses', { params });
      setData(data);
    } finally { setLoading(false); }
  }, [filters]);

  useEffect(() => { load(); }, [load]);

  const clearFilters = () => setFilters({ fiscal_year_id: '', account_number: '', distribution_key_id: '', bank_account: '', date_from: '', date_to: '' });
  const setField = (k, v) => setFilters(f => ({ ...f, [k]: v === ALL ? '' : v }));

  const openQuickEdit = async (row) => {
    try {
      // Recupere les details de la facture pour avoir tous les champs requis par PUT
      const { data: inv } = await api.get(`/invoices/${row.id}`);
      setQuickEdit({
        invoice: inv,
        distribution_key_id: inv.distribution_key_id || '',
        occupant_pct: inv.occupant_pct ?? 0,
        proprietaire_pct: inv.proprietaire_pct ?? 100,
      });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement facture');
    }
  };

  const setOccupant = (val) => {
    const v = Math.max(0, Math.min(100, parseFloat(val) || 0));
    setQuickEdit(q => ({ ...q, occupant_pct: v, proprietaire_pct: +(100 - v).toFixed(2) }));
  };
  const setProprio = (val) => {
    const v = Math.max(0, Math.min(100, parseFloat(val) || 0));
    setQuickEdit(q => ({ ...q, proprietaire_pct: v, occupant_pct: +(100 - v).toFixed(2) }));
  };

  const saveQuickEdit = async () => {
    if (!quickEdit) return;
    setSavingEdit(true);
    try {
      const inv = quickEdit.invoice;
      // PUT preserve les autres champs, on modifie seulement les 3 cibles
      await api.put(`/invoices/${inv.id}`, {
        number: inv.number, date: inv.date, due_date: inv.due_date || '',
        supplier: inv.supplier, description: inv.description,
        total_amount: inv.total_amount, vat_amount: inv.vat_amount || 0,
        account_number: inv.account_number,
        expense_category_id: inv.expense_category_id || '',
        distribution_key_id: quickEdit.distribution_key_id,
        status: inv.status,
        is_private_fee: !!inv.is_private_fee,
        private_fee_owner_id: inv.private_fee_owner_id || '',
        occupant_pct: quickEdit.occupant_pct,
        proprietaire_pct: quickEdit.proprietaire_pct,
        copropriete_id: inv.copropriete_id,
      });
      toast.success('Facture mise a jour');
      setQuickEdit(null);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally {
      setSavingEdit(false);
    }
  };

  const downloadPdf = async () => {
    if (!selectedCopro) { alert('Selectionnez une copropriete'); return; }
    const now = new Date();
    const y = now.getFullYear();
    const df = filters.date_from || `${y}-01-01`;
    const dt = filters.date_to || `${y}-12-31`;
    const params = { copropriete_id: selectedCopro, date_from: df, date_to: dt };
    if (filters.distribution_key_id) params.distribution_key_id = filters.distribution_key_id;
    if (filters.account_number) params.account_number = filters.account_number;
    try {
      const res = await api.get('/reports/depenses/pdf', { params, responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = `liste_depenses_${df}_au_${dt}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('Erreur generation PDF: ' + (e.response?.data?.detail || e.message));
    }
  };

  return (
    <div data-testid="expenses-page">
      <div className="page-header flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h1 className="page-title"><Receipt size={24} className="inline mr-2" />Depenses de l'exercice</h1>
          <p className="page-subtitle">Toutes les depenses comptabilisees, filtrables par nature, cle de repartition et compte bancaire</p>
        </div>
        <Button variant="outline" onClick={downloadPdf} data-testid="expenses-pdf-btn">
          <Download size={16} className="mr-2" />Liste des depenses (PDF)
        </Button>
      </div>

      {/* Filters */}
      <Card className="mb-4 border-slate-200">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-3">
            <Filter size={14} className="text-slate-500" />
            <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Filtres</span>
            <Button variant="ghost" size="sm" onClick={clearFilters} className="ml-auto h-7 text-xs"><X size={12} className="mr-1" />Reinitialiser</Button>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <div>
              <label className="form-label text-xs">Exercice</label>
              <Select value={filters.fiscal_year_id || ALL} onValueChange={v => setField('fiscal_year_id', v)}>
                <SelectTrigger data-testid="filter-fy"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Tous</SelectItem>
                  {years.map(y => <SelectItem key={y.id} value={y.id}>{y.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">Nature (compte 6xx)</label>
              <Select value={filters.account_number || ALL} onValueChange={v => setField('account_number', v)}>
                <SelectTrigger data-testid="filter-account"><SelectValue placeholder="Toutes" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Toutes natures</SelectItem>
                  {(data?.filters?.accounts || []).map(a => <SelectItem key={a} value={a}>{a}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">Cle de repartition</label>
              <Select value={filters.distribution_key_id || ALL} onValueChange={v => setField('distribution_key_id', v)}>
                <SelectTrigger data-testid="filter-key"><SelectValue placeholder="Toutes" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Toutes cles</SelectItem>
                  {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">Compte bancaire</label>
              <Select value={filters.bank_account || ALL} onValueChange={v => setField('bank_account', v)}>
                <SelectTrigger data-testid="filter-bank"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Tous comptes</SelectItem>
                  {bankAccounts.map(a => <SelectItem key={a.number} value={a.number}>{a.number} - {a.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">Du</label>
              <Input type="date" value={filters.date_from} onChange={e => setField('date_from', e.target.value)} data-testid="filter-from" />
            </div>
            <div>
              <label className="form-label text-xs">Au</label>
              <Input type="date" value={filters.date_to} onChange={e => setField('date_to', e.target.value)} data-testid="filter-to" />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Totals */}
      {data && (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
          <Card className="border-[#0055FF] bg-blue-50/40">
            <CardContent className="p-4">
              <div className="text-xs uppercase tracking-wider text-blue-700">Total filtre</div>
              <div className="text-2xl font-black text-[#0055FF] font-mono mt-1" style={{ fontFamily: 'Chivo,sans-serif' }} data-testid="expenses-total">{data.totals.total.toFixed(2)} EUR</div>
              <div className="text-[11px] text-slate-500 mt-1">{data.totals.count} depenses</div>
            </CardContent>
          </Card>
          <Card><CardContent className="p-3">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Par nature (top 3)</div>
            <div className="space-y-1 mt-1">
              {Object.entries(data.totals.by_account).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs"><span className="font-mono">{k}</span><span className="font-mono font-semibold">{v.toFixed(2)}</span></div>
              ))}
            </div>
          </CardContent></Card>
          <Card><CardContent className="p-3">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Par cle (top 3)</div>
            <div className="space-y-1 mt-1">
              {Object.entries(data.totals.by_key).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs"><span className="truncate max-w-[120px]">{k}</span><span className="font-mono font-semibold">{v.toFixed(2)}</span></div>
              ))}
            </div>
          </CardContent></Card>
          <Card><CardContent className="p-3">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Par banque</div>
            <div className="space-y-1 mt-1">
              {Object.entries(data.totals.by_bank).slice(0, 3).map(([k, v]) => (
                <div key={k} className="flex justify-between text-xs"><span className="font-mono">{k}</span><span className="font-mono font-semibold">{v.toFixed(2)}</span></div>
              ))}
              {Object.keys(data.totals.by_bank).length === 0 && <div className="text-xs text-slate-400">Aucun paiement matche</div>}
            </div>
          </CardContent></Card>
        </div>
      )}

      {/* Table */}
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead className="w-24">Date</TableHead>
            <TableHead>N° facture</TableHead>
            <TableHead>Fournisseur</TableHead>
            <TableHead>Description</TableHead>
            <TableHead className="text-xs">Nature</TableHead>
            <TableHead className="text-xs">Cle</TableHead>
            <TableHead className="text-xs text-right" title="Repartition Occupant / Proprietaire">%Occ / %Prop</TableHead>
            <TableHead className="text-right w-24">Montant</TableHead>
            <TableHead className="w-20">Statut</TableHead>
            <TableHead className="w-12 text-center">PJ</TableHead>
            <TableHead className="w-16"></TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {loading && <TableRow><TableCell colSpan={11} className="text-center py-8 text-slate-400">Chargement...</TableCell></TableRow>}
            {!loading && data && data.expenses.length === 0 && <TableRow><TableCell colSpan={11} className="text-center py-12 text-slate-400">Aucune depense</TableCell></TableRow>}
            {!loading && data && data.expenses.map((r, i) => (
              <TableRow key={r.id} className="hover:bg-slate-50/50" data-testid={`expense-row-${i}`}>
                <TableCell className="font-mono text-xs">{fmtDate(r.date)}</TableCell>
                <TableCell className="font-mono text-xs">{r.number}</TableCell>
                <TableCell className="font-medium text-sm">{r.supplier}</TableCell>
                <TableCell className="max-w-[200px] truncate text-xs text-slate-600">{r.description}</TableCell>
                <TableCell className="font-mono text-xs">{r.account_number}<br/><span className="text-[10px] text-slate-400">{r.account_name}</span></TableCell>
                <TableCell className="text-xs">{r.distribution_key_name}</TableCell>
                <TableCell className="text-right text-[11px] font-mono">
                  {(r.occupant_pct ?? 0) > 0 && (
                    <span className="text-amber-700 font-semibold">{(r.occupant_pct ?? 0).toFixed(0)}%</span>
                  )}
                  {(r.occupant_pct ?? 0) > 0 && <span className="text-slate-300"> / </span>}
                  <span className="text-blue-700 font-semibold">{(r.proprietaire_pct ?? 100).toFixed(0)}%</span>
                </TableCell>
                <TableCell className="text-right font-mono font-semibold">{r.total_amount.toFixed(2)}</TableCell>
                <TableCell>
                  {r.paid ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline">Paye {fmtDate(r.paid_info?.date)}</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">Impaye</Badge>}
                </TableCell>
                <TableCell className="text-center text-slate-400">
                  {r.attachments_count > 0 && <Paperclip size={12} className="inline" />}{r.attachments_count > 0 && <span className="text-[10px] ml-0.5">{r.attachments_count}</span>}
                </TableCell>
                <TableCell className="text-right whitespace-nowrap">
                  <Button variant="ghost" size="sm" onClick={() => openQuickEdit(r)} title="Modifier cle + %" data-testid={`quick-edit-expense-${r.id}`} className="text-[#0055FF]"><Pencil size={12} /></Button>
                  <Button variant="ghost" size="sm" onClick={() => navigate(`/invoices?edit=${r.id}`)} title="Edition complete" data-testid={`edit-expense-${r.id}`}><Receipt size={12} /></Button>
                </TableCell>
              </TableRow>
            ))}
            {data && data.expenses.length > 0 && (
              <TableRow className="bg-slate-50 font-bold">
                <TableCell colSpan={7} className="text-right">TOTAL</TableCell>
                <TableCell className="text-right font-mono">{data.totals.total.toFixed(2)} EUR</TableCell>
                <TableCell colSpan={3}></TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {/* Dialog d'edition rapide : cle de repartition + % occupant/proprio */}
      <Dialog open={!!quickEdit} onOpenChange={open => !open && setQuickEdit(null)}>
        <DialogContent className="max-w-md" data-testid="quick-edit-dialog">
          <DialogHeader>
            <DialogTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>
              Modifier la cle + repartition
            </DialogTitle>
            {quickEdit?.invoice && (
              <p className="text-xs text-slate-500 mt-1">
                {quickEdit.invoice.number} - {quickEdit.invoice.supplier} - {Number(quickEdit.invoice.total_amount || 0).toFixed(2)} EUR
              </p>
            )}
          </DialogHeader>
          {quickEdit && (
            <div className="space-y-3 mt-2">
              <div>
                <label className="form-label">Cle de repartition</label>
                <Select
                  value={quickEdit.distribution_key_id || '__none'}
                  onValueChange={v => setQuickEdit(q => ({...q, distribution_key_id: v === '__none' ? '' : v}))}
                >
                  <SelectTrigger data-testid="qe-dist-key"><SelectValue placeholder="Aucune" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none">— Aucune (charge non repartie) —</SelectItem>
                    {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="rounded-md border border-amber-200 bg-amber-50/40 p-3 space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
                  Repartition occupant / proprietaire
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="form-label text-xs">% Occupant</label>
                    <Input type="number" min={0} max={100} step={1}
                      value={quickEdit.occupant_pct}
                      onChange={e => setOccupant(e.target.value)}
                      data-testid="qe-occupant-pct"
                    />
                  </div>
                  <div>
                    <label className="form-label text-xs">% Proprietaire</label>
                    <Input type="number" min={0} max={100} step={1}
                      value={quickEdit.proprietaire_pct}
                      onChange={e => setProprio(e.target.value)}
                      data-testid="qe-proprietaire-pct"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 text-xs pt-1">
                  <div>
                    <div className="text-[10px] uppercase text-slate-500">Part occupant</div>
                    <div className="font-mono font-semibold text-amber-700">
                      {((Number(quickEdit.invoice.total_amount) || 0) * (Number(quickEdit.occupant_pct) || 0) / 100).toFixed(2)} EUR
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase text-slate-500">Part proprietaire</div>
                    <div className="font-mono font-semibold text-blue-700">
                      {((Number(quickEdit.invoice.total_amount) || 0) * (Number(quickEdit.proprietaire_pct) || 0) / 100).toFixed(2)} EUR
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setQuickEdit(null)}>Annuler</Button>
            <Button onClick={saveQuickEdit} disabled={savingEdit} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="qe-save-btn">
              <Save size={14} className="mr-2" />{savingEdit ? 'Enregistrement...' : 'Enregistrer'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
