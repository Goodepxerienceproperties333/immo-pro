import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Calculator, AlertTriangle, CheckCircle2 } from 'lucide-react';

export default function RegularizationDialog({ fiscalYearId, open, onClose, onDone }) {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState(null);
  const [step, setStep] = useState(1); // 1=preview 2=confirmed

  useEffect(() => {
    if (open && fiscalYearId) {
      setLoading(true); setPreview(null); setStep(1);
      api.post(`/fiscal/years/${fiscalYearId}/regularize?dry_run=true`)
        .then(r => setPreview(r.data))
        .catch(err => toast.error(err.response?.data?.detail || 'Erreur preview'))
        .finally(() => setLoading(false));
    }
  }, [open, fiscalYearId]);

  const confirm = async () => {
    setLoading(true);
    try {
      const { data } = await api.post(`/fiscal/years/${fiscalYearId}/regularize`);
      toast.success(`Regularisation effectuee: ${data.summary.owners_debiteurs} debiteur(s), ${data.summary.owners_crediteurs} crediteur(s)`);
      setStep(2); setPreview(data);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
    finally { setLoading(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-5xl max-h-[85vh] overflow-auto" data-testid="regularization-dialog">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Chivo,sans-serif' }}>
            <Calculator size={20} className="inline mr-2" />
            {step === 1 ? 'Cloture - Regularisation par cle de repartition' : 'Regularisation terminee'}
          </DialogTitle>
        </DialogHeader>

        {loading && <div className="text-center py-10 text-slate-500">Calcul en cours...</div>}

        {preview && !loading && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-2">
              <Card><CardContent className="p-3"><div className="text-[10px] uppercase text-slate-500">Budget vote</div><div className="font-mono font-bold text-sm mt-1">{preview.summary.budget_total?.toFixed(2)} EUR</div></CardContent></Card>
              <Card><CardContent className="p-3"><div className="text-[10px] uppercase text-slate-500">Frais reels</div><div className="font-mono font-bold text-sm mt-1">{preview.summary.total_real_expenses?.toFixed(2)} EUR</div></CardContent></Card>
              <Card><CardContent className="p-3"><div className="text-[10px] uppercase text-slate-500">Provisions appelees</div><div className="font-mono font-bold text-sm mt-1">{preview.summary.total_provisions_called?.toFixed(2)} EUR</div></CardContent></Card>
              <Card><CardContent className="p-3"><div className="text-[10px] uppercase text-slate-500">Difference budget-reel</div><div className={`font-mono font-bold text-sm mt-1 ${preview.summary.difference_budget_vs_real >= 0 ? 'text-green-700' : 'text-red-700'}`}>{preview.summary.difference_budget_vs_real?.toFixed(2)} EUR</div></CardContent></Card>
              <Card className="border-[#022D52]"><CardContent className="p-3"><div className="text-[10px] uppercase text-[#01213e]">Repartition</div><div className="text-sm mt-1 font-semibold">{preview.summary.owners_debiteurs} D / {preview.summary.owners_crediteurs} C</div></CardContent></Card>
            </div>

            <div className="mt-4">
              <div className="text-xs font-semibold text-slate-700 mb-2">Detail par nature de depense et cle de repartition</div>
              <div className="border rounded max-h-32 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow><TableHead>Compte</TableHead><TableHead>Cle</TableHead><TableHead className="text-right">Montant</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {preview.by_nature_key?.map((r, i) => (
                      <TableRow key={i}><TableCell className="font-mono text-xs">{r.account}</TableCell><TableCell className="text-xs">{r.key_name}</TableCell><TableCell className="text-right font-mono">{r.amount.toFixed(2)}</TableCell></TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs font-semibold text-slate-700 mb-2">Regularisation par proprietaire</div>
              <div className="border rounded max-h-64 overflow-y-auto">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead><TableHead>Compte</TableHead>
                    <TableHead className="text-right">Provisions appelees</TableHead>
                    <TableHead className="text-right">Frais reels alloues</TableHead>
                    <TableHead className="text-right">Regularisation</TableHead>
                    <TableHead>Statut</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {preview.per_owner?.map((p, i) => (
                      <TableRow key={i} data-testid={`regul-row-${i}`}>
                        <TableCell className="font-medium">{p.owner_name}</TableCell>
                        <TableCell className="font-mono text-[#022D52] text-xs">{p.vcs_code}</TableCell>
                        <TableCell className="font-mono text-xs text-slate-500">{p.account_provisions}</TableCell>
                        <TableCell className="text-right font-mono">{p.provisions_called.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{p.real_expenses.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${p.regularization > 0 ? 'text-red-700' : p.regularization < 0 ? 'text-green-700' : ''}`}>{p.regularization.toFixed(2)}</TableCell>
                        <TableCell><Badge variant="outline" className={p.status === 'debiteur' ? 'bg-red-50 text-red-700 border-red-200' : p.status === 'crediteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50'}>{p.status === 'debiteur' ? 'A facturer' : p.status === 'crediteur' ? 'A rembourser' : 'Solde'}</Badge></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t pt-3">
              {step === 1 ? (
                <>
                  <div className="text-xs text-amber-700 flex items-center gap-2"><AlertTriangle size={14} /> Cette action va EXTOURNER toutes les provisions et AFFECTER les frais reels. Verifiez les chiffres ci-dessus.</div>
                  <div className="flex gap-2">
                    <Button variant="outline" onClick={onClose}>Annuler</Button>
                    <Button onClick={confirm} className="bg-orange-600 hover:bg-orange-700 text-white" data-testid="confirm-regul-btn">
                      <Calculator size={14} className="mr-2" /> Lancer la regularisation
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <div className="text-xs text-green-700 flex items-center gap-2"><CheckCircle2 size={14} /> Ecritures comptables creees (extourne + affectation).</div>
                  <Button onClick={() => { onDone?.(); onClose(); }} className="bg-[#022D52] hover:bg-[#1D4ED8]">Terminer</Button>
                </>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
