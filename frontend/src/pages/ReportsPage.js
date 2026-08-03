import { useState, useEffect, Fragment } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { BarChart3, Download, FileText, Eye, X } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';
import { useAuth } from '@/contexts/AuthContext';

import { fmtEUR } from '@/lib/format';
const API = process.env.REACT_APP_BACKEND_URL;
const VALID_TABS = ['balance', 'bilan', 'resultat', 'decomptes'];

export default function ReportsPage() {
  const { selectedCopro } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialTab = VALID_TABS.includes(searchParams.get('tab')) ? searchParams.get('tab') : 'balance';
  const [tab, setTab] = useState(initialTab);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [balance, setBalance] = useState(null);
  const [bilan, setBilan] = useState(null);
  const [resultat, setResultat] = useState(null);
  const [decomptes, setDecomptes] = useState(null);
  const [years, setYears] = useState([]);
  const [fiscalYearId, setFiscalYearId] = useState('');
  const [viewMode, setViewMode] = useState('before_distribution');
  // iter90fw : filtre proprietaires actuels vs tous (current | all)
  const [ownerFilter, setOwnerFilter] = useState('current');
  const [loading, setLoading] = useState(false);
  // Preview Decompte state
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  const [previewOwner, setPreviewOwner] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    api.get('/fiscal/years').then(r => setYears(r.data || [])).catch(() => {});
    // iter88b : reset des rapports affiches quand on change d'ACP (chinese wall)
    // pour eviter de laisser visible le bilan/balance d'une autre copropriete.
    setBalance(null); setBilan(null); setResultat(null); setDecomptes(null);
  }, [selectedCopro]);

  // Auto-remplit dateFrom/dateTo quand un exercice fiscal est selectionne.
  // Simplifie la saisie : plus besoin d'ouvrir 2 date pickers pour un exercice
  // complet. L'utilisateur peut toujours affiner manuellement apres.
  useEffect(() => {
    if (!fiscalYearId) return;
    const fy = years.find(y => y.id === fiscalYearId);
    if (!fy) return;
    if (fy.start_date) setDateFrom(fy.start_date);
    if (fy.end_date) setDateTo(fy.end_date);
  }, [fiscalYearId, years]);

  const loadBalance = async () => { setLoading(true); try { const params = {}; if (dateFrom) params.date_from = dateFrom; if (dateTo) params.date_to = dateTo; const { data } = await api.get('/reports/balance', { params }); setBalance(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };
  const loadBilan = async () => {
    setLoading(true);
    try {
      const params = { view_mode: viewMode };
      if (dateTo) params.date_to = dateTo;
      if (fiscalYearId) params.fiscal_year_id = fiscalYearId;
      const { data } = await api.get('/reports/bilan', { params });
      setBilan(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur de chargement du bilan');
    } finally {
      setLoading(false);
    }
  };
  const downloadBilanPdf = async () => {
    try {
      const params = { view_mode: viewMode };
      if (dateTo) params.date_to = dateTo;
      if (fiscalYearId) params.fiscal_year_id = fiscalYearId;
      const res = await api.get('/reports/bilan/pdf', { params, responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `bilan_${fiscalYearId || (dateTo || 'date')}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      try {
        const text = await err.response?.data?.text?.();
        const msg = text ? JSON.parse(text).detail : (err.response?.data?.detail || 'Erreur de generation du PDF');
        toast.error(msg);
      } catch {
        toast.error('Erreur de generation du PDF');
      }
    }
  };
  const loadResultat = async () => { setLoading(true); try { const params = {}; if (dateFrom) params.date_from = dateFrom; if (dateTo) params.date_to = dateTo; if (fiscalYearId) params.fiscal_year_id = fiscalYearId; const { data } = await api.get('/reports/resultat', { params }); setResultat(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };
  // iter90fw : auto-reload decomptes quand le filtre owner change
  // (si un chargement precedent existe deja - sinon on attend le clic
  // sur "Generer decomptes").
  useEffect(() => {
    if (decomptes) {
      loadDecomptes();
    }
  }, [ownerFilter]);

  const loadDecomptes = async () => {
    setLoading(true);
    try {
      const params = { owner_filter: ownerFilter };
      if (fiscalYearId) params.fiscal_year_id = fiscalYearId;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await api.get('/reports/decompte', { params });
      setDecomptes(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally {
      setLoading(false);
    }
  };

  const downloadPdf = async (ownerId) => {
    try {
      const params = {};
      if (fiscalYearId) params.fiscal_year_id = fiscalYearId;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const res = await api.get(`/reports/decompte/pdf/${ownerId}`, { params, responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `decompte_${ownerId}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      // Backend returns 400 JSON if fiscal year not closed - need to read blob as text
      try {
        const text = await err.response?.data?.text?.();
        const msg = text ? JSON.parse(text).detail : (err.response?.data?.detail || 'Erreur de generation du PDF');
        toast.error(msg);
      } catch {
        toast.error(err.response?.data?.detail || 'Erreur de generation du PDF');
      }
    }
  };

  const openPreview = async (ownerId, ownerName) => {
    setPreviewLoading(true);
    setPreviewOwner({ id: ownerId, name: ownerName });
    setPreviewOpen(true);
    try {
      const params = { preview: 'true' };
      if (fiscalYearId) params.fiscal_year_id = fiscalYearId;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const res = await api.get(`/reports/decompte/pdf/${ownerId}`, { params, responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'application/pdf' }));
      setPreviewUrl(url);
    } catch (err) {
      try {
        const text = await err.response?.data?.text?.();
        const msg = text ? JSON.parse(text).detail : (err.response?.data?.detail || 'Erreur de previsualisation');
        toast.error(msg);
      } catch {
        toast.error('Erreur de previsualisation du decompte');
      }
      setPreviewOpen(false);
    } finally {
      setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    if (previewUrl) window.URL.revokeObjectURL(previewUrl);
    setPreviewUrl('');
    setPreviewOwner(null);
    setPreviewOpen(false);
  };

  const downloadFromPreview = () => {
    if (!previewUrl || !previewOwner) return;
    const a = document.createElement('a');
    a.href = previewUrl;
    a.download = `decompte_apercu_${previewOwner.name.replace(/\s+/g, '_')}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };
  // iter88b : scope ACP via useAuth (chinese wall reactif) au lieu de localStorage
  const copro = selectedCopro || '';
  const xlsxParam = copro ? `?copropriete_id=${copro}` : '';
  const exportBilanXlsx = () => window.open(`${API}/api/exports/bilan.xlsx${xlsxParam}${dateTo ? (xlsxParam ? '&' : '?') + 'date_to=' + dateTo : ''}`, '_blank');
  const exportBalanceTiersXlsx = () => window.open(`${API}/api/exports/balance-tiers/owners.xlsx${xlsxParam}`, '_blank');
  const exportGrandLivreXlsx = () => {
    const params = new URLSearchParams();
    if (copro) params.set('copropriete_id', copro);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    const qs = params.toString();
    window.open(`${API}/api/exports/grand-livre.xlsx${qs ? '?' + qs : ''}`, '_blank');
  };

  const DateFilters = ({ onLoad, label }) => (
    <Card className="border-slate-200 mb-6"><CardContent className="p-4"><div className="flex flex-wrap gap-4 items-end">
      <div><label className="form-label">Du</label><Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-40" /></div>
      <div><label className="form-label">Au</label><Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-40" /></div>
      <Button onClick={onLoad} className="bg-[#022D52] hover:bg-[#1D4ED8]" disabled={loading}><BarChart3 size={16} className="mr-2" />{label}</Button>
    </div></CardContent></Card>
  );

  return (
    <div data-testid="reports-page">
      <div className="page-header"><h1 className="page-title"><BarChart3 size={24} className="inline mr-2" />Rapports Financiers</h1><p className="page-subtitle">Bilan, compte de resultats, balance et decomptes</p></div>
      <Tabs value={tab} onValueChange={(v) => { setTab(v); setSearchParams(v === 'balance' ? {} : { tab: v }, { replace: true }); }}>
        <TabsList className="mb-4"><TabsTrigger value="balance">Balance</TabsTrigger><TabsTrigger value="bilan">Bilan</TabsTrigger><TabsTrigger value="resultat">Resultat</TabsTrigger><TabsTrigger value="decomptes">Decomptes</TabsTrigger></TabsList>

        <TabsContent value="balance" className="mt-0">
          <DateFilters onLoad={loadBalance} label="Charger balance" />
          {balance && (<>
            <div className="flex justify-end mb-2"><Button onClick={exportGrandLivreXlsx} variant="outline" size="sm" data-testid="export-balance-xlsx"><Download size={14} className="mr-1" /> Export Grand Livre Excel</Button></div>
            <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table><TableHeader><TableRow><TableHead>Compte</TableHead><TableHead>Libelle</TableHead><TableHead className="text-right">Total Debit</TableHead><TableHead className="text-right">Total Credit</TableHead><TableHead className="text-right">Solde Debit</TableHead><TableHead className="text-right">Solde Credit</TableHead></TableRow></TableHeader>
              <TableBody>
                {balance.accounts.map((a, i) => (<TableRow key={i} className="hover:bg-slate-50/50"><TableCell className="font-mono text-sm">{a.account_number}</TableCell><TableCell className="text-sm">{a.account_name}</TableCell><TableCell className="text-right font-mono">{fmtEUR(a.total_debit)}</TableCell><TableCell className="text-right font-mono">{fmtEUR(a.total_credit)}</TableCell><TableCell className="text-right font-mono">{a.solde_debit > 0 ? fmtEUR(a.solde_debit) : ''}</TableCell><TableCell className="text-right font-mono">{a.solde_credit > 0 ? fmtEUR(a.solde_credit) : ''}</TableCell></TableRow>))}
                <TableRow className="bg-slate-50 font-bold"><TableCell colSpan={2}>TOTAUX</TableCell><TableCell className="text-right font-mono">{fmtEUR(balance.totals.total_debit)}</TableCell><TableCell className="text-right font-mono">{fmtEUR(balance.totals.total_credit)}</TableCell><TableCell className="text-right font-mono">{fmtEUR(balance.totals.solde_debit)}</TableCell><TableCell className="text-right font-mono">{fmtEUR(balance.totals.solde_credit)}</TableCell></TableRow>
              </TableBody>
            </Table>
          </div></>)}
        </TabsContent>

        <TabsContent value="bilan" className="mt-0">
          {/* Simplification : le bilan est un arrete a date, donc un seul
              champ "Arrete au" (pas de "Du"). L'exercice fiscal auto-remplit
              la date (fin d'exercice). Modifiable pour un arrete intermediaire. */}
          <Card className="border-slate-200 mb-4"><CardContent className="p-4">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Exercice fiscal</label>
                <select
                  className="border border-slate-200 rounded-md px-3 py-2 text-sm bg-white min-w-[220px]"
                  value={fiscalYearId}
                  onChange={e => setFiscalYearId(e.target.value)}
                  data-testid="bilan-fiscal-year-select"
                >
                  <option value="">— Situation a date libre —</option>
                  {years.map(y => <option key={y.id} value={y.id}>{y.name} ({y.status === 'closed' ? 'cloture' : 'ouvert'})</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Arrete au</label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={e => setDateTo(e.target.value)}
                  className="w-40"
                  data-testid="bilan-date-to"
                />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Vue du bilan</label>
                <select
                  className="border border-slate-200 rounded-md px-3 py-2 text-sm bg-white min-w-[260px]"
                  value={viewMode}
                  onChange={e => setViewMode(e.target.value)}
                  data-testid="bilan-view-mode"
                >
                  <option value="before_distribution">Avant repartition (compte 499 visible)</option>
                  <option value="after_distribution">Apres repartition (499 reparti aux proprietaires)</option>
                </select>
              </div>
              <Button
                onClick={loadBilan}
                className="bg-[#022D52] hover:bg-[#1D4ED8]"
                disabled={loading}
                data-testid="load-bilan-btn"
              >
                <BarChart3 size={16} className="mr-2" />Charger le bilan
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={downloadBilanPdf}
                data-testid="download-bilan-pdf"
              >
                <FileText size={14} className="mr-1" /> PDF Bilan
              </Button>
            </div>
            {(() => {
              const fy = years.find(y => y.id === fiscalYearId);
              if (!fy) return null;
              return (
                <div className="mt-3 text-[11px] text-slate-500" data-testid="bilan-period-info">
                  Exercice <span className="font-semibold text-slate-700">{fy.name}</span> :
                  {' '}du {fmtDate(fy.start_date)} au {fmtDate(fy.end_date)}
                  {dateTo && dateTo !== fy.end_date && (
                    <span className="ml-2 italic text-amber-600">
                      (arrete intermediaire au {fmtDate(dateTo)})
                    </span>
                  )}
                </div>
              );
            })()}
          </CardContent></Card>
          {bilan && (<>
            <div className="flex justify-between items-center mb-3">
              <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${bilan.equilibre ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                {bilan.equilibre ? '● Bilan equilibre' : `● Ecart : ${fmtEUR(bilan.ecart)} EUR`}
              </span>
              <Button onClick={exportBilanXlsx} variant="ghost" size="sm" data-testid="export-bilan-xlsx" className="text-xs"><Download size={12} className="mr-1" /> Excel</Button>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card className="border-slate-200 shadow-sm"><CardHeader className="bg-blue-50/70 rounded-t-md py-2 px-4"><CardTitle className="text-sm font-semibold text-blue-900" style={{fontFamily:'Chivo,sans-serif'}}>ACTIF</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>
                {bilan.actif.filter(r => r.total > 0.01).map((r, i) => (
                  <Fragment key={`a-${i}`}>
                    <TableRow className="bg-slate-50/60 border-b border-slate-100"><TableCell colSpan={2} className="font-semibold text-[11px] uppercase tracking-wide text-slate-600 py-1.5">{r.label}</TableCell><TableCell className="text-right font-mono text-sm font-semibold py-1.5 text-slate-900">{fmtEUR(r.total)}</TableCell></TableRow>
                    {(r.accounts || []).map((a, j) => (<TableRow key={`a-${i}-${j}`} className="hover:bg-slate-50/40 border-b border-slate-50"><TableCell className="font-mono text-[11px] text-slate-400 pl-6 py-1 w-24">{a.account_number}</TableCell><TableCell className="text-sm py-1">{a.account_name}</TableCell><TableCell className="text-right font-mono text-sm py-1 text-slate-700">{fmtEUR(a.amount)}</TableCell></TableRow>))}
                  </Fragment>
                ))}
                <TableRow className="bg-blue-100/70 font-bold border-t-2 border-blue-300"><TableCell colSpan={2} className="text-sm">TOTAL ACTIF</TableCell><TableCell className="text-right font-mono text-sm">{fmtEUR(bilan.total_actif)} EUR</TableCell></TableRow>
              </TableBody></Table>
            </CardContent></Card>
            <Card className="border-slate-200 shadow-sm"><CardHeader className="bg-green-50/70 rounded-t-md py-2 px-4"><CardTitle className="text-sm font-semibold text-green-900" style={{fontFamily:'Chivo,sans-serif'}}>PASSIF</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>
                {bilan.passif.filter(r => r.total > 0.01).map((r, i) => (
                  <Fragment key={`p-${i}`}>
                    <TableRow className="bg-slate-50/60 border-b border-slate-100"><TableCell colSpan={2} className="font-semibold text-[11px] uppercase tracking-wide text-slate-600 py-1.5">{r.label}</TableCell><TableCell className="text-right font-mono text-sm font-semibold py-1.5 text-slate-900">{fmtEUR(r.total)}</TableCell></TableRow>
                    {(r.accounts || []).map((a, j) => (<TableRow key={`p-${i}-${j}`} className="hover:bg-slate-50/40 border-b border-slate-50"><TableCell className="font-mono text-[11px] text-slate-400 pl-6 py-1 w-24">{a.account_number}</TableCell><TableCell className="text-sm py-1">{a.account_name}</TableCell><TableCell className="text-right font-mono text-sm py-1 text-slate-700">{fmtEUR(a.amount)}</TableCell></TableRow>))}
                  </Fragment>
                ))}
                <TableRow className="bg-green-100/70 font-bold border-t-2 border-green-300"><TableCell colSpan={2} className="text-sm">TOTAL PASSIF</TableCell><TableCell className="text-right font-mono text-sm">{fmtEUR(bilan.total_passif)} EUR</TableCell></TableRow>
              </TableBody></Table>
            </CardContent></Card>
          </div></>)}
        </TabsContent>

        <TabsContent value="resultat" className="mt-0">
          {/* Selecteur exercice + dates modifiables (aligne avec le Bilan).
              L'utilisateur choisit un exercice fiscal (auto-remplit Du/Au),
              puis peut affiner manuellement pour un arrete intermediaire. */}
          <Card className="border-slate-200 mb-4"><CardContent className="p-4">
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Exercice fiscal</label>
                <select
                  className="border border-slate-200 rounded-md px-3 py-2 text-sm bg-white min-w-[220px]"
                  value={fiscalYearId}
                  onChange={e => setFiscalYearId(e.target.value)}
                  data-testid="resultat-fiscal-year-select"
                >
                  <option value="">— Periode libre —</option>
                  {years.map(y => <option key={y.id} value={y.id}>{y.name} ({y.status === 'closed' ? 'cloture' : 'ouvert'})</option>)}
                </select>
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Du</label>
                <Input
                  type="date"
                  value={dateFrom}
                  onChange={e => setDateFrom(e.target.value)}
                  className="w-40"
                  data-testid="resultat-date-from"
                />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Au</label>
                <Input
                  type="date"
                  value={dateTo}
                  onChange={e => setDateTo(e.target.value)}
                  className="w-40"
                  data-testid="resultat-date-to"
                />
              </div>
              <Button
                onClick={loadResultat}
                className="bg-[#022D52] hover:bg-[#1D4ED8]"
                disabled={loading}
                data-testid="load-resultat-btn"
              >
                <BarChart3 size={16} className="mr-2" />Charger resultat
              </Button>
              {(dateFrom || dateTo || fiscalYearId) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setFiscalYearId(''); setDateFrom(''); setDateTo(''); }}
                  data-testid="resultat-reset-filters"
                  className="text-slate-500"
                >
                  <X size={14} className="mr-1" /> Reset
                </Button>
              )}
            </div>
            {(() => {
              const fy = years.find(y => y.id === fiscalYearId);
              if (!fy) return null;
              const overridden = (dateFrom && dateFrom !== fy.start_date) || (dateTo && dateTo !== fy.end_date);
              return (
                <div className="mt-3 text-[11px] text-slate-500" data-testid="resultat-period-info">
                  Exercice <span className="font-semibold text-slate-700">{fy.name}</span> :
                  {' '}du {fmtDate(fy.start_date)} au {fmtDate(fy.end_date)}
                  {overridden && (
                    <span className="ml-2 italic text-amber-600">
                      (periode affinee : {fmtDate(dateFrom || fy.start_date)} → {fmtDate(dateTo || fy.end_date)})
                    </span>
                  )}
                </div>
              );
            })()}
          </CardContent></Card>
          {resultat && (<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card className="border-slate-200"><CardHeader className="bg-red-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>CHARGES (Classe 6)</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>
                {resultat.charges.map((r, i) => (
                  <Fragment key={`c-${i}`}>
                    <TableRow className="bg-slate-50/70"><TableCell colSpan={2} className="font-semibold text-xs uppercase text-slate-700">{r.label}</TableCell><TableCell className="text-right font-mono font-semibold">{fmtEUR(r.total)}</TableCell></TableRow>
                    {(r.accounts || []).map((a, j) => (<TableRow key={`c-${i}-${j}`}><TableCell className="font-mono text-sm pl-6">{a.account_number}</TableCell><TableCell className="text-sm">{a.account_name}</TableCell><TableCell className="text-right font-mono text-sm">{fmtEUR(a.amount)}</TableCell></TableRow>))}
                  </Fragment>
                ))}
                <TableRow className="bg-red-50 font-bold border-t-2 border-red-200"><TableCell colSpan={2}>TOTAL CHARGES</TableCell><TableCell className="text-right font-mono">{fmtEUR(resultat.total_charges)} EUR</TableCell></TableRow>
              </TableBody></Table>
            </CardContent></Card>
            <Card className="border-slate-200"><CardHeader className="bg-green-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>PRODUITS (Classe 7)</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>
                {resultat.produits.map((r, i) => (
                  <Fragment key={`pr-${i}`}>
                    <TableRow className="bg-slate-50/70"><TableCell colSpan={2} className="font-semibold text-xs uppercase text-slate-700">{r.label}</TableCell><TableCell className="text-right font-mono font-semibold">{fmtEUR(r.total)}</TableCell></TableRow>
                    {(r.accounts || []).map((a, j) => (<TableRow key={`pr-${i}-${j}`}><TableCell className="font-mono text-sm pl-6">{a.account_number}</TableCell><TableCell className="text-sm">{a.account_name}</TableCell><TableCell className="text-right font-mono text-sm">{fmtEUR(a.amount)}</TableCell></TableRow>))}
                  </Fragment>
                ))}
                <TableRow className="bg-green-50 font-bold border-t-2 border-green-200"><TableCell colSpan={2}>TOTAL PRODUITS</TableCell><TableCell className="text-right font-mono">{fmtEUR(resultat.total_produits)} EUR</TableCell></TableRow>
              </TableBody></Table>
            </CardContent></Card>
            <Card className={`border-2 col-span-full ${resultat.resultat >= 0 ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}><CardContent className="p-6 text-center">
              <div className="text-sm text-slate-600 mb-1">Resultat de l'exercice ({resultat.resultat_label})</div>
              <div className={`text-3xl font-black tracking-tight ${resultat.resultat >= 0 ? 'text-green-700' : 'text-red-700'}`} style={{fontFamily:'Chivo,sans-serif'}}>{fmtEUR(resultat.resultat)} EUR</div>
            </CardContent></Card>
          </div>)}
        </TabsContent>

        <TabsContent value="decomptes" className="mt-0">
          <DateFilters onLoad={loadDecomptes} label="Generer decomptes" />
          <div className="flex flex-wrap items-end gap-3 mb-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Exercice fiscal</label>
              <select
                className="border border-slate-200 rounded-md px-3 py-2 text-sm bg-white min-w-[260px]"
                value={fiscalYearId}
                onChange={e => {
                  const v = e.target.value;
                  setFiscalYearId(v);
                  // Auto-fill date range from selected FY for consistency
                  if (v) {
                    const fy = years.find(y => y.id === v);
                    if (fy) {
                      setDateFrom(fy.start_date || '');
                      setDateTo(fy.end_date || '');
                    }
                  }
                }}
                data-testid="decompte-fiscal-year-select"
              >
                <option value="">— Periode libre (dates ci-dessus) —</option>
                {years.map(y => (
                  <option key={y.id} value={y.id}>
                    {y.name} ({y.status === 'closed' ? 'cloture' : 'ouvert'}) - {fmtDate(y.start_date)} au {fmtDate(y.end_date)}
                  </option>
                ))}
              </select>
              {fiscalYearId && years.find(y => y.id === fiscalYearId)?.status !== 'closed' && (
                <p className="text-[11px] text-amber-600 mt-1">
                  Exercice non cloture - utilisez le bouton <b>Apercu</b> (filigrane) pour visualiser le decompte provisoire.
                </p>
              )}
            </div>
            {/* iter90fw : dropdown filtre Proprietaires actuels / Tous */}
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Perimetre</label>
              <Select value={ownerFilter} onValueChange={setOwnerFilter}>
                <SelectTrigger className="min-w-[220px]" data-testid="owner-filter-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="current" data-testid="owner-filter-current">Proprietaires actuels</SelectItem>
                  <SelectItem value="all" data-testid="owner-filter-all">Tous les proprietaires</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[10px] text-slate-400 mt-1">
                {ownerFilter === 'current'
                  ? 'Proprietaires ayant un lot au 1er jour de l exercice'
                  : 'Inclut aussi les anciens proprietaires avec solde'}
              </p>
            </div>
          </div>
          <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-md text-sm text-amber-800 flex items-start gap-2" data-testid="decompte-warning">
            <FileText size={16} className="mt-0.5 shrink-0" />
            <div>
              <b>Important :</b> le decompte annuel definitif (sans filigrane) n'est genere qu'apres <b>cloture de l'exercice</b>.
              Avant cloture, utilisez le bouton <b>Apercu</b>. Pour un releve a date, utilisez la <i>Situation de compte</i>.
            </div>
          </div>
          {decomptes && (<div className="space-y-4">
            <div className="text-sm text-slate-500 mb-2">Periode: {decomptes.period.from} au {decomptes.period.to} - {decomptes.decomptes.length} proprietaires</div>
            {/* iter90fv : vue tableau compacte (1 ligne par proprietaire) au
                lieu de l'ancien affichage carte + detail des charges. Le
                detail complet est desormais accessible via l'oeil (apercu
                PDF) ou le bouton PDF (telechargement direct). */}
            <div className="border border-slate-200 rounded-md overflow-hidden bg-white" data-testid="decomptes-table">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50 hover:bg-slate-50">
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Proprietaire</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Reference</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Lots</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Quote-part</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Solde compte tiers</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right w-[140px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {decomptes.decomptes.map(d => {
                    const bal = typeof d.tier_balance === 'number' ? d.tier_balance : 0;
                    const balColor = bal > 0.01 ? 'text-red-600' : (bal < -0.01 ? 'text-emerald-600' : 'text-slate-500');
                    const balLabel = bal > 0.01 ? 'Debiteur' : (bal < -0.01 ? 'Crediteur' : 'Solde');
                    return (
                      <TableRow key={d.owner_id} data-testid={`decompte-row-${d.owner_id}`}>
                        <TableCell className="font-medium text-slate-900">{d.owner_name}</TableCell>
                        <TableCell className="font-mono text-xs text-[#022D52]">{d.vcs_code || '-'}</TableCell>
                        <TableCell className="text-xs text-slate-600">{d.lots.map(l => l.number).join(', ') || '-'}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{d.share_pct}%</TableCell>
                        <TableCell className={`text-right font-mono font-semibold ${balColor}`} data-testid={`tier-balance-${d.owner_id}`}>
                          {fmtEUR(Math.abs(bal))} EUR
                          <div className="text-[10px] uppercase tracking-wider font-normal opacity-70">{balLabel}</div>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => openPreview(d.owner_id, d.owner_name)}
                              className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10 h-8 px-2"
                              data-testid={`preview-decompte-${d.owner_id}`}
                              title="Apercu du decompte detaille (filigrane)"
                            >
                              <Eye size={14} />
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => downloadPdf(d.owner_id)}
                              className="h-8 px-2"
                              data-testid={`download-pdf-${d.owner_id}`}
                              title="Telecharger le PDF"
                            >
                              <Download size={14} />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {decomptes.decomptes.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-slate-500 text-sm py-6">
                        Aucun proprietaire sur cette periode
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </div>)}
        </TabsContent>
      </Tabs>

      {/* Preview Decompte Dialog */}
      <Dialog open={previewOpen} onOpenChange={(o) => { if (!o) closePreview(); }}>
        <DialogContent className="max-w-5xl w-[95vw] h-[90vh] p-0 overflow-hidden flex flex-col" data-testid="preview-decompte-dialog">
          <DialogHeader className="px-6 py-3 border-b border-slate-200 bg-gradient-to-r from-[#022D52] to-[#1D4ED8] text-white shrink-0">
            <DialogTitle className="flex items-center justify-between text-white" style={{fontFamily:'Chivo,sans-serif'}}>
              <div className="flex items-center gap-2">
                <Eye size={18} />
                <span>Apercu du decompte annuel</span>
                {previewOwner && <span className="font-mono text-sm opacity-90 ml-2">- {previewOwner.name}</span>}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={downloadFromPreview}
                  disabled={!previewUrl || previewLoading}
                  className="bg-white text-[#022D52] hover:bg-slate-100"
                  data-testid="preview-download-btn"
                >
                  <Download size={14} className="mr-1" /> Telecharger
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={closePreview}
                  className="text-white hover:bg-white/10"
                  data-testid="preview-close-btn"
                >
                  <X size={16} />
                </Button>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-hidden bg-slate-100">
            {previewLoading ? (
              <div className="flex items-center justify-center h-full text-slate-500">
                <div className="text-center">
                  <div className="animate-spin h-10 w-10 border-4 border-[#022D52] border-t-transparent rounded-full mx-auto mb-3" />
                  Generation de l&apos;apercu...
                </div>
              </div>
            ) : previewUrl ? (
              <iframe
                src={previewUrl}
                title="Apercu decompte"
                className="w-full h-full border-0"
                data-testid="preview-iframe"
              />
            ) : (
              <div className="flex items-center justify-center h-full text-slate-400">Aucun apercu disponible</div>
            )}
          </div>
          <div className="px-6 py-2 border-t border-slate-200 bg-amber-50 text-amber-800 text-xs shrink-0">
            <b>Mode apercu</b> - ce decompte porte un filigrane &quot;APERCU - NON DEFINITIF&quot;.
            Pour generer la version definitive (sans filigrane), cloturez d&apos;abord l&apos;exercice fiscal puis cliquez sur &quot;PDF&quot;.
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
