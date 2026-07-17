/**
 * iter90hb : Historique des envois email
 *
 * Affiche les 100 derniers envois de communication avec :
 * - Date/heure
 * - Destinataire(s)
 * - Sujet
 * - Statut (envoye / en erreur / dry-run) avec message d'erreur Microsoft
 * - Type (generic, situation, decompte, appel-fonds)
 *
 * Utilise l'endpoint GET /api/communication/sent-log?limit=100&only_failed=false
 * cree en iter90h8.
 *
 * Critique pour diagnostiquer les mails "acceptes par Graph (202)
 * mais jamais delivres" (permission Mail.Send manquante, boite sans
 * licence Exchange, etc.).
 */
import { useEffect, useState, useCallback } from 'react';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Send, RefreshCw, AlertTriangle, CheckCircle2, Filter, FileText, Search, Info, Loader2 } from 'lucide-react';

const KIND_LABEL = {
  generic: 'Message libre',
  situation: 'Situation compte',
  decompte: 'Decompte',
  'appel-fonds': 'Appel de fonds',
  invitation: 'Invitation',
};

function formatDate(iso) {
  if (!iso) return '-';
  return String(iso).replace('T', ' ').slice(0, 19);
}

function StatusBadge({ row }) {
  if (row.dry_run) {
    return <Badge className="bg-slate-100 text-slate-700 border border-slate-200" data-testid={`status-badge-${row._id}`}>Dry-run</Badge>;
  }
  if (row.status === 'sent') {
    return <Badge className="bg-emerald-100 text-emerald-700 border border-emerald-200" data-testid={`status-badge-${row._id}`}><CheckCircle2 className="h-3 w-3 mr-1 inline" />Envoye</Badge>;
  }
  if (row.status === 'failed') {
    return <Badge className="bg-red-100 text-red-800 border border-red-200" data-testid={`status-badge-${row._id}`}><AlertTriangle className="h-3 w-3 mr-1 inline" />Erreur</Badge>;
  }
  return <Badge className="bg-slate-100 text-slate-600">{row.status || '?'}</Badge>;
}

export default function CommunicationHistoryPage() {
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState({ sent: 0, failed: 0, dry_run: 0 });
  const [loading, setLoading] = useState(false);
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(100);
  const [detailRow, setDetailRow] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit, only_failed: onlyFailed };
      const r = await api.get('/communication/sent-log', { params });
      setRows(r.data?.rows || []);
      setCounts(r.data?.counts || { sent: 0, failed: 0, dry_run: 0 });
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setLoading(false);
    }
  }, [limit, onlyFailed]);

  useEffect(() => { load(); }, [load]);

  const filtered = rows.filter((r) => {
    if (!search) return true;
    const q = search.toLowerCase();
    const to = Array.isArray(r.to) ? r.to.join(',').toLowerCase() : (r.to || '').toLowerCase();
    return (
      (r.subject || '').toLowerCase().includes(q)
      || (r.from_mailbox || '').toLowerCase().includes(q)
      || to.includes(q)
    );
  });

  return (
    <div className="space-y-4" data-testid="communication-history-page">
      <header className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Send className="h-6 w-6 text-[#022D52]" />
            Historique des envois email
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            100 derniers envois via l&apos;onglet Communication. Diagnostiquer les
            envois &quot;acceptes par Microsoft mais jamais delivres&quot; (permission
            Mail.Send manquante, boite sans licence, throttling).
          </p>
        </div>
        <Button variant="ghost" onClick={load} data-testid="btn-refresh-history">
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </header>

      {/* Statistiques */}
      <div className="grid grid-cols-3 gap-3">
        <Card>
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500">Envoyes</div>
            <div className="text-2xl font-bold text-emerald-700 mt-1" data-testid="stats-sent">{counts.sent}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500">En erreur</div>
            <div className={`text-2xl font-bold mt-1 ${counts.failed > 0 ? 'text-red-700' : 'text-slate-500'}`}
                 data-testid="stats-failed">{counts.failed}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-xs uppercase tracking-wider text-slate-500">Dry-run</div>
            <div className="text-2xl font-bold text-slate-600 mt-1" data-testid="stats-dry-run">{counts.dry_run}</div>
          </CardContent>
        </Card>
      </div>

      {counts.dry_run > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3 flex items-start gap-2">
          <Info className="h-4 w-4 text-amber-700 mt-0.5 shrink-0" />
          <div className="text-xs text-amber-900">
            <strong>{counts.dry_run} envoi(s) en mode dry-run</strong> : ces mails
            n&apos;ont PAS ete envoyes reellement (config Microsoft manquante ou
            preview MAIL_ENABLED=false). Verifiez votre config dans{' '}
            <a href="/mon-bureau" className="underline">Mon bureau</a>.
          </div>
        </div>
      )}

      {/* Barre filtres */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative max-w-sm flex-1">
          <Search className="h-4 w-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <Input
            placeholder="Rechercher par sujet, boite, destinataire..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
            data-testid="history-search"
          />
        </div>
        <Button
          variant={onlyFailed ? 'default' : 'outline'}
          size="sm"
          onClick={() => setOnlyFailed((v) => !v)}
          className={onlyFailed ? 'bg-red-600 hover:bg-red-700' : ''}
          data-testid="history-filter-failed"
        >
          <Filter className="h-4 w-4 mr-1" />
          {onlyFailed ? 'Uniquement erreurs' : 'Filtrer les erreurs'}
        </Button>
        <select
          value={limit}
          onChange={(e) => setLimit(parseInt(e.target.value, 10))}
          className="text-sm border border-slate-300 rounded px-2 py-1"
          data-testid="history-limit"
        >
          <option value={50}>50 lignes</option>
          <option value={100}>100 lignes</option>
          <option value={200}>200 lignes</option>
          <option value={500}>500 lignes</option>
        </select>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-8 text-center text-slate-500 flex items-center justify-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Chargement...
            </div>
          ) : filtered.length === 0 ? (
            <div className="p-8 text-center text-slate-500">
              Aucun envoi dans l&apos;historique{search ? ` correspondant a "${search}"` : ''}.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-40">Date</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Boite d&apos;envoi</TableHead>
                  <TableHead>Destinataire(s)</TableHead>
                  <TableHead>Sujet</TableHead>
                  <TableHead className="w-28 text-center">Statut</TableHead>
                  <TableHead className="w-24 text-right">Detail</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((row) => {
                  const toStr = Array.isArray(row.to) ? row.to.join(', ') : String(row.to || '');
                  return (
                    <TableRow key={row._id} data-testid={`history-row-${row._id}`}>
                      <TableCell className="font-mono text-xs text-slate-700">{formatDate(row.sent_at)}</TableCell>
                      <TableCell>
                        <Badge className="bg-slate-100 text-slate-700 text-[10px]">
                          {KIND_LABEL[row.kind] || row.kind || '-'}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{row.from_mailbox}</TableCell>
                      <TableCell className="text-xs max-w-[220px] truncate" title={toStr}>{toStr}</TableCell>
                      <TableCell className="text-xs max-w-[280px] truncate" title={row.subject}>{row.subject || '-'}</TableCell>
                      <TableCell className="text-center"><StatusBadge row={row} /></TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDetailRow(row)}
                          data-testid={`history-detail-${row._id}`}
                        >
                          <FileText className="h-3.5 w-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Dialog detail */}
      <Dialog open={!!detailRow} onOpenChange={(open) => !open && setDetailRow(null)}>
        <DialogContent className="max-w-2xl" data-testid="history-detail-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-[#022D52]" />
              Detail de l&apos;envoi
            </DialogTitle>
          </DialogHeader>
          {detailRow && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs uppercase text-slate-500">Date/heure</div>
                  <div className="font-mono">{formatDate(detailRow.sent_at)}</div>
                </div>
                <div>
                  <div className="text-xs uppercase text-slate-500">Statut</div>
                  <div><StatusBadge row={detailRow} /></div>
                </div>
                <div>
                  <div className="text-xs uppercase text-slate-500">Type</div>
                  <div>{KIND_LABEL[detailRow.kind] || detailRow.kind}</div>
                </div>
                <div>
                  <div className="text-xs uppercase text-slate-500">Boite d&apos;envoi</div>
                  <div className="font-mono text-xs">{detailRow.from_mailbox}</div>
                </div>
              </div>
              <div>
                <div className="text-xs uppercase text-slate-500 mb-1">Destinataire(s)</div>
                <div className="font-mono text-xs bg-slate-50 rounded p-2 whitespace-pre-wrap">
                  {Array.isArray(detailRow.to) ? detailRow.to.join('\n') : String(detailRow.to || '')}
                </div>
              </div>
              <div>
                <div className="text-xs uppercase text-slate-500 mb-1">Sujet</div>
                <div className="text-sm">{detailRow.subject || '-'}</div>
              </div>
              {detailRow.status === 'failed' && detailRow.error_msg && (
                <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-900">
                  <div className="font-semibold mb-1 flex items-center gap-1">
                    <AlertTriangle className="h-4 w-4" /> Erreur Microsoft
                  </div>
                  <div className="whitespace-pre-wrap font-mono text-[11px]" data-testid="history-detail-error">
                    {detailRow.error_msg}
                  </div>
                </div>
              )}
              {detailRow.dry_run && (
                <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                  <strong>Mode dry-run :</strong> ce mail n&apos;a pas ete envoye reellement.
                  Configurez Microsoft Graph (ou SMTP) dans{' '}
                  <a href="/mon-bureau" className="underline">Mon bureau</a> puis testez a nouveau.
                </div>
              )}
              {detailRow.attachment_name && (
                <div>
                  <div className="text-xs uppercase text-slate-500">Piece jointe</div>
                  <div className="text-xs font-mono">{detailRow.attachment_name}</div>
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDetailRow(null)}>Fermer</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
