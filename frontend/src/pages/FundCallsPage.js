import { useState, useEffect, useCallback } from 'react';
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

// Configuration des 4 types d'appels de fonds (label + couleurs + icone + description)
const CALL_TYPES = {
  provisions: {
    label: 'Provisions',
    short: 'Prov.',
    icon: Banknote,
    color: 'bg-blue-100 text-blue-800 border-blue-300',
    chipBg: 'bg-blue-50',
    accent: 'text-blue-700',
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
  const [calls, setCalls] = useState([]);
  const [years, setYears] = useState([]);
  const [distKeys, setDistKeys] = useState([]);
  const [budgets, setBudgets] = useState([]);
  const [selectedCall, setSelectedCall] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState({ name: '', date: '', due_date: '', fiscal_year_id: '', description: '', total_amount: 0, call_type: 'provisions', distribution_key_id: '' });

  const load = useCallback(async () => {
    const [c, y, dk, b] = await Promise.all([api.get('/fund-calls'), api.get('/fiscal/years'), api.get('/distribution-keys'), api.get('/fiscal/budgets')]);
    setCalls(c.data); setYears(y.data); setDistKeys(dk.data); setBudgets(b.data);
  }, []);
  useEffect(() => { load(); }, [load]);

  const budgetName = (id) => budgets.find(b => b.id === id)?.name || '';

  const openCreate = () => {
    const now = new Date().toISOString().split('T')[0];
    setForm({ name: '', date: now, due_date: '', fiscal_year_id: years.find(y => y.status === 'open')?.id || '', description: '', total_amount: 0, call_type: 'provisions', distribution_key_id: '' });
    setDialogOpen(true);
  };

  const saveCall = async () => {
    try {
      await api.post('/fund-calls', { ...form, total_amount: Number(form.total_amount) });
      toast.success('Appel de fonds cree'); setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

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
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
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
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
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

  return (
    <div data-testid="fund-calls-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Megaphone size={24} className="inline mr-2" />Appels de Fonds</h1><p className="page-subtitle">Appels de provisions et fonds de reserve</p></div>
        <div className="flex gap-2">
          {calls.length > 0 && (
            <Button
              onClick={regenerateEntries}
              variant="outline"
              className="border-blue-200 text-blue-700 hover:bg-blue-50"
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
          <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-call-btn"><Plus size={16} className="mr-2" /> Nouvel appel</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="space-y-3">
          {calls.length === 0 ? <p className="text-sm text-slate-400 text-center py-8">Aucun appel de fonds</p> : calls.map(c => {
            const meta = getCallTypeMeta(c.call_type);
            const TypeIcon = meta.icon;
            return (
            <Card key={c.id} className={`cursor-pointer transition-all border-l-4 ${selectedCall?.id === c.id ? 'border-l-[#0055FF] shadow-md border border-[#0055FF]' : `border-l-current ${meta.accent} border-slate-200 hover:border-slate-300`}`} onClick={() => viewCall(c.id)} data-testid={`call-card-${c.id}`}>
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
                  <Badge variant="outline" className="mt-2 text-[10px] bg-blue-50 border-blue-200 text-blue-700" data-testid={`call-budget-badge-${c.id}`}>
                    <Sparkles size={9} className="mr-1" /> Issu du budget {budgetName(c.budget_id) || '—'}
                  </Badge>
                )}
                <div className="flex gap-1 mt-2">
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); generateEntries(c.id); }} title="Generer ecritures"><FileText size={12} /></Button>
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
                    <Badge variant="outline" className="text-xs bg-blue-50 border-blue-200 text-blue-700">
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
                <Table>
                  <TableHeader><TableRow>
                    {(selectedCall.distribution || []).some(d => d.lot_number) && <TableHead>Lot</TableHead>}
                    <TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead>
                    <TableHead className="text-right">Quote-part</TableHead><TableHead className="text-right">Montant</TableHead>
                    <TableHead>Statut</TableHead><TableHead className="w-20"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {(selectedCall.distribution || []).map((d, i) => (
                      <TableRow key={i} className="hover:bg-slate-50/50">
                        {(selectedCall.distribution || []).some(x => x.lot_number) && <TableCell className="font-mono text-sm">{d.lot_number || '-'}</TableCell>}
                        <TableCell className="font-medium">{d.owner_name}</TableCell>
                        <TableCell className="font-mono text-xs text-[#0055FF]">{d.vcs_code}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{d.share}</TableCell>
                        <TableCell className="text-right font-mono font-semibold">{d.amount.toFixed(2)} EUR</TableCell>
                        <TableCell>
                          {d.paid ? <Badge className="bg-green-50 text-green-700 border-green-200" variant="outline">Paye {fmtDate(d.paid_date)}</Badge> : <Badge variant="outline" className="text-slate-400">Impaye</Badge>}
                        </TableCell>
                        <TableCell>
                          {!d.paid && <Button variant="ghost" size="sm" onClick={() => markPaid(selectedCall.id, d.owner_id)} className="text-green-600" title="Marquer paye" data-testid={`mark-paid-${d.owner_id}`}><Check size={14} /></Button>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          );})() : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un appel pour voir le detail</div>
          )}
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg" data-testid="call-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvel appel de fonds</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="call-name" placeholder="Appel Q1 2025" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Date *</label><Input type="date" value={form.date} onChange={e => setForm({...form, date: e.target.value})} /></div>
              <div><label className="form-label">Echeance</label><Input type="date" value={form.due_date} onChange={e => setForm({...form, due_date: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Type d&apos;appel</label>
                <Select value={form.call_type} onValueChange={v => setForm({...form, call_type: v})}>
                  <SelectTrigger data-testid="call-type-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="provisions">
                      <span className="inline-flex items-center gap-2"><Banknote size={12} className="text-blue-600" /> Provisions sur charges</span>
                    </SelectItem>
                    <SelectItem value="reserve">
                      <span className="inline-flex items-center gap-2"><ShieldCheck size={12} className="text-purple-600" /> Fonds de reserve (gros travaux)</span>
                    </SelectItem>
                    <SelectItem value="roulement">
                      <span className="inline-flex items-center gap-2"><Wallet size={12} className="text-emerald-600" /> Fonds de roulement (tresorerie permanente)</span>
                    </SelectItem>
                    <SelectItem value="special">
                      <span className="inline-flex items-center gap-2"><AlertTriangle size={12} className="text-orange-600" /> Appel special (hors budget)</span>
                    </SelectItem>
                  </SelectContent>
                </Select>
                {form.call_type && (
                  <p className="text-[11px] text-slate-500 mt-1.5 leading-snug" data-testid="call-type-help">
                    {getCallTypeMeta(form.call_type).desc}
                  </p>
                )}
              </div>
              <div><label className="form-label">Montant total *</label><Input type="number" step="0.01" value={form.total_amount} onChange={e => setForm({...form, total_amount: e.target.value})} data-testid="call-amount" /></div>
            </div>
            <div><label className="form-label">Cle de repartition</label>
              <Select value={form.distribution_key_id || 'default'} onValueChange={v => setForm({...form, distribution_key_id: v === 'default' ? '' : v})}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">Par tantiemes (defaut)</SelectItem>
                  {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div><label className="form-label">Description</label><Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} /></div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={saveCall} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="call-save-btn">Creer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
