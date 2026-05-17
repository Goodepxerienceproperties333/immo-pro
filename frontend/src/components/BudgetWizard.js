import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { toast } from 'sonner';
import { ChevronLeft, ChevronRight, Send, CheckCircle2, Calendar, Wallet, ShieldCheck, ClipboardList } from 'lucide-react';

const FREQ_OPTIONS = [
  { v: 1, l: 'Unique (annuel)', interval: 12 },
  { v: 2, l: 'Semestriel (2 appels)', interval: 6 },
  { v: 3, l: 'Quadrimestriel (3 appels)', interval: 4 },
  { v: 4, l: 'Trimestriel (4 appels)', interval: 3 },
  { v: 6, l: 'Bi-mensuel (6 appels)', interval: 2 },
  { v: 12, l: 'Mensuel (12 appels)', interval: 1 },
];

export default function BudgetWizard({ budget, distKeys = [], onClose, onDone, mode = 'create' }) {
  const [step, setStep] = useState(1);
  const [frequency, setFrequency] = useState(4);
  const [startDate, setStartDate] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
  });
  const [dueOffset, setDueOffset] = useState(30);
  const [reserveEnabled, setReserveEnabled] = useState(false);
  const [reserveAmount, setReserveAmount] = useState(0);
  const [reserveKeyId, setReserveKeyId] = useState('');
  const [reserveLabel, setReserveLabel] = useState('Fonds de reserve');
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);

  const interval = FREQ_OPTIONS.find(o => o.v === frequency)?.interval || 3;

  const previewCalls = async () => {
    setLoading(true);
    try {
      const payload = {
        budget_id: budget.id,
        frequency,
        start_date: startDate,
        due_offset_days: Number(dueOffset),
        reserve_fund: { enabled: reserveEnabled, amount: Number(reserveAmount) || 0, distribution_key_id: reserveKeyId || '', label: reserveLabel },
        copropriete_id: budget.copropriete_id || '',
      };
      const { data } = await api.post('/fund-calls/preview-from-budget', payload);
      setPreview(data);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur preview'); }
    finally { setLoading(false); }
  };

  // Auto-preview when reaching step 4
  useEffect(() => { if (step === 4) previewCalls(); /* eslint-disable-next-line */ }, [step]);

  const confirmLaunch = async () => {
    setLoading(true);
    try {
      const payload = {
        budget_id: budget.id,
        frequency,
        start_date: startDate,
        due_offset_days: Number(dueOffset),
        reserve_fund: { enabled: reserveEnabled, amount: Number(reserveAmount) || 0, distribution_key_id: reserveKeyId || '', label: reserveLabel },
        copropriete_id: budget.copropriete_id || '',
      };
      const url = mode === 'regenerate' ? '/fund-calls/regenerate-from-budget' : '/fund-calls/generate-from-budget';
      const { data } = await api.post(url, payload);
      if (mode === 'regenerate') {
        toast.success(`${data.created_ids?.length || 0} appels regeneres - ${data.deleted_count || 0} non echus remplaces, ${data.preserved_count || 0} preserves (paiements recus)`);
      } else {
        toast.success(`${data.created_ids?.length || 0} appels generes avec succes`);
      }
      onDone?.();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur generation'); }
    finally { setLoading(false); }
  };

  const STEPS = [
    { n: 1, icon: Calendar, label: 'Frequence' },
    { n: 2, icon: Wallet, label: 'Calendrier' },
    { n: 3, icon: ShieldCheck, label: 'Fonds de reserve' },
    { n: 4, icon: ClipboardList, label: 'Recapitulatif' },
  ];

  const canNext = () => {
    if (step === 1) return frequency > 0;
    if (step === 2) return !!startDate && dueOffset >= 0;
    if (step === 3) return !reserveEnabled || (reserveAmount >= 0);
    return true;
  };

  return (
    <Dialog open onOpenChange={() => onClose?.()}>
      <DialogContent className="max-w-4xl" data-testid="budget-wizard">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Chivo,sans-serif' }}>
            {mode === 'regenerate' ? 'Regenerer les appels non echus' : 'Assistant - Appels de fonds sur budget approuve'}
          </DialogTitle>
        </DialogHeader>
        {mode === 'regenerate' && (
          <div className="bg-amber-50 border border-amber-200 rounded p-2 text-xs text-amber-800 mb-2" data-testid="regen-warning">
            Les appels deja en partie payes seront PRESERVES. Seuls les appels futurs sans paiement seront remplaces.
          </div>
        )}

        {/* Stepper */}
        <div className="flex items-center justify-between border-b pb-3 mb-4">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            const active = step === s.n;
            const done = step > s.n;
            return (
              <div key={s.n} className="flex items-center flex-1">
                <div className={`flex flex-col items-center flex-1 ${active ? 'text-[#0055FF]' : done ? 'text-green-600' : 'text-slate-400'}`}>
                  <div className={`w-9 h-9 rounded-full border-2 flex items-center justify-center ${active ? 'border-[#0055FF] bg-blue-50' : done ? 'border-green-600 bg-green-50' : 'border-slate-300'}`}>
                    {done ? <CheckCircle2 size={18} /> : <Icon size={18} />}
                  </div>
                  <div className="text-xs mt-1 font-medium">{s.label}</div>
                </div>
                {i < STEPS.length - 1 && <div className={`h-0.5 flex-1 ${done ? 'bg-green-500' : 'bg-slate-200'}`} />}
              </div>
            );
          })}
        </div>

        {/* Step 1: Frequency */}
        {step === 1 && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">Combien d'appels lancer sur l'exercice ?</p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {FREQ_OPTIONS.map(opt => (
                <Card key={opt.v} className={`cursor-pointer transition ${frequency === opt.v ? 'border-2 border-[#0055FF] bg-blue-50/50' : 'border-slate-200 hover:border-slate-400'}`} onClick={() => setFrequency(opt.v)} data-testid={`freq-${opt.v}`}>
                  <CardContent className="p-4 text-center">
                    <div className="text-2xl font-black" style={{ fontFamily: 'Chivo,sans-serif' }}>{opt.v}</div>
                    <div className="text-xs text-slate-600 mt-1">{opt.l}</div>
                    <div className="text-[11px] text-slate-400 mt-1">Tous les {opt.interval} mois</div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}

        {/* Step 2: Calendar */}
        {step === 2 && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">Quand a lieu le 1er appel ? Les suivants seront generes automatiquement.</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="form-label">Date du 1er appel</label>
                <Input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} data-testid="wizard-start-date" />
              </div>
              <div>
                <label className="form-label">Echeance (jours apres emission)</label>
                <Input type="number" min={0} value={dueOffset} onChange={e => setDueOffset(e.target.value)} data-testid="wizard-due-offset" />
              </div>
            </div>
            <Card className="border-slate-200 bg-slate-50/50">
              <CardContent className="p-4">
                <div className="text-xs uppercase text-slate-500 mb-2">Calendrier prevu</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {Array.from({ length: frequency }).map((_, i) => {
                    const d = new Date(startDate);
                    d.setMonth(d.getMonth() + i * interval);
                    return (
                      <div key={i} className="border rounded p-2 text-center bg-white">
                        <div className="text-[10px] text-slate-400">Appel {i + 1}/{frequency}</div>
                        <div className="font-mono text-sm font-semibold">{d.toLocaleDateString('fr-BE')}</div>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Step 3: Reserve fund */}
        {step === 3 && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">Y a-t-il un appel pour <b>creer ou alimenter le fonds de reserve</b> ?</p>
            <Card className="border-slate-200">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <div className="font-semibold text-sm">Fonds de reserve</div>
                    <div className="text-xs text-slate-500">Si oui, sera ajoute uniquement au 1er appel.</div>
                  </div>
                  <Switch checked={reserveEnabled} onCheckedChange={setReserveEnabled} data-testid="wizard-reserve-toggle" />
                </div>
                {reserveEnabled && (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 pt-3 border-t">
                    <div>
                      <label className="form-label">Libelle</label>
                      <Input value={reserveLabel} onChange={e => setReserveLabel(e.target.value)} data-testid="wizard-reserve-label" />
                    </div>
                    <div>
                      <label className="form-label">Montant (EUR) *</label>
                      <Input type="number" step="0.01" value={reserveAmount} onChange={e => setReserveAmount(e.target.value)} data-testid="wizard-reserve-amount" />
                    </div>
                    <div>
                      <label className="form-label">Cle de repartition</label>
                      <Select value={reserveKeyId || 'default'} onValueChange={v => setReserveKeyId(v === 'default' ? '' : v)}>
                        <SelectTrigger data-testid="wizard-reserve-key"><SelectValue placeholder="Tantiemes (defaut)" /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="default">Tantiemes (defaut)</SelectItem>
                          {distKeys.map(k => <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Step 4: Recap */}
        {step === 4 && (
          <div className="space-y-3">
            {loading && <div className="text-center py-8 text-slate-500">Calcul en cours...</div>}
            {preview && !loading && (
              <>
                <div className="grid grid-cols-4 gap-3">
                  <Card><CardContent className="p-3"><div className="text-xs text-slate-500">Nombre d'appels</div><div className="text-xl font-black mt-1" style={{ fontFamily: 'Chivo,sans-serif' }}>{preview.summary.n_calls}</div></CardContent></Card>
                  <Card><CardContent className="p-3"><div className="text-xs text-slate-500">Budget annuel</div><div className="text-xl font-black mt-1 font-mono">{preview.summary.budget_total.toFixed(2)}</div></CardContent></Card>
                  <Card><CardContent className="p-3"><div className="text-xs text-slate-500">Fonds reserve</div><div className="text-xl font-black mt-1 font-mono">{preview.summary.reserve_total.toFixed(2)}</div></CardContent></Card>
                  <Card className="border-[#0055FF] bg-blue-50"><CardContent className="p-3"><div className="text-xs text-slate-500">Total appele</div><div className="text-xl font-black mt-1 font-mono text-[#0055FF]">{preview.summary.grand_total.toFixed(2)}</div></CardContent></Card>
                </div>

                <div className="border rounded-md overflow-hidden">
                  <table className="w-full text-sm">
                    <thead><tr className="bg-slate-50 text-xs text-slate-600 uppercase">
                      <th className="p-2 text-left">Appel</th>
                      <th className="p-2 text-left">Date</th>
                      <th className="p-2 text-left">Echeance</th>
                      <th className="p-2 text-right">Montant</th>
                      <th className="p-2 text-right">Dont reserve</th>
                      <th className="p-2 text-center">Proprietaires</th>
                    </tr></thead>
                    <tbody>
                      {preview.calls.map((c, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="p-2 font-medium">{c.name}</td>
                          <td className="p-2 font-mono text-xs">{c.date}</td>
                          <td className="p-2 font-mono text-xs">{c.due_date}</td>
                          <td className="p-2 text-right font-mono font-semibold">{c.total_amount.toFixed(2)}</td>
                          <td className="p-2 text-right font-mono text-xs text-purple-700">{c.reserve_amount > 0 ? c.reserve_amount.toFixed(2) : '-'}</td>
                          <td className="p-2 text-center"><Badge variant="outline">{c.distribution.length}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <details className="text-xs">
                  <summary className="cursor-pointer text-slate-600 select-none">Voir le detail par proprietaire du 1er appel ({preview.calls[0]?.distribution.length || 0})</summary>
                  <div className="border rounded mt-2 max-h-48 overflow-y-auto">
                    <table className="w-full text-xs">
                      <thead><tr className="bg-slate-50"><th className="p-1 text-left">Proprietaire</th><th className="p-1 text-left">VCS</th><th className="p-1 text-right">Montant</th></tr></thead>
                      <tbody>
                        {preview.calls[0]?.distribution.map((d, j) => (
                          <tr key={j} className="border-t border-slate-100">
                            <td className="p-1">{d.owner_name}</td>
                            <td className="p-1 font-mono text-[#0055FF]">{d.vcs_code}</td>
                            <td className="p-1 text-right font-mono">{d.amount.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              </>
            )}
          </div>
        )}

        {/* Navigation */}
        <div className="flex items-center justify-between pt-4 border-t mt-4">
          <Button variant="ghost" onClick={() => step > 1 ? setStep(step - 1) : onClose?.()} data-testid="wizard-back-btn">
            <ChevronLeft size={16} className="mr-1" />{step > 1 ? 'Precedent' : 'Annuler'}
          </Button>
          {step < 4 ? (
            <Button onClick={() => setStep(step + 1)} disabled={!canNext()} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="wizard-next-btn">
              Suivant <ChevronRight size={16} className="ml-1" />
            </Button>
          ) : (
            <Button onClick={confirmLaunch} disabled={loading || !preview} className="bg-green-600 hover:bg-green-700 text-white" data-testid="wizard-launch-btn">
              <Send size={16} className="mr-2" /> {loading ? 'Generation...' : 'Lancer les appels'}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
