import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { ArrowLeft, ScrollText, Search, Download } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const ACTION_LABELS = {
  unlock_entry: { label: 'Modification ecriture verrouillee', color: 'bg-amber-100 text-amber-800 border-amber-300' },
  force_delete_entry: { label: 'Suppression ecriture', color: 'bg-red-100 text-red-800 border-red-300' },
  force_reopen_fy: { label: 'Reouverture forcee exercice', color: 'bg-orange-100 text-orange-800 border-orange-300' },
  user_create: { label: 'Creation utilisateur', color: 'bg-blue-100 text-blue-800 border-blue-300' },
  user_update: { label: 'Modification utilisateur', color: 'bg-blue-100 text-blue-800 border-blue-300' },
  user_delete: { label: 'Suppression utilisateur', color: 'bg-red-100 text-red-800 border-red-300' },
};

export default function AdminAuditLogPage() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ action: '__all__', q: '', limit: 200 });

  const load = async () => {
    setLoading(true);
    try {
      const params = { limit: filters.limit };
      if (filters.action !== '__all__') params.action = filters.action;
      const { data } = await api.get('/admin/audit-log', { params });
      let arr = data || [];
      if (filters.q) {
        const q = filters.q.toLowerCase();
        arr = arr.filter(l =>
          (l.user_email || '').toLowerCase().includes(q) ||
          (l.target_id || '').toLowerCase().includes(q) ||
          (l.details?.reason || '').toLowerCase().includes(q)
        );
      }
      setLogs(arr);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [filters.action, filters.limit]);

  const downloadCsv = () => {
    const head = 'Date;Heure;Utilisateur;Action;Cible;ID Cible;ACP;Justification\n';
    const rows = logs.map(l => [
      (l.timestamp || '').slice(0, 10),
      (l.timestamp || '').slice(11, 16),
      l.user_email || '',
      l.action || '',
      l.target_type || '',
      l.target_id || '',
      l.copropriete_id || '',
      (l.details?.reason || '').replace(/;/g, ',').replace(/\n/g, ' '),
    ].join(';')).join('\n');
    const blob = new Blob([head + rows], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `audit_log_${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4" data-testid="admin-audit-page">
      <div className="flex items-center gap-3">
        <Link to="/admin" className="text-[#0055FF] hover:underline text-sm flex items-center gap-1" data-testid="back-to-admin"><ArrowLeft size={14} /> Retour Admin</Link>
      </div>
      <div className="page-header">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-slate-200 flex items-center justify-center text-slate-700"><ScrollText size={20} /></div>
          <div>
            <h1 className="page-title">Journal d&apos;audit</h1>
            <p className="page-subtitle">Trace de toutes les actions superadmin (lecture seule).</p>
          </div>
          <div className="ml-auto">
            <Button variant="outline" onClick={downloadCsv} disabled={logs.length === 0} data-testid="audit-csv-btn">
              <Download size={14} className="mr-2" /> Exporter CSV
            </Button>
          </div>
        </div>
      </div>

      <Card>
        <CardContent className="pt-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mb-3">
            <Select value={filters.action} onValueChange={v => setFilters({...filters, action: v})}>
              <SelectTrigger className="text-sm" data-testid="filter-action"><SelectValue placeholder="Action" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">Toutes les actions</SelectItem>
                {Object.entries(ACTION_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={filters.q}
              onChange={e => setFilters({...filters, q: e.target.value})}
              placeholder="Filtrer par email, cible, justification..."
              data-testid="filter-q"
            />
            <Select value={String(filters.limit)} onValueChange={v => setFilters({...filters, limit: parseInt(v) || 200})}>
              <SelectTrigger className="text-sm" data-testid="filter-limit"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="50">50 dernieres</SelectItem>
                <SelectItem value="200">200 dernieres</SelectItem>
                <SelectItem value="1000">1000 dernieres</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-base">{logs.length} entree{logs.length > 1 ? 's' : ''}</CardTitle></CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-sm text-slate-500">Chargement...</div>
          ) : logs.length === 0 ? (
            <div className="text-sm text-slate-500 italic">Aucune entree d&apos;audit pour ces criteres.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-slate-600 text-xs uppercase">
                  <tr>
                    <th className="px-2 py-2 text-left">Date</th>
                    <th className="px-2 py-2 text-left">Utilisateur</th>
                    <th className="px-2 py-2 text-left">Action</th>
                    <th className="px-2 py-2 text-left">Cible</th>
                    <th className="px-2 py-2 text-left">Justification</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {logs.map(l => {
                    const meta = ACTION_LABELS[l.action] || { label: l.action, color: 'bg-slate-100 text-slate-800 border-slate-300' };
                    return (
                      <tr key={l.id} className="hover:bg-slate-50" data-testid={`audit-row-${l.id}`}>
                        <td className="px-2 py-2 font-mono text-xs whitespace-nowrap">
                          {fmtDate(l.timestamp)} <span className="text-slate-400">{(l.timestamp || '').slice(11, 16)}</span>
                        </td>
                        <td className="px-2 py-2 text-xs">{l.user_email}</td>
                        <td className="px-2 py-2">
                          <Badge variant="outline" className={`text-xs ${meta.color}`}>{meta.label}</Badge>
                        </td>
                        <td className="px-2 py-2 text-xs font-mono text-slate-500">
                          {l.target_type}:{(l.target_id || '').slice(0, 8)}
                        </td>
                        <td className="px-2 py-2 text-xs text-slate-700 italic max-w-md">
                          {l.details?.reason || '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
