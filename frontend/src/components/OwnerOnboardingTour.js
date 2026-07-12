// iter90dh (Feb 2026) : Tour guide multi-etapes affiche a la premiere
// connexion du proprietaire. 5 etapes (accueil + 4 sections principales)
// avec insistance sur la RESPONSABILITE du proprietaire de maintenir ses
// donnees a jour et les locataires (le syndic est automatiquement notifie).
import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Home, Wallet, FileText, User, Users, ArrowRight, ArrowLeft,
  CheckCircle2, AlertCircle, Sparkles, Info, Mail,
} from 'lucide-react';

// Cle localStorage : versionnee pour re-forcer le tour en cas d'update majeure
const TOUR_STORAGE_KEY_PREFIX = 'owner_tour_completed_v1_';

export function ownerTourStorageKey(email) {
  return `${TOUR_STORAGE_KEY_PREFIX}${(email || '').toLowerCase().trim()}`;
}

// Chaque etape : {icon, iconColor, title, subtitle, body, ...cta}
const STEPS = [
  {
    icon: Sparkles,
    iconColor: 'text-[#022D52]',
    iconBg: 'bg-blue-50',
    title: 'Bienvenue dans votre espace proprietaire',
    subtitle: 'Un aperçu en 5 etapes',
    body: (
      <div className="space-y-3 text-slate-700">
        <p>
          Cet espace vous permet de suivre en toute autonomie votre <b>copropriete</b>,
          vos <b>appels de fonds</b>, vos <b>documents</b> et vos <b>locataires</b>.
        </p>
        <p>
          Le guide qui suit vous presente les principales sections. Vous pourrez
          le revoir a tout moment depuis votre profil.
        </p>
        <div className="rounded-md bg-blue-50 border border-blue-200 p-3 text-sm">
          <div className="flex items-center gap-2 text-blue-800 font-semibold mb-1">
            <Info size={14} /> Un principe cle
          </div>
          <p className="text-blue-900 text-[13px]">
            <b>Vous etes responsable</b> de la mise a jour de vos coordonnees et
            des informations sur vos locataires. Chaque modification declenche
            automatiquement une <b>notification au syndic</b> — pas besoin de mail
            supplementaire.
          </p>
        </div>
      </div>
    ),
  },
  {
    icon: Wallet,
    iconColor: 'text-emerald-600',
    iconBg: 'bg-emerald-50',
    title: 'Votre compte & vos appels de fonds',
    subtitle: 'La partie comptable',
    body: (
      <div className="space-y-3 text-slate-700 text-[14px]">
        <p>
          L’onglet <b>Ma situation</b> vous donne une vue immediate de votre solde :
        </p>
        <ul className="list-disc list-inside space-y-1 text-[13px]">
          <li><b>Solde</b> avec code couleur (rouge = debiteur, vert = crediteur)</li>
          <li><b>Prochain paiement</b> et <b>compte a rebours</b> jusqu’a l’echeance</li>
          <li><b>Repartition</b> de vos charges par categorie sur 12 mois</li>
        </ul>
        <p>
          L’onglet <b>Appels de fonds</b> affiche tous les mouvements du grand livre
          de votre compte : appels, paiements, corrections, mutations. Vous pouvez
          filtrer par periode.
        </p>
        <div className="rounded-md bg-emerald-50 border border-emerald-200 p-3 text-[13px]">
          <div className="flex items-center gap-2 text-emerald-800 font-semibold mb-1">
            <CheckCircle2 size={14} /> Bon a savoir
          </div>
          <p className="text-emerald-900">
            Le solde affiche est <b>aligne avec la comptabilite officielle</b> tenue
            par votre syndic (balance de tiers).
          </p>
        </div>
      </div>
    ),
  },
  {
    icon: FileText,
    iconColor: 'text-violet-600',
    iconBg: 'bg-violet-50',
    title: 'Documents & communications',
    subtitle: 'Tout est archive ici',
    body: (
      <div className="space-y-3 text-slate-700 text-[14px]">
        <p>
          Le syndic partage avec vous les documents utiles :
          reglements, PV d&apos;assemblee, factures, contrats, decomptes annuels, etc.
        </p>
        <ul className="list-disc list-inside space-y-1 text-[13px]">
          <li>
            <b>Documents</b> : classes par categorie avec <b>codes couleur</b>
            pour retrouver rapidement ce que vous cherchez
          </li>
          <li>
            <b>Communications</b> : historique des emails que le syndic vous
            a envoyes (situation de compte, decompte annuel, convocation AG,
            informations diverses) avec le contenu integral consultable
          </li>
        </ul>
        <div className="rounded-md bg-violet-50 border border-violet-200 p-3 text-[13px]">
          <div className="flex items-center gap-2 text-violet-800 font-semibold mb-1">
            <Mail size={14} /> Astuce
          </div>
          <p className="text-violet-900">
            Cliquez sur une communication pour lire le message complet, y compris
            les pieces jointes envoyees.
          </p>
        </div>
      </div>
    ),
  },
  {
    icon: User,
    iconColor: 'text-amber-600',
    iconBg: 'bg-amber-50',
    title: 'Vos donnees personnelles',
    subtitle: 'Votre responsabilite',
    highlight: 'responsibility',
    body: (
      <div className="space-y-3 text-slate-700 text-[14px]">
        <p>
          L’onglet <b>Mon profil</b> vous permet de tenir a jour vos coordonnees :
          nom complet, adresses email, GSM, telephone fixe, adresse postale,
          IBAN pour les remboursements eventuels.
        </p>
        <div className="rounded-md bg-amber-50 border-2 border-amber-300 p-3 text-[13px]">
          <div className="flex items-center gap-2 text-amber-900 font-semibold mb-1">
            <AlertCircle size={16} /> Votre responsabilite
          </div>
          <div className="text-amber-900 space-y-1.5">
            <p>
              <b>Vous devez maintenir ces informations a jour.</b> Le syndic ne peut
              pas deviner un changement d&apos;email, de numero ou d&apos;adresse.
            </p>
            <p>
              Des que vous enregistrez une modification, le syndic est
              <b> automatiquement informe</b> par email — pas besoin de doublon.
            </p>
          </div>
        </div>
        <div className="rounded-md bg-slate-50 border border-slate-200 p-3 text-[12px] italic text-slate-600">
          Info pratique : vos donnees ne sont visibles que par vous et le syndic
          de votre copropriete. Aucun autre proprietaire n&apos;y a acces.
        </div>
      </div>
    ),
  },
  {
    icon: Users,
    iconColor: 'text-rose-600',
    iconBg: 'bg-rose-50',
    title: 'Vos locataires',
    subtitle: 'Votre responsabilite',
    highlight: 'responsibility',
    body: (
      <div className="space-y-3 text-slate-700 text-[14px]">
        <p>
          Si vous louez un ou plusieurs de vos lots, l’onglet <b>Mes locataires</b>
          {' '}vous permet d&apos;enregistrer chaque bail : nom, contact, dates,
          <b> noms a mettre sur la boite aux lettres et la sonnette</b>.
          Un meme locataire peut occuper plusieurs lots.
        </p>
        <div className="rounded-md bg-rose-50 border-2 border-rose-300 p-3 text-[13px]">
          <div className="flex items-center gap-2 text-rose-900 font-semibold mb-1">
            <AlertCircle size={16} /> Votre responsabilite
          </div>
          <div className="text-rose-900 space-y-1.5">
            <p>
              <b>Tenez a jour vos locataires</b> pour que le syndic puisse contacter
              les bonnes personnes en cas de sinistre, d&apos;entretien ou de coursier.
            </p>
            <p>
              Chaque <b>ajout</b>, <b>modification</b> ou <b>suppression</b> declenche
              automatiquement une <b>notification au syndic</b>.
            </p>
          </div>
        </div>
        <div className="rounded-md bg-slate-50 border border-slate-200 p-3 text-[12px] text-slate-700">
          <b>Astuce concierges & coursiers :</b> le champ &laquo; noms boite/sonnette &raquo;
          est particulierement utile aux prestataires et services de secours pour
          localiser rapidement le bon appartement.
        </div>
      </div>
    ),
  },
];

export default function OwnerOnboardingTour({ open, onClose, onFinish }) {
  const [step, setStep] = useState(0);
  const total = STEPS.length;
  const current = STEPS[step];
  const IconComp = current.icon;
  const isLast = step === total - 1;

  const handleFinish = () => {
    onFinish?.();
    setStep(0); // reset for next open
    onClose?.();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleFinish(); }}>
      <DialogContent className="max-w-2xl w-[95vw] p-0 overflow-hidden" data-testid="owner-tour-dialog">
        {/* Header colore */}
        <div className={`${current.iconBg} px-6 py-5 border-b border-slate-200`}>
          <DialogHeader className="space-y-1">
            <div className="flex items-center gap-3">
              <div className={`w-12 h-12 rounded-xl ${current.iconBg} border-2 border-white shadow-sm flex items-center justify-center flex-shrink-0`}>
                <IconComp size={24} className={current.iconColor} />
              </div>
              <div className="flex-1">
                <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="text-lg text-slate-900">
                  {current.title}
                </DialogTitle>
                <div className="text-[12px] text-slate-500 flex items-center gap-2">
                  <Badge variant="outline" className={`text-[10px] ${current.iconBg} ${current.iconColor} border-current`}>
                    Etape {step + 1} / {total}
                  </Badge>
                  <span>{current.subtitle}</span>
                </div>
              </div>
            </div>
          </DialogHeader>

          {/* Progress bar */}
          <div className="mt-4 h-1.5 bg-white/60 rounded-full overflow-hidden">
            <div
              className="h-full bg-[#022D52] transition-all duration-300"
              style={{ width: `${((step + 1) / total) * 100}%` }}
            />
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5 max-h-[55vh] overflow-y-auto">
          {current.body}
        </div>

        {/* Footer navigation */}
        <div className="px-6 py-4 border-t border-slate-100 bg-slate-50/50 flex items-center justify-between">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setStep(Math.max(0, step - 1))}
            disabled={step === 0}
            data-testid="owner-tour-prev-btn"
            className="text-slate-600"
          >
            <ArrowLeft size={14} className="mr-1.5" /> Precedent
          </Button>

          {/* Dots indicators */}
          <div className="flex items-center gap-1.5">
            {STEPS.map((_, i) => (
              <button
                key={i}
                onClick={() => setStep(i)}
                className={`w-2 h-2 rounded-full transition-all ${i === step ? 'bg-[#022D52] w-6' : 'bg-slate-300 hover:bg-slate-400'}`}
                data-testid={`owner-tour-dot-${i}`}
                aria-label={`Aller a l'etape ${i + 1}`}
              />
            ))}
          </div>

          {isLast ? (
            <Button
              size="sm"
              onClick={handleFinish}
              className="bg-[#022D52] hover:bg-[#1D4ED8]"
              data-testid="owner-tour-finish-btn"
            >
              <Home size={14} className="mr-1.5" /> Commencer !
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => setStep(Math.min(total - 1, step + 1))}
              className="bg-[#022D52] hover:bg-[#1D4ED8]"
              data-testid="owner-tour-next-btn"
            >
              Suivant <ArrowRight size={14} className="ml-1.5" />
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
