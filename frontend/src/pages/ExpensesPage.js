import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Receipt, X, Paperclip, Filter } from 'lucide-react';

const ALL = '__all__';

export default function ExpensesPage() {
  const [years, setYears] = useState([]);
  const [data, setData] = useState(null);
  const [filters, setFilters] = useState({ fiscal_year_id: '', account_number: '', distribution_key_id: '', bank_account: '', date_from: '', date_to: '' });
  const [loading, setLoading] = useState(false);
  const [distKeys, setDistKeys] = useState([]);
  const [bankAccounts, setBankAccounts] = useState([]);

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

  return (
    <div data-testid="expenses-page">
      <div className="page-header">
        <h1 className="page-title"><Receipt size={24} className="inline mr-2" />Depenses de l'exercice</h1>
        <p className="page-subtitle">Toutes les depenses comptabilisees, filtrables par nature, cle de repartition et compte bancaire</p>
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
            <TableHead className="text-right w-24">Montant</TableHead>
            <TableHead className="w-20">Statut</TableHead>
            <TableHead className="w-12 text-center">PJ</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {loading && <TableRow><TableCell colSpan={9} className="text-center py-8 text-slate-400">Chargement...</TableCell></TableRow>}
            {!loading && data && data.expenses.length === 0 && <TableRow><TableCell colSpan={9} className="text-center py-12 text-slate-400">Aucune depense</TableCell></TableRow>}
            {!loading && data && data.expenses.map((r, i) => (
              <TableRow key={r.id} className="hover:bg-slate-50/50" data-testid={`expense-row-${i}`}>
                <TableCell className="font-mono text-xs">{r.date}</TableCell>
                <TableCell className="font-mono text-xs">{r.number}</TableCell>
                <TableCell className="font-medium text-sm">{r.supplier}</TableCell>
                <TableCell className="max-w-[200px] truncate text-xs text-slate-600">{r.description}</TableCell>
                <TableCell className="font-mono text-xs">{r.account_number}<br/><span className="text-[10px] text-slate-400">{r.account_name}</span></TableCell>
                <TableCell className="text-xs">{r.distribution_key_name}</TableCell>
                <TableCell className="text-right font-mono font-semibold">{r.total_amount.toFixed(2)}</TableCell>
                <TableCell>
                  {r.paid ? <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline">Paye {r.paid_info?.date}</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">Impaye</Badge>}
                </TableCell>
                <TableCell className="text-center text-slate-400">
                  {r.attachments_count > 0 && <Paperclip size={12} className="inline" />}{r.attachments_count > 0 && <span className="text-[10px] ml-0.5">{r.attachments_count}</span>}
                </TableCell>
              </TableRow>
            ))}
            {data && data.expenses.length > 0 && (
              <TableRow className="bg-slate-50 font-bold">
                <TableCell colSpan={6} className="text-right">TOTAL</TableCell>
                <TableCell className="text-right font-mono">{data.totals.total.toFixed(2)} EUR</TableCell>
                <TableCell colSpan={2}></TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
