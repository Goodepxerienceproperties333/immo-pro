import { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Users, Unlock, ScrollText, ArrowRight, ShieldAlert, Building2, Eye } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

/**
 * Tableau de bord du SUPERADMIN de plateforme.
 * Distinct de l'interface syndic. Donne acces aux outils d'administration :
 *  - Gestion des syndics et utilisateurs
 *  - Outils de deblocage comptable (entries verrouillees, FY clotures)
 *  - Audit log
 *  - Bouton "Mode syndic" pour basculer vers l'interface de gestion ACP
 */
export default function AdminDashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState({ users: 0, acps: 0, recent_audits: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [users, copros, audits] = await Promise.all([
          api.get('/admin/users').catch(() => ({ data: [] })),
          api.get('/coproprietes?include_archived=true').catch(() => ({ data: [] })),
          api.get('/admin/audit-log?limit=10').catch(() => ({ data: [] })),
        ]);
        if (cancelled) return;
        setStats({
          users: users.data.length,
          acps: copros.data.length,
          syndics: users.data.filter(u => u.role === 'syndic').length,
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

  return (
    <div className="space-y-6" data-testid="admin-dashboard">
      <div className="page-header">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-gradient-to-br from-amber-500 to-red-600 flex items-center justify-center text-white">
            <ShieldAlert size={20} />
          </div>
          <div>
            <h1 className="page-title">Administration de la plateforme</h1>
            <p className="page-subtitle">
              Bienvenue {user?.name}. Vous etes connecte en tant que <Badge variant="outline" className="ml-1 border-amber-400 text-amber-700">Super administrateur</Badge>
            </p>
          </div>
          <div className="ml-auto">
            <Button
              variant="outline"
              className="text-[#0055FF] border-[#0055FF]/30 hover:bg-[#0055FF]/10"
              onClick={() => navigate('/')}
              data-testid="switch-syndic-mode-btn"
              title="Basculer vers l'interface de gestion syndic"
            >
              <Building2 size={16} className="mr-2" />
              Mode syndic
            </Button>
          </div>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card data-testid="kpi-users">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Utilisateurs</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : stats.users}</div>
            <div className="text-xs text-slate-500 mt-1">
              {stats.syndics} syndic{stats.syndics > 1 ? 's' : ''} · {stats.gestionnaires} gestionnaire{stats.gestionnaires > 1 ? 's' : ''} · {stats.owners} propri.
            </div>
          </CardContent>
        </Card>
        <Card data-testid="kpi-acps">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Coproprietes (ACPs)</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : stats.acps}</div>
            <div className="text-xs text-slate-500 mt-1">total sur la plateforme</div>
          </CardContent>
        </Card>
        <Card data-testid="kpi-audits">
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Actions admin (10 dernieres)</div>
            <div className="text-2xl font-bold text-slate-900">{loading ? '...' : (stats.recent_audits?.length || 0)}</div>
            <div className="text-xs text-slate-500 mt-1">visibles ci-dessous</div>
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
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="hover:shadow-md transition cursor-pointer" onClick={() => navigate('/admin/users')} data-testid="card-users">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Users size={18} className="text-[#0055FF]" />Gestion des utilisateurs</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600">
            <p>Creez, modifiez ou suspendez les comptes syndics, gestionnaires et proprietaires.</p>
            <p className="mt-2">Attribuez les copropriete(s) accessible(s) a chaque syndic.</p>
            <Button variant="link" className="px-0 mt-2 text-[#0055FF]" data-testid="btn-go-users">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>

        <Card className="hover:shadow-md transition cursor-pointer border-amber-200" onClick={() => navigate('/admin/unlock')} data-testid="card-unlock">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><Unlock size={18} className="text-amber-600" />Outils de deblocage comptable</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600">
            <p>Modifier une ecriture verrouillee, forcer la reouverture d'un exercice, supprimer une ecriture problematique.</p>
            <p className="mt-2 text-amber-700 text-xs"><b>Actions critiques :</b> trace systematique dans le journal d'audit.</p>
            <Button variant="link" className="px-0 mt-2 text-amber-600" data-testid="btn-go-unlock">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>

        <Card className="hover:shadow-md transition cursor-pointer" onClick={() => navigate('/admin/audit')} data-testid="card-audit">
          <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2 text-slate-900"><ScrollText size={18} className="text-slate-600" />Journal d&apos;audit</CardTitle></CardHeader>
          <CardContent className="text-sm text-slate-600">
            <p>Trace complete de toutes les actions superadmin (deblocages, forcages, modifications utilisateurs).</p>
            <p className="mt-2">Filtrable par action et par ACP. Lecture seule, conservation indefinie.</p>
            <Button variant="link" className="px-0 mt-2 text-slate-600" data-testid="btn-go-audit">Ouvrir <ArrowRight size={14} className="ml-1" /></Button>
          </CardContent>
        </Card>
      </div>

      {/* Recent actions */}
      <Card data-testid="recent-audits">
        <CardHeader className="pb-2 flex flex-row items-center justify-between"><CardTitle className="text-base">Dernieres actions d&apos;administration</CardTitle><Link to="/admin/audit" className="text-xs text-[#0055FF] hover:underline">Voir tout</Link></CardHeader>
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
