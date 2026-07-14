import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Users, Truck, Eye, ArrowUpRight, ArrowDownRight, Download, FileText, Link2, EyeOff, ChevronDown } from 'lucide-react';
import FilterBar from '@/components/balance-tiers/FilterBar';
import TiersDetailDialog from '@/components/balance-tiers/TiersDetailDialog';
import LettrerDialog from '@/components/balance-tiers/LettrerDialog';
import SupplierMergeDialog from '@/components/balance-tiers/SupplierMergeDialog';

const API = process.env.REACT_APP_BACKEND_URL;

export default function BalanceTiersPage() {
  const [tab, setTab] = useState('owners');
  const [ownersData, setOwnersData] = useState(null);
  const [suppliersData, setSuppliersData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailType, setDetailType] = useState('');
  const [detailGrouped, setDetailGrouped] = useState(true);
  const [detailOwnerId, setDetailOwnerId] = useState('');
  const [loading, setLoading] = useState(true);
  // ----- Manual reconciliation (lettrage manuel) state -----
  const [lettrerOpen, setLettrerOpen] = useState(false);
  const [lettrerOrphan, setLettrerOrphan] = useState(null);  // {supplier_name, credit, invoice_count}
  const [lettrerSuppliers, setLettrerSuppliers] = useState([]);  // available suppliers for picker
  const [lettrerSearch, setLettrerSearch] = useState('');
  const [lettrerLoading, setLettrerLoading] = useState(false);
  // ----- Multi-selection merge state -----
  const [mergeMode, setMergeMode] = useState(false);
  const [selectedSupplierIds, setSelectedSupplierIds] = useState(new Set());
  const [mergeDialogOpen, setMergeDialogOpen] = useState(false);
  const [mergeKeepId, setMergeKeepId] = useState('');
  const [filters, setFilters] = useState(() => {
    try {
      const saved = localStorage.getItem('balance-tiers-filters');
      if (saved) return JSON.parse(saved);
    } catch { /* ignore parse error */ }
    return { startDate: '', endDate: '', search: '' };
  });
  const [hideZeroBalance, setHideZeroBalance] = useState(() => {
    try {
      return localStorage.getItem('balance-tiers-hide-zero') === 'true';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try { localStorage.setItem('balance-tiers-hide-zero', String(hideZeroBalance)); } catch { /* ignore */ }
  }, [hideZeroBalance]);

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

  const viewOwnerDetail = async (ownerId, grouped = true) => {
    try {
      const params = { group_by_owner: grouped };
      if (filters.startDate) params.start_date = filters.startDate;
      if (filters.endDate) params.end_date = filters.endDate;
      const { data } = await api.get(`/reports/balance-tiers/owners/${ownerId}`, { params });
      setDetail(data); setDetailType('owner');
      setDetailOwnerId(ownerId);
      setDetailGrouped(grouped);
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

  // ----- Manual reconciliation (lettrage manuel d'un fournisseur orphelin) -----
  const openLettrerDialog = async (orphanRow) => {
    setLettrerOrphan(orphanRow);
    setLettrerSearch('');
    setLettrerOpen(true);
    try {
      // Load all suppliers in the current ACP
      const { data } = await api.get('/suppliers');
      setLettrerSuppliers(data || []);
    } catch {
      setLettrerSuppliers([]);
      toast.error('Erreur de chargement des fournisseurs');
    }
  };

  const commitLettrer = async (selectedSupplierId) => {
    if (!lettrerOrphan || !selectedSupplierId) return;
    const coproId = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    if (!coproId || coproId === 'all') {
      toast.error('Selectionnez une ACP avant de lettrer'); return;
    }
    setLettrerLoading(true);
    try {
      const { data } = await api.post('/reports/balance-tiers/lettrer-supplier', {
        copropriete_id: coproId,
        orphan_name: lettrerOrphan.supplier_name,
        supplier_id: selectedSupplierId,
      });
      toast.success(
        `Lettrage OK : ${data.supplier_name} (compte ${data.tier_account}) - ` +
        `${data.invoices_matched} facture(s) matchee(s), ${data.journal_entries_created} ecriture(s) AC creee(s)`
      );
      setLettrerOpen(false);
      setLettrerOrphan(null);
      await load();  // Refresh the table
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Erreur lors du lettrage';
      toast.error(typeof msg === 'string' ? msg : 'Erreur lettrage');
    } finally {
      setLettrerLoading(false);
    }
  };

  if (loading && !ownersData) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#022D52] animate-pulse w-1/2" /></div>;

  // iter90fm : export PDF "Balance des tiers" - vue simplifiee (totaux uniquement)
  // ou vue detaillee (mouvements par tiers), scope selon l'onglet actif.
  const exportBalanceTiersPdf = (scope, view) => {
    const c = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    if (!c || c === 'all') { toast.error('Selectionnez une ACP specifique en haut de page'); return; }
    const params = new URLSearchParams({ copropriete_id: c, scope, view });
    if (filters.startDate) params.set('start_date', filters.startDate);
    if (filters.endDate) params.set('end_date', filters.endDate);
    window.open(`${API}/api/reports/balance-tiers/pdf?${params.toString()}`, '_blank');
  };

  // Apply free-text filter on owner_name / vcs_code client-side
  const applyTextFilter = (list) => {
    if (!filters.search) return list;
    const s = filters.search.toLowerCase().trim();
    return list.filter(x =>
      (x.owner_name || x.supplier_name || '').toLowerCase().includes(s) ||
      (x.vcs_code || x.bce_number || '').toLowerCase().includes(s)
    );
  };

  // iter90cg : filtre "masquer les soldes a zero" pour faciliter la lecture
  // Applique apres le filtre texte. status === 'solde' est defini backend par |balance| < 0.01
  const applyZeroFilter = (list) => {
    if (!hideZeroBalance) return list;
    return list.filter(x => x.status !== 'solde');
  };
  const applyFilters = (list) => applyZeroFilter(applyTextFilter(list));

  return (
    <div data-testid="balance-tiers-page">
      <div className="page-header flex items-start justify-between">
        <div><h1 className="page-title">Balance de Tiers</h1><p className="page-subtitle">Situation de compte des proprietaires et fournisseurs</p></div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" data-testid="export-balance-tiers-xlsx"
            onClick={() => { const c = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || ''; window.open(`${API}/api/exports/balance-tiers/owners.xlsx${c && c !== 'all' ? '?copropriete_id=' + c : ''}`, '_blank'); }}>
            <Download size={14} className="mr-1" /> Export Excel
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" data-testid="export-balance-tiers-pdf-menu-btn">
                <FileText size={14} className="mr-1" /> Export PDF <ChevronDown size={12} className="ml-1" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem data-testid="export-pdf-simplified-btn" onClick={() => exportBalanceTiersPdf(tab, 'simplified')}>
                Vue simplifiee <span className="text-slate-400 ml-1 text-xs">(totaux par {tab === 'owners' ? 'proprietaire' : 'fournisseur'})</span>
              </DropdownMenuItem>
              <DropdownMenuItem data-testid="export-pdf-detailed-btn" onClick={() => exportBalanceTiersPdf(tab, 'detailed')}>
                Vue detaillee <span className="text-slate-400 ml-1 text-xs">(mouvements par tiers)</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
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
            Colonnes <b>Facture / Paye</b> = mouvements sur la periode {filters.startDate || '…'} a {filters.endDate || '…'}.
            Colonne <b>Solde</b> = solde cumulatif du compte tier jusqu&apos;au {filters.endDate || 'jour courant'}.
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
              <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                <div className="text-[11px] text-slate-500">
                  {(() => {
                    const total = ownersData.owners.length;
                    const visible = applyFilters(ownersData.owners).length;
                    const zeros = ownersData.owners.filter(o => o.status === 'solde').length;
                    return hideZeroBalance
                      ? `${visible} affiche(s) sur ${total} - ${zeros} solde(s) a zero masque(s)`
                      : `${visible} proprietaire(s) - dont ${zeros} avec solde a zero`;
                  })()}
                </div>
                <Button
                  variant={hideZeroBalance ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setHideZeroBalance(v => !v)}
                  data-testid="toggle-hide-zero-owners-btn"
                  title={hideZeroBalance ? 'Afficher tous les proprietaires' : 'Masquer les proprietaires avec solde a zero'}
                >
                  {hideZeroBalance ? <Eye size={13} className="mr-1.5" /> : <EyeOff size={13} className="mr-1.5" />}
                  {hideZeroBalance ? 'Afficher les soldes a zero' : 'Masquer les soldes a zero'}
                </Button>
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
                    {applyFilters(ownersData.owners).map((o, idx) => (
                      <TableRow key={o.owner_id || `orphan-${o.account_provisions || o.account_reserve}-${idx}`} className="hover:bg-slate-50/50">
                        <TableCell className="font-medium">
                          {o.owner_name}
                          {o.is_orphan_account ? (
                            <Badge variant="outline" className="ml-2 bg-red-50 border-red-200 text-red-700 text-[10px]" title="Compte tier sans proprietaire rattache. Rattacher le compte a un proprietaire existant ou nettoyer les ecritures orphelines depuis la page Coproprietes.">
                              Orphelin
                            </Badge>
                          ) : o.is_former_owner && (
                            <Badge variant="outline" className="ml-2 bg-amber-50 border-amber-200 text-amber-700 text-[10px]" title="Ancien proprietaire avec solde residuel apres mutation">
                              Ex-prop.
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-[#022D52]">{o.vcs_code}</TableCell>
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
                            {o.owner_id ? (
                              <>
                                <Button variant="ghost" size="sm" onClick={() => viewOwnerDetail(o.owner_id)} data-testid={`view-owner-${o.owner_id}`} title="Detail"><Eye size={14} /></Button>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => {
                                    const c = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
                                    if (!c || c === 'all') { toast.error('Selectionnez une ACP specifique en haut de page'); return; }
                                    const params = new URLSearchParams({ copropriete_id: c });
                                    if (filters.startDate) params.set('start_date', filters.startDate);
                                    if (filters.endDate) params.set('end_date', filters.endDate);
                                    window.open(`${API}/api/reports/situation-compte/${o.owner_id}/pdf?${params.toString()}`, '_blank');
                                  }}
                                  data-testid={`pdf-situation-${o.owner_id}`}
                                  title={(filters.startDate || filters.endDate) ? `Situation de compte PDF (periode ${filters.startDate || '…'} - ${filters.endDate || '…'})` : "Situation de compte PDF (envoi email/postal)"}
                                  className="text-[#022D52] hover:text-[#1D4ED8]"
                                >
                                  <FileText size={14} />
                                </Button>
                              </>
                            ) : (
                              <span className="text-[10px] text-slate-400 italic" title="Compte orphelin : rattacher a un proprietaire ou nettoyer via la page Coproprietes (bouton baguette magique)">
                                Rattacher
                              </span>
                            )}
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
              <Card className="border-orange-200 bg-orange-50 mb-4"><CardContent className="p-4 flex items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <Truck size={20} className="text-orange-600" />
                  <div><div className="text-[10px] uppercase tracking-wider text-orange-600 font-semibold">Total a payer aux fournisseurs</div>
                    <div className="text-xl font-black text-orange-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{suppliersData.total_a_payer.toFixed(2)} EUR</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {!mergeMode ? (
                    <Button size="sm" variant="outline" onClick={() => { setMergeMode(true); setSelectedSupplierIds(new Set()); }} className="border-orange-300 text-orange-700 hover:bg-orange-100" data-testid="enter-merge-mode-btn">
                      <Link2 size={13} className="mr-1.5" /> Fusionner des fournisseurs
                    </Button>
                  ) : (
                    <>
                      <span className="text-xs text-orange-700 font-medium">{selectedSupplierIds.size} selectionne(s)</span>
                      <Button
                        size="sm"
                        onClick={() => {
                          if (selectedSupplierIds.size < 2) { toast.error('Selectionnez au moins 2 fournisseurs'); return; }
                          const selected = suppliersData.suppliers.filter(s => selectedSupplierIds.has(s.supplier_id));
                          const best = selected.find(s => s.vat_number) || selected[0];
                          setMergeKeepId(best?.supplier_id || '');
                          setMergeDialogOpen(true);
                        }}
                        disabled={selectedSupplierIds.size < 2}
                        className="bg-orange-600 hover:bg-orange-700 text-white"
                        data-testid="open-merge-dialog-btn"
                      >Fusionner la selection</Button>
                      <Button size="sm" variant="ghost" onClick={() => { setMergeMode(false); setSelectedSupplierIds(new Set()); }} data-testid="exit-merge-mode-btn">Annuler</Button>
                    </>
                  )}
                </div>
              </CardContent></Card>
              <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                <div className="text-[11px] text-slate-500">
                  {(() => {
                    const total = suppliersData.suppliers.length;
                    const visible = applyFilters(suppliersData.suppliers).length;
                    const zeros = suppliersData.suppliers.filter(s => s.status === 'solde').length;
                    return hideZeroBalance
                      ? `${visible} affiche(s) sur ${total} - ${zeros} solde(s) a zero masque(s)`
                      : `${visible} fournisseur(s) - dont ${zeros} avec solde a zero`;
                  })()}
                </div>
                <Button
                  variant={hideZeroBalance ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setHideZeroBalance(v => !v)}
                  data-testid="toggle-hide-zero-suppliers-btn"
                  title={hideZeroBalance ? 'Afficher tous les fournisseurs' : 'Masquer les fournisseurs avec solde a zero'}
                >
                  {hideZeroBalance ? <Eye size={13} className="mr-1.5" /> : <EyeOff size={13} className="mr-1.5" />}
                  {hideZeroBalance ? 'Afficher les soldes a zero' : 'Masquer les soldes a zero'}
                </Button>
              </div>
              <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
                <Table>
                  <TableHeader><TableRow>
                    {mergeMode && <TableHead className="w-8 px-1"></TableHead>}
                    <TableHead>Fournisseur</TableHead><TableHead>Compte</TableHead><TableHead>N TVA</TableHead>
                    <TableHead className="text-right">Facture</TableHead><TableHead className="text-right">Paye</TableHead>
                    <TableHead className="text-right text-slate-500" title="Debit du compte tier dans le grand livre">D. compte</TableHead>
                    <TableHead className="text-right text-slate-500" title="Credit du compte tier dans le grand livre">C. compte</TableHead>
                    <TableHead className="text-right">Solde</TableHead><TableHead>Statut</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {applyFilters(suppliersData.suppliers).map((s, i) => (
                      <TableRow key={s.supplier_id || `orphan-${i}`} className={`hover:bg-slate-50/50 ${selectedSupplierIds.has(s.supplier_id) ? 'bg-orange-50/50' : ''}`}>
                        {mergeMode && (
                          <TableCell className="px-1">
                            {s.supplier_id && (
                              <input
                                type="checkbox"
                                checked={selectedSupplierIds.has(s.supplier_id)}
                                onChange={() => {
                                  const next = new Set(selectedSupplierIds);
                                  if (next.has(s.supplier_id)) next.delete(s.supplier_id);
                                  else next.add(s.supplier_id);
                                  setSelectedSupplierIds(next);
                                }}
                                data-testid={`merge-select-${s.supplier_id}`}
                              />
                            )}
                          </TableCell>
                        )}
                        <TableCell className="font-medium">
                          {s.supplier_name}
                          {s.orphan && <Badge variant="outline" className="ml-2 text-[10px] bg-amber-50 text-amber-700 border-amber-200">Orphelin</Badge>}
                        </TableCell>
                        <TableCell className="font-mono text-[11px] text-slate-500">{s.tier_account || '—'}</TableCell>
                        <TableCell className="font-mono text-xs">{s.vat_number || '-'}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_invoiced.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_paid.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono text-xs text-slate-500" data-testid={`sup-acc-debit-${s.supplier_id || i}`}>{(s.account_debit ?? s.total_paid ?? 0).toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono text-xs text-slate-500" data-testid={`sup-acc-credit-${s.supplier_id || i}`}>{(s.account_credit ?? s.total_invoiced ?? 0).toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${s.balance > 0 ? 'text-orange-700' : s.balance < 0 ? 'text-green-700' : 'text-slate-500'}`}>{s.balance.toFixed(2)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={s.status === 'crediteur' ? 'bg-orange-50 text-orange-700 border-orange-200' : s.status === 'debiteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50 text-slate-500'}>
                            {s.status === 'crediteur' ? 'A payer' : s.status === 'debiteur' ? 'Trop-paye' : 'Solde'}
                          </Badge>
                        </TableCell>
                        <TableCell>{s.supplier_id ? (
                          <Button variant="ghost" size="sm" onClick={() => viewSupplierDetail(s.supplier_id)} data-testid={`view-supplier-${s.supplier_id}`}><Eye size={14} /></Button>
                        ) : s.orphan ? (
                          <Button variant="ghost" size="sm" onClick={() => openLettrerDialog(s)} className="text-amber-600 hover:text-amber-700 hover:bg-amber-50" title="Lettrer manuellement avec un fournisseur en base" data-testid={`lettrer-orphan-${i}`}><Link2 size={14} /></Button>
                        ) : null}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>

      {/* Dialogs extraits vers /components/balance-tiers/ (iter90bv+iter90by) */}
      <TiersDetailDialog
        open={!!detail}
        onClose={() => setDetail(null)}
        detail={detail}
        detailType={detailType}
        detailGrouped={detailGrouped}
        detailOwnerId={detailOwnerId}
        viewOwnerDetail={viewOwnerDetail}
      />

      <LettrerDialog
        open={lettrerOpen}
        onOpenChange={setLettrerOpen}
        lettrerOrphan={lettrerOrphan}
        lettrerSuppliers={lettrerSuppliers}
        lettrerSearch={lettrerSearch}
        setLettrerSearch={setLettrerSearch}
        lettrerLoading={lettrerLoading}
        commitLettrer={commitLettrer}
      />

      <SupplierMergeDialog
        open={mergeDialogOpen}
        onOpenChange={setMergeDialogOpen}
        suppliersData={suppliersData}
        selectedSupplierIds={selectedSupplierIds}
        mergeKeepId={mergeKeepId}
        setMergeKeepId={setMergeKeepId}
        onSuccess={() => {
          setMergeDialogOpen(false);
          setMergeMode(false);
          setSelectedSupplierIds(new Set());
          load();
        }}
      />
    </div>
  );
}
