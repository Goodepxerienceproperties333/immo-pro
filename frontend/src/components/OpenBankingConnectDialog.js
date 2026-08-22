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
import { Landmark, ExternalLink, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import api from '@/lib/api';

/**
 * iter94h : Modal de connexion bancaire via Enable Banking (PSD2 Belgique).
 * Flow :
 * 1. GET /aspsps?country=BE => liste des banques
 * 2. User choisit une banque
 * 3. POST /authorize/start => URL banque + state
 * 4. window.location.assign(url) => user redirigé vers la banque
 * 5. Après consentement, retour /callback -> /banking?openbanking_success=1
 */
export default function OpenBankingConnectDialog({ open, onOpenChange, coproId }) {
  const [loading, setLoading] = useState(false);
  const [aspsps, setAspsps] = useState([]);
  const [selected, setSelected] = useState('');
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        setLoading(true);
        const [statusRes, aspspsRes] = await Promise.all([
          api.get('/banking/openbanking/status'),
          api.get('/banking/openbanking/aspsps?country=BE'),
        ]);
        setStatus(statusRes.data);
        setAspsps(aspspsRes.data.aspsps || []);
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Impossible de charger les banques');
      } finally {
        setLoading(false);
      }
    })();
  }, [open]);

  const connect = async () => {
    if (!selected || !coproId) return;
    try {
      setLoading(true);
      const { data } = await api.post('/banking/openbanking/authorize/start', {
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
