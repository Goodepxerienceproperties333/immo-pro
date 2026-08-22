import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog';
import { Landmark, ExternalLink, Loader2, RefreshCw, CheckCircle2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import api from '@/lib/api';

/**
 * iter94h + iter94j : Modal de connexion bancaire via Enable Banking (PSD2).
 * - Sprint 1 : connexion banque + retour callback
 * - Sprint 2 : liste des sessions actives + bouton "Sync now"
 */
export default function OpenBankingConnectDialog({ open, onOpenChange, coproId, onSynced }) {
  const [loading, setLoading] = useState(false);
  const [aspsps, setAspsps] = useState([]);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        setLoading(true);
        const [statusRes, aspspsRes] = await Promise.all([
          api.get('/banking/enablebanking/status'),
          api.get('/banking/enablebanking/aspsps?country=BE'),
        ]);
        setStatus(statusRes.data);
        setAspsps(aspspsRes.data.aspsps || []);
        // iter94j : charge sessions actives pour cette ACP
        if (coproId) {
          try {
            const sessionsRes = await api.get(
              `/banking/enablebanking/sessions?copropriete_id=${coproId}`,
            );
            setSessions(sessionsRes.data.sessions || []);
          } catch { /* silent */ }
        }
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Impossible de charger les banques');
      } finally {
        setLoading(false);
      }
    })();
  }, [open, coproId]);

  const syncNow = async () => {
    if (!coproId) return;
    try {
      setSyncing(true);
      const { data } = await api.post(
        `/banking/enablebanking/sync-now?copropriete_id=${coproId}&days_back=30`,
      );
      if (data.sessions === 0) {
        toast.info(data.message || 'Aucune session active');
      } else {
        toast.success(
          `Sync OK : ${data.inserted} nouvelles, `
          + `${data.enriched_from_coda} enrichies (CODA existant), `
          + `${data.skipped_duplicate} doublons ignores`
        );
        if (onSynced) onSynced();
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec de la synchro');
    } finally {
      setSyncing(false);
    }
  };

  // iter94m : revoquer une session (unlink) avec confirmation double
  const revokeSession = async (session) => {
    const bank = session.aspsp_name || 'cette banque';
    const first = window.confirm(
      `Revoquer l'acces a ${bank} ?\n\n`
      + `Le consentement PSD2 sera annule cote banque. `
      + `Les transactions deja importees seront conservees (audit trail).`
    );
    if (!first) return;
    const alsoDelete = window.confirm(
      `Voulez-vous aussi SUPPRIMER les transactions Open Banking `
      + `deja importees depuis ${bank} ?\n\n`
      + `OK = supprime les transactions. Annuler = conserve l'historique.`
    );
    try {
      const { data } = await api.delete(
        `/banking/enablebanking/sessions/${session.session_id}`
        + `?delete_transactions=${alsoDelete ? 'true' : 'false'}`,
      );
      const msg = alsoDelete
        ? `Acces revoque et ${data.deleted_transactions} transaction(s) supprimee(s)`
        : 'Acces revoque, transactions conservees';
      toast.success(msg);
      // Refresh sessions list
      setSessions(sessions.filter(s => s.session_id !== session.session_id));
      if (onSynced) onSynced();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec de la revocation');
    }
  };

  const connect = async () => {
    if (!selected || !coproId) return;
    try {
      setLoading(true);
      const { data } = await api.post('/banking/enablebanking/authorize/start', {
        aspsp_name: selected,
        aspsp_country: 'BE',
        copropriete_id: coproId,
      });
      if (data.url) {
        toast.info('Redirection vers la banque...');
        window.location.assign(data.url);
      } else {
        toast.error("Pas d'URL de redirection retournee");
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec de la connexion');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="openbanking-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Landmark className="h-5 w-5 text-[#022D52]" />
            Connecter un compte bancaire
          </DialogTitle>
          <DialogDescription>
            Synchronisation automatique des mouvements via Open Banking (PSD2).
            Le consentement est valable 90 jours et devra etre renouvele.
          </DialogDescription>
        </DialogHeader>

        {loading && aspsps.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> Chargement des banques...
          </div>
        ) : (
          <div className="space-y-4">
            {/* iter94j : Sessions actives + bouton Sync now */}
            {/* iter94k : Alerte expiration 15j + bouton Renouveler */}
            {sessions.length > 0 && (
              <div className="rounded border border-emerald-200 bg-emerald-50 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-semibold text-emerald-800 flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4" />
                    Comptes deja connectes ({sessions.length})
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={syncNow}
                    disabled={syncing}
                    className="bg-white hover:bg-emerald-100 text-emerald-700 border-emerald-300"
                    data-testid="openbanking-sync-now-btn"
                  >
                    {syncing
                      ? <Loader2 className="h-3 w-3 animate-spin mr-1" />
                      : <RefreshCw className="h-3 w-3 mr-1" />}
                    Synchroniser maintenant
                  </Button>
                </div>
                <div className="space-y-1 text-xs text-emerald-900">
                  {sessions.map((s) => {
                    const needsRenew = s.renewal_needed || s.expired;
                    return (
                      <div key={s.session_id}
                           className={`flex flex-col gap-1 rounded px-2 py-1 ${
                             s.expired ? 'bg-red-100 border border-red-300' :
                             s.renewal_needed ? 'bg-amber-100 border border-amber-300' :
                             ''
                           }`}>
                        <div className="flex justify-between items-center">
                          <span>
                            <b>{s.aspsp_name}</b> — {(s.accounts || []).length} compte(s)
                          </span>
                          <span className={
                            s.expired ? 'text-red-700 font-semibold' :
                            s.renewal_needed ? 'text-amber-700 font-semibold' :
                            'text-emerald-600'
                          }>
                            {s.expired ? 'CONSENTEMENT EXPIRE' :
                             s.days_until_expiration != null
                               ? `expire dans ${Math.max(0, Math.round(s.days_until_expiration))}j`
                               : ''}
                          </span>
                        </div>
                        <div className="flex justify-between items-center">
                          <span className="text-slate-500">
                            {s.last_sync_at
                              ? `sync: ${s.last_sync_at.slice(0, 16).replace('T', ' ')}`
                              : 'jamais synchro'}
                          </span>
                          <div className="flex gap-1">
                            {needsRenew && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={async () => {
                                  try {
                                    const { data } = await api.post(
                                      '/banking/enablebanking/sessions/renew',
                                      { session_id: s.session_id },
                                    );
                                    if (data.url) {
                                      toast.info('Redirection vers la banque...');
                                      window.location.assign(data.url);
                                    }
                                  } catch (err) {
                                    toast.error(err.response?.data?.detail
                                                || 'Echec du renouvellement');
                                  }
                                }}
                                className={`h-6 text-xs ${
                                  s.expired
                                    ? 'bg-red-600 hover:bg-red-700 text-white border-red-700'
                                    : 'bg-amber-500 hover:bg-amber-600 text-white border-amber-600'
                                }`}
                                data-testid={`renew-session-${s.session_id}`}
                              >
                                <RefreshCw className="h-3 w-3 mr-1" />
                                Renouveler
                              </Button>
                            )}
                            {/* iter94m : revoker l'acces */}
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => revokeSession(s)}
                              className="h-6 text-xs text-red-600 hover:bg-red-100 hover:text-red-700"
                              title="Revoquer le consentement bancaire"
                              data-testid={`revoke-session-${s.session_id}`}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {status && !status.configured && (
              <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
                Enable Banking n&apos;est pas configure cote serveur
                (credentials manquants).
              </div>
            )}
            {status && status.configured && aspsps.length === 0 && (
              <div className="rounded border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                Aucune banque disponible pour la Belgique actuellement.
              </div>
            )}
            {aspsps.length > 0 && (
              <>
                <label className="block text-sm font-medium text-slate-700">
                  Choisissez votre banque
                </label>
                <div className="max-h-72 overflow-y-auto space-y-1 border rounded p-2 bg-slate-50">
                  {aspsps.map((a) => (
                    <button
                      key={a.name}
                      onClick={() => setSelected(a.name)}
                      className={`w-full text-left px-3 py-2 rounded flex items-center justify-between transition
                        ${selected === a.name
                          ? 'bg-[#022D52] text-white'
                          : 'bg-white hover:bg-slate-100 text-slate-700'}`}
                      data-testid={`aspsp-${a.name.toLowerCase().replace(/\s+/g, '-')}`}
                    >
                      <div className="flex items-center gap-2">
                        {a.logo && (
                          <img src={a.logo} alt="" className="h-6 w-6 rounded" />
                        )}
                        <span className="font-medium">{a.name}</span>
                        {a.beta && (
                          <span className="text-xs px-2 py-0.5 bg-orange-100 text-orange-700 rounded">
                            Beta
                          </span>
                        )}
                      </div>
                      {selected === a.name && (
                        <ExternalLink className="h-4 w-4" />
                      )}
                    </button>
                  ))}
                </div>
                <div className="text-xs text-slate-500 leading-relaxed">
                  Apres clic sur &quot;Se connecter&quot;, vous serez redirige sur le site
                  securise de votre banque pour autoriser l&apos;acces. Vos
                  identifiants ne transitent jamais par NextGe.
                </div>
              </>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="openbanking-cancel-btn"
          >
            Annuler
          </Button>
          <Button
            onClick={connect}
            disabled={!selected || loading || !coproId}
            className="bg-[#022D52] hover:bg-[#1D4ED8]"
            data-testid="openbanking-connect-btn"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            Se connecter
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
