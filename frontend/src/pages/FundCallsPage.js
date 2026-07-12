import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Check, Megaphone, FileText, Sparkles, ShieldCheck, Wallet, Banknote, AlertTriangle, RefreshCcw } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';
import { useAuth } from '@/contexts/AuthContext';

// Configuration des 4 types d'appels de fonds (label + couleurs + icone + description)
const CALL_TYPES = {
  provisions: {
    label: 'Provisions',
    short: 'Prov.',
    icon: Banknote,
    color: 'bg-blue-100 text-blue-800 border-blue-300',
    chipBg: 'bg-blue-50',
    accent: 'text-[#01213e]',
    desc: 'Avances trimestrielles/annuelles sur charges courantes',
  },
  reserve: {
    label: 'Fonds de reserve',
    short: 'Reserve',
    icon: ShieldCheck,
    color: 'bg-purple-100 text-purple-800 border-purple-300',
    chipBg: 'bg-purple-50',
    accent: 'text-purple-700',
    desc: 'Epargne pour gros travaux a venir (toiture, facade, ascenseur)',
  },
  roulement: {
    label: 'Fonds de roulement',
    short: 'Roulement',
    icon: Wallet,
    color: 'bg-emerald-100 text-emerald-800 border-emerald-300',
    chipBg: 'bg-emerald-50',
    accent: 'text-emerald-700',
    desc: "Tresorerie minimum permanente de l'ACP (avance initiale)",
  },
  special: {
    label: 'Appel special',
    short: 'Special',
    icon: AlertTriangle,
    color: 'bg-orange-100 text-orange-800 border-orange-300',
    chipBg: 'bg-orange-50',
    accent: 'text-orange-700',
    desc: 'Depense exceptionnelle hors budget (sinistre, urgence)',
  },
};

const getCallTypeMeta = (t) => CALL_TYPES[t] || CALL_TYPES.provisions;

export default function FundCallsPage() {
  const { selectedCopro } = useAuth();
  const [calls, setCalls] = useState([]);
  const [years, setYears] = useState([]);
  const [distKeys, setDistKeys] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [selectedCall, setSelectedCall] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: '', date: '', due_date: '', fiscal_year_id: '', description: '', total_amount: 0, call_type: 'provisions', distribution_key_id: '' });
  const fyParams = useFiscalYearParams();

  const load = useCallback(async () => {
    const [c, y, dk, b] = await Promise.all([
      api.get('/fund-calls', { params: fyParams }),
      api.get('/fiscal/years'),
      api.get('/distribution-keys'),
      api.get('/fiscal/budgets'),
    ]);
    setCalls(c.data); setYears(y.data); setDistKeys(dk.data); setBudgets(b.data);
  }, [fyParams.date_from, fyParams.date_to]);
  // iter88b : reload sur changement d'ACP (chinese wall reactif)
  useEffect(() => { load(); }, [load, selectedCopro]);

  const budgetName = (id) => budgets.find(b => b.id === id)?.name || '';

  // iter90ah : cle par defaut pre-selectionnee (is_default ou premiere cle)
  const defaultKeyId = useMemo(() => {
    if (!distKeys.length) return '';
    return (distKeys.find(k => k.is_default) || distKeys[0]).id;
  }, [distKeys]);

  const openCreate = () => {
    const now = new Date().toISOString().split('T')[0];
    setForm({ name: '', date: now, due_date: '', fiscal_year_id: years.find(y => y.status === 'open')?.id || '', description: '', total_amount: 0, call_type: 'provisions', distribution_key_id: defaultKeyId });
    setOwnershipWarnings(null);
    setDialogOpen(true);
  };

  const saveCall = async () => {
    try {
      await api.post('/fund-calls', { ...form, total_amount: Number(form.total_amount) });
      toast.success('Appel de fonds cree'); setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // iter90cn : Preflight warnings quand l'utilisateur change date/cle -
  // detecte les lots sans owner-at-date resolu.
  const [ownershipWarnings, setOwnershipWarnings] = useState(null);
  useEffect(() => {
    if (!dialogOpen || !form.date || !form.distribution_key_id || !selectedCopro || selectedCopro === 'all') {
      setOwnershipWarnings(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const { data } = await api.post('/fund-calls/preflight-manual-call', {
          copropriete_id: selectedCopro,
          date: form.date,
          distribution_key_id: form.distribution_key_id,
        });
        setOwnershipWarnings(data.ownership_at_date_warning || null);
      } catch { setOwnershipWarnings(null); }
    }, 400);
    return () => clearTimeout(timer);
  }, [dialogOpen, form.date, form.distribution_key_id, selectedCopro]);

  const viewCall = async (id) => {
    try { const { data } = await api.get(`/fund-calls/${id}`); setSelectedCall(data); } catch { toast.error('Erreur'); }
  };

  const markPaid = async (callId, ownerId) => {
    try {
      await api.post(`/fund-calls/${callId}/mark-paid`, null, { params: { owner_id: ownerId } });
      toast.success('Paiement enregistre'); viewCall(callId);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const generateEntries = async (callId) => {
    try {
      const { data } = await api.post(`/fund-calls/${callId}/generate-entries`);
      toast.success(data.message);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteCall = async (id) => {
    if (!window.confirm('Supprimer cet appel ?')) return;
    await api.delete(`/fund-calls/${id}`); toast.success('Supprime');
    if (selectedCall?.id === id) setSelectedCall(null); load();
  };

  const deleteAllCalls = async () => {
    const copro = selectedCopro || '';
    if (!copro || copro === 'all') { toast.error('Selectionnez une ACP'); return; }
    if (!window.confirm(`Supprimer TOUS les appels de fonds de cette ACP (${calls.length}) ET leurs ecritures comptables ? Cette action est irreversible.`)) return;
    try {
      const { data } = await api.post(`/fund-calls/delete-all?copropriete_id=${copro}`);
      toast.success(data.message || 'Appels supprimes');
      setSelectedCall(null);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const regenerateEntries = async () => {
    const copro = selectedCopro || '';
    if (!copro || copro === 'all') { toast.error('Selectionnez une ACP'); return; }
    if (!window.confirm(`Regenerer les ecritures comptables des ${calls.length} appel(s) de fonds ? Les libelles seront mis a jour selon le type d'appel.`)) return;
    try {
      const { data } = await api.post(`/fund-calls/regenerate-entries?copropriete_id=${copro}`);
      toast.success(data.message || 'Ecritures regenerees');
      if (selectedCall) viewCall(selectedCall.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  // Detection des appels avec distribution vide (lines presents mais distribution[]==0)
  const brokenCalls = calls.filter(c => {
    const dist = c.distribution || [];
    const hasUseful = dist.some(d => Number(d.amount || 0) > 0.001);
    const hasLines = (c.lines || []).length > 0;
    return hasLines && !hasUseful;
  });

  const repairEmptyDistributions = async () => {
    const copro = selectedCopro || '';
    if (!copro || copro === 'all') { toast.error('Selectionnez une ACP'); return; }
    if (!window.confirm(
      `Reparer la distribution de ${brokenCalls.length} appel(s) ?\n\n`
      + `Les appels ayant des lignes budget mais aucune repartition par lot/proprietaire `
      + `seront recalcules a partir des cles de repartition. Les appels avec paiements sont preserves.`
    )) return;
    try {
      const { data } = await api.post(`/fund-calls/regenerate-empty-distributions?copropriete_id=${copro}`);
      toast.success(data.message, {
        description: data.fixed_count > 0
          ? `${data.fixed.length} appel(s) repare(s) : ${data.fixed.map(f => f.name).join(', ')}`
          : 'Aucun appel reparable trouve',
        duration: 8000,
      });
      load();
      if (selectedCall) viewCall(selectedCall.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la reparation');
    }
  };

  const regenerateSingleDistribution = async (callId) => {
    if (!window.confirm('Regenerer la distribution de cet appel a partir de ses lignes budget ?')) return;
    try {
      const { data } = await api.post(`/fund-calls/${callId}/regenerate-distribution`);
      toast.success(data.message);
      load();
      if (selectedCall?.id === callId) viewCall(callId);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const fixRoundingDrift = async () => {
    const copro = selectedCopro || '';
    if (!copro || copro === 'all') { toast.error('Selectionnez une ACP'); return; }
    if (!window.confirm(
      'Corriger la derive d\'arrondi (0,01 EUR) dans les appels non payes ?\n\n'
      + 'Chaque appel dont la somme des distributions ne correspond pas exactement '
      + 'au total sera ajuste (methode des plus grands restes). Les ecritures '
      + 'comptables seront regenerees automatiquement.\n\n'
      + 'Les appels contenant au moins un paiement sont preserves.'
    )) return;
    try {
      const { data } = await api.post(`/fund-calls/fix-rounding-drift?copropriete_id=${copro}`);
      toast.success(data.message, {
        description: data.fixed_count > 0
          ? `${data.fixed.length} appel(s) corrige(s) : ${data.fixed.map(f => `${f.name} (${f.drift_cents_before > 0 ? '+' : ''}${f.drift_cents_before}c)`).join(', ')}`
          : 'Aucun appel avec derive.',
        duration: 10000,
      });
      load();
      if (selectedCall) viewCall(selectedCall.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la correction');
    }
  };

  return (
    <div data-testid="fund-calls-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Megaphone size={24} className="inline mr-2" />Appels de Fonds</h1><p className="page-subtitle">Appels de provisions et fonds de reserve</p></div>
        <div className="flex gap-2">
          {brokenCalls.length > 0 && (
            <Button
              onClick={repairEmptyDistributions}
              variant="outline"
              className="border-amber-400 text-amber-700 hover:bg-amber-50"
              data-testid="repair-distributions-btn"
              title="Repare les appels dont la distribution par lot/proprietaire est manquante"
            >
              <AlertTriangle size={16} className="mr-2" /> Reparer distribution ({brokenCalls.length})
            </Button>
          )}
          {calls.length > 0 && (
            <Button
              onClick={fixRoundingDrift}
              variant="outline"
              className="border-emerald-300 text-emerald-700 hover:bg-emerald-50"
              data-testid="fix-drift-btn"
              title="Corrige la derive d'arrondi (0,01 EUR) dans les distributions existantes"
            >
              <RefreshCcw size={16} className="mr-2" /> Corriger arrondis
            </Button>
          )}
          {calls.length > 0 && (
            <Button
              onClick={regenerateEntries}
              variant="outline"
              className="border-blue-200 text-[#01213e] hover:bg-blue-50"
              data-testid="regenerate-entries-btn"
              title="Met a jour les libelles des ecritures comptables selon le type d'appel"
            >
              <RefreshCcw size={16} className="mr-2" /> Regenerer ecritures
            </Button>
          )}
          {calls.length > 0 && (
            <Button
              onClick={deleteAllCalls}
              variant="outline"
              className="border-red-200 text-red-600 hover:bg-red-50"
              data-testid="delete-all-calls-btn"
            >
              <Trash2 size={16} className="mr-2" /> Supprimer tous les appels
            </Button>
          )}
          <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-call-btn"><Plus size={16} className="mr-2" /> Nouvel appel</Button>
        </div>
      </div>

      {brokenCalls.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 mb-4 text-sm text-amber-900" data-testid="broken-distributions-banner">
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5 text-amber-600" />
            <div className="flex-1">
              <div className="font-semibold">
                {brokenCalls.length} appel(s) sans distribution detecte(s)
              </div>
              <div className="text-xs mt-1">
                Ces appels ont des <b>lignes budget</b> definies mais aucune <b>repartition par lot/proprietaire</b>.
                Ils n&apos;apparaitront pas correctement dans les decomptes ni dans les mutations. Cliquez sur
                <b> &quot;Reparer distribution&quot;</b> pour les recalculer automatiquement a partir des cles de repartition.
              </div>
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer underline">Voir les appels concernes ({brokenCalls.length})</summary>
                <ul className="mt-2 ml-4 list-disc">
                  {brokenCalls.slice(0, 10).map(c => (
                    <li key={c.id} data-testid={`broken-call-${c.id}`}>
                      <span className="font-mono text-[11px]">{fmtDate(c.date)}</span> - {c.name} <span className="text-amber-700">({(c.total_amount || 0).toFixed(2)} EUR)</span>
                    </li>
                  ))}
                  {brokenCalls.length > 10 && <li>... et {brokenCalls.length - 10} autre(s)</li>}
                </ul>
              </details>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-3">
          {calls.length === 0 ? <p className="text-sm text-slate-400 text-center py-8">Aucun appel de fonds</p> : calls.map(c => {
            const meta = getCallTypeMeta(c.call_type);
            const TypeIcon = meta.icon;
            return (
            <Card key={c.id} className={`cursor-pointer transition-all border-l-4 ${selectedCall?.id === c.id ? 'border-l-[#022D52] shadow-md border border-[#022D52]' : `border-l-current ${meta.accent} border-slate-200 hover:border-slate-300`}`} onClick={() => viewCall(c.id)} data-testid={`call-card-${c.id}`}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-sm">{c.name}</span>
                  <Badge variant="outline" className={c.status === 'completed' ? 'bg-green-50 text-green-700' : c.status === 'partial' ? 'bg-yellow-50 text-yellow-700' : 'bg-slate-50 text-slate-600'}>{c.status === 'completed' ? 'Complet' : c.status === 'partial' ? 'Partiel' : 'En attente'}</Badge>
                </div>
                <div className="flex items-center gap-2 mt-1 mb-2">
                  <Badge className={`${meta.color} text-[10px] font-semibold border`} data-testid={`call-type-badge-${c.id}`}>
                    <TypeIcon size={10} className="mr-1" />
                    {meta.label}
                  </Badge>
                  <span className="text-[11px] text-slate-400">{fmtDate(c.date)}</span>
                </div>
                <div className="font-mono font-bold text-base text-slate-900">{c.total_amount?.toFixed(2)} EUR</div>
                {c.reserve_amount > 0 && c.call_type !== 'reserve' && (
                  <div className="text-[11px] text-purple-700 flex items-center gap-1 mt-1" data-testid={`call-reserve-${c.id}`}>
                    <ShieldCheck size={10} /> dont reserve {c.reserve_amount.toFixed(2)} EUR
                  </div>
                )}
                {c.budget_id && (
                  <Badge variant="outline" className="mt-2 text-[10px] bg-blue-50 border-blue-200 text-[#01213e]" data-testid={`call-budget-badge-${c.id}`}>
                    <Sparkles size={9} className="mr-1" /> Issu du budget {budgetName(c.budget_id) || '—'}
                  </Badge>
                )}
                <div className="flex gap-1 mt-2">
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); generateEntries(c.id); }} title="Generer ecritures"><FileText size={12} /></Button>
                  {brokenCalls.some(bc => bc.id === c.id) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => { e.stopPropagation(); regenerateSingleDistribution(c.id); }}
                      className="text-amber-600 hover:bg-amber-50"
                      title="Cet appel n'a aucune distribution - cliquez pour la regenerer depuis les lignes budget"
                      data-testid={`repair-single-${c.id}`}
                    >
                      <AlertTriangle size={12} />
                    </Button>
                  )}
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); deleteCall(c.id); }} className="text-red-400"><Trash2 size={12} /></Button>
                </div>
              </CardContent>
            </Card>
          );})}
        </div>

        <div className="lg:col-span-2">
          {selectedCall ? (() => {
            const callMeta = getCallTypeMeta(selectedCall.call_type);
            const CallIcon = callMeta.icon;
            return (
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-lg flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
                  {selectedCall.name}
                  <Badge className={`${callMeta.color} text-[11px] font-semibold border`}>
                    <CallIcon size={11} className="mr-1" /> {callMeta.label}
                  </Badge>
                  {selectedCall.budget_id && (
                    <Badge variant="outline" className="text-xs bg-blue-50 border-blue-200 text-[#01213e]">
                      <Sparkles size={10} className="mr-1" /> Budget: {budgetName(selectedCall.budget_id) || '—'}
                    </Badge>
                  )}
                </CardTitle>
                <div className="text-xs text-slate-500">{fmtDate(selectedCall.date)} - Echeance: {fmtDate(selectedCall.due_date) || '-'} - {selectedCall.description}</div>
                {/* Bandeau pedagogique selon le type */}
                <div className={`${callMeta.chipBg} border-l-4 border-current ${callMeta.accent} px-3 py-2 mt-3 rounded-r text-xs leading-relaxed`}>
                  <span className="font-semibold">{callMeta.label}</span> — {callMeta.desc}
                </div>
              </CardHeader>
              <CardContent>
                {selectedCall.lines && selectedCall.lines.length > 0 && (
                  <div className="mb-4 border rounded-md overflow-hidden" data-testid="call-lines-table">
                    <div className="bg-slate-50 px-3 py-1.5 text-xs uppercase tracking-wide text-slate-600 font-semibold border-b">Detail par nature de depense</div>
                    <table className="w-full text-sm">
                      <thead><tr className="text-xs text-slate-500">
                        <th className="p-2 text-left">Compte</th>
                        <th className="p-2 text-left">Libelle</th>
                        <th className="p-2 text-left">Cle de repartition</th>
                        <th className="p-2 text-right">Montant</th>
                      </tr></thead>
                      <tbody>
                        {selectedCall.lines.map((ln, i) => (
                          <tr key={i} className={`border-t border-slate-100 ${ln.is_reserve ? 'bg-purple-50/40' : ''}`}>
                            <td className="p-2 font-mono text-xs">{ln.is_reserve ? <Badge variant="outline" className="text-[10px] bg-purple-100 border-purple-300 text-purple-700"><ShieldCheck size={9} className="mr-1" />RESERVE</Badge> : ln.account_number}</td>
                            <td className="p-2 text-xs">{ln.account_name}</td>
                            <td className="p-2 text-xs text-slate-600">{ln.distribution_key_name || 'Tantiemes'}</td>
                            <td className="p-2 text-right font-mono">{ln.amount.toFixed(2)} EUR</td>
                          </tr>
                        ))}
                        <tr className="border-t-2 bg-slate-50 font-bold text-sm">
                          <td colSpan={3} className="p-2 text-right">TOTAL APPEL</td>
                          <td className="p-2 text-right font-mono">{selectedCall.total_amount.toFixed(2)} EUR</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )}
                {/* Distribution groupee par proprietaire (depuis iter84) */}
                {(() => {
                  const dist = selectedCall.distribution || [];
                  if (dist.length === 0) {
                    return (
                      <div className="text-center text-slate-400 italic py-8 text-sm">
                        Aucune distribution - utilisez &quot;Reparer distribution&quot; pour la recalculer depuis les lignes budget.
                      </div>
                    );
                  }
                  // Regroupe par owner_id (les entries sans lot sont groupees ensemble)
                  // iter85b : cascade parent/enfant - les lots sont tries avec
                  // les parents en premier, puis les enfants indentes sous leur parent.
                  const groups = new Map();
                  dist.forEach((d, idx) => {
                    const key = d.owner_id || `__no_owner_${idx}`;
                    if (!groups.has(key)) {
                      groups.set(key, {
                        owner_id: d.owner_id || '',
                        owner_name: d.owner_name || '(sans proprietaire)',
                        vcs_code: d.vcs_code || '',
                        lots: [],
                        total_amount: 0,
                        total_share: 0,
                        paid_amount: 0,
                      });
                    }
                    const g = groups.get(key);
                    g.lots.push(d);
                    g.total_amount += Number(d.amount || 0);
                    g.total_share += Number(d.share || 0);
                    if (d.paid) g.paid_amount += Number(d.amount || 0);
                  });
                  // Tri cascade parent/enfant dans chaque groupe owner
                  const sortLotsCascade = (lots) => {
                    const byId = new Map();
                    lots.forEach(l => { if (l.lot_id) byId.set(l.lot_id, l); });
                    // Parents = ceux qui n'ont pas de parent_lot_id OU dont le parent n'est pas dans ce groupe
                    const isParent = (l) => !l.parent_lot_id || !byId.has(l.parent_lot_id);
                    const parents = lots.filter(isParent).sort((a, b) =>
                      (a.lot_number || '').localeCompare(b.lot_number || '', undefined, { numeric: true })
                    );
                    const childrenByParent = new Map();
                    lots.filter(l => !isParent(l)).forEach(l => {
                      const arr = childrenByParent.get(l.parent_lot_id) || [];
                      arr.push(l);
                      childrenByParent.set(l.parent_lot_id, arr);
                    });
                    const out = [];
                    parents.forEach(p => {
                      out.push({ ...p, _depth: 0 });
                      const kids = (childrenByParent.get(p.lot_id) || []).sort((a, b) =>
                        (a.lot_number || '').localeCompare(b.lot_number || '', undefined, { numeric: true })
                      );
                      kids.forEach(k => out.push({ ...k, _depth: 1 }));
                    });
                    return out;
                  };
                  groups.forEach(g => { g.lots = sortLotsCascade(g.lots); });
                  const groupsArr = Array.from(groups.values()).sort((a, b) =>
                    (a.owner_name || '').localeCompare(b.owner_name || '')
                  );
                  const grandTotal = groupsArr.reduce((s, g) => s + g.total_amount, 0);
                  return (
                    <div className="border rounded-md overflow-hidden" data-testid="call-distribution-grouped">
                      <div className="bg-slate-50 px-3 py-2 text-xs uppercase tracking-wide text-slate-600 font-semibold border-b flex items-center justify-between">
                        <span>Distribution par proprietaire ({groupsArr.length} proprietaires - {dist.length} lots)</span>
                        <span className="font-mono normal-case text-slate-700">Total: <b>{grandTotal.toFixed(2)} EUR</b></span>
                      </div>
                      <div className="divide-y divide-slate-100">
                        {groupsArr.map(g => {
                          const isPaidFully = g.paid_amount >= g.total_amount - 0.01 && g.total_amount > 0;
                          const isPartial = g.paid_amount > 0.01 && !isPaidFully;
                          return (
                            <div key={g.owner_id || g.owner_name} data-testid={`owner-group-${g.owner_id || 'noid'}`}>
                              {/* Header proprietaire */}
                              <div className={`flex items-center justify-between px-3 py-2 ${isPaidFully ? 'bg-emerald-50' : isPartial ? 'bg-amber-50' : 'bg-slate-50/40'}`}>
                                <div className="flex items-center gap-3 min-w-0">
                                  <div className="text-sm font-semibold text-slate-900 truncate">{g.owner_name}</div>
                                  {g.vcs_code && (
                                    <span className="text-[10px] font-mono text-[#022D52] bg-blue-50 border border-blue-100 px-1.5 py-0.5 rounded">
                                      {g.vcs_code}
                                    </span>
                                  )}
                                  <span className="text-[10px] text-slate-500">{g.lots.length} lot{g.lots.length > 1 ? 's' : ''}</span>
                                  {isPaidFully && (
                                    <Badge className="bg-emerald-50 text-emerald-700 border-emerald-200 text-[10px]" variant="outline">
                                      <Check size={9} className="mr-0.5" /> Paye integralement
                                    </Badge>
                                  )}
                                  {isPartial && (
                                    <Badge className="bg-amber-50 text-amber-700 border-amber-200 text-[10px]" variant="outline">
                                      Partiel {g.paid_amount.toFixed(2)} / {g.total_amount.toFixed(2)} EUR
                                    </Badge>
                                  )}
                                </div>
                                <div className="flex items-center gap-3 shrink-0">
                                  <div className="text-right">
                                    <div className="font-mono font-bold text-sm text-slate-900">
                                      {g.total_amount.toFixed(2)} EUR
                                    </div>
                                    <div className="text-[10px] text-slate-500 font-mono">
                                      quote-part {g.total_share.toFixed(2)}
                                    </div>
                                  </div>
                                  {!isPaidFully && g.owner_id && (
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      onClick={() => markPaid(selectedCall.id, g.owner_id)}
                                      className="text-green-600 h-7"
                                      title="Marquer toutes les lignes de ce proprietaire comme payees"
                                      data-testid={`mark-paid-${g.owner_id}`}
                                    >
                                      <Check size={14} />
                                    </Button>
                                  )}
                                </div>
                              </div>
                              {/* Detail des lots du proprietaire avec cascade
                                  parent/enfant (iter85b). Toujours affiche, meme
                                  quand l'owner n'a qu'un seul lot. */}
                              {g.lots.length > 0 && (
                                <table className="w-full text-xs">
                                  <tbody>
                                    {g.lots.map((d, i) => {
                                      const isChild = d._depth === 1;
                                      return (
                                        <tr key={i} className="border-t border-slate-50 hover:bg-slate-50/50">
                                          <td className={`px-3 py-1.5 ${isChild ? 'pl-14' : 'pl-8'} w-[160px]`}>
                                            <span className="font-mono text-[11px] text-slate-700 inline-flex items-center gap-1">
                                              {isChild && (
                                                <span className="text-slate-400" aria-hidden="true">└─</span>
                                              )}
                                              {d.lot_number ? (
                                                <>Lot {d.lot_number}</>
                                              ) : (
                                                <span className="text-[10px] text-slate-400 italic">part owner</span>
                                              )}
                                              {isChild && (
                                                <span className="text-[9px] text-slate-400 italic">(secondaire)</span>
                                              )}
                                            </span>
                                          </td>
                                          <td className="px-3 py-1.5 text-right font-mono text-[10px] text-slate-500 w-[80px]">
                                            {Number(d.share || 0).toFixed(2)}
                                          </td>
                                          <td className="px-3 py-1.5 text-right font-mono text-[11px] text-slate-700 w-[120px]">
                                            {Number(d.amount || 0).toFixed(2)} EUR
                                          </td>
                                          <td className="px-3 py-1.5 w-[140px]">
                                            {d.paid ? (
                                              <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200 text-[9px]">
                                                Paye {d.paid_date ? fmtDate(d.paid_date) : ''}
                                              </Badge>
                                            ) : (
                                              <span className="text-[10px] text-slate-400 italic">Impaye</span>
                                            )}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      {/* Total general */}
                      <div className="bg-slate-100 px-3 py-2 border-t-2 border-slate-300 flex items-center justify-between font-bold text-sm">
                        <span>TOTAL APPEL</span>
                        <span className="font-mono">{grandTotal.toFixed(2)} EUR</span>
                      </div>
                    </div>
                  );
                })()}
              </CardContent>
            </Card>
          );})() : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un appel pour voir le detail</div>
          )}
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl" data-testid="call-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvel appel de fonds</DialogTitle></DialogHeader>
          <div className="space-y-5 mt-2">
            {/* Selection visuelle du type d'appel */}
            <div>
              <label className="form-label mb-2 block">Type d&apos;appel *</label>
              <div className="grid grid-cols-3 gap-2" data-testid="call-type-cards">
                {['provisions', 'reserve', 'roulement'].map(t => {
                  const meta = CALL_TYPES[t];
                  const Icon = meta.icon;
                  const active = form.call_type === t;
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setForm({ ...form, call_type: t })}
                      data-testid={`call-type-card-${t}`}
                      className={`text-left rounded-lg border-2 p-3 transition ${
                        active
                          ? `${meta.chipBg} ${meta.color} border-current shadow-sm`
                          : 'bg-white border-slate-200 hover:border-slate-300 hover:bg-slate-50'
                      }`}
                    >
                      <div className={`flex items-center gap-2 mb-1.5 ${active ? meta.accent : 'text-slate-700'}`}>
                        <Icon size={16} strokeWidth={2} />
                        <span className="font-semibold text-sm">{meta.label}</span>
                      </div>
                      <div className={`text-[11px] leading-snug ${active ? meta.accent : 'text-slate-500'}`}>
                        {meta.desc}
                      </div>
                    </button>
                  );
                })}
              </div>
              {/* Appel special en secondaire */}
              <button
                type="button"
                onClick={() => setForm({ ...form, call_type: 'special' })}
                data-testid="call-type-card-special"
                className={`mt-2 w-full flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition ${
                  form.call_type === 'special'
                    ? `${CALL_TYPES.special.chipBg} ${CALL_TYPES.special.color} border-current`
                    : 'bg-white border-slate-200 hover:border-slate-300 text-slate-600'
                }`}
              >
                <AlertTriangle size={12} />
                <span className="font-medium">Appel special</span>
                <span className="text-[11px] opacity-70">- {CALL_TYPES.special.desc}</span>
              </button>
              {/* Info : pas de nature de depense pour les fonds permanents */}
              {(form.call_type === 'reserve' || form.call_type === 'roulement') && (
                <div className={`mt-2 rounded-md border ${CALL_TYPES[form.call_type].color} px-3 py-2 text-[11px]`} data-testid="call-type-info">
                  Ce type d&apos;appel alimente directement le compte capital
                  (classe 1). Aucune nature de depense a specifier - juste un
                  montant et une cle de repartition.
                </div>
              )}
            </div>

            <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="call-name" placeholder="Appel Q1 2025" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Date *</label><Input type="date" value={form.date} onChange={e => setForm({...form, date: e.target.value})} data-testid="call-date" /></div>
              <div><label className="form-label">Echeance</label><Input type="date" value={form.due_date} onChange={e => setForm({...form, due_date: e.target.value})} data-testid="call-due-date" /></div>
            </div>
            <div><label className="form-label">Montant total *</label><Input type="number" step="0.01" value={form.total_amount} onChange={e => setForm({...form, total_amount: e.target.value})} data-testid="call-amount" /></div>
            <div><label className="form-label">Cle de repartition</label>
              <Select value={form.distribution_key_id} onValueChange={v => setForm({...form, distribution_key_id: v})}>
                <SelectTrigger data-testid="call-key-select"><SelectValue placeholder={distKeys.length ? "Selectionner une cle" : "Aucune cle - creez-en une"} /></SelectTrigger>
                <SelectContent>
                  {distKeys.map(k => (
                    <SelectItem key={k.id} value={k.id}>
                      {k.name}{k.is_default ? ' (defaut)' : ''}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div><label className="form-label">Description (facultatif)</label><Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} data-testid="call-description" placeholder="Remarques, contexte..." /></div>
            {/* iter90cn : warnings ownership-at-date sur appel manuel */}
            {ownershipWarnings && ownershipWarnings.unresolved_count > 0 && (
              <div className="bg-red-50 border-2 border-red-400 rounded-lg p-3" data-testid="manual-call-ownership-warning">
                <div className="flex items-start gap-2">
                  <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
                  <div className="flex-1 text-xs">
                    <div className="font-semibold text-red-900 mb-1">
                      Attention : {ownershipWarnings.unresolved_count} lot(s) sans propriétaire assigné au {form.date}
                    </div>
                    <div className="text-red-800 mb-1">Corrigez l&apos;ownership avant de créer l&apos;appel pour éviter des distributions incorrectes.</div>
                    <details>
                      <summary className="cursor-pointer text-red-900 font-medium select-none">Voir le détail ({ownershipWarnings.warnings.length})</summary>
                      <div className="mt-1 space-y-0.5 max-h-40 overflow-y-auto">
                        {ownershipWarnings.warnings.map((w, i) => (
                          <div key={i} className="bg-white/70 rounded px-2 py-1 text-[11px]">
                            <span className="font-mono font-medium">Lot {w.lot_number}</span> - <span className="text-red-700">{w.reason}</span>
                          </div>
                        ))}
                      </div>
                    </details>
                  </div>
                </div>
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={saveCall} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="call-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
