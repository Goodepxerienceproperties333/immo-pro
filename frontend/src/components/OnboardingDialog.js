import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import {
  Building2, Users, Megaphone, Calculator, Mail, ShieldCheck,
  Receipt, Landmark, ChevronRight, ChevronLeft, CheckCircle2, Sparkles, FileText, Wallet
} from 'lucide-react';

const SYNDIC_STEPS = [
  {
    icon: Sparkles,
    color: 'from-[#022D52] to-[#1D4ED8]',
    title: 'Bienvenue sur NextGe Copro',
    subtitle: 'Votre plateforme de gestion de copropriete selon le droit belge',
    body: (user) => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Bonjour <span className="font-semibold">{user?.name}</span>, ravis de vous accueillir !</p>
        <p>NextGe Copro respecte strictement le <b>droit belge sur la copropriete</b> (Code civil livre 3, titre 8) et la <b>comptabilite PCMN</b> obligatoire pour les syndics depuis 2018.</p>
        <p className="text-xs text-slate-500 italic">Ce guide rapide (2 minutes) vous montre les 5 etapes pour bien demarrer.</p>
      </div>
    ),
  },
  {
    icon: Building2,
    color: 'from-emerald-500 to-emerald-700',
    title: 'Etape 1 : Creez vos coproprietes (ACPs)',
    subtitle: 'Une fiche par immeuble que vous gerez',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Pour chaque ACP, vous saisissez :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li>Le nom, la BCE et l&apos;adresse</li>
          <li>Les comptes bancaires (compte courant + epargne)</li>
          <li>Les lots et leurs quotites</li>
          <li>Les proprietaires (avec creation de VCS automatique)</li>
        </ul>
        <p className="text-xs bg-emerald-50 border-l-4 border-emerald-400 p-2 text-emerald-900">
          <ShieldCheck size={12} className="inline mr-1" /> Vos ACPs ne sont visibles que par <b>vous</b> et votre equipe. Aucun autre syndic n&apos;a acces a vos donnees (chinese wall + RGPD).
        </p>
      </div>
    ),
  },
  {
    icon: Calculator,
    color: 'from-amber-500 to-orange-600',
    title: 'Etape 2 : Plan comptable PCMN et exercices',
    subtitle: 'La comptabilite est automatique mais auditable',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Chaque ACP dispose de son <b>plan comptable PCMN belge</b> (327 comptes officiels) entierement isole.</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li>Creez un exercice fiscal (12 mois)</li>
          <li>Definissez le budget approuve en AG</li>
          <li>Generez vos appels de fonds (mensuels, trimestriels, annuels)</li>
          <li>A la cloture : extournes auto + AN au 01/01/N+1</li>
        </ul>
        <p className="text-xs bg-amber-50 border-l-4 border-amber-400 p-2 text-amber-900">
          <FileText size={12} className="inline mr-1" /> Toutes les ecritures sont immutables : <b>contre-passation automatique</b> au lieu de suppression. Conforme aux normes d&apos;audit belge.
        </p>
      </div>
    ),
  },
  {
    icon: Receipt,
    color: 'from-blue-500 to-blue-700',
    title: 'Etape 3 : Facturation et paiements',
    subtitle: 'Saisie des factures fournisseurs + import bancaire CODA',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Une fois vos comptes prets :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li><b>Facturation</b> : saisie manuelle ou extraction PDF par IA</li>
          <li><b>Cles de repartition</b> : par tantiemes, surface, ou consommation reelle</li>
          <li><b>Banque</b> : import CODA + auto-lettrage par VCS</li>
          <li><b>Decompte annuel</b> : PDF detaille par lot, cle et nature (modele Finlead)</li>
        </ul>
        <p className="text-xs bg-blue-50 border-l-4 border-blue-400 p-2 text-blue-900">
          <Wallet size={12} className="inline mr-1" /> Repartition <b>occupant / proprietaire</b> automatique selon RD du 12/07/2024 sur les charges locatives.
        </p>
      </div>
    ),
  },
  {
    icon: Users,
    color: 'from-purple-500 to-purple-700',
    title: 'Etape 4 : Invitez votre equipe',
    subtitle: 'Vos gestionnaires acceedent a vos ACPs',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Depuis le menu <b>&laquo; Mon equipe &raquo;</b>, vous pouvez :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li>Inviter des gestionnaires (collegues, comptable, assistant)</li>
          <li>Attribuer un <b>profil</b> avec permissions pre-definies (Comptable, Assistant, etc.)</li>
          <li>Restreindre l&apos;acces a certaines ACPs seulement</li>
          <li>Ajuster les 33 permissions individuelles si besoin</li>
        </ul>
        <p className="text-xs bg-purple-50 border-l-4 border-purple-400 p-2 text-purple-900">
          <ShieldCheck size={12} className="inline mr-1" /> Vos gestionnaires ne voient JAMAIS les donnees des autres syndics. Chacun son perimetre.
        </p>
      </div>
    ),
  },
  {
    icon: CheckCircle2,
    color: 'from-green-500 to-emerald-600',
    title: 'Vous etes pret !',
    subtitle: 'Quelques raccourcis utiles',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Quelques pages que vous utiliserez souvent :</p>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="bg-slate-50 p-2 rounded border border-slate-200">
            <Building2 size={14} className="inline text-emerald-600 mr-1" /> <b>Coproprietes</b> : creer / gerer vos ACPs
          </div>
          <div className="bg-slate-50 p-2 rounded border border-slate-200">
            <Megaphone size={14} className="inline text-[#022D52] mr-1" /> <b>Appels de fonds</b> : trimestriels / annuels
          </div>
          <div className="bg-slate-50 p-2 rounded border border-slate-200">
            <Receipt size={14} className="inline text-amber-600 mr-1" /> <b>Facturation</b> : factures + IA d&apos;extraction
          </div>
          <div className="bg-slate-50 p-2 rounded border border-slate-200">
            <Landmark size={14} className="inline text-purple-600 mr-1" /> <b>Banque</b> : extraits + lettrage auto
          </div>
        </div>
        <p className="text-xs text-slate-500 italic mt-2">
          Besoin d&apos;aide ? Le bouton <Mail size={10} className="inline mx-0.5" /> contact est en bas de chaque page.
        </p>
      </div>
    ),
  },
];

const SUPERADMIN_STEPS = [
  {
    icon: Sparkles,
    color: 'from-amber-500 to-red-600',
    title: 'Bienvenue Super Administrateur',
    subtitle: 'Vous gerez la plateforme NextGe Copro',
    body: (user) => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Bonjour <span className="font-semibold">{user?.name}</span>. Vous etes <b>Super Administrateur</b> de la plateforme.</p>
        <p>Votre role : creer les comptes <b>syndic principaux</b>. Chaque syndic gere ensuite ses propres ACPs et son equipe en autonomie.</p>
      </div>
    ),
  },
  {
    icon: Users,
    color: 'from-blue-500 to-blue-700',
    title: 'Creer un compte syndic',
    subtitle: 'Page &laquo; Comptes syndic &raquo; dans le header',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Pour ajouter un nouveau syndic :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li>Header &gt; <b>Utilisateurs</b> (en haut a droite)</li>
          <li>Bouton <b>Nouveau syndic</b></li>
          <li>Saisissez email + nom (le mot de passe sera defini par le syndic a sa 1ere connexion)</li>
        </ul>
        <p className="text-xs bg-blue-50 border-l-4 border-blue-400 p-2 text-blue-900">
          Vous n&apos;attribuez <b>aucune ACP</b> au syndic : il les creera lui-meme.
        </p>
      </div>
    ),
  },
  {
    icon: ShieldCheck,
    color: 'from-emerald-500 to-emerald-700',
    title: 'Vos outils plateforme',
    subtitle: 'Tableau de bord superadmin',
    body: () => (
      <div className="space-y-3 text-sm text-slate-700">
        <p>Via le bouton <b>Admin</b> du header :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li><b>Vue d&apos;ensemble</b> : volumetrie par syndic (ACPs, lots, owners) pour facturation</li>
          <li><b>Audit Log</b> : trace des operations critiques</li>
          <li><b>Deverrouillage</b> : ouvrir une ecriture verrouillee fiscalement</li>
          <li><b>Profils permissions</b> : catalogue des templates syndic / gestionnaire</li>
        </ul>
        <p className="text-xs bg-emerald-50 border-l-4 border-emerald-400 p-2 text-emerald-900">
          <ShieldCheck size={12} className="inline mr-1" /> Vous ne voyez <b>jamais</b> les donnees comptables des ACPs des syndics. Vous voyez uniquement les volumes pour facturer la plateforme.
        </p>
      </div>
    ),
  },
  {
    icon: CheckCircle2,
    color: 'from-green-500 to-emerald-600',
    title: 'Tout est pret',
    subtitle: 'Vous pouvez commencer',
    body: () => (
      <div className="space-y-2 text-sm text-slate-700">
        <p>Les syndics geront leurs ACPs en autonomie. Vous restez disponible pour :</p>
        <ul className="list-disc list-inside space-y-1 text-slate-600">
          <li>Onboarder de nouveaux syndics</li>
          <li>Resoudre les incidents (deverrouillage, support)</li>
          <li>Facturer la plateforme via la vue plateforme</li>
        </ul>
      </div>
    ),
  },
];

export default function OnboardingDialog() {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (user && user.onboarding_completed === false) {
      // iter90bo : ne pas ouvrir avant que les CGU soient acceptees.
      // Un Radix Dialog ouvert desactive pointer-events sur <body> ->
      // bloque tous les clics sur le LegalAcceptanceModal (div custom).
      let cancelled = false;
      (async () => {
        try {
          const r = await api.get('/legal/my-acceptance');
          if (cancelled) return;
          if (r.data?.needs_accept) return; // CGU en attente, on attend
          // Petit delay pour laisser le UI se monter
          setTimeout(() => { if (!cancelled) setOpen(true); }, 500);
        } catch {
          // Fail-open : ouvrir quand meme apres un delai
          setTimeout(() => { if (!cancelled) setOpen(true); }, 500);
        }
      })();
      return () => { cancelled = true; };
    }
  }, [user]);

  if (!user) return null;
  const isSuper = user.role === 'superadmin' || user.role === 'admin';
  const steps = isSuper ? SUPERADMIN_STEPS : SYNDIC_STEPS;
  const cur = steps[step] || steps[0];

  const close = async () => {
    setOpen(false);
    try {
      await api.post('/auth/onboarding-complete');
      if (refreshUser) await refreshUser();
    } catch (_e) { /* silently */ }
  };

  const next = () => {
    if (step < steps.length - 1) setStep(step + 1);
    else close();
  };

  const goToAction = (path) => {
    close();
    navigate(path);
  };

  const Icon = cur.icon;

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogContent className="max-w-2xl p-0 overflow-hidden" data-testid="onboarding-dialog">
        {/* Top hero band */}
        <div className={`bg-gradient-to-br ${cur.color} text-white p-6 relative`}>
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-xl bg-white/20 backdrop-blur flex items-center justify-center flex-shrink-0">
              <Icon size={28} className="text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-xl font-bold leading-tight" style={{fontFamily: 'Chivo, sans-serif'}}>{cur.title}</h2>
              <p className="text-sm text-white/85 mt-0.5">{cur.subtitle}</p>
            </div>
          </div>
          {/* Step indicator */}
          <div className="flex gap-1.5 mt-5">
            {steps.map((_, i) => (
              <div
                key={i}
                className={`h-1 rounded-full flex-1 transition-all ${i <= step ? 'bg-white' : 'bg-white/30'}`}
              />
            ))}
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          {cur.body(user)}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-slate-50 border-t border-slate-200 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span>Etape {step + 1} / {steps.length}</span>
            <button onClick={close} className="text-[#022D52] hover:underline" data-testid="onboarding-skip">
              Passer le guide
            </button>
          </div>
          <div className="flex gap-2">
            {step > 0 && (
              <Button variant="outline" size="sm" onClick={() => setStep(step - 1)} data-testid="onboarding-prev">
                <ChevronLeft size={14} className="mr-1" /> Precedent
              </Button>
            )}
            {step === 1 && !isSuper && (
              <Button variant="outline" size="sm" onClick={() => goToAction('/coproprietes')} className="border-emerald-500 text-emerald-700" data-testid="onboarding-go-acp">
                Creer une ACP
              </Button>
            )}
            {step === 4 && !isSuper && (
              <Button variant="outline" size="sm" onClick={() => goToAction('/team')} className="border-purple-500 text-purple-700" data-testid="onboarding-go-team">
                Inviter mon equipe
              </Button>
            )}
            {step === 1 && isSuper && (
              <Button variant="outline" size="sm" onClick={() => goToAction('/admin/users')} className="border-blue-500 text-[#01213e]" data-testid="onboarding-go-users">
                Creer un syndic
              </Button>
            )}
            <Button onClick={next} size="sm" className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="onboarding-next">
              {step === steps.length - 1 ? (
                <>Commencer <CheckCircle2 size={14} className="ml-1" /></>
              ) : (
                <>Suivant <ChevronRight size={14} className="ml-1" /></>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
