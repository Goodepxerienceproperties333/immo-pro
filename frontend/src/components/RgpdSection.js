import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Download, Trash2, ShieldCheck, Loader2, Undo2 } from 'lucide-react';
import { toast } from 'sonner';

/**
 * Section RGPD dans le profil utilisateur.
 * - Export art.20 (portabilite) : telecharge un JSON de toutes les donnees
 * - Suppression art.17 (droit a l'oubli) : marque le compte pour purge sous 30 jours
 * - Statut de suppression pending + bouton "Annuler la demande"
 */
export default function RgpdSection() {
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [deletionPending, setDeletionPending] = useState(null);
  const [openDialog, setOpenDialog] = useState(false);

  useEffect(() => {
    // check if a deletion is already pending
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get('/auth/me');
        if (cancelled) return;
        if (r.data?.deletion_requested_at) {
          setDeletionPending({
            requested_at: r.data.deletion_requested_at,
            purge_at_ts: r.data.deletion_purge_at_ts,
          });
        }
      } catch {}
    })();
    return () => { cancelled = true; };
  }, []);

  const exportData = async () => {
    setExporting(true);
    try {
      const r = await api.post('/legal/rgpd/export', {}, { responseType: 'blob' });
      const blob = new Blob([r.data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const now = new Date().toISOString().slice(0, 10);
      a.download = `copromanager-export-${now}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success('Export RGPD telecharge');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors de l\'export');
    } finally {
      setExporting(false);
    }
  };

  const requestDelete = async () => {
    if (confirmText !== 'SUPPRIMER MON COMPTE') {
      toast.error('Saisissez exactement : SUPPRIMER MON COMPTE');
      return;
    }
    setDeleting(true);
    try {
      const r = await api.post('/legal/rgpd/delete-account', { confirm: confirmText });
      toast.success(r.data?.message || 'Demande enregistree');
      // Reload deletion pending status
      setDeletionPending({ requested_at: new Date().toISOString() });
      setOpenDialog(false);
      setConfirmText('');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors de la demande de suppression');
    } finally {
      setDeleting(false);
    }
  };

  const cancelDelete = async () => {
    setCancelling(true);
    try {
      await api.post('/legal/rgpd/cancel-deletion', {});
      toast.success('Demande de suppression annulee');
      setDeletionPending(null);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors de l\'annulation');
    } finally {
      setCancelling(false);
    }
  };

  return (
    <Card className="border-slate-200" data-testid="rgpd-section">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
          <ShieldCheck size={16} className="text-slate-500" />
          Mes donnees (RGPD)
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="text-xs text-slate-600 leading-relaxed">
          Conformement au Reglement General sur la Protection des Donnees (RGPD),
          vous disposez de plusieurs droits sur vos donnees personnelles :
          <ul className="list-disc pl-5 mt-1 space-y-0.5">
            <li><strong>Portabilite</strong> (art. 20) : recuperez toutes vos donnees dans un fichier lisible.</li>
            <li><strong>Effacement</strong> (art. 17) : demandez la suppression definitive de votre compte.</li>
          </ul>
          <div className="mt-2 text-[11px] italic text-slate-500">
            Les donnees comptables (factures, journaux, PCMN) sont conservees 7 ans pour obligations
            legales (art. III.86 CDE) meme apres suppression du compte utilisateur.
          </div>
        </div>

        {/* Export */}
        <div className="border border-slate-200 rounded-lg p-3 flex items-center justify-between gap-3">
          <div className="flex-1">
            <div className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
              <Download size={13} /> Exporter mes donnees
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">
              Telecharge un fichier JSON contenant votre profil, historique de conversations
              support, journal d&apos;audit et donnees liees.
            </div>
          </div>
          <Button
            onClick={exportData}
            disabled={exporting}
            variant="outline"
            size="sm"
            data-testid="rgpd-export-btn"
            className="border-[#0055FF]/30 text-[#0055FF] hover:bg-[#0055FF]/5"
          >
            {exporting ? <><Loader2 size={13} className="animate-spin mr-1.5" /> Export...</> : <><Download size={13} className="mr-1.5" /> Exporter</>}
          </Button>
        </div>

        {/* Delete or Cancel */}
        {deletionPending ? (
          <div className="border border-amber-300 bg-amber-50 rounded-lg p-3 space-y-2" data-testid="rgpd-deletion-pending">
            <div className="text-sm font-semibold text-amber-900 flex items-center gap-1.5">
              <Trash2 size={13} /> Demande de suppression en cours
            </div>
            <div className="text-[11px] text-amber-800">
              Enregistree le {deletionPending.requested_at ? new Date(deletionPending.requested_at).toLocaleString('fr-BE') : '—'}.
              Votre compte sera supprime dans 30 jours. Vous pouvez encore annuler.
            </div>
            <Button
              onClick={cancelDelete}
              disabled={cancelling}
              size="sm"
              variant="outline"
              className="border-amber-400 text-amber-900 hover:bg-amber-100"
              data-testid="rgpd-cancel-delete-btn"
            >
              {cancelling ? <><Loader2 size={13} className="animate-spin mr-1.5" /> Annulation...</> : <><Undo2 size={13} className="mr-1.5" /> Annuler ma demande</>}
            </Button>
          </div>
        ) : (
          <div className="border border-red-200 rounded-lg p-3 flex items-center justify-between gap-3">
            <div className="flex-1">
              <div className="text-sm font-semibold text-red-800 flex items-center gap-1.5">
                <Trash2 size={13} /> Supprimer mon compte
              </div>
              <div className="text-[11px] text-red-600 mt-0.5">
                Action definitive apres 30 jours (delai d&apos;annulation). Votre acces sera coupe
                progressivement, les donnees comptables sont conservees legalement 7 ans.
              </div>
            </div>
            <AlertDialog open={openDialog} onOpenChange={setOpenDialog}>
              <AlertDialogTrigger asChild>
                <Button
                  variant="outline"
                  size="sm"
                  className="border-red-300 text-red-700 hover:bg-red-50"
                  data-testid="rgpd-delete-btn"
                >
                  <Trash2 size={13} className="mr-1.5" /> Supprimer
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent data-testid="rgpd-delete-dialog">
                <AlertDialogHeader>
                  <AlertDialogTitle>Confirmer la suppression du compte</AlertDialogTitle>
                  <AlertDialogDescription>
                    Cette action enregistre une demande de suppression. Votre compte sera supprime
                    definitivement dans 30 jours. Pour confirmer, saisissez exactement :{' '}
                    <strong className="font-mono">SUPPRIMER MON COMPTE</strong>
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <Input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder="SUPPRIMER MON COMPTE"
                  data-testid="rgpd-delete-confirm-input"
                  autoComplete="off"
                />
                <AlertDialogFooter>
                  <AlertDialogCancel data-testid="rgpd-delete-cancel-btn">Annuler</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={(e) => { e.preventDefault(); requestDelete(); }}
                    disabled={confirmText !== 'SUPPRIMER MON COMPTE' || deleting}
                    className="bg-red-600 hover:bg-red-700 text-white"
                    data-testid="rgpd-delete-confirm-btn"
                  >
                    {deleting ? 'Enregistrement...' : 'Supprimer definitivement'}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
