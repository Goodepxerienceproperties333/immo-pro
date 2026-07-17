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
import { LogOut, Home, Wallet, FileText, Receipt, Megaphone, Building2, User, AlertCircle, CheckCircle2, ArrowDownToLine, ArrowLeft, ArrowRight, Copy, UserCog, Users, Plus, Pencil, Trash2, Save, Eye, Gauge, CalendarClock, PieChart as PieChartIcon, TrendingUp, Clock, Sparkles, Mail, MailOpen, Send, Paperclip, ChevronRight, Download } from 'lucide-react';
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
      setPeriodEnd(fy.end_date || '');
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
  const chargesByCategory = useMemo(() => {
    const twelveMonthsAgo = new Date();
    twelveMonthsAgo.setMonth(twelveMonthsAgo.getMonth() - 12);
    const totals = {};
    for (const c of chargesMemo) {
      if (!c.date) continue;
      const d = new Date(c.date);
      if (d < twelveMonthsAgo) continue;
      const cat = c.category || 'Autres';
      totals[cat] = (totals[cat] || 0) + (c.my_amount || 0);
    }
    return Object.entries(totals)
      .map(([name, value]) => ({ name, value: Math.round(value * 100) / 100 }))
      .sort((a, b) => b.value - a.value);
  }, [chargesMemo]);
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
    coproprietes_count: 1,
    lots_count: acpLotsCount,
    total_called: acpStats ? acpStats.total_called : 0,
    total_paid: acpStats ? acpStats.total_paid : 0,
    balance: acpStats ? acpStats.balance : 0,
    status: acpStats ? acpStats.status : 'solde',
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
            <SituationHero
              status={stats.status}
              balance={stats.balance || 0}
              totalCalled={stats.total_called || 0}
              totalPaid={stats.total_paid || 0}
              nextCall={nextCall}
              totalPending={totalPending}
              totalCharges12m={totalCharges12m}
              pendingCount={upcomingWithCountdown.length}
              copyVcs={copyVcs}
            />
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
              <ChargesDonut data={chargesByCategory} total={totalCharges12m} />
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
                      <TableRow key={c.id} data-testid={`charge-row-${c.id}`}>
                        <TableCell className="text-xs">{fmtDate(c.date)}</TableCell>
                        <TableCell className="text-sm font-medium">{c.supplier}</TableCell>
                        <TableCell className="text-xs font-mono text-slate-600">{c.number || <span className="text-slate-300 italic">-</span>}</TableCell>
                        <TableCell className="text-xs text-slate-600 max-w-xs truncate" title={c.description}>{c.description}</TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={`text-[10px] ${c.status === 'paid' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}
                          >
                            {c.status === 'paid' ? 'Payee' : 'En attente'}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-slate-400">{fmt(c.total_amount)}</TableCell>
                        <TableCell className="text-right font-mono text-sm text-slate-900 font-semibold">{fmt(c.my_amount)}</TableCell>
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
// ==============================================================

// Palette pour donut charges (categoriel, contrastee)
const CHARGE_COLORS = ['#022D52', '#8B5CF6', '#10B981', '#F59E0B', '#EF4444', '#EC4899', '#06B6D4', '#84CC16', '#F97316', '#6366F1', '#14B8A6', '#A855F7'];

function SituationHero({ status, balance, totalCalled, totalPaid, nextCall, totalPending, totalCharges12m, pendingCount, copyVcs }) {
  // Bloc solde : couleur selon statut
  const isDebtor = status === 'debiteur';
  const isCreditor = status === 'crediteur';
  const soldeBg = isDebtor
    ? 'bg-gradient-to-br from-red-50 to-red-100 border-red-200'
    : isCreditor
      ? 'bg-gradient-to-br from-emerald-50 to-emerald-100 border-emerald-200'
      : 'bg-gradient-to-br from-slate-50 to-slate-100 border-slate-200';
  const soldeText = isDebtor ? 'text-red-700' : isCreditor ? 'text-emerald-700' : 'text-slate-700';
  const soldeLabel = isDebtor ? 'Vous devez' : isCreditor ? 'Solde en votre faveur' : 'Compte solde';
  const soldeIcon = isDebtor
    ? <AlertCircle size={20} className="text-red-600" />
    : isCreditor
      ? <CheckCircle2 size={20} className="text-emerald-600" />
      : <Gauge size={20} className="text-slate-500" />;

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
            <Badge variant="outline" className={`text-[10px] ${soldeText} border-current`}>{status || '—'}</Badge>
          </div>
          <div className={`text-3xl font-bold ${soldeText}`} style={{fontFamily:'Chivo,sans-serif'}}>{fmt(Math.abs(balance))}</div>
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
              <div className={`text-3xl font-bold ${nextTextColor}`} style={{fontFamily:'Chivo,sans-serif'}}>{fmt(nextCall.amount)}</div>
              <div className="mt-1 text-[13px] font-medium text-slate-700 truncate" title={nextCall.fund_call_name}>{nextCall.fund_call_name}</div>
              <div className={`mt-2 text-xs font-semibold flex items-center gap-1.5 ${nextTextColor}`}>
                <Clock size={12} />
                {nextLabel} {nextCall.due_date && <span className="text-slate-500 font-normal">({fmtDate(nextCall.due_date)})</span>}
              </div>
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

      {/* Carte 3 : Total charges 12 mois */}
      <Card className="bg-gradient-to-br from-blue-50 to-indigo-100 border-2 border-blue-200" data-testid="situation-charges-card">
        <CardContent className="p-5">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              <TrendingUp size={20} className="text-[#022D52]" />
              <span className="text-[11px] uppercase tracking-wider font-semibold text-[#01213e]">Charges 12 mois</span>
            </div>
          </div>
          <div className="text-3xl font-bold text-blue-800" style={{fontFamily:'Chivo,sans-serif'}}>{fmt(totalCharges12m)}</div>
          <div className="mt-2 text-xs text-slate-600">Cumul de votre quote-part sur les 12 derniers mois</div>
          <div className="mt-3 text-[11px] text-slate-500 pt-2 border-t border-white/60">
            {totalCharges12m > 0 ? "Voir la repartition par categorie ci-dessous" : "Aucune charge sur la periode"}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ChargesDonut({ data, total }) {
  return (
    <Card className="lg:col-span-3 border-slate-200" data-testid="situation-donut-card">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <PieChartIcon size={16} className="text-[#022D52]" />
          <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Charges par categorie (12 mois)</CardTitle>
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
                <span className="text-blue-800">Piece jointe : {data.attachment_filename || 'document.pdf'}</span>
                <span className="text-[10px] text-[#022D52] italic ml-auto">
                  (envoyee par email, non stockee dans le portail)
                </span>
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
}) {
  const selectedFy = fiscalYears.find(y => y.id === selectedFyId);
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


