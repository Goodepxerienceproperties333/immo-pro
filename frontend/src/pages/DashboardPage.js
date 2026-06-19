import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { Users, Building2, UserCheck, Receipt, AlertCircle, TrendingUp, Home, ArrowLeft, Landmark, FileText, Megaphone, Sparkles, Loader2 } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

export default function DashboardPage() {
  const { selectedCopro, setSelectedCopro, isAdmin } = useAuth();
  const [coproprietes, setCoproprietes] = useState([]);
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [seeding, setSeeding] = useState(false);

  const reload = useCallback(() => {
    api.get('/coproprietes').then(r => setCoproprietes(r.data)).catch(() => {});
  }, []);

  useEffect(() => { reload(); }, [reload]);

  // Load stats when a copro is selected
  useEffect(() => {
    setLoading(true);
    const params = selectedCopro ? { copropriete_id: selectedCopro } : {};
    api.get('/dashboard/stats', { params }).then(r => { setStats(r.data); setLoading(false); }).catch(() => setLoading(false));
    if (selectedCopro) {
      api.get('/dashboard/health-audit', { params }).then(r => setHealth(r.data)).catch(() => setHealth(null));
    } else {
      setHealth(null);
    }
  }, [selectedCopro]);

  const getDefaultIban = (c) => (c.bank_accounts || []).find(b => b.is_default)?.iban || (c.bank_accounts || [])[0]?.iban || '-';
  const selectedCoproData = coproprietes.find(c => c.id === selectedCopro);

  const handleSeedDemo = async () => {
    if (!window.confirm('Generer une ACP de demonstration avec donnees fictives ?')) return;
    setSeeding(true);
    try {
      const { data } = await api.post('/admin/demo/seed', {});
      toast.success(`ACP demo creee: ${data.name} (${data.counts.owners} prop, ${data.counts.lots} lots, ${data.counts.invoices} factures)`);
      reload();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur seed demo');
    } finally {
      setSeeding(false);
    }
  };

  // ---- ACP TILES VIEW (no copro selected) ----
  if (!selectedCopro) {
    return (
      <div data-testid="dashboard-page">
        <div className="page-header flex items-start justify-between">
          <div>
            <h1 className="page-title">Tableau de bord</h1>
            <p className="page-subtitle">Selectionnez une copropriete pour acceder a sa gestion</p>
          </div>
          {isAdmin && (
            <Button
              onClick={handleSeedDemo}
              disabled={seeding}
              variant="outline"
              size="sm"
              className="border-[#0055FF]/30 text-[#0055FF] hover:bg-[#0055FF]/5"
              data-testid="seed-demo-btn"
            >
              {seeding ? <Loader2 size={14} className="mr-2 animate-spin" /> : <Sparkles size={14} className="mr-2" />}
              Generer ACP de demo
            </Button>
          )}
        </div>

        {/* Global stats */}
        {stats && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
            {[
              { label: 'Coproprietes', value: stats.coproprietes_count || coproprietes.length, icon: Home, color: '#0055FF' },
              { label: 'Proprietaires', value: stats.owners_count || 0, icon: Users, color: '#0284C7' },
              { label: 'Lots', value: stats.lots_count || 0, icon: Building2, color: '#00A650' },
              { label: 'Factures impayees', value: stats.unpaid_invoices || 0, icon: AlertCircle, color: '#DC2626' },
            ].map((kpi, i) => (
              <Card key={i} className="border-slate-200 hover:shadow-sm transition-shadow">
                <CardContent className="p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] uppercase tracking-[0.15em] font-semibold text-slate-500">{kpi.label}</span>
                    <kpi.icon size={16} style={{color: kpi.color}} strokeWidth={1.5} />
                  </div>
                  <div className="text-2xl font-bold text-slate-900 tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>{kpi.value}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* ACP Tiles */}
        <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Vos coproprietes</div>
        {coproprietes.length === 0 ? (
          <Card className="border-slate-200"><CardContent className="p-8 text-center text-slate-400">Aucune copropriete creee. Allez dans Coproprietes pour en creer une.</CardContent></Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {coproprietes.filter(c => c.status !== 'archived').map(c => (
              <Card
                key={c.id}
                className="border-slate-200 hover:border-[#0055FF] hover:shadow-lg cursor-pointer transition-all group"
                onClick={() => setSelectedCopro(c.id)}
                data-testid={`copro-tile-${c.id}`}
              >
                <CardContent className="p-5">
                  <div className="flex items-start justify-between mb-3">
                    <div className="w-10 h-10 rounded-md bg-[#0055FF]/10 flex items-center justify-center group-hover:bg-[#0055FF] transition-colors">
                      <Home size={20} className="text-[#0055FF] group-hover:text-white transition-colors" />
                    </div>
                    {c.reference && <Badge variant="outline" className="font-mono text-[10px]">{c.reference}</Badge>}
                  </div>
                  <h3 className="font-bold text-slate-900 mb-1" style={{fontFamily:'Chivo,sans-serif'}}>{c.name}</h3>
                  {c.city && <p className="text-xs text-slate-500">{c.address ? `${c.address}, ` : ''}{c.postal_code} {c.city}</p>}
                  {c.bce && <p className="text-[10px] text-slate-400 font-mono mt-1">BCE: {c.bce}</p>}
                  <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between">
                    <span className="text-[10px] text-slate-400 font-mono">{getDefaultIban(c)}</span>
                    <span className="text-xs text-[#0055FF] font-medium opacity-0 group-hover:opacity-100 transition-opacity">Ouvrir</span>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ---- PER-ACP DASHBOARD ----
  if (loading) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div>;

  const kpis = [
    { label: 'Proprietaires', value: stats?.owners_count || 0, icon: Users, color: '#0055FF' },
    { label: 'Lots', value: stats?.lots_count || 0, icon: Building2, color: '#0284C7' },
    { label: 'Locataires', value: stats?.tenants_count || 0, icon: UserCheck, color: '#00A650' },
    { label: 'Factures', value: stats?.invoices_count || 0, icon: Receipt, color: '#FF6B00' },
    { label: 'Impayees', value: stats?.unpaid_invoices || 0, icon: AlertCircle, color: '#DC2626' },
    { label: 'Total charges', value: `${(stats?.total_charges || 0).toLocaleString('fr-BE')} EUR`, icon: TrendingUp, color: '#0055FF' },
  ];

  return (
    <div data-testid="dashboard-page">
      <div className="page-header flex items-center gap-4">
        <Button variant="ghost" size="sm" onClick={() => setSelectedCopro('')} className="text-slate-400 hover:text-slate-700" data-testid="back-to-copros">
          <ArrowLeft size={18} />
        </Button>
        <div>
          <h1 className="page-title">{selectedCoproData?.name || 'Copropriete'}</h1>
          <p className="page-subtitle">{selectedCoproData?.reference} - {selectedCoproData?.city || ''} {selectedCoproData?.bce ? `(BCE: ${selectedCoproData.bce})` : ''}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 mb-6">
        {kpis.map((kpi, i) => (
          <Card key={i} className="border-slate-200">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] uppercase tracking-[0.15em] font-semibold text-slate-500">{kpi.label}</span>
                <kpi.icon size={16} style={{color: kpi.color}} strokeWidth={1.5} />
              </div>
              <div className="text-2xl font-bold text-slate-900 tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>{kpi.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* SANTE COMPTABLE */}
      {health && (
        <Card className="border-slate-200 mb-6" data-testid="health-audit-card">
          <CardContent className="p-5">
            <div className="flex items-start justify-between mb-3 gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <h3 className="text-base font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Sante comptable</h3>
                  <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full ${
                    health.score >= 90 ? 'bg-green-100 text-green-700' :
                    health.score >= 75 ? 'bg-blue-100 text-blue-700' :
                    health.score >= 50 ? 'bg-orange-100 text-orange-700' :
                    'bg-red-100 text-red-700'
                  }`} data-testid="health-label">{health.health_label}</span>
                </div>
                <p className="text-xs text-slate-500">Detection automatique d&apos;anomalies sur l&apos;ACP - seuil {health.days_threshold}j</p>
              </div>
              <div className="text-right">
                <div className={`text-3xl font-bold tracking-tight ${
                  health.score >= 90 ? 'text-green-600' :
                  health.score >= 75 ? 'text-blue-600' :
                  health.score >= 50 ? 'text-orange-600' :
                  'text-red-600'
                }`} style={{fontFamily:'Chivo,sans-serif'}} data-testid="health-score">{health.score}<span className="text-base text-slate-400">/100</span></div>
              </div>
            </div>
            {/* Mini bars stats */}
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3">
              {[
                { label: 'Fact > 60j', val: health.stats.invoices_overdue, color: 'text-red-600 bg-red-50 border-red-200' },
                { label: 'Doublons', val: health.stats.duplicates, color: 'text-orange-600 bg-orange-50 border-orange-200' },
                { label: 'Orphelins', val: health.stats.orphans, color: 'text-amber-600 bg-amber-50 border-amber-200' },
                { label: 'Desequilibres', val: health.stats.unbalanced, color: 'text-red-600 bg-red-50 border-red-200' },
                { label: 'Owners retard', val: health.stats.owners_late, color: 'text-red-600 bg-red-50 border-red-200' },
              ].map((s, i) => (
                <div key={i} className={`text-center border rounded-md py-1.5 ${s.val > 0 ? s.color : 'text-slate-400 bg-slate-50 border-slate-200'}`}>
                  <div className="text-lg font-bold leading-none">{s.val}</div>
                  <div className="text-[10px] uppercase tracking-wider mt-0.5">{s.label}</div>
                </div>
              ))}
            </div>
            {health.anomalies.length > 0 && (
              <details className="mt-2">
                <summary className="text-xs font-semibold text-slate-600 cursor-pointer hover:text-slate-900 select-none" data-testid="health-anomalies-toggle">
                  Voir les {health.anomalies.length} anomalie(s) detectee(s)
                </summary>
                <div className="mt-2 space-y-2">
                  {health.anomalies.map((a, i) => (
                    <div key={i} className={`text-xs border-l-4 pl-3 py-1 ${
                      a.severity === 'high' ? 'border-red-500 bg-red-50/30' : 'border-orange-400 bg-orange-50/30'
                    }`}>
                      <div className="font-semibold text-slate-700">{a.title}</div>
                      <div className="text-slate-500">Categorie : <span className="font-mono">{a.category}</span></div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="border-slate-200">
          <CardContent className="p-6">
            <h3 className="text-lg font-semibold text-slate-900 mb-4" style={{fontFamily:'Chivo,sans-serif'}}>Dernieres ecritures</h3>
            {stats?.recent_entries?.length > 0 ? (
              <div className="space-y-3">
                {stats.recent_entries.map((entry, i) => (
                  <div key={i} className="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
                    <div><span className="text-sm font-medium text-slate-700">{entry.description}</span><div className="text-xs text-slate-400">{fmtDate(entry.date)} - {entry.journal_type}</div></div>
                    <span className="text-sm font-semibold text-slate-900">{entry.total_debit?.toFixed(2)} EUR</span>
                  </div>
                ))}
              </div>
            ) : <p className="text-sm text-slate-400">Aucune ecriture recente</p>}
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-6">
            <h3 className="text-lg font-semibold text-slate-900 mb-4" style={{fontFamily:'Chivo,sans-serif'}}>Actions rapides</h3>
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: 'Facture', href: '/invoices', color: 'bg-orange-50 text-orange-700 border-orange-200', icon: Receipt },
                { label: 'Ecriture', href: '/journals', color: 'bg-green-50 text-green-700 border-green-200', icon: FileText },
                { label: 'Extrait', href: '/banking', color: 'bg-blue-50 text-blue-700 border-blue-200', icon: Landmark },
                { label: 'Appel fonds', href: '/fund-calls', color: 'bg-purple-50 text-purple-700 border-purple-200', icon: Megaphone },
              ].map((a, i) => (
                <a key={i} href={a.href} className={`${a.color} rounded-md border px-3 py-3 text-sm font-medium text-center hover:opacity-80 transition-opacity flex items-center justify-center gap-2`}>
                  <a.icon size={14} /> {a.label}
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
