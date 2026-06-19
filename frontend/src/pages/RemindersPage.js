import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Bell, Download, AlertTriangle } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const API = process.env.REACT_APP_BACKEND_URL;

const SEVERITY_STYLE = {
  critique: 'bg-red-600 text-white',
  urgent: 'bg-orange-500 text-white',
  rappel2: 'bg-amber-400 text-slate-900',
  rappel1: 'bg-yellow-200 text-slate-800',
};

const SEVERITY_LABEL = {
  critique: 'Critique (+90j)',
  urgent: 'Urgent (+30j)',
  rappel2: 'Rappel 2 (+7j)',
  rappel1: 'Rappel 1',
};

export default function RemindersPage() {
  const [data, setData] = useState(null);
  const [graceDays, setGraceDays] = useState(0);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/reminders/late-payments', { params: { grace_days: graceDays } });
      setData(data);
    } catch { toast.error('Erreur de chargement des rappels'); }
    finally { setLoading(false); }
  }, [graceDays]);

  useEffect(() => { load(); }, [load]);

  const downloadLetter = (ownerId, coproId) => {
    if (!coproId) { toast.error('Copropriete inconnue'); return; }
    window.open(`${API}/api/reminders/owner/${ownerId}/letter?copropriete_id=${coproId}`, '_blank');
  };

  return (
    <div data-testid="reminders-page">
      <div className="page-header flex items-start justify-between">
        <div>
          <h1 className="page-title"><Bell size={24} className="inline mr-2" />Rappels de paiement</h1>
          <p className="page-subtitle">Appels de fonds en retard, triees par severite</p>
        </div>
        <div className="flex items-end gap-3">
          <div>
            <label className="form-label">Delai de grace (j)</label>
            <Input type="number" min={0} value={graceDays} onChange={e => setGraceDays(Number(e.target.value) || 0)} className="w-24" data-testid="grace-days-input" />
          </div>
          <Button onClick={load} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="reload-reminders-btn">Rafraichir</Button>
        </div>
      </div>

      {loading && <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div>}

      {!loading && data && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mb-6">
            <Card className="border-slate-200"><CardContent className="p-4">
              <div className="text-xs text-slate-500 uppercase">Total retards</div>
              <div className="text-2xl font-black mt-1" style={{ fontFamily: 'Chivo,sans-serif' }} data-testid="reminders-total-count">{data.summary.total_count}</div>
              <div className="text-xs text-slate-500 mt-1 font-mono">{data.summary.total_amount.toFixed(2)} EUR</div>
            </CardContent></Card>
            {['critique', 'urgent', 'rappel2', 'rappel1'].map(s => (
              <Card key={s} className="border-slate-200"><CardContent className="p-4">
                <div className="text-xs text-slate-500 uppercase">{SEVERITY_LABEL[s]}</div>
                <div className="text-2xl font-black mt-1" style={{ fontFamily: 'Chivo,sans-serif' }}>{data.summary.by_severity[s] || 0}</div>
              </CardContent></Card>
            ))}
          </div>

          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                <TableHead>Severite</TableHead><TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead>
                <TableHead>Appel</TableHead><TableHead>Echeance</TableHead>
                <TableHead className="text-right">Retard</TableHead><TableHead className="text-right">Montant</TableHead>
                <TableHead className="w-32">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {data.late_payments.length === 0 ? (
                  <TableRow><TableCell colSpan={8} className="text-center py-12 text-slate-400">
                    <AlertTriangle size={32} className="inline mb-2 text-green-500" /><br />
                    Aucun appel en retard
                  </TableCell></TableRow>
                ) : data.late_payments.map((r, i) => (
                  <TableRow key={i} className="hover:bg-slate-50/50" data-testid={`reminder-row-${i}`}>
                    <TableCell><Badge className={SEVERITY_STYLE[r.severity]}>{SEVERITY_LABEL[r.severity]}</Badge></TableCell>
                    <TableCell className="font-medium">{r.owner_name}</TableCell>
                    <TableCell className="font-mono text-xs text-[#0055FF]">{r.vcs_code}</TableCell>
                    <TableCell>{r.fund_call_name}</TableCell>
                    <TableCell className="font-mono">{fmtDate(r.due_date)}</TableCell>
                    <TableCell className="text-right font-mono">{r.days_late} j</TableCell>
                    <TableCell className="text-right font-mono font-semibold">{r.amount.toFixed(2)} EUR</TableCell>
                    <TableCell>
                      <Button variant="outline" size="sm" onClick={() => downloadLetter(r.owner_id, r.copropriete_id)} data-testid={`reminder-letter-${i}`}>
                        <Download size={14} className="mr-1" /> Lettre PDF
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      )}
    </div>
  );
}
