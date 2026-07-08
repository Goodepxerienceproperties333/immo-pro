import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Activity, Shield, AlertTriangle, Eye, Globe2, RefreshCcw, CheckCircle2, XCircle } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

function fmtRelativeOrDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now - d;
    const min = Math.floor(diffMs / 60000);
    if (min < 1) return 'A l\'instant';
    if (min < 60) return `Il y a ${min} min`;
    const h = Math.floor(min / 60);
    if (h < 24) return `Il y a ${h}h`;
    const days = Math.floor(h / 24);
    if (days < 30) return `Il y a ${days}j`;
    return fmtDate(iso);
  } catch (_e) {
    return iso;
  }
}

function fmtFullDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('fr-BE', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  } catch (_e) { return iso; }
}

export default function AdminLoginHistoryPage() {
  const { isSuperadmin } = useAuth();
  const [summary, setSummary] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailUser, setDetailUser] = useState(null);
  const [detailRows, setDetailRows] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [filterRole, setFilterRole] = useState('all');

  const load = async () => {
    if (!isSuperadmin) return;
    setLoading(true);
    try {
      const r = await api.get('/admin/login-history/syndics-summary');
      setSummary(r.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [isSuperadmin]); // eslint-disable-line react-hooks/exhaustive-deps

  const openDetail = async (u) => {
    setDetailUser(u);
    setDetailLoading(true);
    try {
      const r = await api.get('/admin/login-history', { params: { user_id: u.user_id, limit: 100 } });
      setDetailRows(r.data.items || []);
    } finally {
      setDetailLoading(false);
    }
  };

  if (!isSuperadmin) {
    return (
      <div className="max-w-2xl mx-auto mt-8 text-center bg-amber-50 border border-amber-200 rounded p-6">
        <Shield size={36} className="mx-auto text-amber-600 mb-2" />
        <p className="text-sm text-slate-700">Acces reserve au super administrateur.</p>
      </div>
    );
  }

  const filtered = summary.filter(s => filterRole === 'all' || s.role === filterRole);
  const totalLogins30d = summary.reduce((acc, s) => acc + (s.logins_last_30d || 0), 0);
  const totalFails30d = summary.reduce((acc, s) => acc + (s.failed_last_30d || 0), 0);
  const activeUsers = summary.filter(s => s.logins_last_7d > 0).length;

  return (
    <div data-testid="admin-login-history-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title"><Activity size={22} className="inline mr-2" />Historique des connexions</h1>
          <p className="page-subtitle">Audit des connexions par utilisateur (syndic + gestionnaires + admin).</p>
        </div>
        <Button variant="outline" size="sm" onClick={load} disabled={loading} data-testid="reload-btn">
          <RefreshCcw size={14} className="mr-1" /> Actualiser
        </Button>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-5">
        <Card><CardContent className="p-4">
          <div className="text-xs uppercase tracking-wider text-slate-500">Total utilisateurs</div>
          <div className="text-2xl font-bold text-slate-900 mt-1">{summary.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs uppercase tracking-wider text-slate-500">Actifs (7 derniers jours)</div>
          <div className="text-2xl font-bold text-emerald-600 mt-1">{activeUsers}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs uppercase tracking-wider text-slate-500">Connexions reussies (30j)</div>
          <div className="text-2xl font-bold text-[#2563EB] mt-1">{totalLogins30d}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs uppercase tracking-wider text-slate-500">Echecs (30j)</div>
          <div className="text-2xl font-bold text-red-600 mt-1 flex items-center gap-2">
            {totalFails30d}
            {totalFails30d > 0 && <AlertTriangle size={18} className="text-red-500" />}
          </div>
        </CardContent></Card>
      </div>

      {/* Role filters */}
      <div className="flex gap-2 mb-3">
        {[
          { v: 'all', l: 'Tous' },
          { v: 'syndic', l: 'Syndics' },
          { v: 'gestionnaire', l: 'Gestionnaires' },
          { v: 'superadmin', l: 'Super admins' },
        ].map(f => (
          <Button
            key={f.v}
            size="sm"
            variant={filterRole === f.v ? 'default' : 'outline'}
            onClick={() => setFilterRole(f.v)}
            className={filterRole === f.v ? 'bg-[#2563EB] hover:bg-[#1D4ED8]' : ''}
            data-testid={`filter-${f.v}`}
          >
            {f.l} ({f.v === 'all' ? summary.length : summary.filter(s => s.role === f.v).length})
          </Button>
        ))}
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Utilisateur</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Derniere connexion</TableHead>
                <TableHead className="text-center">7j</TableHead>
                <TableHead className="text-center">30j</TableHead>
                <TableHead className="text-center">Echecs 30j</TableHead>
                <TableHead className="text-right">Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Chargement...</TableCell></TableRow>
              ) : filtered.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucun utilisateur</TableCell></TableRow>
              ) : filtered.map(s => (
                <TableRow key={s.user_id} className={s.failed_last_30d > 5 ? 'bg-red-50/50' : ''} data-testid={`row-${s.user_id}`}>
                  <TableCell>
                    <div className="text-sm font-medium text-slate-900">{s.name}</div>
                    <div className="text-xs text-slate-500 font-mono">{s.email}</div>
                  </TableCell>
                  <TableCell>
                    {s.role === 'superadmin' && <Badge className="bg-purple-100 text-purple-800 border-purple-300">Super admin</Badge>}
                    {s.role === 'syndic' && <Badge className="bg-red-100 text-red-800 border-red-300">Syndic</Badge>}
                    {s.role === 'gestionnaire' && <Badge className="bg-blue-100 text-blue-800 border-blue-300">Gestionnaire</Badge>}
                  </TableCell>
                  <TableCell>
                    <div className="text-xs text-slate-700">{fmtRelativeOrDate(s.last_login_at)}</div>
                    {s.last_login_ip && <div className="text-[10px] text-slate-400 font-mono flex items-center gap-1"><Globe2 size={9} />{s.last_login_ip}</div>}
                  </TableCell>
                  <TableCell className="text-center text-sm font-mono">{s.logins_last_7d}</TableCell>
                  <TableCell className="text-center text-sm font-mono">{s.logins_last_30d}</TableCell>
                  <TableCell className="text-center text-sm font-mono">
                    {s.failed_last_30d > 0 ? (
                      <span className="text-red-600 font-semibold">{s.failed_last_30d}</span>
                    ) : (
                      <span className="text-slate-400">0</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => openDetail(s)} data-testid={`detail-${s.user_id}`}>
                      <Eye size={14} />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Detail dialog */}
      <Dialog open={!!detailUser} onOpenChange={(o) => !o && setDetailUser(null)}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-hidden flex flex-col" data-testid="detail-dialog">
          <DialogHeader>
            <DialogTitle className="text-base">
              Historique de {detailUser?.name}{' '}
              <span className="text-xs text-slate-500 font-mono">({detailUser?.email})</span>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto">
            {detailLoading ? (
              <div className="text-center py-8 text-slate-400 text-sm">Chargement...</div>
            ) : detailRows.length === 0 ? (
              <div className="text-center py-8 text-slate-400 text-sm">Aucune tentative de connexion enregistree.</div>
            ) : (
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    <th className="text-left px-3 py-2">Date</th>
                    <th className="text-left px-3 py-2">Statut</th>
                    <th className="text-left px-3 py-2">IP</th>
                    <th className="text-left px-3 py-2">User-Agent</th>
                  </tr>
                </thead>
                <tbody>
                  {detailRows.map((r) => (
                    <tr key={r.id} className="border-b border-slate-100 hover:bg-slate-50/50">
                      <td className="px-3 py-2 font-mono text-slate-700">{fmtFullDate(r.created_at)}</td>
                      <td className="px-3 py-2">
                        {r.success ? (
                          <span className="inline-flex items-center gap-1 text-emerald-700 font-medium"><CheckCircle2 size={12} /> Reussi</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-red-600 font-medium" title={r.reason || ''}><XCircle size={12} /> Echec {r.reason ? `(${r.reason})` : ''}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-slate-600">{r.ip || '—'}</td>
                      <td className="px-3 py-2 text-slate-500 truncate max-w-xs" title={r.user_agent}>{r.user_agent || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
