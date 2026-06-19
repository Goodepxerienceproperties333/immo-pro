import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Users, Truck, Eye, ArrowUpRight, ArrowDownRight, Download, FileText, Filter, X } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

// ---- Period presets (re-usable) ----
function getPreset(name) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const y = today.getFullYear();
  const m = today.getMonth();
  switch (name) {
    case 'today': return { start: iso(today), end: iso(today) };
    case 'month': return { start: iso(new Date(y, m, 1)), end: iso(today) };
    case 'prev_month': return { start: iso(new Date(y, m - 1, 1)), end: iso(new Date(y, m, 0)) };
    case 'quarter': {
      const qstart = Math.floor(m / 3) * 3;
      return { start: iso(new Date(y, qstart, 1)), end: iso(today) };
    }
    case 'year': return { start: `${y}-01-01`, end: iso(today) };
    case 'prev_year': return { start: `${y - 1}-01-01`, end: `${y - 1}-12-31` };
    case 'all': return { start: '', end: '' };
    default: return null;
  }
}

function FilterBar({ startDate, endDate, search, onChange, storageKey }) {
  const apply = (next) => {
    onChange(next);
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify(next));
  };
  const reset = () => apply({ startDate: '', endDate: '', search: '' });
  const setPreset = (name) => {
    const p = getPreset(name);
    if (!p) return;
    apply({ startDate: p.start, endDate: p.end, search });
  };
  return (
    <div className="mb-4 p-3 bg-slate-50 border border-slate-200 rounded-md flex flex-wrap items-end gap-3" data-testid="filter-bar">
      <div className="flex items-center gap-1.5">
        <Filter size={14} className="text-slate-500" />
        <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Filtres</span>
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Du</label>
        <Input type="date" value={startDate} onChange={e => apply({ startDate: e.target.value, endDate, search })} className="h-8 text-xs w-36" data-testid="filter-start-date" />
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Au</label>
        <Input type="date" value={endDate} onChange={e => apply({ startDate, endDate: e.target.value, search })} className="h-8 text-xs w-36" data-testid="filter-end-date" />
      </div>
      <div className="flex-1 min-w-[160px]">
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Recherche</label>
        <Input value={search} onChange={e => apply({ startDate, endDate, search: e.target.value })} placeholder="Nom, VCS..." className="h-8 text-xs" data-testid="filter-search" />
      </div>
      <div className="flex flex-wrap gap-1">
        {[
          ['today', "Auj."],
          ['month', 'Ce mois'],
          ['prev_month', 'Mois -1'],
          ['quarter', 'Trim.'],
          ['year', 'Annee'],
          ['prev_year', 'N-1'],
          ['all', 'Tout'],
        ].map(([k, l]) => (
          <Button key={k} size="sm" variant="outline" className="h-8 text-[10px] px-2" onClick={() => setPreset(k)} data-testid={`filter-preset-${k}`}>
            {l}
          </Button>
        ))}
      </div>
      {(startDate || endDate || search) && (
        <Button size="sm" variant="ghost" className="h-8 text-red-600" onClick={reset} data-testid="filter-reset">
          <X size={12} className="mr-1" /> Reset
        </Button>
      )}
    </div>
  );
}

export default function BalanceTiersPage() {
  const [tab, setTab] = useState('owners');
  const [ownersData, setOwnersData] = useState(null);
  const [suppliersData, setSuppliersData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailType, setDetailType] = useState('');
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState(() => {
    try {
      const saved = localStorage.getItem('balance-tiers-filters');
      if (saved) return JSON.parse(saved);
    } catch {}
    return { startDate: '', endDate: '', search: '' };
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (filters.startDate) params.start_date = filters.startDate;
      if (filters.endDate) params.end_date = filters.endDate;
      const [o, s] = await Promise.all([
        api.get('/reports/balance-tiers/owners', { params }),
        api.get('/reports/balance-tiers/suppliers', { params }),
      ]);
      setOwnersData(o.data); setSuppliersData(s.data);
    } catch { toast.error('Erreur de chargement'); }
    finally { setLoading(false); }
  }, [filters.startDate, filters.endDate]);
  useEffect(() => { load(); }, [load]);

  const viewOwnerDetail = async (ownerId) => {
    try {
      const params = {};
      if (filters.startDate) params.start_date = filters.startDate;
      if (filters.endDate) params.end_date = filters.endDate;
      const { data } = await api.get(`/reports/balance-tiers/owners/${ownerId}`, { params });
      setDetail(data); setDetailType('owner');
    } catch { toast.error('Erreur'); }
  };
  const viewSupplierDetail = async (supplierId) => {
    try {
      const params = {};
      if (filters.startDate) params.start_date = filters.startDate;
      if (filters.endDate) params.end_date = filters.endDate;
      const { data } = await api.get(`/reports/balance-tiers/suppliers/${supplierId}`, { params });
      setDetail(data); setDetailType('supplier');
    } catch { toast.error('Erreur'); }
  };

  if (loading && !ownersData) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div>;

  // Apply free-text filter on owner_name / vcs_code client-side
  const applyTextFilter = (list) => {
    if (!filters.search) return list;
    const s = filters.search.toLowerCase().trim();
    return list.filter(x =>
      (x.owner_name || x.supplier_name || '').toLowerCase().includes(s) ||
      (x.vcs_code || x.bce_number || '').toLowerCase().includes(s)
    );
  };

  return (
    <div data-testid="balance-tiers-page">
      <div className="page-header flex items-start justify-between">
        <div><h1 className="page-title">Balance de Tiers</h1><p className="page-subtitle">Situation de compte des proprietaires et fournisseurs</p></div>
        <Button variant="outline" size="sm" data-testid="export-balance-tiers-xlsx"
          onClick={() => { const c = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || ''; window.open(`${API}/api/exports/balance-tiers/owners.xlsx${c && c !== 'all' ? '?copropriete_id=' + c : ''}`, '_blank'); }}>
          <Download size={14} className="mr-1" /> Export Excel
        </Button>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4" data-testid="tiers-tabs">
          <TabsTrigger value="owners"><Users size={14} className="mr-2" /> Proprietaires</TabsTrigger>
          <TabsTrigger value="suppliers"><Truck size={14} className="mr-2" /> Fournisseurs</TabsTrigger>
        </TabsList>

        <FilterBar
          startDate={filters.startDate}
          endDate={filters.endDate}
          search={filters.search}
          storageKey="balance-tiers-filters"
          onChange={setFilters}
        />
        {(filters.startDate || filters.endDate) && (
          <div className="mb-3 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5 inline-block">
            Soldes calcules <b>uniquement sur la periode</b> {filters.startDate || '…'} a {filters.endDate || '…'}.
            Decoche &quot;Tout&quot; pour la situation complete a date.
          </div>
        )}

        <TabsContent value="owners" className="mt-0">
          {ownersData && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                <Card className="border-red-200 bg-red-50"><CardContent className="p-4 flex items-center gap-3">
                  <ArrowUpRight size={20} className="text-red-600" />
                  <div><div className="text-[10px] uppercase tracking-wider text-red-600 font-semibold">Total debiteurs</div>
                    <div className="text-xl font-black text-red-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{ownersData.total_debiteurs.toFixed(2)} EUR</div>
                    <div className="text-[10px] text-red-500">Proprietaires qui doivent a la copropriete</div>
                  </div>
                </CardContent></Card>
                <Card className="border-green-200 bg-green-50"><CardContent className="p-4 flex items-center gap-3">
                  <ArrowDownRight size={20} className="text-green-600" />
                  <div><div className="text-[10px] uppercase tracking-wider text-green-600 font-semibold">Total crediteurs</div>
                    <div className="text-xl font-black text-green-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{ownersData.total_crediteurs.toFixed(2)} EUR</div>
                    <div className="text-[10px] text-green-500">Copropriete doit rembourser</div>
                  </div>
                </CardContent></Card>
              </div>
              <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead>
                    <TableHead className="text-xs">Comptes</TableHead>
                    <TableHead className="text-right text-xs" title="Solde compte Provisions 40000XXX">Solde Prov.</TableHead>
                    <TableHead className="text-right text-xs" title="Solde compte Reserve 40010XXX">Solde Reserve</TableHead>
                    <TableHead className="text-right">Appele</TableHead><TableHead className="text-right">Paye</TableHead>
                    <TableHead className="text-right">Solde</TableHead><TableHead>Statut</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {applyTextFilter(ownersData.owners).map(o => (
                      <TableRow key={o.owner_id} className="hover:bg-slate-50/50">
                        <TableCell className="font-medium">
                          {o.owner_name}
                          {o.is_former_owner && (
                            <Badge variant="outline" className="ml-2 bg-amber-50 border-amber-200 text-amber-700 text-[10px]" title="Ancien proprietaire avec solde residuel apres mutation">
                              Ex-prop.
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-[#0055FF]">{o.vcs_code}</TableCell>
                        <TableCell className="font-mono text-[11px] text-slate-500">
                          {o.account_provisions || '—'} <span className="text-slate-300">/</span> {o.account_reserve || '—'}
                        </TableCell>
                        <TableCell className={`text-right font-mono text-xs ${(o.provisions_balance||0) > 0.01 ? 'text-red-600' : (o.provisions_balance||0) < -0.01 ? 'text-green-600' : 'text-slate-400'}`}>{(o.provisions_balance||0).toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono text-xs ${(o.reserve_balance||0) > 0.01 ? 'text-red-600' : (o.reserve_balance||0) < -0.01 ? 'text-green-600' : 'text-slate-400'}`}>{(o.reserve_balance||0).toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{o.total_called.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{o.total_paid.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${o.balance > 0 ? 'text-red-700' : o.balance < 0 ? 'text-green-700' : 'text-slate-500'}`}>{o.balance.toFixed(2)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={o.status === 'debiteur' ? 'bg-red-50 text-red-700 border-red-200' : o.status === 'crediteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50 text-slate-500'}>
                            {o.status === 'debiteur' ? 'Debiteur' : o.status === 'crediteur' ? 'Crediteur' : 'Solde'}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1">
                            <Button variant="ghost" size="sm" onClick={() => viewOwnerDetail(o.owner_id)} data-testid={`view-owner-${o.owner_id}`} title="Detail"><Eye size={14} /></Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                const c = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
                                if (!c || c === 'all') { toast.error('Selectionnez une ACP specifique en haut de page'); return; }
                                window.open(`${API}/api/reports/situation-compte/${o.owner_id}/pdf?copropriete_id=${c}`, '_blank');
                              }}
                              data-testid={`pdf-situation-${o.owner_id}`}
                              title="Situation de compte PDF (envoi email/postal)"
                              className="text-[#0055FF] hover:text-[#0040CC]"
                            >
                              <FileText size={14} />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="suppliers" className="mt-0">
          {suppliersData && (
            <>
              <Card className="border-orange-200 bg-orange-50 mb-4"><CardContent className="p-4 flex items-center gap-3">
                <Truck size={20} className="text-orange-600" />
                <div><div className="text-[10px] uppercase tracking-wider text-orange-600 font-semibold">Total a payer aux fournisseurs</div>
                  <div className="text-xl font-black text-orange-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{suppliersData.total_a_payer.toFixed(2)} EUR</div>
                </div>
              </CardContent></Card>
              <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Fournisseur</TableHead><TableHead>Compte</TableHead><TableHead>N TVA</TableHead>
                    <TableHead className="text-right">Facture</TableHead><TableHead className="text-right">Paye</TableHead>
                    <TableHead className="text-right">Solde</TableHead><TableHead>Statut</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {applyTextFilter(suppliersData.suppliers).map((s, i) => (
                      <TableRow key={s.supplier_id || `orphan-${i}`} className="hover:bg-slate-50/50">
                        <TableCell className="font-medium">
                          {s.supplier_name}
                          {s.orphan && <Badge variant="outline" className="ml-2 text-[10px] bg-amber-50 text-amber-700 border-amber-200">Orphelin</Badge>}
                        </TableCell>
                        <TableCell className="font-mono text-[11px] text-slate-500">{s.tier_account || '—'}</TableCell>
                        <TableCell className="font-mono text-xs">{s.vat_number || '-'}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_invoiced.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_paid.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${s.balance > 0 ? 'text-orange-700' : s.balance < 0 ? 'text-green-700' : 'text-slate-500'}`}>{s.balance.toFixed(2)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={s.status === 'crediteur' ? 'bg-orange-50 text-orange-700 border-orange-200' : s.status === 'debiteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50 text-slate-500'}>
                            {s.status === 'crediteur' ? 'A payer' : s.status === 'debiteur' ? 'Trop-paye' : 'Solde'}
                          </Badge>
                        </TableCell>
                        <TableCell>{s.supplier_id ? <Button variant="ghost" size="sm" onClick={() => viewSupplierDetail(s.supplier_id)} data-testid={`view-supplier-${s.supplier_id}`}><Eye size={14} /></Button> : null}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>

      {/* Detail Dialog */}
      <Dialog open={!!detail} onOpenChange={() => setDetail(null)}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto" data-testid="tiers-detail-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              Situation de compte: {detailType === 'owner' ? detail?.owner?.name : detail?.supplier?.name}
            </DialogTitle>
            {detailType === 'owner' && detail?.owner?.vcs_code && (
              <p className="font-mono text-sm text-[#0055FF]">{detail.owner.vcs_code}</p>
            )}
          </DialogHeader>
          <div className="mt-2">
            <div className="flex gap-4 mb-4 text-sm">
              <div><span className="text-slate-500">Total debit:</span> <span className="font-mono font-bold">{detail?.total_debit?.toFixed(2)} EUR</span></div>
              <div><span className="text-slate-500">Total credit:</span> <span className="font-mono font-bold">{detail?.total_credit?.toFixed(2)} EUR</span></div>
              <div>
                <span className="text-slate-500">Solde:</span>
                <span className={`font-mono font-bold ml-1 ${detail?.status === 'debiteur' ? 'text-red-700' : detail?.status === 'crediteur' ? (detailType === 'owner' ? 'text-green-700' : 'text-orange-700') : 'text-slate-600'}`}>
                  {detail?.balance?.toFixed(2)} EUR
                </span>
                <Badge variant="outline" className="ml-2 text-[10px]">{detail?.status === 'debiteur' ? (detailType === 'owner' ? 'Doit payer' : 'Trop-paye') : detail?.status === 'crediteur' ? (detailType === 'owner' ? 'A rembourser' : 'A payer') : 'Solde'}</Badge>
              </div>
            </div>
            <div className="border rounded-md overflow-hidden">
              <Table>
                <TableHeader><TableRow>
                  <TableHead className="w-24">Date</TableHead><TableHead>Description</TableHead><TableHead>Ref</TableHead>
                  <TableHead className="text-right w-28">Debit</TableHead><TableHead className="text-right w-28">Credit</TableHead><TableHead className="text-right w-28">Solde</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {(detail?.movements || []).map((m, i) => (
                    <TableRow key={i} className="hover:bg-slate-50/50">
                      <TableCell className="font-mono text-xs">{m.date}</TableCell>
                      <TableCell className="text-sm">{m.description}</TableCell>
                      <TableCell className="text-xs text-slate-400">{m.reference}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{m.debit > 0 ? m.debit.toFixed(2) : ''}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{m.credit > 0 ? m.credit.toFixed(2) : ''}</TableCell>
                      <TableCell className={`text-right font-mono text-sm font-semibold ${m.running_balance > 0 ? 'text-red-700' : m.running_balance < 0 ? 'text-green-700' : ''}`}>{m.running_balance?.toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
