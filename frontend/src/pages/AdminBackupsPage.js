import { useEffect, useState, useCallback } from 'react';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Archive, Download, Trash2, PlayCircle, RefreshCw, HardDrive, Clock,
  CheckCircle2, XCircle, RotateCcw, Building2,
} from 'lucide-react';

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';

function humanSize(bytes) {
  if (!bytes) return '-';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function humanDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('fr-BE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function AdminBackupsPage() {
  const [backups, setBackups] = useState([]);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [restoreDialog, setRestoreDialog] = useState(null);
  const [restoreDryRun, setRestoreDryRun] = useState(true);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [b, r] = await Promise.all([
        api.get('/admin/backups?limit=200'),
        api.get('/admin/backups/runs?limit=30'),
      ]);
      setBackups(b.data.backups || []);
      setRuns(r.data.runs || []);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const triggerAll = async () => {
    if (!window.confirm('Declencher un backup manuel de TOUTES les ACPs maintenant ?')) return;
    setTriggering(true);
    try {
      const r = await api.post('/admin/backups/trigger');
      toast.success(`${r.data.success}/${r.data.total} ACP sauvegardees`);
      load();
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setTriggering(false); }
  };

  const remove = async (b) => {
    if (!window.confirm(`Supprimer le backup ${b.copropriete_name} du ${humanDate(b.created_at)} ?`)) return;
    try {
      await api.delete(`/admin/backups/${b.backup_id}`);
      toast.success('Backup supprime');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const download = async (b) => {
    try {
      const r = await api.get(`/admin/backups/${b.backup_id}/download`, { responseType: 'blob' });
      const url = URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `backup_${b.copropriete_name}_${b.created_at.slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const openRestore = (b) => {
    setRestoreDialog(b);
    setRestoreDryRun(true);
    setRestoreResult(null);
  };

  const doRestore = async () => {
    setRestoring(true);
    try {
      const r = await api.post(
        `/admin/backups/restore/${restoreDialog.backup_id}`,
        { dry_run: restoreDryRun },
      );
      setRestoreResult(r.data);
      if (!restoreDryRun) {
        toast.success('Restauration terminee');
        load();
      }
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setRestoring(false); }
  };

  // Group by ACP
  const byAcp = {};
  backups.forEach(b => {
    if (!byAcp[b.copropriete_id]) byAcp[b.copropriete_id] = { name: b.copropriete_name, items: [] };
    byAcp[b.copropriete_id].items.push(b);
  });

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-4" data-testid="admin-backups-page">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <HardDrive className="h-6 w-6 text-[#022D52]" />
            Sauvegardes ACP
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Backup automatique quotidien 00h00 (Europe/Brussels). Retention 30 quotidiens + 12 mensuels par ACP.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={load} data-testid="btn-refresh-backups"><RefreshCw className="h-4 w-4" /></Button>
          <Button onClick={triggerAll} disabled={triggering} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="btn-trigger-backup">
            <PlayCircle className="h-4 w-4 mr-1" /> Backup maintenant
          </Button>
        </div>
      </header>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Total backups</div>
          <div className="text-lg font-semibold" data-testid="stat-backups-total">{backups.length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">ACPs sauvegardees</div>
          <div className="text-lg font-semibold" data-testid="stat-backups-acp-count">{Object.keys(byAcp).length}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Taille totale</div>
          <div className="text-lg font-semibold">{humanSize(backups.reduce((a, b) => a + (b.size_bytes || 0), 0))}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Dernier run</div>
          <div className="text-sm font-medium">{runs[0] ? humanDate(runs[0].created_at) : '-'}</div>
          {runs[0] && (
            <div className="text-xs mt-1">
              {runs[0].failed > 0 ? (
                <span className="text-red-600">{runs[0].failed} echec(s)</span>
              ) : (
                <span className="text-emerald-600">{runs[0].success} OK</span>
              )}
            </div>
          )}
        </CardContent></Card>
      </div>

      {/* Runs history */}
      {runs.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Clock className="h-4 w-4 text-slate-500" /> Historique des jobs (derniers)
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Source</TableHead>
                <TableHead className="text-center">Total</TableHead>
                <TableHead className="text-center">OK</TableHead>
                <TableHead className="text-center">Echecs</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {runs.slice(0, 10).map((r, i) => (
                  <TableRow key={i}>
                    <TableCell>{humanDate(r.created_at)}</TableCell>
                    <TableCell><Badge variant="outline">{r.source}</Badge></TableCell>
                    <TableCell className="text-center">{r.total}</TableCell>
                    <TableCell className="text-center text-emerald-600">{r.success}</TableCell>
                    <TableCell className="text-center text-red-600">{r.failed || 0}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Backups by ACP */}
      <div className="space-y-4">
        {Object.entries(byAcp).sort((a, b) => a[1].name.localeCompare(b[1].name)).map(([cid, group]) => (
          <Card key={cid} data-testid={`acp-backups-${cid}`}>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Building2 className="h-4 w-4 text-[#022D52]" />
                {group.name} <span className="text-xs text-slate-400 font-normal">({group.items.length} backups)</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader><TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Taille</TableHead>
                  <TableHead className="text-center">Collections</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {group.items.map(b => (
                    <TableRow key={b.backup_id} data-testid={`row-backup-${b.backup_id}`}>
                      <TableCell>{humanDate(b.created_at)}</TableCell>
                      <TableCell>
                        <Badge className={b.type === 'manual' ? 'bg-violet-100 text-violet-700' : 'bg-blue-100 text-[#01213e]'}>
                          {b.type || 'daily'}
                        </Badge>
                      </TableCell>
                      <TableCell>{humanSize(b.size_bytes)}</TableCell>
                      <TableCell className="text-center text-xs">
                        {Object.values(b.collections_counts || {}).reduce((a, x) => a + x, 0)}
                      </TableCell>
                      <TableCell className="text-right space-x-1">
                        <Button size="sm" variant="outline" onClick={() => download(b)}
                                data-testid={`btn-download-${b.backup_id}`}>
                          <Download className="h-3 w-3" />
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => openRestore(b)}
                                data-testid={`btn-restore-${b.backup_id}`}>
                          <RotateCcw className="h-3 w-3" />
                        </Button>
                        <Button size="sm" variant="ghost" className="text-red-600"
                                onClick={() => remove(b)}
                                data-testid={`btn-delete-${b.backup_id}`}>
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        ))}
        {Object.keys(byAcp).length === 0 && !loading && (
          <Card><CardContent className="p-10 text-center text-slate-500">
            Aucun backup. Cliquez sur &quot;Backup maintenant&quot; pour en creer.
          </CardContent></Card>
        )}
      </div>

      {/* Restore Dialog */}
      <Dialog open={!!restoreDialog} onOpenChange={(o) => !o && setRestoreDialog(null)}>
        <DialogContent data-testid="dialog-restore">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <RotateCcw className="h-5 w-5 text-amber-600" /> Restaurer un backup
            </DialogTitle>
          </DialogHeader>
          {restoreDialog && (
            <div className="space-y-3">
              <div className="text-sm">
                <div><b>ACP :</b> {restoreDialog.copropriete_name}</div>
                <div><b>Date :</b> {humanDate(restoreDialog.created_at)}</div>
                <div><b>Taille :</b> {humanSize(restoreDialog.size_bytes)}</div>
              </div>
              <div className="p-3 bg-amber-50 border-l-4 border-amber-400 text-xs">
                <b>⚠ Attention</b> — La restauration ecrase les documents existants ayant le meme <code>id</code> (upsert).
                Toujours faire un backup manuel <b>juste avant</b> la restauration si vous n&apos;etes pas en dry-run.
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={restoreDryRun}
                       onChange={(e) => setRestoreDryRun(e.target.checked)}
                       data-testid="checkbox-dry-run" />
                Mode dry-run (simulation, aucune ecriture en base)
              </label>
              {restoreResult && (
                <Card>
                  <CardHeader className="pb-2"><CardTitle className="text-sm">Resultat {restoreResult.dry_run && '(dry-run)'}</CardTitle></CardHeader>
                  <CardContent className="text-xs">
                    <pre className="bg-slate-50 p-2 rounded overflow-x-auto">
                      {JSON.stringify(restoreResult.collections, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRestoreDialog(null)}>Fermer</Button>
            <Button onClick={doRestore} disabled={restoring}
                    className={restoreDryRun ? 'bg-slate-600 hover:bg-slate-700' : 'bg-amber-600 hover:bg-amber-700'}
                    data-testid="btn-confirm-restore">
              {restoreDryRun ? 'Simuler' : 'Restaurer pour de vrai'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
