import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollText, Shield, ExternalLink, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '@/contexts/AuthContext';

/**
 * Modal bloquant affiche a la connexion tant que l'utilisateur n'a pas
 * accepte la version en vigueur des CGU / Politique de Confidentialite.
 */
export default function LegalAcceptanceModal() {
  const { user } = useAuth();
  const [needs, setNeeds] = useState(false);
  const [versions, setVersions] = useState({ cgu_version: 1, privacy_version: 1 });
  const [acceptedCgu, setAcceptedCgu] = useState(false);
  const [acceptedPrivacy, setAcceptedPrivacy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) { setNeeds(false); setLoading(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get('/legal/my-acceptance');
        if (cancelled) return;
        setNeeds(!!r.data.needs_accept);
        setVersions(r.data.current_versions || { cgu_version: 1, privacy_version: 1 });
      } catch (e) {
        // fail-open (ne pas bloquer l'app en cas d'erreur reseau)
        if (!cancelled) setNeeds(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user]);

  const submit = async () => {
    if (!acceptedCgu || !acceptedPrivacy) {
      toast.error('Vous devez accepter les deux documents pour continuer.');
      return;
    }
    setSaving(true);
    try {
      await api.post('/legal/accept', {
        cgu_version: versions.cgu_version,
        privacy_version: versions.privacy_version,
      });
      setNeeds(false);
      toast.success('Merci ! Conditions acceptees.');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Impossible d\'enregistrer votre acceptation.');
    } finally {
      setSaving(false);
    }
  };

  if (loading || !needs) return null;

  return (
    <div
      data-testid="legal-acceptance-modal"
      className="fixed inset-0 bg-slate-900/70 backdrop-blur-sm z-[10000] flex items-center justify-center p-4"
      // iter90bo : pointer-events: auto force en inline pour bypass tout
      // "pointer-events: none" pose par un Radix Dialog concurrent ouvert
      // en meme temps (ex: OnboardingDialog) qui desactive les clics sur
      // <body>. Sans ca, l'user ne peut PAS cliquer les checkboxes ou le
      // bouton "J'accepte" -> bug prod signale.
      style={{ fontFamily: 'Inter, system-ui, sans-serif', pointerEvents: 'auto' }}
    >
      <div className="bg-white rounded-lg shadow-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 bg-gradient-to-r from-[#022D52]/5 to-slate-50">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-[#022D52]/10 flex items-center justify-center">
              <ScrollText size={20} className="text-[#022D52]" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-900" style={{ fontFamily: 'Chivo, sans-serif' }}>
                Mise a jour des conditions
              </h2>
              <p className="text-xs text-slate-500">
                Nous avons besoin de votre acceptation pour continuer.
              </p>
            </div>
          </div>
        </div>

        <div className="px-6 py-5 space-y-4 overflow-auto flex-1">
          <p className="text-sm text-slate-700 leading-relaxed">
            Pour continuer a utiliser NextGe Copro, veuillez prendre connaissance et accepter les
            documents suivants (version en vigueur). Vous pouvez les consulter en integralite en
            cliquant sur les liens ci-dessous.
          </p>

          <div className="border border-slate-200 rounded-lg p-4 flex items-start gap-3 hover:border-[#022D52]/40 transition-colors">
            <Checkbox
              id="acc-cgu"
              checked={acceptedCgu}
              onCheckedChange={(v) => setAcceptedCgu(!!v)}
              data-testid="legal-accept-cgu"
              className="mt-0.5"
            />
            <label htmlFor="acc-cgu" className="flex-1 cursor-pointer">
              <div className="flex items-center gap-2">
                <ScrollText size={14} className="text-slate-500" />
                <span className="text-sm font-medium text-slate-800">
                  J&apos;accepte les <Link to="/legal/cgu" target="_blank" className="text-[#022D52] hover:underline inline-flex items-center gap-0.5">Conditions Generales d&apos;Utilisation <ExternalLink size={11} /></Link>
                </span>
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5 ml-6">
                Version {versions.cgu_version} — regit l&apos;utilisation de la plateforme.
              </div>
            </label>
          </div>

          <div className="border border-slate-200 rounded-lg p-4 flex items-start gap-3 hover:border-[#022D52]/40 transition-colors">
            <Checkbox
              id="acc-privacy"
              checked={acceptedPrivacy}
              onCheckedChange={(v) => setAcceptedPrivacy(!!v)}
              data-testid="legal-accept-privacy"
              className="mt-0.5"
            />
            <label htmlFor="acc-privacy" className="flex-1 cursor-pointer">
              <div className="flex items-center gap-2">
                <Shield size={14} className="text-slate-500" />
                <span className="text-sm font-medium text-slate-800">
                  J&apos;ai lu la <Link to="/legal/privacy" target="_blank" className="text-[#022D52] hover:underline inline-flex items-center gap-0.5">Politique de Confidentialite (RGPD) <ExternalLink size={11} /></Link>
                </span>
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5 ml-6">
                Version {versions.privacy_version} — explique comment vos donnees sont traitees.
              </div>
            </label>
          </div>

          <div className="bg-amber-50 border border-amber-200 rounded-md px-3 py-2 text-[12px] text-amber-800 leading-relaxed">
            En cliquant sur <strong>&laquo; J&apos;accepte et je continue &raquo;</strong>, vous confirmez avoir pris
            connaissance des documents. Un journal d&apos;acceptation (date, IP, navigateur) est
            conserve a titre de preuve conformement au RGPD.
          </div>
        </div>

        <div className="px-6 py-4 border-t border-slate-200 bg-slate-50 flex items-center justify-end gap-2">
          <Button
            onClick={submit}
            disabled={saving || !acceptedCgu || !acceptedPrivacy}
            className="bg-[#022D52] hover:bg-[#1D4ED8] text-white"
            data-testid="legal-accept-submit-btn"
          >
            {saving ? <><Loader2 size={14} className="animate-spin mr-2" /> Enregistrement...</> : 'J\'accepte et je continue'}
          </Button>
        </div>
      </div>
    </div>
  );
}
