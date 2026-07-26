import { useState, useEffect, useMemo } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { LogOut, Home, Wallet, FileText, Receipt, Megaphone, Building2, User, AlertCircle, CheckCircle2, ArrowDownToLine, ArrowLeft, ArrowRight, Copy, UserCog, Users, Plus, Pencil, Trash2, Save, Eye, Gauge, CalendarClock, PieChart as PieChartIcon, TrendingUp, Clock, Sparkles, Mail, MailOpen, Send, Paperclip, ChevronRight, ChevronDown, Download, TrendingDown } from 'lucide-react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip } from 'recharts';
import { sanitizeHtml } from '@/lib/sanitizeHtml';
import OwnerOnboardingTour, { ownerTourStorageKey } from '@/components/OwnerOnboardingTour';

// Iter90db : palette couleur deterministe par nom de categorie de document
// (index -> style bordure/fond/texte). Le hash simple s'assure que la meme
// categorie recoit toujours la meme couleur, meme entre sessions/machines.
const CATEGORY_COLOR_PALETTE = [
  { bg: 'bg-blue-50', text: 'text-[#01213e]', border: 'border-blue-200', dot: 'bg-blue-500' },
  { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200', dot: 'bg-emerald-500' },
  { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200', dot: 'bg-amber-500' },
  { bg: 'bg-violet-50', text: 'text-violet-700', border: 'border-violet-200', dot: 'bg-violet-500' },
  { bg: 'bg-rose-50', text: 'text-rose-700', border: 'border-rose-200', dot: 'bg-rose-500' },
  { bg: 'bg-cyan-50', text: 'text-cyan-700', border: 'border-cyan-200', dot: 'bg-cyan-500' },
  { bg: 'bg-lime-50', text: 'text-lime-700', border: 'border-lime-200', dot: 'bg-lime-500' },
  { bg: 'bg-fuchsia-50', text: 'text-fuchsia-700', border: 'border-fuchsia-200', dot: 'bg-fuchsia-500' },
  { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200', dot: 'bg-orange-500' },
  { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200', dot: 'bg-indigo-500' },
  { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200', dot: 'bg-teal-500' },
  { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200', dot: 'bg-pink-500' },
];

function categoryColor(name) {
  const key = (name || 'Sans categorie').toLowerCase().trim();
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0;
  }
  return CATEGORY_COLOR_PALETTE[Math.abs(hash) % CATEGORY_COLOR_PALETTE.length];
}

// Iter90db : palette + libelle par kind de communication
const COMM_KIND_META = {
  situation: { label: 'Situation de compte', color: 'bg-blue-100 text-[#01213e] border-blue-200', icon: Wallet },
  decompte: { label: 'Decompte annuel', color: 'bg-purple-100 text-purple-700 border-purple-200', icon: FileText },
  mutation: { label: 'Decompte de mutation', color: 'bg-amber-100 text-amber-700 border-amber-200', icon: Home },
  generic: { label: 'Communication', color: 'bg-slate-100 text-slate-700 border-slate-200', icon: Mail },
};

const fmt = (n) => new Intl.NumberFormat('fr-BE', { style: 'currency', currency: 'EUR' }).format(n || 0);
const fmtDate = (s) => s ? new Date(s).toLocaleDateString('fr-BE') : '-';

export default function OwnerPortalPage() {
  const { user, logout } = useAuth();
  const [dashboard, setDashboard] = useState(null);
  const [coproprietes, setCoproprietes] = useState([]);
  const [fundCalls, setFundCalls] = useState([]);
  const [charges, setCharges] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [communications, setCommunications] = useState([]);
  const [selectedComm, setSelectedComm] = useState(null); // detail email ouvert
  // iter90do : selectedAcp commence a null -> ecran de selection ACP obligatoire.
  // Aucune donnee financiere n'est affichee tant qu'une ACP n'est pas choisie.
  const [selectedAcp, setSelectedAcp] = useState(null);
  const [financialLoading, setFinancialLoading] = useState(false);
  // Iter90dd : mouvements du grand livre + filtre periode
  const [movements, setMovements] = useState([]);
  const [movementsLoading, setMovementsLoading] = useState(false);
  const [openingBalance, setOpeningBalance] = useState(0);
  const [closingBalance, setClosingBalance] = useState(0);
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  // iter90hz : comptes bancaires de l'ACP (transparence + affichage IBAN)
  const [bankAccounts, setBankAccounts] = useState([]);
  const [bankAccountsLoading, setBankAccountsLoading] = useState(false);
  // iter90i0 : filtre par plage de dates (defaut : 1er jour de l'exercice
  // comptable selectionne -> aujourd'hui). Le proprio peut modifier.
  const [bankAccountsStart, setBankAccountsStart] = useState('');
  const [bankAccountsEnd, setBankAccountsEnd] = useState('');
  // iter90fy : exercices comptables de l'ACP selectionnee (remplace les
  // selecteurs de date libres par une liste d'exercices comptables). L'user
  // ne peut voir que les FY de ses ACPs (backend /owner/fiscal-years/{cid}).
  const [fiscalYears, setFiscalYears] = useState([]);
  const [selectedFyId, setSelectedFyId] = useState('');
  // Iter90dh : tour guide de premiere connexion
  const [showTour, setShowTour] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // iter89 : profil editable + tenants self-service
  const [profileForm, setProfileForm] = useState(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [tenants, setTenants] = useState([]);
  const [myLots, setMyLots] = useState([]);
  const [tenantDialog, setTenantDialog] = useState(false);
  const [editingTenant, setEditingTenant] = useState(null);
  // iter90dg : rent_amount retire, mailbox_names ajoute, multi-lots (lot_ids[])
  const [tenantForm, setTenantForm] = useState({ name: '', email: '', phone: '', lot_ids: [], lease_start: '', lease_end: '', mailbox_names: '' });

  const loadTenants = async () => {
    try {
      const { data } = await api.get('/owner/tenants');
      setTenants(data.tenants || []);
      setMyLots(data.lots || []);
    } catch { /* silent : pas de droit ou pas de lot */ }
  };

  useEffect(() => {
    (async () => {
      try {
        // iter90do : chargement initial minimal (aucune donnee financiere).
        // On charge uniquement la liste des ACPs + le profil de l'owner. Les
        // fund-calls/invoices/docs/comms sont charges UNIQUEMENT quand une
        // ACP est selectionnee (chinese wall strict).
        const [dash, copros] = await Promise.all([
          api.get('/owner/dashboard'),
          api.get('/owner/coproprietes'),
        ]);
        setDashboard(dash.data);
        setCoproprietes(copros.data);
        // iter89 : init profile form from owner data
        const o = dash.data?.owner || {};
        setProfileForm({
          first_name: o.first_name || '', last_name: o.last_name || '',
          address: o.address || '', postal_code: o.postal_code || '',
          city: o.city || '', country: o.country || 'Belgique',
          email: o.email || '', email2: o.email2 || '',
          phone: o.phone || '', phone2: o.phone2 || '',
        });
        await loadTenants();
        // Iter90dh : affiche le tour guide si pas encore vu
        try {
          const key = ownerTourStorageKey(o.email || user?.email || '');
          if (!localStorage.getItem(key)) {
            setTimeout(() => setShowTour(true), 800);  // laisse l'UI se poser
          }
        } catch { /* localStorage indisponible : skip */ }
      } catch (err) {
        setError(err.response?.data?.detail || 'Erreur de chargement');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // iter90do : auto-selection si une seule ACP (skip picker)
  useEffect(() => {
    if (coproprietes.length === 1 && !selectedAcp) {
      setSelectedAcp(coproprietes[0].id);
    }
  }, [coproprietes, selectedAcp]);

  // iter90do : charge les donnees financieres UNIQUEMENT apres selection ACP
  useEffect(() => {
    if (!selectedAcp) {
      // Vide les donnees precedentes pour eviter de leaker entre ACPs
      setFundCalls([]);
      setCharges([]);
      setDocuments([]);
      setCommunications([]);
      return;
    }
    setFinancialLoading(true);
    (async () => {
      try {
        const params = { copropriete_id: selectedAcp };
        const [fc, inv, docs, comms] = await Promise.all([
          api.get('/owner/fund-calls', { params }),
          api.get('/owner/invoices', { params }),
          api.get('/owner/documents', { params }),
          api.get('/owner/communications', { params }).catch(() => ({ data: [] })),
        ]);
        setFundCalls(fc.data);
        setCharges(inv.data);
        setDocuments(docs.data);
        setCommunications(comms.data);
      } catch (err) {
        setError(err.response?.data?.detail || 'Erreur chargement des donnees ACP');
      } finally {
        setFinancialLoading(false);
      }
    })();
  }, [selectedAcp]);

  // iter90fy : charge les exercices comptables (fiscal_years) de l'ACP
  // selectionnee, et selectionne automatiquement le dernier CLOTURE. Le
  // periodStart/End sera derive de la FY selectionnee via un autre effet.
  useEffect(() => {
    if (!selectedAcp) {
      setFiscalYears([]);
      setSelectedFyId('');
      return;
    }
    (async () => {
      try {
        const r = await api.get(`/owner/fiscal-years/${selectedAcp}`);
        const years = r.data || [];
        setFiscalYears(years);
        // Preselection : dernier CLOTURE, sinon dernier tout court
        const closed = years.filter(y => y.status === 'closed');
        const preferred = closed[0] || years[0];
        setSelectedFyId(preferred?.id || '');
      } catch {
        setFiscalYears([]);
        setSelectedFyId('');
      }
    })();
  }, [selectedAcp]);

  // iter90fy : quand la FY selectionnee change, applique sa periode
  // start_date/end_date aux mouvements.
  useEffect(() => {
    if (!selectedFyId) {
      setPeriodStart('');
      setPeriodEnd('');
      return;
    }
    const fy = fiscalYears.find(y => y.id === selectedFyId);
    if (fy) {
      setPeriodStart(fy.start_date || '');
      // iter90hx : par defaut, la periode se termine A LA DATE D'AUJOURD'HUI
      // (pas la fin de l'exercice) car le proprio veut voir sa situation
      // actuelle - modifiable via le nouveau selecteur de date dans MovementsTab.
      const todayIso = new Date().toISOString().slice(0, 10);
      const fyEnd = fy.end_date || todayIso;
      setPeriodEnd(fyEnd < todayIso ? fyEnd : todayIso);
    }
  }, [selectedFyId, fiscalYears]);

  // Iter90dd : recharge les mouvements du grand livre quand ACP ou periode change
  // iter90do : skip si aucune ACP selectionnee (chinese wall strict).
  useEffect(() => {
    if (!dashboard || !selectedAcp) {
      setMovements([]);
      setOpeningBalance(0);
      setClosingBalance(0);
      return;
    }
    setMovementsLoading(true);
    const params = { copropriete_id: selectedAcp };
    if (periodStart) params.start_date = periodStart;
    if (periodEnd) params.end_date = periodEnd;
    api.get('/owner/movements', { params })
      .then((r) => {
        setMovements(r.data?.movements || []);
        setOpeningBalance(r.data?.opening_balance || 0);
        setClosingBalance(r.data?.closing_balance || 0);
      })
      .catch(() => { setMovements([]); setOpeningBalance(0); setClosingBalance(0); })
      .finally(() => setMovementsLoading(false));
  }, [selectedAcp, periodStart, periodEnd, dashboard]);

  // iter90hz : charge les comptes bancaires de l'ACP (transparence pour le
  // proprio + affichage de l'IBAN de virement dans le panneau paiement rapide).
  // iter90i0 : filtre par plage de dates (start_date / end_date). Par
  // defaut initialise au 1er jour de l'exercice comptable courant, la fin
  // etant fixee a aujourd'hui. Le proprio peut modifier les deux.
  useEffect(() => {
    if (!selectedAcp) {
      setBankAccounts([]);
      return;
    }
    setBankAccountsLoading(true);
    const params = {};
    if (bankAccountsStart) params.start_date = bankAccountsStart;
    if (bankAccountsEnd) params.end_date = bankAccountsEnd;
    api.get(`/owner/bank-accounts/${selectedAcp}`, { params })
      .then((r) => setBankAccounts(r.data?.bank_accounts || []))
      .catch(() => setBankAccounts([]))
      .finally(() => setBankAccountsLoading(false));
  }, [selectedAcp, bankAccountsStart, bankAccountsEnd]);

  // iter90i0 : quand l'ACP ou l'exercice comptable change, on repositionne
  // automatiquement la periode Comptes bancaires au 1er jour de l'exercice
  // -> aujourd'hui. Le proprio peut ensuite affiner via les 2 date pickers.
  useEffect(() => {
    if (!selectedFyId || !fiscalYears || fiscalYears.length === 0) return;
    const fy = fiscalYears.find(y => y.id === selectedFyId);
    if (!fy) return;
    const todayIso = new Date().toISOString().slice(0, 10);
    setBankAccountsStart(fy.start_date || '');
    setBankAccountsEnd(todayIso);
  }, [selectedFyId, fiscalYears, selectedAcp]);

  // iter90hz : compte "vue" par defaut de l'ACP (celui vers lequel le
  // proprio doit virer). Fallback : le premier compte de la liste.
  const defaultAcpAccount = useMemo(() => {
    if (!bankAccounts || bankAccounts.length === 0) return null;
    return bankAccounts.find(b => b.type === 'vue' && b.is_default)
        || bankAccounts.find(b => b.type === 'vue')
        || bankAccounts.find(b => b.is_default)
        || bankAccounts[0];
  }, [bankAccounts]);


  // iter90da : calculs memoized pour l'onglet "Ma situation".
  // Doivent etre AVANT les early returns (loading / error) pour respecter
  // les rules-of-hooks.
  // iter90do : les donnees sont deja scopees par API (copropriete_id envoye).
  // Le filtre client "selectedAcp === all" est retire.
  const chargesMemo = useMemo(() => charges, [charges]);
  const pendingCallsMemo = useMemo(
    () => (dashboard?.pending_calls || []).filter(p => p.copropriete_id === selectedAcp),
    [dashboard, selectedAcp],
  );

  // iter90h5 : les trimestres sont calcules a partir du DEBUT DE L'EXERCICE
  // FISCAL selectionne, pas de l'annee civile. Exemple pour un exercice
  // 01/03/2026 -> 28/02/2027 : T1=Mar-Mai, T2=Jun-Aout, T3=Sep-Nov, T4=Dec-Fev.
  const selectedFyMeta = useMemo(() => {
    return fiscalYears.find(y => y.id === selectedFyId) || null;
  }, [fiscalYears, selectedFyId]);

  const fyStartDate = useMemo(() => {
    if (selectedFyMeta?.start_date) {
      const d = new Date(selectedFyMeta.start_date);
      if (!isNaN(d.getTime())) return d;
    }
    // Fallback : debut annee civile courante
    return new Date(new Date().getFullYear(), 0, 1);
  }, [selectedFyMeta]);

  const fyEndDate = useMemo(() => {
    if (selectedFyMeta?.end_date) {
      const d = new Date(selectedFyMeta.end_date);
      if (!isNaN(d.getTime())) return new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59);
    }
    // Fallback : fin annee civile courante
    return new Date(new Date().getFullYear(), 11, 31, 23, 59, 59);
  }, [selectedFyMeta]);

  // Bornes d'un trimestre a partir du debut d'exercice : ajoute (idx*3) mois.
  const getQuarterBounds = useMemo(() => {
    return (qId) => {
      if (qId === 'year') {
        return { start: fyStartDate, end: fyEndDate };
      }
      const qIdx = { T1: 0, T2: 1, T3: 2, T4: 3 }[qId] ?? 0;
      const s = new Date(fyStartDate.getFullYear(), fyStartDate.getMonth() + qIdx * 3, 1);
      const e = new Date(fyStartDate.getFullYear(), fyStartDate.getMonth() + qIdx * 3 + 3, 0, 23, 59, 59);
      return { start: s, end: e };
    };
  }, [fyStartDate, fyEndDate]);

  // Trimestre courant = celui contenant `now`, si dans l'exercice, sinon T1.
  const currentQuarter = useMemo(() => {
    const now = new Date();
    for (const q of ['T1', 'T2', 'T3', 'T4']) {
      const { start, end } = getQuarterBounds(q);
      if (now >= start && now <= end) return q;
    }
    return 'T1';
  }, [getQuarterBounds]);

  const [selectedQuarter, setSelectedQuarter] = useState('T1');
  // Recale selectedQuarter sur le trimestre courant quand l'exercice change
  useEffect(() => {
    setSelectedQuarter(currentQuarter);
  }, [currentQuarter]);

  // Labels de mois par trimestre (dynamique selon exercice)
  const quarterLabels = useMemo(() => {
    const MONTHS_FR = ['Jan', 'Fev', 'Mar', 'Avr', 'Mai', 'Jun', 'Jul', 'Aou', 'Sep', 'Oct', 'Nov', 'Dec'];
    return ['T1', 'T2', 'T3', 'T4'].map((q, i) => {
      const s = new Date(fyStartDate.getFullYear(), fyStartDate.getMonth() + i * 3, 1);
      const e = new Date(fyStartDate.getFullYear(), fyStartDate.getMonth() + i * 3 + 2, 1);
      return { id: q, label: q, range: `${MONTHS_FR[s.getMonth()]} - ${MONTHS_FR[e.getMonth()]}` };
    });
  }, [fyStartDate]);

  const fyLabel = useMemo(() => {
    return selectedFyMeta?.name || `${fyStartDate.getFullYear()}`;
  }, [selectedFyMeta, fyStartDate]);

  // Bornes du trimestre selectionne (ou de l'exercice complet)
  const quarterBounds = useMemo(() => {
    return getQuarterBounds(selectedQuarter);
  }, [selectedQuarter, getQuarterBounds]);

  const inQuarter = useMemo(() => {
    return (isoStr) => {
      if (!isoStr) return false;
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return false;
      return d >= quarterBounds.start && d <= quarterBounds.end;
    };
  }, [quarterBounds]);

  // iter90i8 : agregats du trimestre selectionne pour l'INFO (prochain paiement,
  // nombre d'appels en attente sur la periode).
  // iter90h2 : Le SOLDE et le STATUS proviennent desormais du grand livre
  // (dashboard.stats_by_acp), pas de fund_calls.paid (flag statique obsolete
  // apres lettrage bancaire). Fix : le proprio voit sa vraie situation
  // comptable de l'ACP (coherente avec l'onglet "Appels de fonds").
  const quarterAgg = useMemo(() => {
    let calledQ = 0;
    let paidQ = 0;
    let pendingCount = 0;
    let nextCall = null;
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    for (const fc of fundCalls) {
      const inQ = inQuarter(fc.date) || inQuarter(fc.due_date);
      if (!inQ) continue;
      const amt = Number(fc.my_amount || 0);
      calledQ += amt;
      if (fc.paid) {
        paidQ += amt;
      } else {
        pendingCount += 1;
        const dueIso = fc.due_date || fc.date;
        if (dueIso) {
          const due = new Date(dueIso);
          due.setHours(0, 0, 0, 0);
          const daysDelta = Math.round((due - now) / (1000 * 60 * 60 * 24));
          let urgency = 'ok';
          if (daysDelta < 0) urgency = 'overdue';
          else if (daysDelta <= 7) urgency = 'urgent';
          else if (daysDelta <= 30) urgency = 'soon';
          const cand = {
            fund_call_name: fc.name,
            amount: amt,
            due_date: dueIso,
            vcs_code: fc.vcs_code,
            daysDelta, urgency,
          };
          if (!nextCall || (cand.daysDelta !== null && (nextCall.daysDelta === null || cand.daysDelta < nextCall.daysDelta))) {
            nextCall = cand;
          }
        }
      }
    }
    return {
      // Trimestriels (informatif uniquement)
      totalCalledQuarter: +calledQ.toFixed(2),
      totalPaidQuarter: +paidQ.toFixed(2),
      pendingCount,
      nextCall,
    };
  }, [fundCalls, inQuarter]);

  // iter90h3 : stats calcules a partir des `movements` deja charges (source
  // /owner/movements, filtree par exercice fiscal). Aligne "Ma situation" sur
  // l'onglet "Appels de fonds" (meme source, meme resultat).
  // Fallback : si movements pas encore charge, on retombe sur acpStats
  // (cumule sans filtre FY) pour ne pas afficher un ecran vide.
  const movementStats = useMemo(() => {
    if (!movements || movements.length === 0) {
      return null;
    }
    let sumDebit = 0;
    let sumCredit = 0;
    for (const m of movements) {
      sumDebit += Number(m.debit || 0);
      sumCredit += Number(m.credit || 0);
    }
    const openDebtor = openingBalance > 0 ? openingBalance : 0;
    const openCreditor = openingBalance < 0 ? -openingBalance : 0;
    const total_called = +(openDebtor + sumDebit).toFixed(2);
    const total_paid = +(openCreditor + sumCredit).toFixed(2);
    const balance = +Number(closingBalance || 0).toFixed(2);
    return {
      total_called,
      total_paid,
      balance,
      status: balance > 0.01 ? 'debiteur' : balance < -0.01 ? 'crediteur' : 'solde',
    };
  }, [movements, openingBalance, closingBalance]);

  const chargesByCategory = useMemo(() => {
    // iter90i8 : filtre les charges par trimestre selectionne (au lieu des
    // 12 derniers mois). Rend le donut coherent avec la vue trimestrielle.
    const totals = {};
    for (const c of chargesMemo) {
      if (!c.date) continue;
      if (!inQuarter(c.date)) continue;
      const cat = c.category || 'Autres';
      totals[cat] = (totals[cat] || 0) + (c.my_amount || 0);
    }
    return Object.entries(totals)
      .map(([name, value]) => ({ name, value: Math.round(value * 100) / 100 }))
      .sort((a, b) => b.value - a.value);
  }, [chargesMemo, inQuarter]);
  const upcomingWithCountdown = useMemo(() => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    return [...pendingCallsMemo]
      .map(p => {
        const due = p.due_date ? new Date(p.due_date) : null;
        if (due) due.setHours(0, 0, 0, 0);
        const daysDelta = due ? Math.round((due - now) / (1000 * 60 * 60 * 24)) : null;
        let urgency = 'ok';
        if (daysDelta !== null) {
          if (daysDelta < 0) urgency = 'overdue';
          else if (daysDelta <= 7) urgency = 'urgent';
          else if (daysDelta <= 30) urgency = 'soon';
        }
        return { ...p, daysDelta, urgency };
      })
      .sort((a, b) => {
        if (a.daysDelta === null) return 1;
        if (b.daysDelta === null) return -1;
        return a.daysDelta - b.daysDelta;
      });
  }, [pendingCallsMemo]);

  const handleLogout = async () => { await logout(); window.location.href = '/login'; };

  const copyVcs = (vcs) => {
    navigator.clipboard.writeText(vcs);
    toast.success('VCS copie dans le presse-papier');
  };

  // iter89 : sauvegarde du profil + notification syndic automatique
  const saveProfile = async () => {
    setSavingProfile(true);
    try {
      const { data } = await api.put('/owner/me', profileForm);
      if (data.updated) {
        toast.success('Coordonnees mises a jour - votre syndic a ete averti par email');
        // Reload dashboard pour rafraichir l'identite affichee
        const dash = await api.get('/owner/dashboard');
        setDashboard(dash.data);
      } else {
        toast.info('Aucune modification a enregistrer');
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la sauvegarde');
    } finally {
      setSavingProfile(false);
    }
  };

  // iter89 : CRUD locataires self-service (iter90dg : multi-lots + mailbox_names)
  const openCreateTenant = () => {
    setEditingTenant(null);
    setTenantForm({
      name: '', email: '', phone: '',
      lot_ids: myLots.length === 1 ? [myLots[0].id] : [],
      lease_start: '', lease_end: '', mailbox_names: '',
    });
    setTenantDialog(true);
  };
  const openEditTenant = (t) => {
    setEditingTenant(t);
    setTenantForm({
      name: t.name || '', email: t.email || '', phone: t.phone || '',
      // Compat : lit lot_ids[] ou lot_id single (legacy)
      lot_ids: t.lot_ids && t.lot_ids.length > 0
        ? t.lot_ids
        : (t.lot_id ? [t.lot_id] : []),
      lease_start: t.lease_start || '',
      lease_end: t.lease_end || '',
      mailbox_names: t.mailbox_names || '',
    });
    setTenantDialog(true);
  };
  const toggleTenantLot = (lotId) => {
    setTenantForm(prev => {
      const has = (prev.lot_ids || []).includes(lotId);
      return {
        ...prev,
        lot_ids: has ? prev.lot_ids.filter(x => x !== lotId) : [...(prev.lot_ids || []), lotId],
      };
    });
  };
  const saveTenant = async () => {
    if (!tenantForm.name?.trim()) { toast.error('Nom obligatoire'); return; }
    if (!tenantForm.lot_ids || tenantForm.lot_ids.length === 0) {
      toast.error('Au moins un lot doit etre selectionne'); return;
    }
    try {
      if (editingTenant) {
        await api.put(`/owner/tenants/${editingTenant.id}`, tenantForm);
        toast.success('Locataire modifie - syndic averti');
      } else {
        await api.post('/owner/tenants', tenantForm);
        toast.success('Locataire ajoute - syndic averti');
      }
      setTenantDialog(false);
      await loadTenants();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const deleteTenant = async (t) => {
    if (!window.confirm(`Supprimer le locataire "${t.name}" ?`)) return;
    try {
      await api.delete(`/owner/tenants/${t.id}`);
      toast.success('Locataire supprime - syndic averti');
      await loadTenants();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  if (loading) return <div className="min-h-screen flex items-center justify-center text-slate-500">Chargement de votre espace...</div>;

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-slate-50">
        <Card className="max-w-md border-orange-200">
          <CardContent className="p-6 text-center">
            <AlertCircle size={36} className="mx-auto text-orange-500 mb-3" />
            <h2 className="font-semibold text-slate-900 mb-2" style={{fontFamily:'Chivo,sans-serif'}}>Acces impossible</h2>
            <p className="text-sm text-slate-600 mb-4">{error}</p>
            <Button onClick={handleLogout} variant="outline">Se deconnecter</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // iter90do : ecran de selection ACP obligatoire quand plusieurs ACPs.
  // AUCUNE donnee financiere ne doit apparaitre ici (chinese wall strict) :
  // solde, appels, factures, docs, communications sont HORS ecran.
  if (!selectedAcp && coproprietes.length > 1) {
    const ownerName = dashboard?.owner?.name || user?.name || 'Proprietaire';
    return (
      <div className="min-h-screen bg-slate-50">
        <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
          <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <img src="/logo-nextge.png" alt="NextGe Copro" className="h-9 w-auto" />
              <div>
                <h1 className="text-base font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Espace proprietaire</h1>
                <p className="text-[11px] text-slate-500">{ownerName}</p>
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={handleLogout} data-testid="logout-btn" className="text-slate-500">
              <LogOut size={14} className="mr-1.5" /> Deconnexion
            </Button>
          </div>
        </header>

        <main className="max-w-4xl mx-auto p-6" data-testid="acp-picker-screen">
          <div className="mb-6 text-center">
            <h2 className="text-2xl font-bold text-slate-900 mb-2" style={{fontFamily:'Chivo,sans-serif'}}>
              Bienvenue {ownerName.split(' ')[0]}
            </h2>
            <p className="text-slate-600">
              Vous etes proprietaire dans <b>{coproprietes.length}</b> coproprietes.
              Selectionnez celle que vous souhaitez consulter :
            </p>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {coproprietes.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelectedAcp(c.id)}
                data-testid={`acp-picker-${c.id}`}
                className="text-left bg-white border-2 border-slate-200 rounded-xl p-5 hover:border-[#02A9AA] hover:shadow-lg transition-all group"
              >
                <div className="flex items-start gap-3">
                  <div className="w-11 h-11 rounded-lg bg-[#022D52] group-hover:bg-[#02A9AA] transition-colors flex items-center justify-center text-white flex-shrink-0">
                    <Building2 size={20} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-slate-900 truncate" style={{fontFamily:'Chivo,sans-serif'}}>
                      {c.name}
                    </div>
                    {c.address && (
                      <div className="text-xs text-slate-500 mt-0.5 line-clamp-2">
                        {c.address}{c.postal_code || c.city ? `, ${c.postal_code || ''} ${c.city || ''}`.trim() : ''}
                      </div>
                    )}
                    {c.reference && (
                      <div className="text-[11px] text-slate-400 mt-1 font-mono">Ref: {c.reference}</div>
                    )}
                    <div className="mt-2 text-[11px] text-slate-500">
                      <b className="text-slate-700">{c.my_lots?.length || 0}</b> lot(s) a votre nom
                    </div>
                  </div>
                  <ChevronRight size={18} className="text-slate-300 group-hover:text-[#02A9AA] transition-colors" />
                </div>
              </button>
            ))}
          </div>

          <p className="text-center text-xs text-slate-400 mt-6">
            Vos donnees comptables sont strictement cloisonnees par copropriete
            (Chinese wall). Vous ne verrez que les informations relatives a la
            copropriete selectionnee.
          </p>
        </main>
      </div>
    );
  }

  const owner = dashboard?.owner || {};
  // Iter90dd/do : stats effectives pour l'ACP selectionnee.
  const acpStats = (selectedAcp && dashboard?.stats_by_acp?.[selectedAcp])
    ? dashboard.stats_by_acp[selectedAcp]
    : null;
  // iter90do : donnees toujours scopees a l'ACP selectionnee (chinese wall strict).
  // Plus de mode "all" : selectedAcp est toujours defini quand on affiche
  // le dashboard financier.
  const acpLotsCount = coproprietes.find(c => c.id === selectedAcp)?.my_lots?.length || 0;

  const stats = {
    // ⚠️ VERROU iter90h3 : NE PAS MODIFIER sans mettre a jour
    // /app/backend/tests/test_iter90h3_owner_movements_consistency.py
    // Regle metier : "Ma situation" DOIT afficher le meme solde que l'onglet
    // "Appels de fonds" (source : /owner/movements, scope FY selectionne).
    // acpStats (source /dashboard) somme SANS filtre FY -> a garder en
    // fallback uniquement quand movements pas encore charge.
    coproprietes_count: 1,
    lots_count: acpLotsCount,
    total_called: movementStats ? movementStats.total_called : (acpStats ? acpStats.total_called : 0),
    total_paid: movementStats ? movementStats.total_paid : (acpStats ? acpStats.total_paid : 0),
    balance: movementStats ? movementStats.balance : (acpStats ? acpStats.balance : 0),
    status: movementStats ? movementStats.status : (acpStats ? acpStats.status : 'solde'),
    pending_calls_count: (dashboard?.pending_calls || []).filter(p => p.copropriete_id === selectedAcp).length,
  };
  const filteredFundCalls = fundCalls;
  const filteredCharges = chargesMemo;
  const filteredDocs = documents;
  const filteredCommunications = communications;
  const filteredPendingCalls = pendingCallsMemo;

  const nextCall = upcomingWithCountdown[0] || null;
  const totalPending = upcomingWithCountdown.reduce((s, p) => s + (p.amount || 0), 0);
  const totalCharges12m = chargesByCategory.reduce((s, x) => s + x.value, 0);

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-md bg-[#022D52] text-white flex items-center justify-center text-xs font-bold" style={{fontFamily:'Chivo,sans-serif'}}>CP</div>
            <div>
              <h1 className="text-base font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Espace proprietaire</h1>
              <p className="text-[11px] text-slate-500">{owner.name}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={handleLogout} data-testid="logout-btn" className="text-slate-500"><LogOut size={14} className="mr-1.5" /> Deconnexion</Button>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">

        {/* Owner identity card */}
        <Card className="mb-6 border-slate-200 bg-gradient-to-br from-white to-slate-50" data-testid="owner-id-card">
          <CardContent className="p-5">
            <div className="flex items-start justify-between flex-wrap gap-4">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-full bg-[#022D52]/10 flex items-center justify-center text-[#022D52]">
                  <User size={22} />
                </div>
                <div>
                  <h2 className="font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>{owner.name}</h2>
                  <p className="text-xs text-slate-500">{owner.email}{owner.phone ? ` - ${owner.phone}` : ''}</p>
                </div>
              </div>
              {owner.vcs_code && (
                <div className="text-right">
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-0.5">Communication structuree (VCS)</div>
                  <button onClick={() => copyVcs(owner.vcs_code)} className="font-mono text-sm text-[#022D52] hover:bg-blue-50 px-2 py-1 rounded inline-flex items-center gap-1.5" data-testid="copy-vcs-btn">
                    {owner.vcs_code}
                    <Copy size={11} />
                  </button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          <StatCard icon={<Building2 size={16}/>} label="Coproprietes" value={stats.coproprietes_count || 0} />
          <StatCard icon={<Home size={16}/>} label="Lots" value={stats.lots_count || 0} />
          <StatCard icon={<Wallet size={16}/>} label="Total appele" value={fmt(stats.total_called)} />
          <StatCard
            icon={stats.status === 'debiteur' ? <AlertCircle size={16}/> : <CheckCircle2 size={16}/>}
            label="Solde"
            value={fmt(stats.balance)}
            highlight={stats.status === 'debiteur' ? 'red' : (stats.status === 'crediteur' ? 'green' : 'neutral')}
            badge={stats.status}
          />
        </div>

        {/* Pending alerts */}
        {dashboard?.pending_calls?.length > 0 && (
          <Card className="border-orange-200 bg-orange-50/40 mb-6" data-testid="pending-calls-alert">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <AlertCircle size={16} className="text-orange-600" />
                <span className="font-semibold text-orange-900 text-sm">Appels en attente de paiement ({dashboard.pending_calls.length})</span>
              </div>
              <div className="space-y-1.5">
                {dashboard.pending_calls.map((p, i) => (
                  <div key={i} className="flex items-center justify-between text-sm bg-white rounded px-3 py-2 border border-orange-100">
                    <div>
                      <span className="font-medium text-slate-900">{p.fund_call_name}</span>
                      <span className="text-xs text-slate-500 ml-2">Echeance: {fmtDate(p.due_date)}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-orange-700">{fmt(p.amount)}</span>
                      <button onClick={() => copyVcs(p.vcs_code)} className="font-mono text-[10px] text-[#022D52] bg-blue-50 hover:bg-blue-100 px-2 py-0.5 rounded inline-flex items-center gap-1" title="Copier VCS pour le virement">
                        {p.vcs_code}<Copy size={9} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* ACP selector + Tabs */}
        <Tabs defaultValue="situation" className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <TabsList>
              <TabsTrigger value="situation" data-testid="tab-situation"><Sparkles size={14} className="mr-1.5" /> Ma situation</TabsTrigger>
              <TabsTrigger value="coproprietes" data-testid="tab-coproprietes"><Building2 size={14} className="mr-1.5" /> Mes coproprietes</TabsTrigger>
              <TabsTrigger value="fund-calls" data-testid="tab-fund-calls"><Megaphone size={14} className="mr-1.5" /> Appels de fonds</TabsTrigger>
              <TabsTrigger value="charges" data-testid="tab-charges"><Receipt size={14} className="mr-1.5" /> Charges</TabsTrigger>
              <TabsTrigger value="bank-accounts" data-testid="tab-bank-accounts"><Wallet size={14} className="mr-1.5" /> Comptes bancaires</TabsTrigger>
              <TabsTrigger value="documents" data-testid="tab-documents"><FileText size={14} className="mr-1.5" /> Documents</TabsTrigger>
              <TabsTrigger value="communications" data-testid="tab-communications"><Mail size={14} className="mr-1.5" /> Communications</TabsTrigger>
              <TabsTrigger value="profile" data-testid="tab-profile"><UserCog size={14} className="mr-1.5" /> Mon profil</TabsTrigger>
              <TabsTrigger value="tenants" data-testid="tab-tenants"><Users size={14} className="mr-1.5" /> Mes locataires</TabsTrigger>
            </TabsList>
            {coproprietes.length >= 1 && (
              <div className="flex items-center gap-2">
                <select
                  value={selectedAcp || ''}
                  onChange={e => setSelectedAcp(e.target.value)}
                  className="text-sm border border-slate-200 rounded-md px-3 py-1.5 bg-white"
                  data-testid="acp-selector"
                >
                  {coproprietes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                {coproprietes.length > 1 && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSelectedAcp(null)}
                    data-testid="btn-change-acp"
                    className="text-xs"
                  >
                    <Building2 size={12} className="mr-1" /> Changer
                  </Button>
                )}
              </div>
            )}
          </div>

          <TabsContent value="situation" className="mt-0 space-y-5" data-testid="situation-tab-content">
            {/* iter90h5 : selecteur de trimestre base sur l'exercice fiscal */}
            <QuarterSelector
              selected={selectedQuarter}
              onChange={setSelectedQuarter}
              currentQuarter={currentQuarter}
              quarters={quarterLabels}
              fyLabel={fyLabel}
            />
            <SituationHero
              status={stats.status}
              balance={stats.balance}
              totalCalled={stats.total_called}
              totalPaid={stats.total_paid}
              nextCall={quarterAgg.nextCall}
              totalPending={stats.balance > 0.01 ? stats.balance : 0}
              totalCharges12m={totalCharges12m}
              pendingCount={quarterAgg.pendingCount}
              copyVcs={copyVcs}
              periodLabel={selectedQuarter === 'year' ? `Exercice ${fyLabel}` : `${selectedQuarter} ${fyLabel}`}
              isYearView={selectedQuarter === 'year'}
            />
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
              <ChargesDonut data={chargesByCategory} total={totalCharges12m} periodLabel={selectedQuarter === 'year' ? `Exercice ${fyLabel}` : `${selectedQuarter} ${fyLabel}`} />
              <UpcomingTimeline items={upcomingWithCountdown} copyVcs={copyVcs} />
            </div>
          </TabsContent>

          <TabsContent value="coproprietes" className="mt-0">
            {coproprietes.length === 0 ? (
              <Card><CardContent className="p-8 text-center text-slate-400">Aucune copropriete liee a votre compte</CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {coproprietes.map(c => (
                  <Card key={c.id} className="border-slate-200" data-testid={`copro-card-${c.id}`}>
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between">
                        <div>
                          <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>{c.name}</CardTitle>
                          <p className="text-xs text-slate-500 mt-0.5">{c.reference}</p>
                        </div>
                        <Badge variant="outline" className="text-[10px]">{c.my_total_quotity} tantiemes</Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="pt-2">
                      <p className="text-xs text-slate-600 mb-2">{c.address}, {c.postal_code} {c.city}</p>
                      <div className="space-y-1 mb-3">
                        <div className="text-[11px] uppercase tracking-wider text-slate-400">Vos lots ({c.my_lots.length})</div>
                        {c.my_lots.map(l => (
                          <div key={l.id} className="text-xs flex items-center justify-between border-l-2 border-[#022D52]/30 pl-2 py-0.5">
                            <span className="text-slate-700">Lot {l.number} {l.description ? `- ${l.description}` : ''}</span>
                            <span className="font-mono text-slate-500">{l.quotity}</span>
                          </div>
                        ))}
                      </div>
                      {/* iter90fy : le decompte annuel n'est dispo qu'apres
                          cloture de l'exercice par le syndic. Sinon on
                          affiche une info claire au lieu du bouton. */}
                      {c.has_closed_fiscal_year ? (
                        <a
                          href={`${process.env.REACT_APP_BACKEND_URL}/api/owner/decompte/pdf?copropriete_id=${c.id}${c.latest_closed_fiscal_year?.id ? `&fiscal_year_id=${c.latest_closed_fiscal_year.id}` : ''}`}
                          target="_blank" rel="noreferrer"
                          className="inline-flex items-center gap-1.5 text-xs text-[#022D52] hover:bg-blue-50 px-3 py-1.5 rounded-md border border-[#022D52]/20"
                          data-testid={`download-decompte-${c.id}`}
                          title={`Decompte de l'exercice cloture : ${c.latest_closed_fiscal_year?.name || ''}`}
                        >
                          <ArrowDownToLine size={12} />
                          Telecharger mon decompte annuel {c.latest_closed_fiscal_year?.name ? `(${c.latest_closed_fiscal_year.name})` : ''}
                        </a>
                      ) : (
                        <div
                          className="inline-flex items-center gap-1.5 text-xs text-slate-500 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-md"
                          data-testid={`decompte-unavailable-${c.id}`}
                        >
                          <CalendarClock size={12} />
                          Decompte annuel disponible apres cloture de l&apos;exercice par le syndic
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="fund-calls" className="mt-0" data-testid="fund-calls-tab-content">
            {/* Iter90dd : refonte - mouvements du grand livre (aligne balance de tiers)
                iter90fy : selecteur exercice comptable au lieu de dates libres
                iter90g3 : bouton telechargement PDF des appels de fonds/mouvements */}
            <MovementsTab
              movements={movements}
              loading={movementsLoading}
              openingBalance={openingBalance}
              closingBalance={closingBalance}
              fiscalYears={fiscalYears}
              selectedFyId={selectedFyId}
              onSelectedFyId={setSelectedFyId}
              acpFiltered={selectedAcp !== 'all'}
              copyVcs={copyVcs}
              vcsCode={owner.vcs_code}
              copropriete_id={selectedAcp}
              periodStart={periodStart}
              periodEnd={periodEnd}
              onPeriodStartChange={setPeriodStart}
              onPeriodEndChange={setPeriodEnd}
              defaultAcpAccount={defaultAcpAccount}
            />
          </TabsContent>

          <TabsContent value="charges" className="mt-0">
            <Card><CardContent className="p-0">
              {filteredCharges.length === 0 ? (
                <div className="p-8 text-center text-slate-400">Aucune charge enregistree</div>
              ) : (
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Fournisseur</TableHead>
                    <TableHead>N&deg; facture</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead>Statut</TableHead>
                    <TableHead className="text-right">Total facture</TableHead>
                    <TableHead className="text-right">Votre part</TableHead>
                    <TableHead className="text-center">Facture</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {filteredCharges.map(c => (
                      <TableRow key={c.id} data-testid={`charge-row-${c.id}`} className={c.source === 'od' ? 'bg-indigo-50/40' : ''}>
                        <TableCell className="text-xs">{fmtDate(c.date)}</TableCell>
                        <TableCell className="text-sm font-medium">
                          {c.supplier}
                          {c.source === 'od' && (
                            <Badge variant="outline" className="ml-1 text-[9px] bg-indigo-50 text-indigo-700 border-indigo-200" data-testid={`charge-od-badge-${c.id}`} title={`Operation Diverse ${c.account_number ? '(compte ' + c.account_number + ')' : ''}`}>OD</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-xs font-mono text-slate-600">{c.number || <span className="text-slate-300 italic">-</span>}</TableCell>
                        <TableCell className="text-xs text-slate-600 max-w-xs truncate" title={c.description}>{c.description}</TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={`text-[10px] ${c.source === 'od' ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : c.status === 'paid' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}
                          >
                            {c.source === 'od' ? 'Ecriture diverse' : (c.status === 'paid' ? 'Payee' : 'En attente')}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-slate-400">{fmt(c.total_amount)}</TableCell>
                        <TableCell className="text-right font-mono text-sm text-slate-900 font-semibold">
                          {fmt(c.my_amount)}
                          {c.computed_share && (
                            <div
                              className="text-[10px] font-normal text-blue-600 mt-0.5"
                              title={`Quote-part projetee via la cle de repartition${c.distribution_key_name ? ` "${c.distribution_key_name}"` : ''}${c.my_share_pct ? ` (${c.my_share_pct.toFixed(2)} %)` : ''}. La ventilation definitive sera figee au decompte annuel.`}
                              data-testid={`charge-projected-${c.id}`}
                            >
                              Projete {c.my_share_pct ? `(${c.my_share_pct.toFixed(2)} %)` : ''}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-center">
                          {c.attachments && c.attachments.length > 0 ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-7 px-2 text-xs text-[#022D52] hover:bg-blue-50"
                              data-testid={`view-invoice-btn-${c.id}`}
                              onClick={async () => {
                                const att = c.attachments[0];
                                const loadingToast = toast.loading('Ouverture de la facture...');
                                try {
                                  const resp = await api.get(
                                    `/owner/invoices/${c.id}/attachments/${att.id}/download`,
                                    { params: { disposition: 'inline' }, responseType: 'blob' },
                                  );
                                  const blob = new Blob([resp.data], { type: att.mime_type || 'application/pdf' });
                                  const url = window.URL.createObjectURL(blob);
                                  // Iter90dd : utilise <a> plutot que window.open (bypass popup blocker)
                                  const link = document.createElement('a');
                                  link.href = url;
                                  link.target = '_blank';
                                  link.rel = 'noopener noreferrer';
                                  document.body.appendChild(link);
                                  link.click();
                                  document.body.removeChild(link);
                                  toast.dismiss(loadingToast);
                                  setTimeout(() => window.URL.revokeObjectURL(url), 60000);
                                } catch (err) {
                                  toast.dismiss(loadingToast);
                                  console.error('Erreur telechargement facture', err);
                                  const status = err.response?.status;
                                  const msg = err.response?.data?.detail || err.message || 'Erreur inconnue';
                                  toast.error(`Impossible d'ouvrir la facture${status ? ` (${status})` : ''} : ${msg}`);
                                }
                              }}
                            >
                              <Eye size={13} className="mr-1" /> Voir
                            </Button>
                          ) : (
                            <span className="text-[10px] text-slate-300 italic">Aucune PJ</span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="bg-slate-50 font-semibold">
                      <TableCell colSpan={6} className="text-right">Total votre quote-part:</TableCell>
                      <TableCell className="text-right font-mono">{fmt(filteredCharges.reduce((s,c) => s + c.my_amount, 0))}</TableCell>
                      <TableCell></TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              )}
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="documents" className="mt-0">
            <OwnerDocumentsView documents={filteredDocs} />
          </TabsContent>

          {/* iter90hw + iter90hz + iter90i0 : nouvel onglet "Comptes bancaires"
              (transparence totale sur les comptes de l'ACP : IBAN, solde
              comptable, mouvements filtres par periode) */}
          <TabsContent value="bank-accounts" className="mt-0" data-testid="bank-accounts-tab-content">
            <BankAccountsTab
              bankAccounts={bankAccounts}
              loading={bankAccountsLoading}
              acpSelected={!!selectedAcp && selectedAcp !== 'all'}
              startDate={bankAccountsStart}
              endDate={bankAccountsEnd}
              onStartDateChange={setBankAccountsStart}
              onEndDateChange={setBankAccountsEnd}
              fiscalYear={fiscalYears.find(y => y.id === selectedFyId) || null}
            />
          </TabsContent>

          {/* Iter90db : Communications - historique des emails envoyes par le syndic */}
          <TabsContent value="communications" className="mt-0" data-testid="communications-tab-content">
            <CommunicationsTab
              communications={filteredCommunications}
              onOpenComm={(id) => setSelectedComm(id)}
            />
          </TabsContent>

          {/* iter89 : Mon profil - modification self-service */}
          <TabsContent value="profile" className="mt-0">
            <Card data-testid="profile-card">
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Mes coordonnees</CardTitle>
                    <p className="text-xs text-slate-500">Modifiez vos informations. Toute modification est automatiquement transmise par email a votre syndic.</p>
                  </div>
                  {/* iter90dh : bouton "Revoir le guide" */}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowTour(true)}
                    className="text-xs shrink-0"
                    data-testid="show-tour-btn"
                  >
                    <Sparkles size={13} className="mr-1.5" /> Revoir le guide
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {profileForm && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Nom</label>
                        <Input value={profileForm.last_name} onChange={e => setProfileForm({...profileForm, last_name: e.target.value})} data-testid="profile-last-name" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Prenom</label>
                        <Input value={profileForm.first_name} onChange={e => setProfileForm({...profileForm, first_name: e.target.value})} data-testid="profile-first-name" />
                      </div>
                    </div>
                    <div>
                      <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Adresse</label>
                      <Input value={profileForm.address} onChange={e => setProfileForm({...profileForm, address: e.target.value})} data-testid="profile-address" />
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Code postal</label>
                        <Input value={profileForm.postal_code} onChange={e => setProfileForm({...profileForm, postal_code: e.target.value})} data-testid="profile-postal" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Ville</label>
                        <Input value={profileForm.city} onChange={e => setProfileForm({...profileForm, city: e.target.value})} data-testid="profile-city" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Pays</label>
                        <Input value={profileForm.country} onChange={e => setProfileForm({...profileForm, country: e.target.value})} data-testid="profile-country" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email principal</label>
                        <Input value={profileForm.email} onChange={e => setProfileForm({...profileForm, email: e.target.value})} data-testid="profile-email" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email secondaire</label>
                        <Input value={profileForm.email2} onChange={e => setProfileForm({...profileForm, email2: e.target.value})} data-testid="profile-email2" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM</label>
                        <Input value={profileForm.phone} onChange={e => setProfileForm({...profileForm, phone: e.target.value})} data-testid="profile-phone" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM 2</label>
                        <Input value={profileForm.phone2} onChange={e => setProfileForm({...profileForm, phone2: e.target.value})} data-testid="profile-phone2" />
                      </div>
                    </div>
                    <div className="pt-3 border-t border-slate-100">
                      <Button onClick={saveProfile} disabled={savingProfile} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="profile-save-btn">
                        <Save size={14} className="mr-1.5" />
                        {savingProfile ? 'Enregistrement...' : 'Enregistrer mes modifications'}
                      </Button>
                      <p className="text-[11px] text-slate-500 italic mt-2">
                        Pour modifier votre VCS ou votre code auxiliaire (impacts comptables), contactez directement votre syndic.
                      </p>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* iter89 : Mes locataires - CRUD self-service */}
          <TabsContent value="tenants" className="mt-0">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
                <div>
                  <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Mes locataires</CardTitle>
                  <p className="text-xs text-slate-500">Toute modification est notifiee a votre syndic par email.</p>
                </div>
                <Button onClick={openCreateTenant} size="sm" className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="add-tenant-btn" disabled={myLots.length === 0}>
                  <Plus size={14} className="mr-1.5" /> Ajouter
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                {tenants.length === 0 ? (
                  <div className="p-8 text-center text-slate-400 text-sm">
                    {myLots.length === 0 ? "Aucun lot enregistre - contactez votre syndic" : "Aucun locataire. Cliquez sur Ajouter pour en creer un."}
                  </div>
                ) : (
                  <Table>
                    <TableHeader><TableRow>
                      <TableHead>Locataire</TableHead><TableHead>Lot(s)</TableHead>
                      <TableHead>Email</TableHead><TableHead>GSM</TableHead>
                      <TableHead>Bail</TableHead><TableHead>Boite/Sonnette</TableHead>
                      <TableHead className="w-24">Actions</TableHead>
                    </TableRow></TableHeader>
                    <TableBody>
                      {tenants.map(t => {
                        // Iter90dg : plusieurs lots possibles
                        const tenantLotIds = t.lot_ids && t.lot_ids.length > 0
                          ? t.lot_ids
                          : (t.lot_id ? [t.lot_id] : []);
                        const tenantLots = tenantLotIds
                          .map(id => myLots.find(l => l.id === id))
                          .filter(Boolean);
                        return (
                          <TableRow key={t.id} data-testid={`tenant-row-${t.id}`}>
                            <TableCell className="font-medium text-sm">{t.name}</TableCell>
                            <TableCell className="text-xs">
                              {tenantLots.length === 0 ? '-' : (
                                <div className="flex flex-wrap gap-1">
                                  {tenantLots.map(l => (
                                    <Badge key={l.id} variant="outline" className="text-[10px] bg-slate-50">
                                      {l.number}{l.description ? ' - ' + l.description : ''}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="text-xs font-mono text-slate-600">{t.email || '-'}</TableCell>
                            <TableCell className="text-xs font-mono text-slate-600">{t.phone || '-'}</TableCell>
                            <TableCell className="text-xs text-slate-600">
                              {t.lease_start ? fmtDate(t.lease_start) : '-'} - {t.lease_end ? fmtDate(t.lease_end) : '-'}
                            </TableCell>
                            <TableCell className="text-xs text-slate-700 max-w-[180px] truncate" title={t.mailbox_names || ''}>
                              {t.mailbox_names || <span className="text-slate-300">-</span>}
                            </TableCell>
                            <TableCell>
                              <div className="flex gap-1">
                                <Button variant="ghost" size="sm" onClick={() => openEditTenant(t)} data-testid={`edit-tenant-${t.id}`}><Pencil size={13} /></Button>
                                <Button variant="ghost" size="sm" onClick={() => deleteTenant(t)} className="text-red-500" data-testid={`delete-tenant-${t.id}`}><Trash2 size={13} /></Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* iter89 : Dialog locataire (create / edit) */}
      <Dialog open={tenantDialog} onOpenChange={setTenantDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingTenant ? 'Modifier le locataire' : 'Nouveau locataire'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Nom complet *</label>
              <Input value={tenantForm.name} onChange={e => setTenantForm({...tenantForm, name: e.target.value})} data-testid="tenant-name" />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">
                Lot(s) * <span className="text-slate-400 italic normal-case">(cochez un ou plusieurs lots)</span>
              </label>
              {myLots.length === 0 ? (
                <div className="text-xs text-slate-400 italic p-2">Aucun lot enregistre</div>
              ) : (
                <div className="border border-slate-200 rounded-md p-2 max-h-40 overflow-y-auto space-y-1 bg-slate-50/50" data-testid="tenant-lots-checkbox-list">
                  {myLots.map(l => {
                    const checked = (tenantForm.lot_ids || []).includes(l.id);
                    return (
                      <label
                        key={l.id}
                        className="flex items-center gap-2 px-2 py-1 rounded cursor-pointer hover:bg-white text-sm"
                        data-testid={`tenant-lot-checkbox-${l.id}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleTenantLot(l.id)}
                          className="w-4 h-4 accent-[#022D52]"
                        />
                        <span className="font-mono text-xs text-slate-500 min-w-[40px]">Lot {l.number}</span>
                        {l.description && <span className="text-slate-700 truncate">{l.description}</span>}
                      </label>
                    );
                  })}
                </div>
              )}
              {(tenantForm.lot_ids || []).length > 0 && (
                <div className="text-[10px] text-slate-500 mt-1">
                  {tenantForm.lot_ids.length} lot(s) selectionne(s)
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email</label>
                <Input value={tenantForm.email} onChange={e => setTenantForm({...tenantForm, email: e.target.value})} data-testid="tenant-email" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM</label>
                <Input value={tenantForm.phone} onChange={e => setTenantForm({...tenantForm, phone: e.target.value})} data-testid="tenant-phone" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Debut bail</label>
                <Input type="date" value={tenantForm.lease_start} onChange={e => setTenantForm({...tenantForm, lease_start: e.target.value})} data-testid="tenant-lease-start" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Fin bail</label>
                <Input type="date" value={tenantForm.lease_end} onChange={e => setTenantForm({...tenantForm, lease_end: e.target.value})} data-testid="tenant-lease-end" />
              </div>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">
                Noms a mentionner sur la boite aux lettres / sonnette
              </label>
              <Input
                value={tenantForm.mailbox_names}
                onChange={e => setTenantForm({...tenantForm, mailbox_names: e.target.value})}
                placeholder="Ex : Famille Dupont, Cabinet SPRL Dupont"
                data-testid="tenant-mailbox-names"
              />
              <p className="text-[10px] text-slate-400 mt-1 italic">
                Informe le syndic des noms visibles sur la boite/sonnette (utile pour les concierges, coursiers, secours).
              </p>
            </div>
            <div className="flex gap-2 justify-end pt-2 border-t border-slate-100">
              <Button variant="outline" onClick={() => setTenantDialog(false)}>Annuler</Button>
              <Button onClick={saveTenant} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="tenant-save-btn">
                {editingTenant ? 'Enregistrer' : 'Ajouter'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter90db : Dialog detail communication */}
      <CommunicationDetailDialog
        commId={selectedComm}
        onClose={() => setSelectedComm(null)}
      />

      {/* iter90dh : Tour guide de premiere connexion */}
      <OwnerOnboardingTour
        open={showTour}
        onClose={() => setShowTour(false)}
        onFinish={() => {
          try {
            const key = ownerTourStorageKey(owner?.email || user?.email || '');
            localStorage.setItem(key, new Date().toISOString());
          } catch { /* skip */ }
        }}
      />

      <footer className="max-w-6xl mx-auto pt-6 pb-4 border-t border-slate-200 mt-8">
        <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-[10px] text-slate-400" data-testid="owner-portal-legal-footer">
          <a href="/legal/cgu" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">CGU</a>
          <span>·</span>
          <a href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Confidentialite</a>
          <span>·</span>
          <a href="/legal/mentions" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Mentions Legales</a>
          <span>·</span>
          <a href="/legal/cookies" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Cookies</a>
        </div>
      </footer>
    </div>
  );
}

function StatCard({ icon, label, value, highlight, badge }) {
  const color = highlight === 'red' ? 'text-red-600' : highlight === 'green' ? 'text-green-600' : 'text-slate-900';
  return (
    <Card className="border-slate-200">
      <CardContent className="p-4">
        <div className="flex items-center gap-2 mb-1.5 text-slate-400">
          {icon}<span className="text-[10px] uppercase tracking-wider">{label}</span>
        </div>
        <div className={`text-xl font-semibold ${color}`} style={{fontFamily:'Chivo,sans-serif'}}>{value}</div>
        {badge && <Badge variant="outline" className={`mt-1 text-[10px] ${highlight === 'red' ? 'border-red-200 text-red-700' : highlight === 'green' ? 'border-green-200 text-green-700' : ''}`}>{badge}</Badge>}
      </CardContent>
    </Card>
  );
}

// ==============================================================
// iter90da (Feb 2026) : onglet "Ma situation" - visuels executifs
// iter90i8 : sélecteur de trimestre pour lecture apaisante
// ==============================================================

// Palette pour donut charges (categoriel, contrastee)
const CHARGE_COLORS = ['#022D52', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#EC4899', '#06B6D4', '#84CC16', '#F97316', '#6366F1', '#14B8A6', '#A855F7'];

// iter90h5 : selecteur de trimestre - les trimestres suivent l'EXERCICE FISCAL,
// pas l'annee civile. Les labels sont dynamiques (ex: Mar-Mai pour un FY
// demarrant le 01/03). Le bouton "annee entiere" affiche le nom de l'exercice.
function QuarterSelector({ selected, onChange, currentQuarter, quarters, fyLabel }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="quarter-selector">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <CalendarClock size={16} className="text-[#022D52] shrink-0" />
          <div className="flex flex-col">
            <span className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold">Periode</span>
            <span className="text-[10px] text-slate-400">Exercice {fyLabel}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap ml-auto">
          {quarters.map((q) => {
            const isCurrent = q.id === currentQuarter;
            const isSelected = q.id === selected;
            return (
              <button
                key={q.id}
                type="button"
                onClick={() => onChange(q.id)}
                className={`relative flex flex-col items-center px-3 py-1.5 rounded-md border transition-all ${isSelected
                  ? 'bg-[#022D52] text-white border-[#022D52] shadow-sm'
                  : 'bg-white text-slate-700 border-slate-200 hover:border-[#022D52]/40 hover:bg-slate-50'}`}
                data-testid={`quarter-btn-${q.id}`}
              >
                <span className="text-xs font-semibold">{q.label}</span>
                <span className={`text-[9px] mt-0.5 ${isSelected ? 'text-white/70' : 'text-slate-400'}`}>
                  {q.range}
                </span>
                {isCurrent && !isSelected && (
                  <span className="absolute -top-1.5 -right-1 bg-emerald-500 text-white text-[8px] rounded-full px-1 py-0.5 leading-none">
                    En cours
                  </span>
                )}
              </button>
            );
          })}
          <div className="w-px h-8 bg-slate-200 mx-1" />
          <button
            type="button"
            onClick={() => onChange('year')}
            className={`px-3 py-1.5 rounded-md border transition-all text-xs font-semibold ${selected === 'year'
              ? 'bg-[#022D52] text-white border-[#022D52] shadow-sm'
              : 'bg-white text-slate-700 border-slate-200 hover:border-[#022D52]/40 hover:bg-slate-50'}`}
            data-testid="quarter-btn-year"
            title="Vue de l'exercice complet"
          >
            {fyLabel} entier
          </button>
        </div>
      </div>
    </div>
  );
}

function SituationHero({ status, balance, totalCalled, totalPaid, nextCall, totalPending, totalCharges12m, pendingCount, copyVcs, periodLabel, isYearView }) {
  // Bloc solde : couleur selon statut
  const isDebtor = status === 'debiteur';
  const isCreditor = status === 'crediteur';
  // iter90i8 : palette apaisante quand le proprio n'a rien a payer sur la
  // periode (bleu doux au lieu de gris terne, pour valoriser la bonne situation).
  const soldeBg = isDebtor
    ? 'bg-gradient-to-br from-red-50 to-red-100 border-red-200'
    : isCreditor
      ? 'bg-gradient-to-br from-emerald-50 to-emerald-100 border-emerald-200'
      : 'bg-gradient-to-br from-sky-50 to-blue-50 border-sky-200';
  const soldeText = isDebtor ? 'text-red-700' : isCreditor ? 'text-emerald-700' : 'text-sky-800';
  // iter90i8 : label plus apaisant pour la situation en regle
  const soldeLabel = isDebtor
    ? 'A payer'
    : isCreditor
      ? 'Solde en votre faveur'
      : 'Rien a payer pour cette periode';
  const soldeIcon = isDebtor
    ? <AlertCircle size={20} className="text-red-600" />
    : isCreditor
      ? <CheckCircle2 size={20} className="text-emerald-600" />
      : <CheckCircle2 size={20} className="text-sky-600" />;

  // Ratio paye/appele pour barre de progression
  const paidRatio = totalCalled > 0 ? Math.max(0, Math.min(100, (totalPaid / totalCalled) * 100)) : 100;

  // Bloc prochain paiement : couleur selon urgence
  let nextBg = 'bg-gradient-to-br from-slate-50 to-slate-100 border-slate-200';
  let nextTextColor = 'text-slate-700';
  let nextIconColor = 'text-slate-500';
  if (nextCall) {
    if (nextCall.urgency === 'overdue' || nextCall.urgency === 'urgent') {
      nextBg = 'bg-gradient-to-br from-red-50 to-red-100 border-red-200';
      nextTextColor = 'text-red-700';
      nextIconColor = 'text-red-600';
    } else if (nextCall.urgency === 'soon') {
      nextBg = 'bg-gradient-to-br from-amber-50 to-amber-100 border-amber-200';
      nextTextColor = 'text-amber-700';
      nextIconColor = 'text-amber-600';
    } else {
      nextBg = 'bg-gradient-to-br from-emerald-50 to-emerald-100 border-emerald-200';
      nextTextColor = 'text-emerald-700';
      nextIconColor = 'text-emerald-600';
    }
  } else if (balance > 0.01) {
    // Iter90dd : solde debiteur sans pending detaille -> carte rouge
    nextBg = 'bg-gradient-to-br from-red-50 to-red-100 border-red-200';
    nextTextColor = 'text-red-700';
    nextIconColor = 'text-red-600';
  }

  const nextLabel = (() => {
    if (!nextCall) return 'Aucun paiement en attente';
    if (nextCall.daysDelta === null) return 'Echeance non renseignee';
    if (nextCall.daysDelta < 0) return `En retard de ${Math.abs(nextCall.daysDelta)} jour(s)`;
    if (nextCall.daysDelta === 0) return "A payer aujourd'hui";
    if (nextCall.daysDelta === 1) return 'A payer demain';
    return `Dans ${nextCall.daysDelta} jour(s)`;
  })();

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4" data-testid="situation-hero">
      {/* Carte 1 : Solde global */}
      <Card className={`${soldeBg} border-2`} data-testid="situation-solde-card">
        <CardContent className="p-5">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              {soldeIcon}
              <span className={`text-[11px] uppercase tracking-wider font-semibold ${soldeText}`}>{soldeLabel}</span>
            </div>
            {/* iter90i8 : badge periode active */}
            {periodLabel && (
              <Badge variant="outline" className={`text-[10px] ${soldeText} border-current`} data-testid="situation-period-badge">
                {periodLabel}
              </Badge>
            )}
          </div>
          <div className={`text-3xl font-bold ${soldeText}`} style={{fontFamily:'Chivo,sans-serif'}}>
            {balance > 0.01 ? fmt(balance) : (
              <span className="text-2xl">Aucun montant du</span>
            )}
          </div>
          <div className="mt-3 text-[11px] text-slate-600 space-y-0.5">
            <div className="flex justify-between"><span>Total appele :</span><span className="font-mono">{fmt(totalCalled)}</span></div>
            <div className="flex justify-between"><span>Total paye :</span><span className="font-mono">{fmt(totalPaid)}</span></div>
          </div>
          {/* Barre de progression */}
          <div className="mt-3">
            <div className="text-[10px] text-slate-500 mb-1 flex justify-between">
              <span>Paye</span><span>{paidRatio.toFixed(0)}%</span>
            </div>
            <div className="h-2 rounded-full bg-white/60 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${isDebtor ? 'bg-red-500' : 'bg-emerald-500'}`}
                style={{ width: `${paidRatio}%` }}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Carte 2 : Prochain paiement */}
      <Card className={`${nextBg} border-2`} data-testid="situation-next-card">
        <CardContent className="p-5">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              <CalendarClock size={20} className={nextIconColor} />
              <span className={`text-[11px] uppercase tracking-wider font-semibold ${nextTextColor}`}>Prochain paiement</span>
            </div>
            {pendingCount > 0 && <Badge variant="outline" className={`text-[10px] ${nextTextColor} border-current`}>{pendingCount} en attente</Badge>}
          </div>
          {nextCall ? (
            <>
              {/* iter90h4 : ajustement dynamique selon le solde courant.
                  amountToPay = nextCall.amount + balance (creditor reduit, debtor augmente). */}
              {(() => {
                const adjusted = Math.max(0, (Number(nextCall.amount) || 0) + (Number(balance) || 0));
                const hasAdjustment = Math.abs(Number(balance) || 0) > 0.01;
                const isReduced = (Number(balance) || 0) < -0.01;
                return (
                  <>
                    <div className={`text-3xl font-bold ${nextTextColor}`} style={{fontFamily:'Chivo,sans-serif'}} data-testid="next-payment-amount">
                      {fmt(adjusted)}
                    </div>
                    <div className="mt-1 text-[13px] font-medium text-slate-700 truncate" title={nextCall.fund_call_name}>{nextCall.fund_call_name}</div>
                    {hasAdjustment && (
                      <div className={`mt-1.5 text-[11px] ${isReduced ? 'text-emerald-700' : 'text-red-700'} bg-white/50 rounded px-2 py-1 border ${isReduced ? 'border-emerald-200' : 'border-red-200'}`} data-testid="next-payment-adjustment">
                        <div className="flex justify-between font-mono">
                          <span>Appel :</span><span>{fmt(nextCall.amount)}</span>
                        </div>
                        <div className="flex justify-between font-mono">
                          <span>{isReduced ? 'Votre credit :' : 'Solde du :'}</span>
                          <span>{isReduced ? `- ${fmt(Math.abs(balance))}` : `+ ${fmt(balance)}`}</span>
                        </div>
                        <div className="flex justify-between font-mono font-bold border-t border-current mt-1 pt-1">
                          <span>A payer :</span><span>{fmt(adjusted)}</span>
                        </div>
                      </div>
                    )}
                    <div className={`mt-2 text-xs font-semibold flex items-center gap-1.5 ${nextTextColor}`}>
                      <Clock size={12} />
                      {nextLabel} {nextCall.due_date && <span className="text-slate-500 font-normal">({fmtDate(nextCall.due_date)})</span>}
                    </div>
                  </>
                );
              })()}
              {nextCall.vcs_code && (
                <button
                  onClick={() => copyVcs(nextCall.vcs_code)}
                  className="mt-3 font-mono text-[10px] text-[#022D52] bg-white/60 hover:bg-white px-2 py-1 rounded inline-flex items-center gap-1"
                  data-testid="situation-next-vcs-btn"
                >
                  {nextCall.vcs_code}<Copy size={9} />
                </button>
              )}
              {pendingCount > 1 && (
                <div className="mt-3 text-[11px] text-slate-600 pt-2 border-t border-white/60">
                  Total en attente : <span className="font-mono font-semibold">{fmt(totalPending)}</span>
                </div>
              )}
            </>
          ) : balance > 0.01 ? (
            // Iter90dd : fallback quand balance debiteur sans pending detaille
            <>
              <div className="text-3xl font-bold text-red-700" style={{fontFamily:'Chivo,sans-serif'}}>{fmt(balance)}</div>
              <div className="mt-1 text-[13px] font-medium text-slate-700">Solde a payer</div>
              <div className="mt-2 text-xs font-semibold flex items-center gap-1.5 text-red-700">
                <Clock size={12} />
                Voir onglet &quot;Appels de fonds&quot; pour le detail
              </div>
              <div className="mt-3 text-[11px] text-slate-600 italic">
                Utilisez votre communication structuree pour tout virement
              </div>
            </>
          ) : (
            <>
              <div className={`text-3xl font-bold ${nextTextColor}`} style={{fontFamily:'Chivo,sans-serif'}}>—</div>
              <div className="mt-2 text-xs text-slate-600">Tout est a jour. Merci !</div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Carte 3 : Total charges de la periode selectionnee (trimestre ou annee) */}
      <Card className="bg-gradient-to-br from-blue-50 to-indigo-100 border-2 border-blue-200" data-testid="situation-charges-card">
        <CardContent className="p-5">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              <TrendingUp size={20} className="text-[#022D52]" />
              <span className="text-[11px] uppercase tracking-wider font-semibold text-[#01213e]">
                {isYearView ? 'Charges exercice' : 'Charges trimestre'}
              </span>
            </div>
            {periodLabel && (
              <Badge variant="outline" className="text-[10px] text-blue-800 border-blue-300">
                {periodLabel}
              </Badge>
            )}
          </div>
          <div className="text-3xl font-bold text-blue-800" style={{fontFamily:'Chivo,sans-serif'}}>{fmt(totalCharges12m)}</div>
          <div className="mt-2 text-xs text-slate-600">
            {isYearView
              ? "Cumul de votre quote-part sur l'exercice comptable"
              : "Cumul de votre quote-part sur le trimestre selectionne"}
          </div>
          <div className="mt-3 text-[11px] text-slate-500 pt-2 border-t border-white/60">
            {totalCharges12m > 0 ? "Voir la repartition par categorie ci-dessous" : "Aucune charge sur la periode"}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ChargesDonut({ data, total, periodLabel }) {
  return (
    <Card className="lg:col-span-3 border-slate-200" data-testid="situation-donut-card">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <PieChartIcon size={16} className="text-[#022D52]" />
            <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>
              Charges par categorie{periodLabel ? ` - ${periodLabel}` : ''}
            </CardTitle>
          </div>
          <span className="text-[11px] text-slate-500 font-mono">Total : {fmt(total)}</span>
        </div>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            Aucune charge sur les 12 derniers mois
          </div>
        ) : (
          <div className="flex flex-col md:flex-row items-center gap-4">
            <div className="w-full md:w-1/2 h-64 relative">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={data}
                    cx="50%"
                    cy="50%"
                    innerRadius={55}
                    outerRadius={90}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {data.map((entry, index) => (
                      <Cell key={entry.name} fill={CHARGE_COLORS[index % CHARGE_COLORS.length]} />
                    ))}
                  </Pie>
                  <RechartsTooltip
                    formatter={(value) => fmt(value)}
                    contentStyle={{ fontSize: '12px', borderRadius: '6px' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <div className="text-[10px] uppercase tracking-wider text-slate-400">Total</div>
                <div className="text-lg font-bold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>{fmt(total)}</div>
              </div>
            </div>
            <div className="w-full md:w-1/2 space-y-2">
              {data.map((entry, index) => {
                const pct = total > 0 ? (entry.value / total) * 100 : 0;
                return (
                  <div key={entry.name} className="flex items-center gap-2" data-testid={`donut-legend-${index}`}>
                    <span
                      className="w-3 h-3 rounded-sm flex-shrink-0"
                      style={{ background: CHARGE_COLORS[index % CHARGE_COLORS.length] }}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium text-slate-800 truncate">{entry.name}</div>
                      <div className="text-[10px] text-slate-500">{pct.toFixed(1)}%</div>
                    </div>
                    <div className="text-xs font-mono font-semibold text-slate-900">{fmt(entry.value)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function UpcomingTimeline({ items, copyVcs }) {
  return (
    <Card className="lg:col-span-2 border-slate-200" data-testid="situation-timeline-card">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <CalendarClock size={16} className="text-[#022D52]" />
          <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Prochaines echeances</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-sm">
            <CheckCircle2 size={32} className="mx-auto mb-2 text-emerald-500" />
            Vous etes a jour. Aucun appel en attente.
          </div>
        ) : (
          <div className="relative pl-6 max-h-80 overflow-y-auto pr-1">
            {/* Ligne verticale de timeline */}
            <div className="absolute left-2 top-2 bottom-2 w-0.5 bg-slate-200" />
            <div className="space-y-3">
              {items.slice(0, 8).map((it, i) => {
                let dotColor = 'bg-emerald-500';
                let badgeCls = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                let label = '';
                if (it.urgency === 'overdue') {
                  dotColor = 'bg-red-500 animate-pulse';
                  badgeCls = 'bg-red-50 text-red-700 border-red-200';
                  label = `${Math.abs(it.daysDelta)}j de retard`;
                } else if (it.urgency === 'urgent') {
                  dotColor = 'bg-red-500';
                  badgeCls = 'bg-red-50 text-red-700 border-red-200';
                  label = it.daysDelta === 0 ? "Aujourd'hui" : `Dans ${it.daysDelta}j`;
                } else if (it.urgency === 'soon') {
                  dotColor = 'bg-amber-500';
                  badgeCls = 'bg-amber-50 text-amber-700 border-amber-200';
                  label = `Dans ${it.daysDelta}j`;
                } else {
                  label = it.daysDelta !== null ? `Dans ${it.daysDelta}j` : 'A venir';
                }
                return (
                  <div key={`${it.fund_call_name}-${i}`} className="relative" data-testid={`timeline-item-${i}`}>
                    <span className={`absolute -left-4 top-1.5 w-3 h-3 rounded-full ring-2 ring-white ${dotColor}`} />
                    <div className="bg-white border border-slate-200 rounded-md p-2.5 hover:shadow-sm transition-shadow">
                      <div className="flex items-start justify-between gap-2 mb-1">
                        <div className="text-xs font-medium text-slate-900 truncate flex-1" title={it.fund_call_name}>{it.fund_call_name}</div>
                        <Badge variant="outline" className={`text-[9px] whitespace-nowrap ${badgeCls}`}>{label}</Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-[10px] text-slate-500">{fmtDate(it.due_date)}</span>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold font-mono text-slate-900">{fmt(it.amount)}</span>
                          {it.vcs_code && (
                            <button
                              onClick={() => copyVcs(it.vcs_code)}
                              className="font-mono text-[9px] text-[#022D52] bg-blue-50 hover:bg-blue-100 px-1.5 py-0.5 rounded inline-flex items-center gap-0.5"
                              title="Copier VCS"
                            >
                              <Copy size={9} />
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {items.length > 8 && (
              <div className="text-[10px] text-slate-400 text-center pt-2 italic">
                +{items.length - 8} autre(s) echeance(s)
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ==============================================================
// iter90db (Feb 2026) : Legende categories docs + Tab Communications
// ==============================================================

function DocumentCategoryLegend({ documents }) {
  const counts = documents.reduce((acc, d) => {
    const name = d.category_name || 'Sans categorie';
    acc[name] = (acc[name] || 0) + 1;
    return acc;
  }, {});
  const cats = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (cats.length <= 1) return null;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 p-2 rounded-md bg-slate-50 border border-slate-200" data-testid="doc-legend">
      <span className="text-[10px] uppercase tracking-wider text-slate-500 mr-1">Categories :</span>
      {cats.map(([name, count]) => {
        const color = categoryColor(name);
        return (
          <span
            key={name}
            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium ${color.bg} ${color.text} border ${color.border}`}
          >
            <span className={`w-1.5 h-1.5 rounded-full ${color.dot}`} />
            {name}
            <span className="text-[10px] opacity-70">({count})</span>
          </span>
        );
      })}
    </div>
  );
}

// iter90hu (juil 2026) : nouvelle vue documents cote proprietaire.
// - Encart "Dernier document ajoute" en haut
// - Tuiles par categorie avec code couleur (grand format cliquable)
// - Click sur une tuile -> vue detaillee des documents de cette categorie
function OwnerDocumentsView({ documents }) {
  const [selectedCategory, setSelectedCategory] = useState(null);
  if (!documents || documents.length === 0) {
    return (
      <Card><CardContent className="p-8 text-center text-slate-400">
        Aucun document partage
      </CardContent></Card>
    );
  }
  // Groupement par categorie
  const byCategory = documents.reduce((acc, d) => {
    const name = d.category_name || 'Sans categorie';
    if (!acc[name]) acc[name] = [];
    acc[name].push(d);
    return acc;
  }, {});
  const categoryList = Object.keys(byCategory).sort();
  // Dernier document (tri par created_at desc)
  const lastDoc = [...documents].sort((a, b) => {
    const da = a.created_at || '';
    const db = b.created_at || '';
    return db.localeCompare(da);
  })[0];

  // Vue "dans une categorie"
  if (selectedCategory) {
    const catDocs = byCategory[selectedCategory] || [];
    const color = categoryColor(selectedCategory);
    return (
      <div data-testid="owner-docs-category-view">
        <div className="flex items-center gap-3 mb-4">
          <Button variant="outline" size="sm" onClick={() => setSelectedCategory(null)} data-testid="docs-back-to-categories">
            <ArrowLeft size={14} className="mr-1" /> Retour
          </Button>
          <Badge className={`${color.bg} ${color.text} ${color.border} border`}>
            <span className={`inline-block w-2 h-2 rounded-full ${color.dot} mr-1.5`} />
            {selectedCategory}
          </Badge>
          <span className="text-xs text-slate-500">{catDocs.length} document{catDocs.length > 1 ? 's' : ''}</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {catDocs.map(d => (
            <Card key={d.id} className={`border-l-4 hover:shadow-md transition-shadow ${color.border} border-slate-200`} data-testid={`doc-card-${d.id}`}>
              <CardContent className="p-3">
                <div className="flex items-start gap-2 mb-1">
                  <div className={`w-8 h-8 rounded-md ${color.bg} flex items-center justify-center flex-shrink-0`}>
                    <FileText size={15} className={color.text} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-slate-900 truncate">{d.title}</div>
                    {d.description && <div className="text-[11px] text-slate-500 line-clamp-2">{d.description}</div>}
                  </div>
                </div>
                <div className="flex items-center justify-between mt-2 gap-2">
                  <span className="text-[10px] text-slate-400">{fmtDate(d.created_at)}</span>
                  {(d.filename || d.gridfs_id) && (
                    <a
                      href={`${process.env.REACT_APP_BACKEND_URL}/api/documents/${d.id}/download`}
                      target="_blank" rel="noreferrer"
                      className="text-[#022D52] hover:bg-blue-50 p-1 rounded flex items-center gap-1 text-[11px]"
                      title="Telecharger"
                    >
                      <ArrowDownToLine size={13} /> Consulter
                    </a>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    );
  }

  // Vue par defaut : encart dernier + tuiles catégories
  return (
    <div data-testid="owner-docs-categories-view">
      {/* Encart dernier document ajoute */}
      {lastDoc && (
        <Card className="mb-4 border-blue-200 bg-blue-50/30" data-testid="owner-docs-latest-card">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-lg bg-blue-100 flex items-center justify-center flex-shrink-0">
                <FileText size={20} className="text-[#022D52]" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">Dernier document ajoute</div>
                <div className="font-semibold text-sm text-slate-900 truncate">{lastDoc.title}</div>
                <div className="text-[11px] text-slate-500 flex items-center gap-2 mt-0.5">
                  <span>{lastDoc.category_name || 'Sans categorie'}</span>
                  <span>-</span>
                  <span>{fmtDate(lastDoc.created_at)}</span>
                </div>
              </div>
              {(lastDoc.filename || lastDoc.gridfs_id) && (
                <a
                  href={`${process.env.REACT_APP_BACKEND_URL}/api/documents/${lastDoc.id}/download`}
                  target="_blank" rel="noreferrer"
                  className="bg-[#022D52] hover:bg-[#1D4ED8] text-white px-3 py-1.5 rounded text-xs flex items-center gap-1"
                  data-testid="owner-docs-latest-download"
                >
                  <ArrowDownToLine size={13} /> Consulter
                </a>
              )}
            </div>
          </CardContent>
        </Card>
      )}
      {/* Tuiles par categorie */}
      <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-2">Categories</div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {categoryList.map(name => {
          const color = categoryColor(name);
          const catDocs = byCategory[name];
          return (
            <Card
              key={name}
              className={`cursor-pointer hover:shadow-md transition-all border-2 ${color.border}`}
              onClick={() => setSelectedCategory(name)}
              data-testid={`docs-category-tile-${name.replace(/\s+/g, '-').toLowerCase()}`}
            >
              <CardContent className={`p-4 ${color.bg}`}>
                <div className="flex items-center gap-3">
                  <div className={`w-11 h-11 rounded-lg bg-white/50 flex items-center justify-center flex-shrink-0`}>
                    <FileText size={20} className={color.text} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className={`text-sm font-semibold truncate ${color.text}`}>{name}</div>
                    <div className={`text-[11px] mt-0.5 ${color.text} opacity-75`}>
                      {catDocs.length} document{catDocs.length > 1 ? 's' : ''}
                    </div>
                  </div>
                  <ArrowRight size={16} className={color.text} />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function CommunicationsTab({ communications, onOpenComm }) {
  if (!communications || communications.length === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-slate-400">
          <MailOpen size={32} className="mx-auto mb-2 text-slate-300" />
          Aucun email envoye par votre syndic pour le moment
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-2" data-testid="communications-list">
      {communications.map((c) => {
        const meta = COMM_KIND_META[c.kind] || COMM_KIND_META.generic;
        const KindIcon = meta.icon;
        // Extract classes from meta.color (e.g. "bg-blue-100 text-[#01213e] border-blue-200")
        const [bgCls, textCls] = meta.color.split(' ');
        return (
          <Card
            key={c.id}
            className="border-slate-200 hover:shadow-md hover:border-[#022D52]/30 transition-all cursor-pointer"
            data-testid={`comm-card-${c.id}`}
            onClick={() => onOpenComm(c.id)}
          >
            <CardContent className="p-3">
              <div className="flex items-start gap-3">
                <div className={`w-9 h-9 rounded-md ${bgCls} flex items-center justify-center flex-shrink-0`}>
                  <KindIcon size={16} className={textCls} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="font-semibold text-sm text-slate-900 truncate">{c.subject}</span>
                      {c.has_attachment && <Paperclip size={12} className="text-slate-400 flex-shrink-0" />}
                    </div>
                    <span className="text-[11px] text-slate-500 whitespace-nowrap">{fmtDate(c.sent_at)}</span>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <Badge variant="outline" className={`text-[10px] ${meta.color}`}>{meta.label}</Badge>
                    <span className="text-[11px] text-slate-500 flex items-center gap-1">
                      <Send size={10} /> {c.from_mailbox}
                    </span>
                    {c.copropriete_name && (
                      <span className="text-[11px] text-slate-500">- {c.copropriete_name}</span>
                    )}
                  </div>
                  {c.body_preview && (
                    <div className="text-[12px] text-slate-600 line-clamp-2 leading-relaxed">{c.body_preview}</div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

function CommunicationDetailDialog({ commId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!commId) { setData(null); return; }
    setLoading(true); setError(null);
    api.get(`/owner/communications/${commId}`)
      .then((r) => setData(r.data))
      .catch((err) => setError(err.response?.data?.detail || 'Erreur de chargement'))
      .finally(() => setLoading(false));
  }, [commId]);

  const meta = data ? (COMM_KIND_META[data.kind] || COMM_KIND_META.generic) : null;

  return (
    <Dialog open={!!commId} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-3xl w-[95vw] max-h-[90vh] overflow-y-auto" data-testid="comm-detail-dialog">
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="pr-8">
            {data?.subject || 'Communication'}
          </DialogTitle>
        </DialogHeader>
        {loading && <div className="p-6 text-center text-slate-400 text-sm">Chargement...</div>}
        {error && (
          <div className="p-4 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}
        {data && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 pb-3 border-b border-slate-100">
              {meta && <Badge variant="outline" className={`text-[11px] ${meta.color}`}>{meta.label}</Badge>}
              <div className="text-[11px] text-slate-500 flex items-center gap-1">
                <Send size={11} /> De {data.from_mailbox}
              </div>
              <div className="text-[11px] text-slate-500">A : {(data.to || []).join(', ')}</div>
              <div className="text-[11px] text-slate-500 ml-auto">{fmtDate(data.sent_at)}</div>
            </div>
            {data.copropriete_name && (
              <div className="text-[11px] text-slate-500">
                <Building2 size={11} className="inline mr-1" />
                Copropriete : {data.copropriete_name}
              </div>
            )}
            {data.has_attachment && (
              <div className="flex items-center gap-2 p-2 bg-blue-50 border border-blue-200 rounded text-xs">
                <Paperclip size={13} className="text-[#022D52]" />
                <span className="text-blue-800 flex-1">
                  Piece jointe : <b>{data.attachment_filename || 'document.pdf'}</b>
                </span>
                {/* iter90hs : PJ archivee en GridFS -> consultable et disponible en onglet Documents */}
                {data.attachment_gridfs_id ? (
                  <a
                    href={`${process.env.REACT_APP_BACKEND_URL}/api/owner/communications/${data.id}/attachment/download`}
                    target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-1 bg-[#022D52] hover:bg-[#1D4ED8] text-white px-2.5 py-1 rounded text-[11px]"
                    data-testid="comm-attachment-download"
                    title="Consulter le document"
                  >
                    <ArrowDownToLine size={11} /> Consulter
                  </a>
                ) : (
                  <span className="text-[10px] text-slate-500 italic">
                    (retrouvez la aussi dans l&apos;onglet Documents)
                  </span>
                )}
              </div>
            )}
            <div
              className="prose prose-sm max-w-none p-4 border border-slate-200 rounded bg-white text-slate-800"
              style={{fontSize:'14px', lineHeight:'1.6'}}
              dangerouslySetInnerHTML={{ __html: sanitizeHtml(data.body_html || '<p class="text-slate-400">Contenu vide</p>') }}
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ==============================================================
// iter90dd (Feb 2026) : Tab Appels de fonds refonte en Mouvements
// ==============================================================

// Meta par journal_type pour couleurs / libelles
const JOURNAL_TYPE_META = {
  VE: { label: 'Appel de fonds', color: 'bg-orange-50 text-orange-700 border-orange-200', dotColor: 'bg-orange-500' },
  FI: { label: 'Paiement', color: 'bg-emerald-50 text-emerald-700 border-emerald-200', dotColor: 'bg-emerald-500' },
  OD: { label: 'Ecriture diverse', color: 'bg-slate-100 text-slate-700 border-slate-200', dotColor: 'bg-slate-500' },
  AN: { label: 'Report a nouveau', color: 'bg-blue-50 text-[#01213e] border-blue-200', dotColor: 'bg-blue-500' },
  ACH: { label: 'Achat', color: 'bg-purple-50 text-purple-700 border-purple-200', dotColor: 'bg-purple-500' },
};

function MovementsTab({
  movements, loading, openingBalance, closingBalance,
  fiscalYears, selectedFyId, onSelectedFyId,
  acpFiltered, copyVcs, vcsCode,
  copropriete_id, periodStart, periodEnd,
  onPeriodStartChange, onPeriodEndChange,
  defaultAcpAccount,
}) {
  const selectedFy = fiscalYears.find(y => y.id === selectedFyId);
  // iter90hx : montant a payer = solde debiteur (debit-credit sur la periode)
  const amountToPay = useMemo(() => {
    if (!movements || movements.length === 0) return 0;
    const sum = movements.reduce((acc, m) => {
      const d = Number(m.debit || 0);
      const c = Number(m.credit || 0);
      return acc + d - c;
    }, 0);
    // Ajouter le solde d'ouverture (a nouveau)
    const opening = Number(openingBalance || 0);
    return Math.max(0, +(sum + opening).toFixed(2));
  }, [movements, openingBalance]);
  // iter90hx : URL du QR code, rafraichie a chaque changement de periode
  const qrHref = useMemo(() => {
    if (!copropriete_id || !acpFiltered) return null;
    const p = new URLSearchParams();
    if (amountToPay > 0) p.set('amount', amountToPay.toFixed(2));
    // Nonce sur le hash pour forcer le refresh de l'image cote navigateur
    const url = `${process.env.REACT_APP_BACKEND_URL}/api/owner/payment-qr/${copropriete_id}`;
    return `${url}?${p.toString()}&t=${Date.now()}`;
  }, [copropriete_id, acpFiltered, amountToPay]);
  // iter90g3 : URL de telechargement PDF des mouvements. Utilise
  // process.env.REACT_APP_BACKEND_URL avec les cookies d'auth (target=_blank
  // + credentials='include' via header <a>). Les query params passent
  // copropriete_id et la periode selectionnee.
  const pdfHref = useMemo(() => {
    if (!copropriete_id) return null;
    const params = new URLSearchParams({ copropriete_id });
    if (periodStart) params.set('start_date', periodStart);
    if (periodEnd) params.set('end_date', periodEnd);
    return `${process.env.REACT_APP_BACKEND_URL}/api/owner/movements/pdf?${params.toString()}`;
  }, [copropriete_id, periodStart, periodEnd]);
  return (
    <div className="space-y-4" data-testid="movements-tab-body">
      {/* iter90fy : selecteur d'exercice comptable au lieu de dates libres.
          L'utilisateur voit uniquement les exercices de SES ACPs (backend
          /owner/fiscal-years/{cid} enforce chinese wall). */}
      <Card className="border-slate-200 bg-slate-50/60">
        <CardContent className="p-3">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="text-xs font-semibold text-slate-600 flex items-center gap-1.5">
              <CalendarClock size={13} className="text-[#022D52]" />
              Exercice comptable :
            </div>
            {fiscalYears.length === 0 ? (
              <span className="text-xs text-slate-400 italic">
                Aucun exercice defini pour cette copropriete
              </span>
            ) : (
              <>
                <Select value={selectedFyId} onValueChange={onSelectedFyId}>
                  <SelectTrigger className="h-8 text-xs w-[260px]" data-testid="fiscal-year-select">
                    <SelectValue placeholder="Choisir un exercice" />
                  </SelectTrigger>
                  <SelectContent>
                    {fiscalYears.map((fy) => (
                      <SelectItem key={fy.id} value={fy.id} data-testid={`fiscal-year-opt-${fy.id}`}>
                        <div className="flex items-center gap-2">
                          <span>{fy.name}</span>
                          <span className="text-[10px] text-slate-400">({fy.start_date} - {fy.end_date})</span>
                          {fy.status === 'closed' && (
                            <span className="text-[9px] uppercase tracking-wider font-semibold px-1 rounded bg-emerald-100 text-emerald-700 border border-emerald-200">
                              Cloture
                            </span>
                          )}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedFy && (
                  <span className="text-[11px] text-slate-500 font-mono">
                    Du {selectedFy.start_date} au {selectedFy.end_date}
                  </span>
                )}
              </>
            )}
            {!acpFiltered && (
              <span className="ml-auto text-[10px] text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
                Selectionnez une copropriete pour voir un solde detaille
              </span>
            )}
            {/* iter90g3 : bouton de telechargement PDF du grand livre.
                Ne s'affiche que si un exercice est selectionne (pour eviter
                un PDF vide ou trop volumineux). */}
            {acpFiltered && selectedFy && movements.length > 0 && pdfHref && (
              <a
                href={pdfHref}
                target="_blank"
                rel="noreferrer"
                className={`${acpFiltered ? '' : 'ml-auto'} inline-flex items-center gap-1.5 text-xs text-white bg-[#022D52] hover:bg-[#01213e] px-3 py-1.5 rounded-md border border-[#022D52] transition-colors`}
                data-testid="download-movements-pdf-btn"
              >
                <Download size={12} />
                Telecharger le PDF
              </a>
            )}
          </div>
        </CardContent>
      </Card>

      {/* iter90hx : Panneau paiement rapide avec selecteur de date + QR code */}
      {acpFiltered && (
        <Card className="border-slate-200 overflow-hidden" data-testid="quickpay-panel">
          <CardContent className="p-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="md:col-span-2 space-y-3">
                <div className="text-xs font-semibold text-slate-600 flex items-center gap-1.5">
                  <CalendarClock size={13} className="text-[#022D52]" />
                  Situation a la date de votre choix
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5 block">Du</label>
                    <input
                      type="date"
                      value={periodStart || ''}
                      onChange={(e) => onPeriodStartChange && onPeriodStartChange(e.target.value)}
                      className="w-full h-8 px-2 text-xs border border-slate-300 rounded"
                      data-testid="quickpay-date-from"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5 block">Au</label>
                    <input
                      type="date"
                      value={periodEnd || ''}
                      onChange={(e) => onPeriodEndChange && onPeriodEndChange(e.target.value)}
                      className="w-full h-8 px-2 text-xs border border-slate-300 rounded"
                      data-testid="quickpay-date-to"
                    />
                  </div>
                </div>
                <div className="rounded-md bg-gradient-to-br from-slate-50 to-white border border-slate-200 p-4">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Somme a payer</div>
                  <div className={`text-3xl font-black mt-1 ${amountToPay > 0.01 ? 'text-[#DC2626]' : 'text-emerald-600'}`}
                       style={{fontFamily:'Chivo,sans-serif'}} data-testid="quickpay-amount">
                    {amountToPay > 0.01
                      ? amountToPay.toLocaleString('fr-BE', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' EUR'
                      : 'Situation en regle'}
                  </div>
                  {/* iter90hz : IBAN du compte de virement de l'ACP (transparence) */}
                  {defaultAcpAccount && defaultAcpAccount.iban && (
                    <div className="mt-3 pt-3 border-t border-slate-200" data-testid="quickpay-iban-block">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 flex items-center justify-between">
                        <span>Compte a crediter</span>
                        {defaultAcpAccount.label && (
                          <span className="normal-case tracking-normal text-[10px] text-slate-400 italic">
                            {defaultAcpAccount.label}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <code className="text-xs font-mono text-[#022D52] bg-emerald-50 border border-emerald-200 px-2 py-1 rounded flex-1 truncate" data-testid="quickpay-iban">
                          {defaultAcpAccount.iban}
                        </code>
                        <button
                          onClick={() => {
                            try {
                              navigator.clipboard.writeText(defaultAcpAccount.iban.replace(/\s+/g, ''));
                              copyVcs && (toast.success ? null : null);
                            } catch { /* no-op */ }
                            toast.success('IBAN copie dans le presse-papier');
                          }}
                          className="p-1.5 hover:bg-slate-100 rounded text-slate-500"
                          data-testid="quickpay-copy-iban"
                          title="Copier l'IBAN"
                        >
                          <Copy size={12} />
                        </button>
                      </div>
                    </div>
                  )}
                  {vcsCode && (
                    <div className="mt-3 pt-3 border-t border-slate-200">
                      <div className="text-[10px] uppercase tracking-wider text-slate-500">Communication structuree</div>
                      <div className="flex items-center gap-2 mt-1">
                        <code className="text-xs font-mono text-[#022D52] bg-blue-50 border border-blue-200 px-2 py-1 rounded flex-1 truncate">{vcsCode}</code>
                        <button onClick={() => copyVcs && copyVcs(vcsCode)} className="p-1.5 hover:bg-slate-100 rounded text-slate-500" data-testid="quickpay-copy-vcs">
                          <Copy size={12} />
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
              <div className="flex flex-col items-center justify-center bg-blue-50/40 rounded-md p-3 border border-blue-200">
                {amountToPay > 0.01 && qrHref ? (
                  <>
                    <img
                      src={qrHref}
                      alt="Code QR de paiement"
                      className="w-40 h-40 rounded bg-white p-2 border border-slate-200 shadow-sm"
                      data-testid="quickpay-qr-img"
                    />
                    <div className="text-[10px] text-center text-slate-600 mt-2 leading-tight font-medium">
                      Scannez pour payer avec votre app bancaire
                    </div>
                    <div className="text-[9px] text-slate-400 mt-0.5">Belfius / BNP / ING / KBC / ...</div>
                  </>
                ) : (
                  <div className="text-center text-emerald-700 py-8">
                    <CheckCircle2 size={40} className="mx-auto mb-2 text-emerald-500" />
                    <div className="text-xs font-semibold">Rien a payer</div>
                    <div className="text-[10px] text-slate-500">sur cette periode</div>
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <Card><CardContent className="p-8 text-center text-slate-400 text-sm">Chargement des mouvements...</CardContent></Card>
      ) : movements.length === 0 && !selectedFyId ? (
        <Card>
          <CardContent className="p-8 text-center text-slate-400">
            <CheckCircle2 size={32} className="mx-auto mb-2 text-emerald-500" />
            Aucun mouvement enregistre sur ce compte
          </CardContent>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="w-24">Date</TableHead>
                    <TableHead className="w-32">Type</TableHead>
                    <TableHead>Description</TableHead>
                    <TableHead className="text-right w-28">Debit</TableHead>
                    <TableHead className="text-right w-28">Credit</TableHead>
                    <TableHead className="text-right w-28">Solde</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {/* Ligne opening balance : n'affiche que si une FY est
                      selectionnee (i.e. periode contextuelle definie). */}
                  {selectedFy?.start_date && (
                    <TableRow className="bg-blue-50/40 border-t-2 border-blue-200">
                      <TableCell className="text-xs font-mono">{fmtDate(selectedFy.start_date)}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px] bg-blue-50 text-[#01213e] border-blue-200">
                          Solde initial
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs italic text-slate-600">
                        Report a la date du {fmtDate(selectedFy.start_date)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-slate-400">-</TableCell>
                      <TableCell className="text-right font-mono text-xs text-slate-400">-</TableCell>
                      <TableCell className={`text-right font-mono text-xs font-semibold ${openingBalance > 0.01 ? 'text-red-600' : openingBalance < -0.01 ? 'text-emerald-600' : 'text-slate-500'}`}>
                        {fmt(openingBalance)}
                      </TableCell>
                    </TableRow>
                  )}
                  {movements.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-slate-400 text-sm py-6">
                        Aucun mouvement dans la periode selectionnee
                      </TableCell>
                    </TableRow>
                  )}
                  {movements.map((m, i) => {
                    const meta = JOURNAL_TYPE_META[m.journal_type] || JOURNAL_TYPE_META.OD;
                    return (
                      <TableRow key={`${m.reference}-${i}`} data-testid={`movement-row-${i}`} className="hover:bg-slate-50/50">
                        <TableCell className="text-xs font-mono text-slate-600">{fmtDate(m.date)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={`text-[10px] ${meta.color}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${meta.dotColor} mr-1`} />
                            {meta.label}
                          </Badge>
                          {m.is_mutation && (
                            <Badge variant="outline" className="text-[9px] bg-amber-50 text-amber-700 border-amber-200 ml-1">
                              MUT
                            </Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-slate-800">
                          <div className="font-medium">{m.description || m.fund_call_name || '-'}</div>
                          {m.reference && (
                            <div className="text-[10px] text-slate-400 font-mono">{m.reference}</div>
                          )}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {m.debit > 0 ? <span className="text-slate-900">{fmt(m.debit)}</span> : <span className="text-slate-300">-</span>}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {m.credit > 0 ? <span className="text-emerald-600">{fmt(m.credit)}</span> : <span className="text-slate-300">-</span>}
                        </TableCell>
                        <TableCell className={`text-right font-mono text-xs font-semibold ${m.running_balance > 0.01 ? 'text-red-600' : m.running_balance < -0.01 ? 'text-emerald-600' : 'text-slate-500'}`}>
                          {fmt(m.running_balance)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {/* Ligne closing balance */}
                  {movements.length > 0 && (
                    <TableRow className="bg-slate-50 border-t-2 border-slate-300 font-semibold">
                      <TableCell className="text-xs" colSpan={3}>
                        <span className="uppercase tracking-wider text-[10px] text-slate-500">Solde final</span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-slate-500">
                        {fmt(movements.reduce((s, m) => s + (m.debit || 0), 0))}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-emerald-600">
                        {fmt(movements.reduce((s, m) => s + (m.credit || 0), 0))}
                      </TableCell>
                      <TableCell className={`text-right font-mono text-sm ${closingBalance > 0.01 ? 'text-red-600' : closingBalance < -0.01 ? 'text-emerald-600' : 'text-slate-500'}`}>
                        {fmt(closingBalance)}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Info VCS pour paiement */}
      {closingBalance > 0.01 && vcsCode && (
        <Card className="border-blue-200 bg-blue-50/40">
          <CardContent className="p-3 flex items-center gap-3 flex-wrap">
            <Wallet size={16} className="text-[#022D52]" />
            <div className="text-xs">
              <div className="font-semibold text-blue-900">Pour regler votre solde de {fmt(closingBalance)}</div>
              <div className="text-[#01213e]">Utilisez la communication structuree :</div>
            </div>
            <button
              onClick={() => copyVcs(vcsCode)}
              className="font-mono text-xs text-[#022D52] bg-white hover:bg-blue-100 px-3 py-1.5 rounded border border-blue-200 inline-flex items-center gap-1.5"
              data-testid="movements-vcs-btn"
            >
              {vcsCode}<Copy size={11} />
            </button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}


// iter90hw + iter90hz + iter90i0 + iter90i3 : onglet "Comptes bancaires" -
// transparence totale pour le proprio : IBAN + solde comptable + totaux
// credits/debits par periode ; les mouvements detailles se deplient au clic
// sur l'entete du compte (compact par defaut, extensible a la demande).
function BankAccountsTab({
  bankAccounts, loading, acpSelected,
  startDate, endDate, onStartDateChange, onEndDateChange, fiscalYear,
}) {
  // iter90i3 : etat "developpe" par IBAN (rendu compact par defaut)
  const [expanded, setExpanded] = useState({});
  const toggleExpanded = (key) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }));

  // iter90i3 : calcul des totaux (credit / debit / net) par compte, sur les
  // mouvements de la periode. Utilise `transaction_type` s'il est present,
  // sinon deduit du signe de `amount`.
  const summaryByAccount = useMemo(() => {
    return (bankAccounts || []).map((ba) => {
      const mvs = ba.recent_movements || [];
      let totalCredit = 0;
      let totalDebit = 0;
      for (const m of mvs) {
        const amt = Number(m.amount || 0);
        const isCredit = m.transaction_type === 'credit' || (!m.transaction_type && amt >= 0);
        if (isCredit) totalCredit += Math.abs(amt);
        else totalDebit += Math.abs(amt);
      }
      return {
        totalCredit: +totalCredit.toFixed(2),
        totalDebit: +totalDebit.toFixed(2),
        net: +(totalCredit - totalDebit).toFixed(2),
        count: mvs.length,
      };
    });
  }, [bankAccounts]);

  const copyIban = (iban, e) => {
    if (e && e.stopPropagation) e.stopPropagation();
    if (!iban) return;
    try {
      navigator.clipboard.writeText(iban.replace(/\s+/g, ''));
      toast.success('IBAN copie dans le presse-papier');
    } catch {
      toast.error('Impossible de copier l\'IBAN');
    }
  };

  const resetToFiscalYear = () => {
    if (!fiscalYear) return;
    const todayIso = new Date().toISOString().slice(0, 10);
    onStartDateChange && onStartDateChange(fiscalYear.start_date || '');
    onEndDateChange && onEndDateChange(todayIso);
  };

  if (!acpSelected) {
    return (
      <Card className="border-amber-200 bg-amber-50/40">
        <CardContent className="p-6 text-center text-sm text-amber-700" data-testid="bank-accounts-no-acp">
          Selectionnez une copropriete pour voir ses comptes bancaires.
        </CardContent>
      </Card>
    );
  }

  // iter90i0 : bandeau de filtre par date - toujours affiche, meme quand
  // il n'y a pas encore de resultat charge (loading / empty).
  const dateFilterBar = (
    <Card className="border-slate-200 bg-slate-50/60" data-testid="bank-accounts-date-filter">
      <CardContent className="p-3">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="text-xs font-semibold text-slate-600 flex items-center gap-1.5">
            <CalendarClock size={13} className="text-[#022D52]" />
            Periode :
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[10px] uppercase tracking-wider text-slate-500">Du</label>
            <input
              type="date"
              value={startDate || ''}
              onChange={(e) => onStartDateChange && onStartDateChange(e.target.value)}
              className="h-8 px-2 text-xs border border-slate-300 rounded"
              data-testid="bank-accounts-start-date"
            />
            <label className="text-[10px] uppercase tracking-wider text-slate-500">Au</label>
            <input
              type="date"
              value={endDate || ''}
              onChange={(e) => onEndDateChange && onEndDateChange(e.target.value)}
              className="h-8 px-2 text-xs border border-slate-300 rounded"
              data-testid="bank-accounts-end-date"
            />
          </div>
          {fiscalYear && (
            <Button
              variant="outline"
              size="sm"
              onClick={resetToFiscalYear}
              className="h-8 text-[11px] text-[#022D52] border-[#022D52]/30"
              data-testid="bank-accounts-reset-fy"
              title={`Reinitialiser au 1er jour de l'exercice ${fiscalYear.name || ''}`}
            >
              <ArrowLeft size={11} className="mr-1" /> Exercice {fiscalYear.name || ''}
            </Button>
          )}
          <div className="ml-auto text-[10px] text-slate-500 italic">
            Debut par defaut : 1er jour de l&apos;exercice courant
          </div>
        </div>
      </CardContent>
    </Card>
  );

  if (loading) {
    return (
      <div className="space-y-4" data-testid="bank-accounts-body-loading">
        {dateFilterBar}
        <Card>
          <CardContent className="p-8 text-center text-slate-400 text-sm">
            Chargement des comptes bancaires...
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!bankAccounts || bankAccounts.length === 0) {
    return (
      <div className="space-y-4" data-testid="bank-accounts-body-empty">
        {dateFilterBar}
        <Card>
          <CardContent className="p-8 text-center text-slate-400 text-sm">
            Aucun compte bancaire configure pour cette copropriete.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="bank-accounts-list">
      {dateFilterBar}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-slate-500 italic flex-1">
          Vue transparente des comptes de l&apos;ACP. Les IBAN sont partiellement masques
          pour votre securite ; le solde comptable est actualise en temps reel
          (independant du filtre de date). Cliquez sur l&apos;entete d&apos;un compte
          pour deplier/replier ses mouvements.
        </div>
        {bankAccounts.length > 1 && (
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                const all = {};
                bankAccounts.forEach((ba, i) => { all[`${ba.iban}-${i}`] = true; });
                setExpanded(all);
              }}
              className="text-xs h-7"
              data-testid="bank-accounts-expand-all"
            >
              <ChevronDown size={12} className="mr-1" /> Tout deplier
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setExpanded({})}
              className="text-xs h-7"
              data-testid="bank-accounts-collapse-all"
            >
              <ChevronRight size={12} className="mr-1" /> Tout replier
            </Button>
          </div>
        )}
      </div>
      {bankAccounts.map((ba, idx) => {
        const key = `${ba.iban}-${idx}`;
        const isOpen = !!expanded[key];
        const summary = summaryByAccount[idx] || { totalCredit: 0, totalDebit: 0, net: 0, count: 0 };
        return (
        <Card key={key} className="border-slate-200 overflow-hidden" data-testid={`bank-account-card-${idx}`}>
          {/* Entete cliquable = toggle expanded */}
          <button
            type="button"
            onClick={() => toggleExpanded(key)}
            className="w-full text-left hover:bg-slate-50/70 transition-colors"
            data-testid={`bank-account-toggle-${idx}`}
            aria-expanded={isOpen}
          >
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="flex items-start gap-2 flex-1 min-w-0">
                  <div className="mt-1 shrink-0 text-slate-400">
                    {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </div>
                  <div className="min-w-0">
                    <CardTitle className="text-base flex items-center gap-2 flex-wrap" style={{fontFamily:'Chivo,sans-serif'}}>
                      <Wallet size={16} className="text-[#022D52]" />
                      {ba.label || (ba.type === 'vue' ? 'Compte a vue' : 'Compte epargne')}
                      {ba.is_default && (
                        <Badge variant="outline" className="text-[9px] bg-emerald-50 text-emerald-700 border-emerald-200">
                          Par defaut
                        </Badge>
                      )}
                    </CardTitle>
                    <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                      <code className="text-xs font-mono text-[#022D52] bg-blue-50 border border-blue-200 px-2 py-1 rounded" data-testid={`bank-account-iban-${idx}`}>
                        {ba.iban || '-'}
                      </code>
                      {ba.iban && (
                        <span
                          role="button"
                          tabIndex={0}
                          onClick={(e) => copyIban(ba.iban, e)}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') copyIban(ba.iban, e); }}
                          className="p-1.5 hover:bg-slate-100 rounded text-slate-500 cursor-pointer"
                          title="Copier l'IBAN"
                          data-testid={`bank-account-copy-iban-${idx}`}
                        >
                          <Copy size={12} />
                        </span>
                      )}
                      {ba.pcmn_number && (
                        <span className="text-[10px] text-slate-400">PCMN {ba.pcmn_number}</span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Solde comptable</div>
                  <div className={`text-2xl font-black mt-0.5 ${ba.balance > 0.01 ? 'text-emerald-600' : ba.balance < -0.01 ? 'text-red-600' : 'text-slate-500'}`} style={{fontFamily:'Chivo,sans-serif'}} data-testid={`bank-account-balance-${idx}`}>
                    {fmt(ba.balance || 0)}
                  </div>
                </div>
              </div>
              {/* iter90i3 : bandeau totaux periode (toujours visible) */}
              <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2" data-testid={`bank-account-totals-${idx}`}>
                <div className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Mouvements</div>
                  <div className="text-sm font-bold text-slate-700 font-mono">
                    {typeof ba.movements_total_count === 'number' ? ba.movements_total_count : summary.count}
                  </div>
                </div>
                <div className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1.5">
                  <div className="text-[9px] uppercase tracking-wider text-emerald-700 flex items-center gap-1">
                    <TrendingUp size={10} /> Paiements recus
                  </div>
                  <div className="text-sm font-bold text-emerald-700 font-mono" data-testid={`bank-account-credit-total-${idx}`}>
                    {fmt(summary.totalCredit)}
                  </div>
                </div>
                <div className="rounded border border-red-200 bg-red-50 px-2 py-1.5">
                  <div className="text-[9px] uppercase tracking-wider text-red-700 flex items-center gap-1">
                    <TrendingDown size={10} /> Paiements effectues
                  </div>
                  <div className="text-sm font-bold text-red-700 font-mono" data-testid={`bank-account-debit-total-${idx}`}>
                    {fmt(summary.totalDebit)}
                  </div>
                </div>
                <div className={`rounded border px-2 py-1.5 ${summary.net > 0.01 ? 'border-emerald-200 bg-emerald-50/60' : summary.net < -0.01 ? 'border-red-200 bg-red-50/60' : 'border-slate-200 bg-slate-50'}`}>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Net periode</div>
                  <div className={`text-sm font-bold font-mono ${summary.net > 0.01 ? 'text-emerald-700' : summary.net < -0.01 ? 'text-red-700' : 'text-slate-700'}`} data-testid={`bank-account-net-${idx}`}>
                    {fmt(summary.net)}
                  </div>
                </div>
              </div>
              {!isOpen && (
                <div className="mt-2 text-[10px] text-slate-400 italic">
                  Cliquez pour afficher le detail des mouvements
                </div>
              )}
            </CardHeader>
          </button>
          {/* iter90i3 : detail des mouvements affichable uniquement quand deplie */}
          {isOpen && (
            <CardContent className="pt-0 border-t border-slate-100" data-testid={`bank-account-detail-${idx}`}>
              {ba.movements_total_count > (ba.movements_limit || 0) && (
                <div className="text-[11px] text-amber-600 italic pt-2">
                  Affichage des {ba.movements_limit} plus recents (total {ba.movements_total_count})
                </div>
              )}
              {(ba.recent_movements || []).length === 0 ? (
                <div className="text-xs text-slate-400 italic py-4 text-center border border-dashed border-slate-200 rounded mt-2">
                  Aucun mouvement bancaire sur la periode selectionnee
                </div>
              ) : (
                <div className="overflow-x-auto mt-2">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-[10px] uppercase tracking-wider">Date</TableHead>
                        <TableHead className="text-[10px] uppercase tracking-wider">Sens</TableHead>
                        <TableHead className="text-[10px] uppercase tracking-wider">Contrepartie</TableHead>
                        <TableHead className="text-[10px] uppercase tracking-wider">Communication / Detail</TableHead>
                        <TableHead className="text-[10px] uppercase tracking-wider text-right">Montant</TableHead>
                        <TableHead className="text-[10px] uppercase tracking-wider text-center">Statut</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {ba.recent_movements.map((mv, midx) => {
                        const isCredit = mv.transaction_type === 'credit' || mv.amount >= 0;
                        return (
                          <TableRow key={`${idx}-mv-${midx}`} data-testid={`bank-account-mv-${idx}-${midx}`}>
                            <TableCell className="text-xs font-mono text-slate-600 whitespace-nowrap">{fmtDate(mv.date)}</TableCell>
                            <TableCell>
                              <Badge
                                variant="outline"
                                className={`text-[9px] ${isCredit
                                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                  : 'bg-red-50 text-red-700 border-red-200'}`}
                              >
                                {isCredit ? 'Recu' : 'Paye'}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-xs text-slate-800 max-w-[200px]">
                              {mv.counterparty ? (
                                <div className="font-medium truncate" title={mv.counterparty}>{mv.counterparty}</div>
                              ) : (
                                <span className="text-slate-300 italic">Non renseigne</span>
                              )}
                              {mv.counterparty_account && (
                                <div className="text-[10px] font-mono text-slate-400 truncate" title={mv.counterparty_account}>
                                  {mv.counterparty_account}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="text-[11px] max-w-[280px]">
                              {mv.communication ? (
                                <div className="text-slate-700 break-words" title={mv.communication}>
                                  {mv.communication}
                                </div>
                              ) : (
                                <span className="text-slate-300 italic">-</span>
                              )}
                            </TableCell>
                            <TableCell className={`text-right font-mono text-xs font-semibold whitespace-nowrap ${isCredit ? 'text-emerald-600' : 'text-red-600'}`}>
                              {fmt(mv.amount)}
                            </TableCell>
                            <TableCell className="text-center">
                              <Badge
                                variant="outline"
                                className={`text-[9px] ${mv.matched ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-50 text-slate-500 border-slate-200'}`}
                                title={mv.matched ? (mv.match_type ? `Lettre (${mv.match_type})` : 'Lettre') : 'A traiter (non lettree)'}
                              >
                                {mv.matched ? 'Lettre' : 'A traiter'}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          )}
        </Card>
      );})}
    </div>
  );
}



