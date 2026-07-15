import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { useNavigate } from 'react-router-dom';
import { Receipt, X, Paperclip, Filter, Pencil, Download, Save, ChevronDown, ChevronRight, LayoutGrid, List, AlertCircle, Wand2, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/contexts/AuthContext';
import { fmtDate } from '@/lib/dateFmt';

const ALL = '__all__';

export default function ExpensesPage() {
  const navigate = useNavigate();
  const { selectedCopro } = useAuth();
  const [years, setYears] = useState([]);
  const [data, setData] = useState(null);
  // Filters reordered : Cle (N1) -> Nature (N2) -> Compte (N3) -> dates
  // No bank account filter (does not belong here - moved to /banking).
  const [filters, setFilters] = useState({
    fiscal_year_id: '',
    distribution_key_id: '',
    expense_category_id: '',
    account_number: '',
    date_from: '',
    date_to: '',
  });
  const [loading, setLoading] = useState(false);
  const [distKeys, setDistKeys] = useState([]);
  const [natures, setNatures] = useState([]);
  // View mode : 'flat' (table classique) ou 'grouped' (hierarchie Cle -> Nature -> Compte)
  const [viewMode, setViewMode] = useState(() => localStorage.getItem('expenses_view_mode') || 'grouped');
  // Collapsed groups by composite key (key_id, nature_id, account_number)
  const [collapsed, setCollapsed] = useState({});
  // Inline edit dialog state : modifier cle de repartition + % occupant/proprio
  const [quickEdit, setQuickEdit] = useState(null);
  const [savingEdit, setSavingEdit] = useState(false);
  // iter90g8 : dialog Diagnostic factures non categorisees
  const [diagOpen, setDiagOpen] = useState(false);
  const [diagLoading, setDiagLoading] = useState(false);
  const [diagData, setDiagData] = useState(null);
  const [applyingSupplier, setApplyingSupplier] = useState('');

  useEffect(() => {
    Promise.all([
      api.get('/fiscal/years'),
      api.get('/distribution-keys'),
      api.get('/expense-categories').catch(() => ({ data: [] })),
    ]).then(([y, dk, nat]) => {
      setYears(y.data); setDistKeys(dk.data);
      setNatures(nat.data || []);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    localStorage.setItem('expenses_view_mode', viewMode);
  }, [viewMode]);

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

  const clearFilters = () => setFilters({ fiscal_year_id: '', distribution_key_id: '', expense_category_id: '', account_number: '', date_from: '', date_to: '' });
  const setField = (k, v) => setFilters(f => ({ ...f, [k]: v === ALL ? '' : v }));

  // iter90g8 : Diagnostic factures non categorisees (rubrique "Autres charges")
  const loadDiagnostic = useCallback(async () => {
    if (!selectedCopro) { toast.error('Selectionnez une copropriete'); return; }
    setDiagLoading(true);
    try {
      const params = { copropriete_id: selectedCopro };
      if (filters.fiscal_year_id) params.fiscal_year_id = filters.fiscal_year_id;
      const { data } = await api.get('/invoices/uncategorized-diagnostic', { params });
      setDiagData(data);
    } catch (e) {
      toast.error('Erreur diagnostic : ' + (e.response?.data?.detail || e.message));
    } finally { setDiagLoading(false); }
  }, [selectedCopro, filters.fiscal_year_id]);

  const openDiagnostic = () => {
    setDiagOpen(true);
    if (!diagData) loadDiagnostic();
  };

  const applySupplierSuggestion = async (supplierRow) => {
    if (!supplierRow.suggestion) return;
    if (!window.confirm(
      `Appliquer le compte ${supplierRow.suggestion.account_number} `
      + `(${supplierRow.suggestion.expense_category_name || 'nature auto'}) `
      + `sur ${supplierRow.invoice_count} facture(s) de "${supplierRow.supplier}" ?`
    )) return;
    setApplyingSupplier(supplierRow.supplier);
    try {
      const ids = supplierRow.sample_invoices.map(i => i.id);
      const { data } = await api.post('/invoices/bulk-assign-account', {
        invoice_ids: ids,
        account_number: supplierRow.suggestion.account_number,
        expense_category_id: supplierRow.suggestion.expense_category_id || '',
        copropriete_id: selectedCopro,
      });
      toast.success(`${data.updated} facture(s) mise(s) a jour pour ${supplierRow.supplier}`);
      // Recharge diagnostic + tableau depenses
      await loadDiagnostic();
      await load();
    } catch (e) {
      toast.error('Echec bulk-assign : ' + (e.response?.data?.detail || e.message));
    } finally { setApplyingSupplier(''); }
  };

  const openQuickEdit = async (row) => {
    try {
      if (row.source === 'journal') {
        // Journal-sourced expense (FI/OD : bank fees, financial expenses). The
        // editable fields live on the journal entry's LINE, identified by
        // (entry_id, account_number).
        setQuickEdit({
          source: 'journal',
          entry_id: row.id,
          line_account: row.source_account || row.account_number,
          row_snapshot: row,
          account_number: row.account_number,
          expense_category_id: row.expense_category_id || '',
          distribution_key_id: row.distribution_key_id || '',
          occupant_pct: row.occupant_pct ?? 0,
          proprietaire_pct: row.proprietaire_pct ?? 100,
          description: row.description || '',
        });
        return;
      }
      // Invoice : load full doc for PUT
      // iter90fg : facture multi-lignes -> row.id = "{inv_id}::line-{idx}",
      // il faut utiliser row.invoice_id pour recuperer la facture reelle.
      const invId = row.invoice_id || row.id;
      const { data: inv } = await api.get(`/invoices/${invId}`);
      setQuickEdit({
        source: 'invoice',
        invoice: inv,
        account_number: inv.account_number || '',
        expense_category_id: inv.expense_category_id || '',
        distribution_key_id: inv.distribution_key_id || '',
        occupant_pct: inv.occupant_pct ?? 0,
        proprietaire_pct: inv.proprietaire_pct ?? 100,
        description: inv.description || '',
      });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement');
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
      if (quickEdit.source === 'journal') {
        // PUT entries/{id}/line-quick : updates the targeted line in the journal entry
        await api.put(`/accounting/entries/${quickEdit.entry_id}/line-quick`, {
          line_account: quickEdit.line_account,
          new_account: quickEdit.account_number !== quickEdit.line_account ? quickEdit.account_number : undefined,
          expense_category_id: quickEdit.expense_category_id,
          distribution_key_id: quickEdit.distribution_key_id,
          occupant_pct: quickEdit.occupant_pct,
          proprietaire_pct: quickEdit.proprietaire_pct,
          description: quickEdit.description,
        });
      } else {
        const inv = quickEdit.invoice;
        await api.put(`/invoices/${inv.id}`, {
          number: inv.number, date: inv.date, due_date: inv.due_date || '',
          supplier: inv.supplier, description: quickEdit.description ?? inv.description,
          total_amount: inv.total_amount, vat_amount: inv.vat_amount || 0,
          account_number: quickEdit.account_number || inv.account_number,
          expense_category_id: quickEdit.expense_category_id,
          distribution_key_id: quickEdit.distribution_key_id,
          status: inv.status,
          is_private_fee: !!inv.is_private_fee,
          private_fee_owner_id: inv.private_fee_owner_id || '',
          occupant_pct: quickEdit.occupant_pct,
          proprietaire_pct: quickEdit.proprietaire_pct,
          copropriete_id: inv.copropriete_id,
        });
      }
      toast.success('Depense mise a jour');
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
    if (filters.expense_category_id) params.expense_category_id = filters.expense_category_id;
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

  // iter90d : exports PDF additionnels (journaux comptables + liste exhaustive factures)
  const downloadOtherPdf = async (kind) => {
    if (!selectedCopro) { alert('Selectionnez une copropriete'); return; }
    const now = new Date();
    const y = now.getFullYear();
    const df = filters.date_from || `${y}-01-01`;
    const dt = filters.date_to || `${y}-12-31`;
    const params = { copropriete_id: selectedCopro, date_from: df, date_to: dt };
    const path = kind === 'journals' ? '/reports/journals/pdf' : '/reports/invoices-list/pdf';
    const fname = kind === 'journals' ? `journaux_${df}_au_${dt}.pdf` : `liste_factures_${df}_au_${dt}.pdf`;
    try {
      const res = await api.get(path, { params, responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = fname;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('Erreur generation PDF: ' + (e.response?.data?.detail || e.message));
    }
  };

  // ---- Build hierarchical groups : Cle (N1) -> Nature (N2) -> Compte (N3) -> rows ----
  // We compute this client-side from the flat expenses[] returned by the backend.
  // For each row, the grouping keys are :
  //   N1 : distribution_key_id (or "" = "Sans cle")
  //   N2 : expense_category_id (or "" = "Sans nature")
  //   N3 : account_number
  // Each group keeps its rows, subtotal, count.
  const groups = useMemo(() => {
    if (!data || !data.expenses) return [];
    const byKey = new Map();
    for (const r of data.expenses) {
      const k1 = r.distribution_key_id || '__no_key';
      const k2 = r.expense_category_id || '__no_nature';
      const k3 = r.account_number || '__no_account';
      if (!byKey.has(k1)) {
        byKey.set(k1, {
          key_id: r.distribution_key_id || '',
          key_name: r.distribution_key_name || 'Sans cle',
          subtotal: 0,
          count: 0,
          natures: new Map(),
        });
      }
      const g1 = byKey.get(k1);
      g1.subtotal += r.total_amount;
      g1.count += 1;
      if (!g1.natures.has(k2)) {
        g1.natures.set(k2, {
          nature_id: r.expense_category_id || '',
          nature_name: r.expense_category_name || r.account_name || 'Sans nature',
          nature_code: r.expense_category_code || '',
          subtotal: 0,
          count: 0,
          accounts: new Map(),
        });
      }
      const g2 = g1.natures.get(k2);
      g2.subtotal += r.total_amount;
      g2.count += 1;
      if (!g2.accounts.has(k3)) {
        g2.accounts.set(k3, {
          account_number: r.account_number || '',
          account_name: r.account_name || '',
          subtotal: 0,
          count: 0,
          rows: [],
        });
      }
      const g3 = g2.accounts.get(k3);
      g3.subtotal += r.total_amount;
      g3.count += 1;
      g3.rows.push(r);
    }
    // Convert maps to sorted arrays
    return Array.from(byKey.values())
      .sort((a, b) => (a.key_name || '').localeCompare(b.key_name || ''))
      .map(g1 => ({
        ...g1,
        natures: Array.from(g1.natures.values())
          .sort((a, b) => (a.nature_name || '').localeCompare(b.nature_name || ''))
          .map(g2 => ({
            ...g2,
            accounts: Array.from(g2.accounts.values())
              .sort((a, b) => (a.account_number || '').localeCompare(b.account_number || '')),
          })),
      }));
  }, [data]);

  const toggleCollapse = (key) => setCollapsed(c => ({ ...c, [key]: !c[key] }));

  return (
    <div data-testid="expenses-page">
      <div className="page-header flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h1 className="page-title"><Receipt size={24} className="inline mr-2" />Depenses de l&apos;exercice</h1>
          <p className="page-subtitle">Vue hierarchique : Cle de repartition &rarr; Nature &rarr; Compte comptable</p>
        </div>
        <div className="flex items-center gap-2">
          {/* View mode toggle : Flat list vs Grouped hierarchy */}
          <div className="inline-flex rounded-md border border-slate-200 bg-white">
            <button onClick={() => setViewMode('grouped')} className={`px-3 py-1.5 text-xs flex items-center gap-1 ${viewMode === 'grouped' ? 'bg-[#022D52] text-white' : 'text-slate-600 hover:bg-slate-50'}`} data-testid="view-grouped">
              <LayoutGrid size={12} /> Hierarchique
            </button>
            <button onClick={() => setViewMode('flat')} className={`px-3 py-1.5 text-xs flex items-center gap-1 ${viewMode === 'flat' ? 'bg-[#022D52] text-white' : 'text-slate-600 hover:bg-slate-50'}`} data-testid="view-flat">
              <List size={12} /> Liste plate
            </button>
          </div>
          <Button variant="outline" onClick={downloadPdf} data-testid="expenses-pdf-btn">
            <Download size={16} className="mr-2" />Liste des depenses (PDF)
          </Button>
          <Button variant="outline" onClick={() => downloadOtherPdf('invoices')} data-testid="invoices-list-pdf-btn" title="Toutes les factures, tous statuts confondus">
            <Download size={16} className="mr-2" />Liste des factures (PDF)
          </Button>
          <Button variant="outline" onClick={() => downloadOtherPdf('journals')} data-testid="journals-pdf-btn" title="Tous les journaux comptables (AC / OD / BQ / VE)">
            <Download size={16} className="mr-2" />Journaux (PDF)
          </Button>
          <Button
            variant="outline"
            onClick={openDiagnostic}
            data-testid="uncategorized-diagnostic-btn"
            title="Trouver et corriger en masse les factures sans compte comptable (rubrique 'Autres charges' du decompte)"
            className="border-amber-300 text-amber-700 hover:bg-amber-50"
          >
            <AlertCircle size={16} className="mr-2" />Autres charges : diagnostic
          </Button>
        </div>
      </div>

      {/* Filters - reordered : Exercice / Cle (N1) / Nature (N2) / Compte (N3) / Dates */}
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
              <label className="form-label text-xs">
                <span className="text-[10px] font-bold text-[#022D52] mr-1">N1</span>
                Cle de repartition
              </label>
              <Select value={filters.distribution_key_id || ALL} onValueChange={v => setField('distribution_key_id', v)}>
                <SelectTrigger data-testid="filter-key"><SelectValue placeholder="Toutes" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Toutes cles</SelectItem>
                  {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.code ? `${k.code} - ${k.name}` : k.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">
                <span className="text-[10px] font-bold text-[#022D52] mr-1">N2</span>
                Nature de depense
              </label>
              <Select value={filters.expense_category_id || ALL} onValueChange={v => setField('expense_category_id', v)}>
                <SelectTrigger data-testid="filter-nature"><SelectValue placeholder="Toutes" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Toutes natures</SelectItem>
                  {natures.map(n => <SelectItem key={n.id} value={n.id}>{n.code ? `${n.code} - ${n.name}` : n.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="form-label text-xs">
                <span className="text-[10px] font-bold text-[#022D52] mr-1">N3</span>
                Compte comptable
              </label>
              <Select value={filters.account_number || ALL} onValueChange={v => setField('account_number', v)}>
                <SelectTrigger data-testid="filter-account"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Tous comptes</SelectItem>
                  {(data?.filters?.accounts || []).map(a => <SelectItem key={a} value={a}>{a}</SelectItem>)}
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
      {data && (() => {
        // iter90f : Top 3 + bucket "Autres" + footer "Total" => somme widget = Total filtre
        const buildBreakdown = (mapping) => {
          const entries = Object.entries(mapping || {}).sort((a, b) => b[1] - a[1]);
          const top3 = entries.slice(0, 3);
          const restSum = entries.slice(3).reduce((s, [, v]) => s + v, 0);
          const widgetTotal = entries.reduce((s, [, v]) => s + v, 0);
          return { top3, restSum: Math.round(restSum * 100) / 100, widgetTotal: Math.round(widgetTotal * 100) / 100 };
        };
        const byNature = buildBreakdown(data.totals.by_nature || {});
        const byKey = buildBreakdown(data.totals.by_key || {});
        return (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <Card className="border-[#022D52] bg-blue-50/40">
              <CardContent className="p-4">
                <div className="text-xs uppercase tracking-wider text-[#01213e]">Total filtre</div>
                <div className="text-2xl font-black text-[#022D52] font-mono mt-1" style={{ fontFamily: 'Chivo,sans-serif' }} data-testid="expenses-total">{data.totals.total.toFixed(2)} EUR</div>
                <div className="text-[11px] text-slate-500 mt-1">{data.totals.count} depenses</div>
              </CardContent>
            </Card>
            <Card data-testid="widget-by-nature"><CardContent className="p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Par nature (top 3)</div>
              <div className="space-y-1 mt-1">
                {byNature.top3.map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs" data-testid={`widget-nature-row-${k}`}>
                    <span className="truncate max-w-[170px]" title={k}>{k}</span>
                    <span className="font-mono font-semibold">{v.toFixed(2)}</span>
                  </div>
                ))}
                {byNature.restSum > 0.005 && (
                  <div className="flex justify-between text-xs text-slate-500 italic" data-testid="widget-nature-others">
                    <span>Autres</span>
                    <span className="font-mono">{byNature.restSum.toFixed(2)}</span>
                  </div>
                )}
                <div className="flex justify-between text-xs pt-1 mt-1 border-t border-slate-200 font-semibold" data-testid="widget-nature-total">
                  <span className="uppercase text-[10px] tracking-wider">Total</span>
                  <span className="font-mono">{byNature.widgetTotal.toFixed(2)}</span>
                </div>
              </div>
            </CardContent></Card>
            <Card data-testid="widget-by-key"><CardContent className="p-3">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Par cle (top 3)</div>
              <div className="space-y-1 mt-1">
                {byKey.top3.map(([k, v]) => (
                  <div key={k} className="flex justify-between text-xs" data-testid={`widget-key-row-${k}`}>
                    <span className="truncate max-w-[170px]" title={k}>{k}</span>
                    <span className="font-mono font-semibold">{v.toFixed(2)}</span>
                  </div>
                ))}
                {byKey.restSum > 0.005 && (
                  <div className="flex justify-between text-xs text-slate-500 italic" data-testid="widget-key-others">
                    <span>Autres</span>
                    <span className="font-mono">{byKey.restSum.toFixed(2)}</span>
                  </div>
                )}
                <div className="flex justify-between text-xs pt-1 mt-1 border-t border-slate-200 font-semibold" data-testid="widget-key-total">
                  <span className="uppercase text-[10px] tracking-wider">Total</span>
                  <span className="font-mono">{byKey.widgetTotal.toFixed(2)}</span>
                </div>
              </div>
            </CardContent></Card>
          </div>
        );
      })()}

      {/* Hierarchical or flat table */}
      {viewMode === 'flat' ? (
        <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
          <Table>
            <TableHeader><TableRow>
              <TableHead className="w-24">Date</TableHead>
              <TableHead>N&deg; facture</TableHead>
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
                <TableRow key={r.id} className={`hover:bg-slate-50/50 ${r.is_private_fee ? 'bg-purple-50/30' : ''}`} data-testid={`expense-row-${i}`}>
                  <TableCell className="font-mono text-xs">{fmtDate(r.date)}</TableCell>
                  <TableCell className="font-mono text-xs">{r.number}</TableCell>
                  <TableCell className="font-medium text-sm">{r.supplier}</TableCell>
                  <TableCell className="max-w-[260px] text-xs text-slate-600">
                    <div className="truncate">{r.description}</div>
                    {r.is_private_fee && (
                      <div className="mt-1 flex items-center gap-1 flex-wrap" data-testid={`expense-private-${r.id}`}>
                        <Badge className="bg-purple-100 text-purple-700 border-purple-200 text-[10px] font-semibold" variant="outline">
                          Privatif
                        </Badge>
                        {r.private_fee_owners_display && (
                          <span className="text-[10px] text-purple-700 italic truncate" title={r.private_fee_owners_display}>
                            {r.private_fee_owners_display}
                          </span>
                        )}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{r.account_number}<br/><span className="text-[10px] text-slate-400">{r.expense_category_name || r.account_name}</span></TableCell>
                  <TableCell className="text-xs">{r.distribution_key_name}</TableCell>
                  <TableCell className="text-right text-[11px] font-mono">
                    {(r.occupant_pct ?? 0) > 0 && (
                      <span className="text-amber-700 font-semibold">{(r.occupant_pct ?? 0).toFixed(0)}%</span>
                    )}
                    {(r.occupant_pct ?? 0) > 0 && <span className="text-slate-300"> / </span>}
                    <span className="text-[#01213e] font-semibold">{(r.proprietaire_pct ?? 100).toFixed(0)}%</span>
                  </TableCell>
                  <TableCell className="text-right font-mono font-semibold">{r.total_amount.toFixed(2)}</TableCell>
                  <TableCell>
                    {r.paid ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline">Paye {fmtDate(r.paid_info?.date)}</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">Impaye</Badge>}
                  </TableCell>
                  <TableCell className="text-center text-slate-400">
                    {r.attachments_count > 0 && <Paperclip size={12} className="inline" />}{r.attachments_count > 0 && <span className="text-[10px] ml-0.5">{r.attachments_count}</span>}
                  </TableCell>
                  <TableCell className="text-right whitespace-nowrap">
                    <Button variant="ghost" size="sm" onClick={() => openQuickEdit(r)} title="Modifier nature / cle / repartition" data-testid={`quick-edit-expense-${r.id}`} className="text-[#022D52]"><Pencil size={12} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => navigate(r.source === 'journal' ? `/accounting?entry=${r.id}` : `/invoices?edit=${r.invoice_id || r.id}`)} title="Edition complete" data-testid={`edit-expense-${r.id}`}><Receipt size={12} /></Button>
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
      ) : (
        /* ---- HIERARCHICAL VIEW : Cle (N1) -> Nature (N2) -> Compte (N3) -> rows ---- */
        <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
          {loading && <div className="text-center py-12 text-slate-400 text-sm">Chargement...</div>}
          {!loading && groups.length === 0 && <div className="text-center py-12 text-slate-400 text-sm">Aucune depense</div>}
          {!loading && groups.map((g1) => {
            const k1 = `k1-${g1.key_id || 'none'}`;
            const c1 = collapsed[k1];
            return (
              <div key={k1} className="border-b border-slate-200 last:border-b-0" data-testid={`group-key-${g1.key_id}`}>
                {/* Niveau 1 : Cle de repartition */}
                <button onClick={() => toggleCollapse(k1)} className="w-full flex items-center gap-2 px-4 py-3 bg-[#022D52]/10 hover:bg-[#022D52]/15 text-left">
                  {c1 ? <ChevronRight size={14} className="text-[#022D52]" /> : <ChevronDown size={14} className="text-[#022D52]" />}
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#022D52] text-white">N1 CLE</span>
                  <span className="font-semibold text-sm text-slate-800">{g1.key_name}</span>
                  <span className="ml-auto flex items-center gap-3">
                    <span className="text-[11px] text-slate-500">{g1.count} depense(s)</span>
                    <span className="font-mono font-bold text-base text-[#022D52]" data-testid={`group-key-${g1.key_id}-subtotal`}>{g1.subtotal.toFixed(2)} EUR</span>
                  </span>
                </button>
                {!c1 && g1.natures.map((g2) => {
                  const k2 = `k2-${g1.key_id || 'none'}-${g2.nature_id || 'none'}`;
                  const c2 = collapsed[k2];
                  return (
                    <div key={k2} className="border-t border-slate-100" data-testid={`group-nature-${g2.nature_id}`}>
                      {/* Niveau 2 : Nature de depense */}
                      <button onClick={() => toggleCollapse(k2)} className="w-full flex items-center gap-2 pl-10 pr-4 py-2 bg-amber-50/60 hover:bg-amber-50 text-left">
                        {c2 ? <ChevronRight size={12} className="text-amber-700" /> : <ChevronDown size={12} className="text-amber-700" />}
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-600 text-white">N2 NATURE</span>
                        <span className="font-medium text-sm text-slate-700">
                          {g2.nature_code ? <span className="font-mono text-xs text-slate-500 mr-1">{g2.nature_code}</span> : null}
                          {g2.nature_name}
                        </span>
                        <span className="ml-auto flex items-center gap-3">
                          <span className="text-[11px] text-slate-500">{g2.count} depense(s)</span>
                          <span className="font-mono font-semibold text-sm text-amber-800">{g2.subtotal.toFixed(2)} EUR</span>
                        </span>
                      </button>
                      {!c2 && g2.accounts.map((g3) => {
                        const k3 = `k3-${g1.key_id || 'none'}-${g2.nature_id || 'none'}-${g3.account_number}`;
                        const c3 = collapsed[k3];
                        return (
                          <div key={k3} className="border-t border-slate-100" data-testid={`group-account-${g3.account_number}`}>
                            {/* Niveau 3 : Compte comptable */}
                            <button onClick={() => toggleCollapse(k3)} className="w-full flex items-center gap-2 pl-16 pr-4 py-1.5 bg-slate-50/60 hover:bg-slate-50 text-left">
                              {c3 ? <ChevronRight size={11} className="text-slate-600" /> : <ChevronDown size={11} className="text-slate-600" />}
                              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-700 text-white">N3 CPTE</span>
                              <span className="font-mono text-xs text-slate-700">{g3.account_number}</span>
                              <span className="text-xs text-slate-500 truncate max-w-[400px]">{g3.account_name}</span>
                              <span className="ml-auto flex items-center gap-3">
                                <span className="text-[11px] text-slate-500">{g3.count}</span>
                                <span className="font-mono font-semibold text-xs text-slate-700">{g3.subtotal.toFixed(2)} EUR</span>
                              </span>
                            </button>
                            {!c3 && (
                              <div className="pl-20 pr-4 py-2 bg-white">
                                <Table>
                                  <TableHeader>
                                    <TableRow className="text-[10px]">
                                      <TableHead className="w-24 h-7">Date</TableHead>
                                      <TableHead className="h-7">Fournisseur</TableHead>
                                      <TableHead className="h-7">Description</TableHead>
                                      <TableHead className="h-7 text-right" title="Repartition Occupant / Proprietaire">%Occ/%Prop</TableHead>
                                      <TableHead className="h-7 text-right w-24">Montant</TableHead>
                                      <TableHead className="h-7 w-20">Statut</TableHead>
                                      <TableHead className="h-7 w-12 text-center">PJ</TableHead>
                                      <TableHead className="h-7 w-16"></TableHead>
                                    </TableRow>
                                  </TableHeader>
                                  <TableBody>
                                    {g3.rows.map((r, ri) => (
                                      <TableRow key={r.id} className={`hover:bg-slate-50/50 text-xs ${r.is_private_fee ? 'bg-purple-50/30' : ''}`} data-testid={`hier-row-${r.id}`}>
                                        <TableCell className="font-mono">{fmtDate(r.date)}</TableCell>
                                        <TableCell className="font-medium">{r.supplier}</TableCell>
                                        <TableCell className="max-w-[300px] text-slate-600">
                                          <div className="truncate">{r.description}</div>
                                          {r.is_private_fee && (
                                            <div className="mt-0.5 flex items-center gap-1 flex-wrap">
                                              <Badge className="bg-purple-100 text-purple-700 border-purple-200 text-[9px] font-semibold py-0 px-1" variant="outline">
                                                Privatif
                                              </Badge>
                                              {r.private_fee_owners_display && (
                                                <span className="text-[9px] text-purple-700 italic truncate" title={r.private_fee_owners_display}>
                                                  {r.private_fee_owners_display}
                                                </span>
                                              )}
                                            </div>
                                          )}
                                        </TableCell>
                                        <TableCell className="text-right font-mono">
                                          {(r.occupant_pct ?? 0) > 0 && (
                                            <span className="text-amber-700 font-semibold">{(r.occupant_pct ?? 0).toFixed(0)}%</span>
                                          )}
                                          {(r.occupant_pct ?? 0) > 0 && <span className="text-slate-300"> / </span>}
                                          <span className="text-[#01213e] font-semibold">{(r.proprietaire_pct ?? 100).toFixed(0)}%</span>
                                        </TableCell>
                                        <TableCell className="text-right font-mono font-semibold">{r.total_amount.toFixed(2)}</TableCell>
                                        <TableCell>
                                          {r.paid ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[9px]" variant="outline">Paye</Badge> : <Badge variant="outline" className="text-slate-400 text-[9px]">Impaye</Badge>}
                                        </TableCell>
                                        <TableCell className="text-center text-slate-400">
                                          {r.attachments_count > 0 && <><Paperclip size={10} className="inline" /><span className="text-[9px] ml-0.5">{r.attachments_count}</span></>}
                                        </TableCell>
                                        <TableCell className="text-right whitespace-nowrap">
                                          <Button variant="ghost" size="sm" onClick={() => openQuickEdit(r)} title="Modifier" data-testid={`hier-quick-edit-${r.id}`} className="text-[#022D52] h-6 w-6 p-0"><Pencil size={11} /></Button>
                                          <Button variant="ghost" size="sm" onClick={() => navigate(r.source === 'journal' ? `/accounting?entry=${r.id}` : `/invoices?edit=${r.invoice_id || r.id}`)} title="Edition complete" data-testid={`hier-edit-${r.id}`} className="h-6 w-6 p-0"><Receipt size={11} /></Button>
                                        </TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {data && data.expenses.length > 0 && (
            <div className="flex items-center justify-end gap-6 px-4 py-3 bg-slate-100 border-t border-slate-300">
              <span className="text-xs uppercase tracking-wider font-bold text-slate-600">Total general</span>
              <span className="font-mono font-black text-lg text-[#022D52]">{data.totals.total.toFixed(2)} EUR</span>
              <span className="text-[11px] text-slate-500">({data.totals.count} depenses)</span>
            </div>
          )}
        </div>
      )}

      {/* Dialog d'edition rapide : Nature + Compte + Cle + Repartition (occupant/proprio) */}
      <Dialog open={!!quickEdit} onOpenChange={open => !open && setQuickEdit(null)}>
        <DialogContent className="max-w-md" data-testid="quick-edit-dialog">
          <DialogHeader>
            <DialogTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>
              {quickEdit?.source === 'journal' ? 'Modifier la depense (frais bancaire / financier)' : 'Modifier la facture'}
            </DialogTitle>
            {quickEdit?.invoice && (
              <p className="text-xs text-slate-500 mt-1">
                {quickEdit.invoice.number} - {quickEdit.invoice.supplier} - {Number(quickEdit.invoice.total_amount || 0).toFixed(2)} EUR
              </p>
            )}
            {quickEdit?.source === 'journal' && quickEdit?.row_snapshot && (
              <p className="text-xs text-slate-500 mt-1">
                {quickEdit.row_snapshot.date} - {quickEdit.row_snapshot.supplier} - {Number(quickEdit.row_snapshot.total_amount || 0).toFixed(2)} EUR
                <span className="ml-2 px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 text-[10px] font-mono">{quickEdit.row_snapshot.journal_type}</span>
              </p>
            )}
          </DialogHeader>
          {quickEdit && (
            <div className="space-y-3 mt-2">
              <div>
                <label className="form-label">
                  <span className="text-[10px] font-bold text-amber-700 mr-1">N2</span>
                  Nature de depense
                </label>
                <Select
                  value={quickEdit.expense_category_id || '__none'}
                  onValueChange={v => setQuickEdit(q => ({...q, expense_category_id: v === '__none' ? '' : v}))}
                >
                  <SelectTrigger data-testid="qe-nature"><SelectValue placeholder="Aucune" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none">— Aucune nature —</SelectItem>
                    {natures.map(n => <SelectItem key={n.id} value={n.id}>{n.code ? `${n.code} - ${n.name}` : n.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="form-label">
                  <span className="text-[10px] font-bold text-slate-700 mr-1">N3</span>
                  Compte comptable (6xx / 75x)
                </label>
                <Input
                  value={quickEdit.account_number || ''}
                  onChange={e => setQuickEdit(q => ({...q, account_number: e.target.value}))}
                  placeholder="ex : 61060 ou 650"
                  className="font-mono"
                  data-testid="qe-account"
                />
                <p className="text-[10px] text-slate-500 mt-1">
                  {quickEdit.source === 'journal' ? 'Changer ici reaffecte la ligne du journal au bon compte de charge.' : 'Le compte de charge est repris depuis la facture.'}
                </p>
              </div>
              <div>
                <label className="form-label">
                  <span className="text-[10px] font-bold text-[#022D52] mr-1">N1</span>
                  Cle de repartition
                </label>
                <Select
                  value={quickEdit.distribution_key_id || '__none'}
                  onValueChange={v => setQuickEdit(q => ({...q, distribution_key_id: v === '__none' ? '' : v}))}
                >
                  <SelectTrigger data-testid="qe-dist-key"><SelectValue placeholder="Aucune" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none">— Aucune (charge non repartie) —</SelectItem>
                    {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.code ? `${k.code} - ${k.name}` : k.name}</SelectItem>)}
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
                {(() => {
                  const baseAmount = quickEdit.invoice
                    ? Number(quickEdit.invoice.total_amount) || 0
                    : Number(quickEdit.row_snapshot?.total_amount) || 0;
                  return (
                    <div className="grid grid-cols-2 gap-3 text-xs pt-1">
                      <div>
                        <div className="text-[10px] uppercase text-slate-500">Part occupant</div>
                        <div className="font-mono font-semibold text-amber-700">
                          {(baseAmount * (Number(quickEdit.occupant_pct) || 0) / 100).toFixed(2)} EUR
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase text-slate-500">Part proprietaire</div>
                        <div className="font-mono font-semibold text-[#01213e]">
                          {(baseAmount * (Number(quickEdit.proprietaire_pct) || 0) / 100).toFixed(2)} EUR
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
              {quickEdit.source === 'journal' && (
                <div>
                  <label className="form-label">Description</label>
                  <Input
                    value={quickEdit.description || ''}
                    onChange={e => setQuickEdit(q => ({...q, description: e.target.value}))}
                    data-testid="qe-desc"
                  />
                </div>
              )}
            </div>
          )}
          <DialogFooter className="mt-2">
            <Button variant="outline" onClick={() => setQuickEdit(null)}>Annuler</Button>
            <Button onClick={saveQuickEdit} disabled={savingEdit} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="qe-save-btn">
              <Save size={14} className="mr-2" />{savingEdit ? 'Enregistrement...' : 'Enregistrer'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* iter90g8 : Dialog Diagnostic factures non categorisees */}
      <Dialog open={diagOpen} onOpenChange={setDiagOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" data-testid="uncategorized-diagnostic-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertCircle className="text-amber-600" size={20} />
              Diagnostic : factures sans compte comptable
            </DialogTitle>
          </DialogHeader>
          <div className="text-xs text-slate-600 mb-3">
            Les factures sans compte PCMN tombent dans la rubrique &quot;Autres charges&quot; du decompte annuel au lieu d&apos;etre agregees sur le bon compte (ex : 6140 Assurance incendie). Ce diagnostic propose le compte le plus frequent utilise pour chaque fournisseur, base sur l&apos;historique deja classifie.
          </div>
          {diagLoading ? (
            <div className="text-center py-8 text-slate-500 text-sm">Analyse en cours...</div>
          ) : !diagData ? (
            <div className="text-center py-8 text-slate-500 text-sm">Cliquez sur Actualiser pour lancer le diagnostic.</div>
          ) : diagData.total_uncategorized === 0 ? (
            <div className="text-center py-8 text-emerald-700 flex flex-col items-center gap-2" data-testid="diag-empty">
              <CheckCircle2 size={40} />
              <div className="font-semibold">Aucune facture non categorisee</div>
              <div className="text-xs text-slate-500">Toutes vos factures ont un compte comptable.</div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="rounded-md border border-amber-300 bg-amber-50/50 p-3 text-sm">
                <div className="flex justify-between items-center">
                  <div>
                    <div className="font-semibold text-amber-900">
                      {diagData.total_uncategorized} facture(s) sans compte
                    </div>
                    <div className="text-xs text-amber-800 mt-0.5">
                      Total : <span className="font-mono font-bold">{diagData.total_amount_uncategorized.toFixed(2)} EUR</span>
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={loadDiagnostic} data-testid="diag-refresh">
                    Actualiser
                  </Button>
                </div>
              </div>
              {diagData.by_supplier.map((sup, i) => (
                <div key={i} className="border border-slate-200 rounded-md p-3" data-testid={`diag-supplier-${i}`}>
                  <div className="flex justify-between items-start gap-3">
                    <div className="flex-1">
                      <div className="font-semibold text-sm text-slate-900">{sup.supplier}</div>
                      <div className="text-xs text-slate-500">
                        {sup.invoice_count} facture(s) - Total{' '}
                        <span className="font-mono font-bold">{sup.total_amount.toFixed(2)} EUR</span>
                      </div>
                      {sup.suggestion ? (
                        <div className="mt-2 rounded bg-emerald-50 border border-emerald-200 p-2 text-xs">
                          <div className="font-semibold text-emerald-900 flex items-center gap-1">
                            <Wand2 size={12} />
                            Suggestion : compte <span className="font-mono">{sup.suggestion.account_number}</span>
                            {sup.suggestion.expense_category_name && (
                              <span className="text-slate-600">
                                ({sup.suggestion.expense_category_name})
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-emerald-700 mt-0.5">
                            Confiance {(sup.suggestion.confidence * 100).toFixed(0)}% - base sur {sup.suggestion.based_on_invoices} facture(s) deja classifiees
                          </div>
                        </div>
                      ) : (
                        <div className="mt-2 text-[11px] text-slate-400 italic">
                          Aucune suggestion (pas d&apos;historique classifiie pour ce fournisseur)
                        </div>
                      )}
                      <details className="mt-2 text-[11px] text-slate-500">
                        <summary className="cursor-pointer hover:text-slate-700">
                          Voir echantillon ({Math.min(sup.sample_invoices.length, 5)})
                        </summary>
                        <div className="mt-1 space-y-0.5">
                          {sup.sample_invoices.map((inv, j) => (
                            <div key={j} className="font-mono">
                              {inv.date} - {inv.number || inv.reference || '(sans ref)'} - {inv.amount.toFixed(2)} EUR
                            </div>
                          ))}
                        </div>
                      </details>
                    </div>
                    {sup.suggestion && (
                      <Button
                        size="sm"
                        onClick={() => applySupplierSuggestion(sup)}
                        disabled={applyingSupplier === sup.supplier}
                        className="bg-[#022D52] hover:bg-[#1D4ED8]"
                        data-testid={`diag-apply-${i}`}
                      >
                        {applyingSupplier === sup.supplier ? 'Application...' : 'Appliquer'}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiagOpen(false)}>Fermer</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
