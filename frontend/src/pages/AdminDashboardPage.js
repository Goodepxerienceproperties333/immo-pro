import { useEffect, useState, Fragment } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Users, Unlock, ScrollText, ArrowRight, ShieldAlert, IdCard, Building2, ChevronDown, ChevronRight as ChevRight, Briefcase, Merge, ShieldCheck } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

export default function AdminDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState({ users: 0, acps: 0, recent_audits: [] });
  const [syndics, setSyndics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [users, copros, audits, sov] = await Promise.all([
          api.get('/admin/users').catch(() => ({ data: [] })),
          api.get('/coproprietes?include_archived=true').catch(() => ({ data: [] })),
          api.get('/admin/audit-log?limit=8').catch(() => ({ data: [] })),
          api.get('/admin/syndics-overview').catch(() => ({ data: [] })),
        ]);
        if (cancelled) return;
        setSyndics(sov.data || []);
        setStats({
          users: users.data.length,
          acps: copros.data.length,
          syndics: users.data.filter(u => u.role === 'syndic' || u.role === 'admin').length,
          gestionnaires: users.data.filter(u => u.role === 'gestionnaire').length,
          owners: users.data.filter(u => u.role === 'owner').length,
          recent_audits: audits.data || [],
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const toggle = (id) => setExpanded(prev => ({ ...prev, [id]: !prev[id] }));

  return (
    <div className="space-y-6" data-testid="admin-dashboard">
      <div className="page-header">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-gradient-to-br from-amber-500 to-red-600 flex items-center justify-center text-white">
            <ShieldAlert size={20} />
          </div>
          <div>
            <h1 className="page-title">Administration de la plateforme</h1>
            <div className="page-subtitle">
              Bienvenue {user?.name}. <Badge variant="outline" className="ml-1 border-amber-400 text-amber-700">Super administrateur</Badge>
            </div>
          </div>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card data-testid="kpi-syndics">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Syndics actifs</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : stats.syndics}</div>
            <div className="text-xs text-slate-500 mt-1">+ {stats.gestionnaires} gestionnaire(s) &middot; {stats.owners} proprietaires</div>
          </CardContent>
        </Card>
        <Card data-testid="kpi-acps">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Coproprietes (ACPs)</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : stats.acps}</div>
            <div className="text-xs text-slate-500 mt-1">total sur la plateforme</div>
          </CardContent>
        </Card>
        <Card data-testid="kpi-lots">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Lots geres (cumul)</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : syndics.reduce((s, x) => s + (x.total_lots || 0), 0)}</div>
            <div className="text-xs text-slate-500 mt-1">{syndics.reduce((s, x) => s + (x.total_owners || 0), 0)} proprietaires distincts</div>
          </CardContent>
        </Card>
        <Card data-testid="kpi-emergent">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-emerald-600 font-semibold mb-1">Statut</div>
            <div className="text-2xl font-bold text-emerald-700">Operationnel</div>
            <div className="text-xs text-slate-500 mt-1">backend + frontend OK</div>
          </CardContent>
        </Card>
      </div>

      {/* Action cards */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="hover:shadow-md transition cursor-pointer" onClick={() => navigate('/admin/users')} data-testid="card-users">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Users size={18} className="text-[#022D52]" />Utilisateurs</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Creer/suspendre des syndics, gestionnaires, proprietaires. Attribuer les ACPs.</p>
            <Button variant="link" className="px-0 mt-2 text-[#022D52]" data-testid="btn-go-users">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer" onClick={() => navigate('/admin/role-templates')} data-testid="card-templates">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><IdCard size={18} className="text-emerald-600" />Profils utilisateurs</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Definir des profils (Comptable, Assistant, etc.) que les syndics piocheront.</p>
            <Button variant="link" className="px-0 mt-2 text-emerald-600" data-testid="btn-go-templates">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer border-amber-200" onClick={() => navigate('/admin/unlock')} data-testid="card-unlock">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Unlock size={18} className="text-amber-600" />Deblocage comptable</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Modifier une ecriture verrouillee, forcer la reouverture d&apos;un exercice.</p>
            <Button variant="link" className="px-0 mt-2 text-amber-600" data-testid="btn-go-unlock">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer border-purple-200" onClick={() => navigate('/admin/duplicates')} data-testid="card-duplicates">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Merge size={18} className="text-purple-600" />Doublons potentiels</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Detecter et fusionner les doublons fournisseurs, proprietaires et utilisateurs (chinese wall par syndic).</p>
            <Button variant="link" className="px-0 mt-2 text-purple-600" data-testid="btn-go-duplicates">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer border-indigo-200" onClick={() => navigate('/admin/expense-categories-dedupe')} data-testid="card-dedupe-cats">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Merge size={18} className="text-indigo-600" />Nettoyage Natures de depense</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Fusionner les Natures dupliquees (accents, casse) et nettoyer les libelles mal formes par ACP.</p>
            <Button variant="link" className="px-0 mt-2 text-indigo-600" data-testid="btn-go-dedupe-cats">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer" onClick={() => navigate('/admin/audit')} data-testid="card-audit">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><ScrollText size={18} className="text-slate-600" />Journal d&apos;audit</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Trace complete des actions superadmin. Export CSV pour compliance.</p>
            <Button variant="link" className="px-0 mt-2 text-slate-600" data-testid="btn-go-audit">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer border-amber-200" onClick={() => navigate('/admin/mutations-audit')} data-testid="card-mutations-audit">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><ShieldCheck size={18} className="text-amber-600" />Audit des mutations</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Detecter les mutations avec sens (vendeur/acheteur) inverse et les reparer (contre-passation des ODs).</p>
            <Button variant="link" className="px-0 mt-2 text-amber-600" data-testid="btn-go-mutations-audit">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
        <Card className="hover:shadow-md transition cursor-pointer border-emerald-200" onClick={() => navigate('/admin/quality-audit')} data-testid="card-quality-audit">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><ShieldCheck size={18} className="text-emerald-600" />Quality Audit (anti-doublons)</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600 pt-1">
            <p>Rapport global : BCE dup, PCMN orphelins, comptes bancaires dup, NC sans ecriture. Export CSV.</p>
            <Button variant="link" className="px-0 mt-2 text-emerald-600" data-testid="btn-go-quality-audit">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
      </div>

      {/* Vue par syndic (la grosse table) */}
      <Card data-testid="syndics-overview">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2"><Briefcase size={18} className="text-[#022D52]" />Vue par syndic - bases de facturation</CardTitle>
            <Badge variant="outline" className="text-xs">{syndics.length} syndic{syndics.length > 1 ? 's' : ''}</Badge>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-sm text-slate-500">Chargement...</div>
          ) : syndics.length === 0 ? (
            <div className="text-sm text-slate-500 italic">Aucun syndic enregistre. Creez-en un via Utilisateurs.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-slate-600 text-xs uppercase">
                  <tr>
                    <th className="px-2 py-2 text-left w-6"></th>
                    <th className="px-2 py-2 text-left">Syndic</th>
                    <th className="px-2 py-2 text-left">Email</th>
                    <th className="px-2 py-2 text-right">ACPs</th>
                    <th className="px-2 py-2 text-right">Lots</th>
                    <th className="px-2 py-2 text-right">Proprietaires</th>
                    <th className="px-2 py-2 text-right">Factures</th>
                    <th className="px-2 py-2 text-center">Compte cree</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {syndics.map(s => (
                    <Fragment key={s.syndic_id}>
                      <tr className="hover:bg-slate-50 cursor-pointer" onClick={() => toggle(s.syndic_id)} data-testid={`syndic-row-${s.syndic_id}`}>
                        <td className="px-2 py-2 text-center text-slate-400">
                          {expanded[s.syndic_id] ? <ChevronDown size={14} /> : <ChevRight size={14} />}
                        </td>
                        <td className="px-2 py-2 font-semibold">{s.name}{s.role === 'admin' && <Badge className="ml-2 text-[10px]" variant="outline">admin</Badge>}</td>
                        <td className="px-2 py-2 text-slate-600 text-xs font-mono">{s.email}</td>
                        <td className="px-2 py-2 text-right font-mono">{s.copros_count}</td>
                        <td className="px-2 py-2 text-right font-mono font-semibold text-[#022D52]">{s.total_lots}</td>
                        <td className="px-2 py-2 text-right font-mono">{s.total_owners}</td>
                        <td className="px-2 py-2 text-right font-mono text-slate-500">{s.total_invoices}</td>
                        <td className="px-2 py-2 text-center text-xs text-slate-500">{fmtDate(s.created_at)}</td>
                      </tr>
                      {expanded[s.syndic_id] && (
                        <tr key={`${s.syndic_id}-detail`} className="bg-slate-50">
                          <td colSpan={8} className="px-4 py-3">
                            {s.coproprietes.length === 0 ? (
                              <div className="text-xs italic text-slate-500">Aucune ACP assignee a ce syndic.</div>
                            ) : (
                              <div className="space-y-1">
                                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-2">Detail par copropriete</div>
                                <table className="w-full text-xs">
                                  <thead className="text-slate-500 border-b border-slate-200">
                                    <tr>
                                      <th className="text-left px-1 py-1">Nom</th>
                                      <th className="text-left px-1 py-1">Reference</th>
                                      <th className="text-right px-1 py-1">Lots</th>
                                      <th className="text-right px-1 py-1">Proprios</th>
                                      <th className="text-right px-1 py-1">Factures</th>
                                      <th className="text-center px-1 py-1">Exercice ouvert</th>
                                      <th className="text-center px-1 py-1">Derniere activite</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {s.coproprietes.map(c => (
                                      <tr key={c.id} className="border-b border-slate-100" data-testid={`copro-detail-${c.id}`}>
                                        <td className="px-1 py-1 font-semibold">{c.name}</td>
                                        <td className="px-1 py-1 font-mono text-slate-500">{c.reference || '—'}</td>
                                        <td className="px-1 py-1 text-right font-mono">{c.lots_count}</td>
                                        <td className="px-1 py-1 text-right font-mono">{c.owners_count}</td>
                                        <td className="px-1 py-1 text-right font-mono text-slate-500">{c.invoices_count}</td>
                                        <td className="px-1 py-1 text-center">
                                          {c.current_fy ? (
                                            <Badge variant="outline" className="text-[10px] bg-emerald-50 text-emerald-700 border-emerald-300">{c.current_fy.name}</Badge>
                                          ) : (
                                            <Badge variant="outline" className="text-[10px] bg-slate-100 text-slate-500">Aucun</Badge>
                                          )}
                                        </td>
                                        <td className="px-1 py-1 text-center text-slate-500">
                                          {c.last_je_date || c.last_bank_tx_date ? fmtDate(c.last_je_date || c.last_bank_tx_date) : '—'}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
                <tfoot className="bg-slate-100 text-slate-700 font-semibold">
                  <tr>
                    <td colSpan={3} className="px-2 py-2 text-right">TOTAL</td>
                    <td className="px-2 py-2 text-right font-mono">{syndics.reduce((s, x) => s + x.copros_count, 0)}</td>
                    <td className="px-2 py-2 text-right font-mono text-[#022D52]">{syndics.reduce((s, x) => s + x.total_lots, 0)}</td>
                    <td className="px-2 py-2 text-right font-mono">{syndics.reduce((s, x) => s + x.total_owners, 0)}</td>
                    <td className="px-2 py-2 text-right font-mono">{syndics.reduce((s, x) => s + x.total_invoices, 0)}</td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent admin actions */}
      <Card data-testid="recent-audits">
        <CardHeader className="pb-2 flex flex-row items-center justify-between"><CardTitle className="text-base">Dernieres actions d&apos;administration</CardTitle><Link to="/admin/audit" className="text-xs text-[#022D52] hover:underline">Voir tout</Link></CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-sm text-slate-500">Chargement...</div>
          ) : (stats.recent_audits || []).length === 0 ? (
            <div className="text-sm text-slate-500 italic">Aucune action enregistree</div>
          ) : (
            <div className="divide-y divide-slate-200">
              {(stats.recent_audits || []).map(a => (
                <div key={a.id} className="py-2 text-sm flex items-start gap-3" data-testid={`audit-row-${a.id}`}>
                  <span className="text-slate-400 text-xs font-mono w-32 shrink-0">{fmtDate(a.timestamp)} {(a.timestamp||'').slice(11,16)}</span>
                  <Badge variant="outline" className="shrink-0">{a.action}</Badge>
                  <span className="text-slate-600 flex-1">
                    <span className="font-semibold">{a.user_email}</span> &middot; <span className="text-slate-500">{a.target_type}:{a.target_id?.slice(0,8)}</span>
                    {a.details?.reason && <span className="text-slate-400 italic ml-2">- {a.details.reason}</span>}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
