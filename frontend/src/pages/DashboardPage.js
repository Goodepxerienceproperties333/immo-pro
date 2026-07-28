import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { Users, Building2, UserCheck, Receipt, AlertCircle, TrendingUp, Home, ArrowLeft, Landmark, FileText, Megaphone, Sparkles, Loader2, Scale, ArrowLeftRight, FileBarChart, Truck } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';
import AdaptiveQuickActions from '@/components/AdaptiveQuickActions';
import SyndicOwnersGlobalTab from '@/components/SyndicOwnersGlobalTab';
import SyndicSuppliersGlobalTab from '@/components/SyndicSuppliersGlobalTab';

export default function DashboardPage() {
  const { selectedCopro, setSelectedCopro, isSuperadmin, user } = useAuth();
  const [coproprietes, setCoproprietes] = useState([]);
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState('');
  const [loading, setLoading] = useState(true);

  const loadHealth = useCallback(() => {
    if (!selectedCopro) { setHealth(null); setHealthError(''); return; }
    setHealth(null); setHealthError('');
    api.get('/dashboard/health-audit', { params: { copropriete_id: selectedCopro } })
      .then(r => setHealth(r.data))
      .catch(err => {
        setHealth(null);
        setHealthError(err.response?.data?.detail || err.message || 'Erreur de chargement de la sante');
        console.error('[Sante comptable] error:', err);
      });
  }, [selectedCopro]);
  const [seeding, setSeeding] = useState(false);

  const reload = useCallback(() => {
    api.get('/coproprietes?include_archived=true').then(r => setCoproprietes(r.data)).catch(() => {});
  }, []);

  useEffect(() => { reload(); }, [reload]);

  // Load stats when a copro is selected
  useEffect(() => {
    setLoading(true);
    const params = selectedCopro ? { copropriete_id: selectedCopro } : {};
    api.get('/dashboard/stats', { params }).then(r => { setStats(r.data); setLoading(false); }).catch(() => setLoading(false));
    loadHealth();
  }, [selectedCopro, loadHealth]);

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
          {isSuperadmin && (
            <Button
              onClick={handleSeedDemo}
              disabled={seeding}
              variant="outline"
              size="sm"
              className="border-[#022D52]/30 text-[#022D52] hover:bg-[#022D52]/5"
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
              { label: 'Coproprietes', value: stats.coproprietes_count || coproprietes.length, icon: Home, color: '#022D52' },
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

        {/* iter93b : onglets centralises Syndic - ACPs / Proprietaires / Fournisseurs */}
        <Tabs defaultValue="acps" className="w-full">
          <TabsList className="mb-4" data-testid="syndic-dashboard-tabs">
            <TabsTrigger value="acps" data-testid="tab-acps"><Home size={13} className="mr-1" /> Coproprietes</TabsTrigger>
            <TabsTrigger value="owners" data-testid="tab-owners"><Users size={13} className="mr-1" /> Tous mes proprietaires</TabsTrigger>
            <TabsTrigger value="suppliers" data-testid="tab-suppliers"><Truck size={13} className="mr-1" /> Tous mes fournisseurs</TabsTrigger>
          </TabsList>

          <TabsContent value="acps" data-testid="tab-content-acps">
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Vos coproprietes</div>
            {coproprietes.length === 0 ? (
              <Card className="border-slate-200"><CardContent className="p-8 text-center text-slate-400">Aucune copropriete creee. Allez dans Coproprietes pour en creer une.</CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {coproprietes.map(c => {
                  const isArchived = c.status === 'archived';
                  return (
                  <Card
                    key={c.id}
                    className={`border-slate-200 hover:border-[#022D52] hover:shadow-lg cursor-pointer transition-all group ${isArchived ? 'bg-slate-50 opacity-80' : ''}`}
                    onClick={() => setSelectedCopro(c.id)}
                    data-testid={`copro-tile-${c.id}`}
                  >
                    <CardContent className="p-5">
                      <div className="flex items-start justify-between mb-3">
                        <div className={`w-10 h-10 rounded-md flex items-center justify-center transition-colors ${isArchived ? 'bg-slate-200' : 'bg-[#022D52]/10 group-hover:bg-[#022D52]'}`}>
                          <Home size={20} className={isArchived ? 'text-slate-500' : 'text-[#022D52] group-hover:text-white transition-colors'} />
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          {c.reference && <Badge variant="outline" className="font-mono text-[10px]">{c.reference}</Badge>}
                          {isArchived && <Badge variant="outline" className="text-[9px] bg-amber-50 border-amber-300 text-amber-800">ARCHIVEE</Badge>}
                        </div>
                      </div>
                      <h3 className="font-bold text-slate-900 mb-1" style={{fontFamily:'Chivo,sans-serif'}}>{c.name}</h3>
                      {c.city && <p className="text-xs text-slate-500">{c.address ? `${c.address}, ` : ''}{c.postal_code} {c.city}</p>}
                      {c.bce && <p className="text-[10px] text-slate-400 font-mono mt-1">BCE: {c.bce}</p>}
                      <div className="mt-3 pt-3 border-t border-slate-100 flex items-center justify-between">
                        <span className="text-[10px] text-slate-400 font-mono">{getDefaultIban(c)}</span>
                        <span className="text-xs text-[#022D52] font-medium opacity-0 group-hover:opacity-100 transition-opacity">Ouvrir</span>
                      </div>
                    </CardContent>
                  </Card>
                  );
                })}
              </div>
            )}
          </TabsContent>

          <TabsContent value="owners" data-testid="tab-content-owners">
            <SyndicOwnersGlobalTab coproprietes={coproprietes} />
          </TabsContent>

          <TabsContent value="suppliers" data-testid="tab-content-suppliers">
            <SyndicSuppliersGlobalTab coproprietes={coproprietes} />
          </TabsContent>
        </Tabs>
      </div>
    );
  }

  // ---- PER-ACP DASHBOARD ----
  if (loading) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#022D52] animate-pulse w-1/2" /></div>;

  const kpis = [
    { label: 'Proprietaires', value: stats?.owners_count || 0, icon: Users, color: '#022D52' },
    { label: 'Lots', value: stats?.lots_count || 0, icon: Building2, color: '#0284C7' },
    { label: 'Locataires', value: stats?.tenants_count || 0, icon: UserCheck, color: '#00A650' },
    { label: 'Factures', value: stats?.invoices_count || 0, icon: Receipt, color: '#FF6B00' },
    { label: 'Impayees', value: stats?.unpaid_invoices || 0, icon: AlertCircle, color: '#DC2626' },
    { label: 'Total charges', value: `${(stats?.total_charges || 0).toLocaleString('fr-BE')} EUR`, icon: TrendingUp, color: '#022D52' },
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

      {/* SANTE COMPTABLE - toujours visible quand une ACP est selectionnee */}
      <Card className="border-slate-200 mb-6" data-testid="health-audit-card">
        <CardContent className="p-5">
          {!health && !healthError && (
            <div className="flex items-center gap-3 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" />
              Calcul de la sante comptable...
            </div>
          )}
          {!health && healthError && (
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-red-600">
                <span className="font-semibold">Sante comptable indisponible :</span> {healthError}
              </div>
              <Button onClick={loadHealth} size="sm" variant="outline" data-testid="health-retry">Reessayer</Button>
            </div>
          )}
          {health && (<>
            <div className="flex items-start justify-between mb-3 gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-3 mb-1">
                  <h3 className="text-base font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Sante comptable</h3>
                  <span className={`text-[10px] uppercase tracking-wider font-bold px-2 py-0.5 rounded-full ${
                    health.score >= 90 ? 'bg-green-100 text-green-700' :
                    health.score >= 75 ? 'bg-blue-100 text-[#01213e]' :
                    health.score >= 50 ? 'bg-orange-100 text-orange-700' :
                    'bg-red-100 text-red-700'
                  }`} data-testid="health-label">{health.health_label}</span>
                </div>
                <p className="text-xs text-slate-500">Detection automatique d&apos;anomalies sur l&apos;ACP - seuil {health.days_threshold}j</p>
              </div>
              <div className="text-right">
                <div className={`text-3xl font-bold tracking-tight ${
                  health.score >= 90 ? 'text-green-600' :
                  health.score >= 75 ? 'text-[#022D52]' :
                  health.score >= 50 ? 'text-orange-600' :
                  'text-red-600'
                }`} style={{fontFamily:'Chivo,sans-serif'}} data-testid="health-score">{health.score}<span className="text-base text-slate-400">/100</span></div>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
              {[
                { label: 'Fact > 60j', val: health.stats.invoices_overdue, color: 'text-red-600 bg-red-50 border-red-200' },
                { label: 'Doublons', val: health.stats.duplicates, color: 'text-orange-600 bg-orange-50 border-orange-200' },
                { label: 'Orphelins', val: health.stats.orphans, color: 'text-amber-600 bg-amber-50 border-amber-200' },
                { label: 'Desequilibres', val: health.stats.unbalanced, color: 'text-red-600 bg-red-50 border-red-200' },
                // iter90i2 : KPI "Owners retard" retire (donnees imprecises).
              ].map((s, i) => (
                <div key={i} className={`text-center border rounded-md py-1.5 ${s.val > 0 ? s.color : 'text-slate-400 bg-slate-50 border-slate-200'}`}>
                  <div className="text-lg font-bold leading-none">{s.val}</div>
                  <div className="text-[10px] uppercase tracking-wider mt-0.5">{s.label}</div>
                </div>
              ))}
            </div>
            {(() => {
              // iter90i2 : filtre les anomalies owners_late/pending (donnees imprecises).
              const filteredAnomalies = (health.anomalies || []).filter(
                a => a.category !== 'owners_late' && a.category !== 'owners_pending'
              );
              return filteredAnomalies.length > 0 && (
              <details className="mt-3" open>
                <summary className="text-xs font-semibold text-slate-700 cursor-pointer hover:text-slate-900 select-none flex items-center gap-1.5" data-testid="health-anomalies-toggle">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-orange-500 animate-pulse" />
                  Voir les {filteredAnomalies.length} anomalie(s) detectee(s)
                </summary>
                <div className="mt-3 space-y-3">
                  {filteredAnomalies.map((a, i) => {
                    // Configuration visuelle par categorie
                    const catCfg = {
                      invoices_overdue: { icon: '📄', color: 'red', label: 'Factures impayees' },
                      owners_late:      { icon: '👤', color: 'red', label: 'Proprietaires en retard' },
                      owners_pending:   { icon: '⏳', color: 'orange', label: 'En attente de traitement' },
                      duplicates:       { icon: '⚠️', color: 'orange', label: 'Doublons potentiels' },
                      orphans:          { icon: '🔗', color: 'orange', label: 'Comptes tier orphelins' },
                      unbalanced:       { icon: '⚖️', color: 'red', label: 'Ecritures non equilibrees' },
                    }[a.category] || { icon: '❓', color: 'slate', label: a.category };
                    const badgeCls = {
                      red:    'bg-red-50 text-red-700 border-red-200',
                      orange: 'bg-orange-50 text-orange-700 border-orange-200',
                      slate:  'bg-slate-50 text-slate-700 border-slate-200',
                    }[catCfg.color];

                    return (
                      <div key={i} className={`text-xs border rounded-lg overflow-hidden ${badgeCls}`}>
                        {/* Header categorie */}
                        <div className={`px-3 py-2 border-b border-current/20 flex items-center gap-2 font-semibold`}>
                          <span className="text-sm">{catCfg.icon}</span>
                          <span>{a.title}</span>
                          <span className="ml-auto text-[10px] uppercase tracking-wider opacity-60">{catCfg.label}</span>
                        </div>
                        {/* Items details */}
                        {Array.isArray(a.items) && a.items.length > 0 && (
                          <div className="bg-white/70 p-2" data-testid={`health-items-${a.category}`}>
                            {/* Factures impayees */}
                            {a.category === 'invoices_overdue' && (
                              <div className="overflow-x-auto">
                                <table className="w-full text-[11px]">
                                  <thead className="text-slate-500">
                                    <tr><th className="text-left pb-1">Fournisseur</th><th className="text-left pb-1">N°</th><th className="text-right pb-1">Montant</th><th className="text-right pb-1">Echeance</th><th className="text-right pb-1">Retard</th></tr>
                                  </thead>
                                  <tbody>
                                    {a.items.map((it, j) => (
                                      <tr key={j} className="border-t border-slate-100">
                                        <td className="py-1 truncate max-w-[180px]" title={it.supplier}>{it.supplier}</td>
                                        <td className="py-1 font-mono text-slate-600">{it.number}</td>
                                        <td className="py-1 text-right font-mono text-red-700">{Number(it.amount || 0).toFixed(2)}</td>
                                        <td className="py-1 text-right font-mono text-slate-500">{fmtDate(it.due_date)}</td>
                                        <td className="py-1 text-right font-semibold text-red-700">{it.age_days}j</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                            {/* iter90i2 : tableau "Proprietaires en retard" supprime.
                                Les donnees sont fausses (comptage brut sans logique
                                FIFO ni tenir compte du solde tier). L'user prefere
                                consulter Espace Proprietaire ou Balance des tiers
                                pour une info fiable. */}
                            {/* Doublons */}
                            {a.category === 'duplicates' && (
                              <ul className="space-y-1">
                                {a.items.map((it, j) => (
                                  <li key={j} className="flex items-center gap-2">
                                    <span className="font-semibold">{it.supplier || 'Fournisseur'}</span>
                                    <span className="text-slate-500">·</span>
                                    <span className="font-mono text-red-700">{Number(it.amount || 0).toFixed(2)} EUR</span>
                                    {Array.isArray(it.invoices) && (
                                      <span className="text-slate-500">
                                        · {it.invoices.map(x => x.number).filter(Boolean).join(', ')}
                                      </span>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            )}
                            {/* Orphelins */}
                            {a.category === 'orphans' && (
                              <ul className="space-y-1">
                                {a.items.map((it, j) => (
                                  <li key={j} className="flex items-center gap-2">
                                    <span className="font-mono font-semibold text-slate-800">{it.account}</span>
                                    {it.name && <span className="text-slate-500">- {it.name}</span>}
                                    {typeof it.balance === 'number' && (
                                      <span className="font-mono text-red-700 ml-auto">{it.balance.toFixed(2)} EUR</span>
                                    )}
                                    <span className="text-[10px] text-slate-400 uppercase">{it.type === 'supplier' ? 'fourn.' : 'prop.'}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                            {/* Ecritures non equilibrees */}
                            {a.category === 'unbalanced' && (
                              <ul className="space-y-1">
                                {a.items.map((it, j) => (
                                  <li key={j} className="flex items-center gap-2">
                                    <span className="font-mono text-slate-700">{it.reference || it.id}</span>
                                    {it.date && <span className="text-slate-500">- {fmtDate(it.date)}</span>}
                                    <span className="ml-auto font-mono text-red-700">ecart {Number(it.ecart || 0).toFixed(2)} EUR</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                            {/* Compteur restant */}
                            {typeof a.count === 'number' && a.count > a.items.length && (
                              <div className="text-[10px] text-slate-400 italic mt-2 text-center">
                                ... et {a.count - a.items.length} autre(s) non affiche(s)
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </details>
              );
            })()}
          </>)}
        </CardContent>
      </Card>

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
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-lg font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Actions rapides</h3>
              <span className="text-[10px] text-slate-400 uppercase tracking-wider">Adapte a votre usage</span>
            </div>
            <AdaptiveQuickActions
              scopeKey={`u:${user?.id || 'anon'}-c:${selectedCopro || 'all'}`}
              actions={[
                { id: 'invoice',   label: 'Facture',        href: '/invoices',      color: 'bg-orange-50 text-orange-700 border-orange-200',  icon: Receipt },
                { id: 'entry',     label: 'Ecriture',       href: '/journals',      color: 'bg-green-50 text-green-700 border-green-200',     icon: FileText },
                { id: 'banking',   label: 'Extrait',        href: '/banking',       color: 'bg-blue-50 text-[#01213e] border-blue-200',        icon: Landmark },
                { id: 'funds',     label: 'Appel fonds',    href: '/fund-calls',    color: 'bg-purple-50 text-purple-700 border-purple-200',  icon: Megaphone },
                { id: 'tiers',     label: 'Balance tiers',  href: '/balance-tiers', color: 'bg-teal-50 text-teal-700 border-teal-200',        icon: Scale },
                { id: 'mutation',  label: 'Mutation lot',   href: '/coproprietes',  color: 'bg-rose-50 text-rose-700 border-rose-200',        icon: ArrowLeftRight },
                { id: 'owners',    label: 'Proprietaires',  href: '/owners',        color: 'bg-indigo-50 text-indigo-700 border-indigo-200',  icon: Users },
                { id: 'suppliers', label: 'Fournisseurs',   href: '/suppliers',     color: 'bg-cyan-50 text-cyan-700 border-cyan-200',        icon: Building2 },
                { id: 'reports',   label: 'Rapports',       href: '/reports',       color: 'bg-slate-50 text-slate-700 border-slate-200',     icon: FileBarChart },
                // iter90i4 : "Doublons" retire des actions rapides syndic
                // (reserve superadmin -> /admin/duplicates)
              ]}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
