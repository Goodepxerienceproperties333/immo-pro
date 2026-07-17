import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { Bell, Download, AlertTriangle, Users, Truck, FileText } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const API = process.env.REACT_APP_BACKEND_URL;

const SEVERITY_STYLE = {
  critique: 'bg-red-600 text-white',
  urgent: 'bg-orange-500 text-white',
  rappel2: 'bg-amber-400 text-slate-900',
  rappel1: 'bg-yellow-200 text-slate-800',
};

const SEVERITY_LABEL = {
  critique: 'Critique (+90j)',
  urgent: 'Urgent (+30j)',
  rappel2: 'Rappel 2 (+7j)',
  rappel1: 'Rappel 1',
};

export default function RemindersPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState('owners');
  const [ownersData, setOwnersData] = useState(null);
  const [suppliersData, setSuppliersData] = useState(null);
  const [graceDays, setGraceDays] = useState(0);
  // iter90hi : par defaut on affiche le trimestre en cours
  const [dateFrom, setDateFrom] = useState(() => {
    const t = new Date();
    const qStart = Math.floor(t.getMonth() / 3) * 3;
    return new Date(t.getFullYear(), qStart, 1).toISOString().slice(0, 10);
  });
  const [dateTo, setDateTo] = useState(() => {
    const t = new Date();
    const qStart = Math.floor(t.getMonth() / 3) * 3;
    return new Date(t.getFullYear(), qStart + 3, 0).toISOString().slice(0, 10);
  });
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { grace_days: graceDays };
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      // iter90hh : charge en parallele les 2 flux (proprietaires + fournisseurs)
      const [own, sup] = await Promise.all([
        api.get('/reminders/late-payments', { params }),
        api.get('/reminders/supplier-late-payments', { params }),
      ]);
      setOwnersData(own.data);
      setSuppliersData(sup.data);
    } catch { toast.error('Erreur de chargement des rappels'); }
    finally { setLoading(false); }
  }, [graceDays, dateFrom, dateTo]);

  useEffect(() => { load(); }, [load]);

  // iter90hg : raccourcis periode
  const setPeriodPreset = (preset) => {
    const today = new Date();
    const y = today.getFullYear();
    const m = today.getMonth();
    const fmt = (d) => d.toISOString().slice(0, 10);
    if (preset === 'this-month') {
      setDateFrom(fmt(new Date(y, m, 1)));
      setDateTo(fmt(new Date(y, m + 1, 0)));
    } else if (preset === 'last-month') {
      setDateFrom(fmt(new Date(y, m - 1, 1)));
      setDateTo(fmt(new Date(y, m, 0)));
    } else if (preset === 'this-quarter') {
      const qStart = Math.floor(m / 3) * 3;
      setDateFrom(fmt(new Date(y, qStart, 1)));
      setDateTo(fmt(new Date(y, qStart + 3, 0)));
    } else if (preset === 'year') {
      setDateFrom(`${y}-01-01`);
      setDateTo(`${y}-12-31`);
    } else if (preset === 'all') {
      setDateFrom('');
      setDateTo('');
    }
  };

  const downloadLetter = (ownerId, coproId) => {
    if (!coproId) { toast.error('Copropriete inconnue'); return; }
    window.open(`${API}/api/reminders/owner/${ownerId}/letter?copropriete_id=${coproId}`, '_blank');
  };

  const openInvoice = (invoiceId) => {
    if (!invoiceId) return;
    navigate(`/invoices?highlight=${invoiceId}`);
  };

  const currentData = tab === 'owners' ? ownersData : suppliersData;

  // iter90hi : detecte quel preset est actif (pour surligner le bouton)
  const activePreset = (() => {
    if (!dateFrom && !dateTo) return 'all';
    const t = new Date();
    const y = t.getFullYear();
    const m = t.getMonth();
    const fmt = (d) => d.toISOString().slice(0, 10);
    if (dateFrom === fmt(new Date(y, m, 1)) && dateTo === fmt(new Date(y, m + 1, 0))) return 'this-month';
    if (dateFrom === fmt(new Date(y, m - 1, 1)) && dateTo === fmt(new Date(y, m, 0))) return 'last-month';
    const qStart = Math.floor(m / 3) * 3;
    if (dateFrom === fmt(new Date(y, qStart, 1)) && dateTo === fmt(new Date(y, qStart + 3, 0))) return 'this-quarter';
    if (dateFrom === `${y}-01-01` && dateTo === `${y}-12-31`) return 'year';
    return null;
  })();

  const presetBtnClass = (key) => activePreset === key
    ? 'bg-[#022D52] text-white hover:bg-[#1D4ED8] border-[#022D52]'
    : '';

  return (
    <div data-testid="reminders-page">
      <div className="page-header flex flex-col gap-3">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <h1 className="page-title"><Bell size={24} className="inline mr-2" />Rappels de paiement</h1>
            <p className="page-subtitle">Retards de paiement des proprietaires et factures fournisseurs impayees</p>
          </div>
          <div className="flex items-end gap-3 flex-wrap">
            <div>
              <label className="form-label">Echeance du</label>
              <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-40" data-testid="reminders-date-from" />
            </div>
            <div>
              <label className="form-label">Au</label>
              <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-40" data-testid="reminders-date-to" />
            </div>
            <div>
              <label className="form-label">Delai de grace (j)</label>
              <Input type="number" min={0} value={graceDays} onChange={e => setGraceDays(Number(e.target.value) || 0)} className="w-24" data-testid="grace-days-input" />
            </div>
            <Button onClick={load} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="reload-reminders-btn">Rafraichir</Button>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-500 uppercase font-semibold">Periode :</span>
          <Button variant="outline" size="sm" onClick={() => setPeriodPreset('this-month')} data-testid="preset-this-month" className={presetBtnClass('this-month')}>Ce mois</Button>
          <Button variant="outline" size="sm" onClick={() => setPeriodPreset('last-month')} data-testid="preset-last-month" className={presetBtnClass('last-month')}>Mois -1</Button>
          <Button variant="outline" size="sm" onClick={() => setPeriodPreset('this-quarter')} data-testid="preset-this-quarter" className={presetBtnClass('this-quarter')}>Trimestre</Button>
          <Button variant="outline" size="sm" onClick={() => setPeriodPreset('year')} data-testid="preset-year" className={presetBtnClass('year')}>Annee</Button>
          <Button variant="outline" size="sm" onClick={() => setPeriodPreset('all')} data-testid="preset-all" className={presetBtnClass('all')}>Tout</Button>
        </div>
      </div>

      {loading && <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#022D52] animate-pulse w-1/2" /></div>}

      {!loading && (
        <Tabs value={tab} onValueChange={setTab} className="w-full" data-testid="reminders-tabs">
          <TabsList className="mb-4">
            <TabsTrigger value="owners" data-testid="tab-owners" className="gap-2">
              <Users size={16} /> Proprietaires
              {ownersData && (
                <Badge variant="outline" className="ml-1 bg-red-50 border-red-200 text-red-700">{ownersData.summary.total_count}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="suppliers" data-testid="tab-suppliers" className="gap-2">
              <Truck size={16} /> Fournisseurs
              {suppliersData && (
                <Badge variant="outline" className="ml-1 bg-orange-50 border-orange-200 text-orange-700">{suppliersData.summary.total_count}</Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {currentData && (
            <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-6">
              <Card className="border-slate-200"><CardContent className="p-4">
                <div className="text-xs text-slate-500 uppercase">
                  {tab === 'owners' ? 'Proprietaires en retard' : 'Factures en retard'}
                </div>
                <div className="text-2xl font-black mt-1" style={{ fontFamily: 'Chivo,sans-serif' }} data-testid="reminders-total-count">{currentData.summary.total_count}</div>
                <div className="text-xs text-slate-500 mt-1 font-mono">{currentData.summary.total_amount.toFixed(2)} EUR</div>
              </CardContent></Card>
              {['critique', 'urgent', 'rappel2', 'rappel1'].map(s => (
                <Card key={s} className="border-slate-200"><CardContent className="p-4">
                  <div className="text-xs text-slate-500 uppercase">{SEVERITY_LABEL[s]}</div>
                  <div className="text-2xl font-black mt-1" style={{ fontFamily: 'Chivo,sans-serif' }}>{currentData.summary.by_severity[s] || 0}</div>
                </CardContent></Card>
              ))}
            </div>
          )}

          <TabsContent value="owners">
            <OwnersTable data={ownersData} onDownloadLetter={downloadLetter} />
          </TabsContent>

          <TabsContent value="suppliers">
            <SuppliersTable data={suppliersData} onOpenInvoice={openInvoice} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function OwnersTable({ data, onDownloadLetter }) {
  if (!data) return null;
  return (
    <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
      <Table>
        <TableHeader><TableRow>
          <TableHead>Severite</TableHead><TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead>
          <TableHead>Appel</TableHead><TableHead>Echeance</TableHead>
          <TableHead className="text-right">Retard</TableHead><TableHead className="text-right">Montant</TableHead>
          <TableHead className="w-32">Actions</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {data.late_payments.length === 0 ? (
            <TableRow><TableCell colSpan={8} className="text-center py-12 text-slate-400">
              <AlertTriangle size={32} className="inline mb-2 text-green-500" /><br />
              Aucun appel en retard sur cette periode
            </TableCell></TableRow>
          ) : data.late_payments.map((r, i) => (
            <TableRow key={i} className="hover:bg-slate-50/50" data-testid={`reminder-row-${i}`}>
              <TableCell><Badge className={SEVERITY_STYLE[r.severity]}>{SEVERITY_LABEL[r.severity]}</Badge></TableCell>
              <TableCell className="font-medium">{r.owner_name}</TableCell>
              <TableCell className="font-mono text-xs text-[#022D52]">{r.vcs_code}</TableCell>
              <TableCell>{r.fund_call_name}</TableCell>
              <TableCell className="font-mono">{fmtDate(r.due_date)}</TableCell>
              <TableCell className="text-right font-mono">{r.days_late} j</TableCell>
              <TableCell className="text-right font-mono font-semibold">{r.amount.toFixed(2)} EUR</TableCell>
              <TableCell>
                <Button variant="outline" size="sm" onClick={() => onDownloadLetter(r.owner_id, r.copropriete_id)} data-testid={`reminder-letter-${i}`}>
                  <Download size={14} className="mr-1" /> Lettre PDF
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function SuppliersTable({ data, onOpenInvoice }) {
  if (!data) return null;
  return (
    <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
      <Table>
        <TableHeader><TableRow>
          <TableHead>Severite</TableHead><TableHead>Fournisseur</TableHead><TableHead>N Facture</TableHead>
          <TableHead>Echeance</TableHead><TableHead className="text-right">Retard</TableHead>
          <TableHead className="text-right">Deja paye</TableHead>
          <TableHead className="text-right">Reste du</TableHead>
          <TableHead className="w-32">Actions</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {data.late_payments.length === 0 ? (
            <TableRow><TableCell colSpan={8} className="text-center py-12 text-slate-400">
              <AlertTriangle size={32} className="inline mb-2 text-green-500" /><br />
              Aucune facture fournisseur en retard sur cette periode
            </TableCell></TableRow>
          ) : data.late_payments.map((r, i) => (
            <TableRow key={i} className="hover:bg-slate-50/50" data-testid={`supplier-reminder-row-${i}`}>
              <TableCell><Badge className={SEVERITY_STYLE[r.severity]}>{SEVERITY_LABEL[r.severity]}</Badge></TableCell>
              <TableCell className="font-medium">{r.supplier_name}</TableCell>
              <TableCell className="font-mono text-xs">{r.invoice_number || '-'}</TableCell>
              <TableCell className="font-mono">{fmtDate(r.due_date)}</TableCell>
              <TableCell className="text-right font-mono">{r.days_late} j</TableCell>
              <TableCell className="text-right font-mono text-slate-500">{r.amount_paid.toFixed(2)} EUR</TableCell>
              <TableCell className="text-right font-mono font-semibold text-red-700">{r.amount.toFixed(2)} EUR</TableCell>
              <TableCell>
                <Button variant="outline" size="sm" onClick={() => onOpenInvoice(r.invoice_id)} data-testid={`supplier-open-invoice-${i}`}>
                  <FileText size={14} className="mr-1" /> Ouvrir
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
