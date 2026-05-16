import { useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Search, BookOpen } from 'lucide-react';

export default function GrandLivrePage() {
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [accountFrom, setAccountFrom] = useState('');
  const [accountTo, setAccountTo] = useState('');
  const [ledger, setLedger] = useState([]);
  const [loading, setLoading] = useState(false);

  const loadLedger = async () => {
    setLoading(true);
    try {
      const params = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      if (accountFrom) params.account_from = accountFrom;
      if (accountTo) params.account_to = accountTo;
      const { data } = await api.get('/reports/grand-livre', { params });
      setLedger(data);
    } catch { toast.error('Erreur de chargement'); }
    finally { setLoading(false); }
  };

  return (
    <div data-testid="grand-livre-page">
      <div className="page-header"><h1 className="page-title"><BookOpen size={24} className="inline mr-2" />Grand Livre</h1><p className="page-subtitle">Detail de tous les mouvements par compte</p></div>

      <Card className="border-slate-200 mb-6"><CardContent className="p-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div><label className="form-label">Du</label><Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-40" /></div>
          <div><label className="form-label">Au</label><Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-40" /></div>
          <div><label className="form-label">Compte de</label><Input value={accountFrom} onChange={e => setAccountFrom(e.target.value)} placeholder="100000" className="w-32" /></div>
          <div><label className="form-label">Compte a</label><Input value={accountTo} onChange={e => setAccountTo(e.target.value)} placeholder="799999" className="w-32" /></div>
          <Button onClick={loadLedger} className="bg-[#0055FF] hover:bg-[#0040CC]" disabled={loading} data-testid="load-ledger-btn">
            <Search size={16} className="mr-2" /> {loading ? 'Chargement...' : 'Charger'}
          </Button>
        </div>
      </CardContent></Card>

      {ledger.length === 0 ? (
        <div className="text-center py-12 text-slate-400">Selectionnez une periode et cliquez sur Charger</div>
      ) : (
        <div className="space-y-6">
          {ledger.map(account => (
            <Card key={account.account_number} className="border-slate-200">
              <CardHeader className="pb-2 bg-slate-50 rounded-t-md">
                <CardTitle className="text-base flex items-center justify-between" style={{fontFamily:'Chivo,sans-serif'}}>
                  <span><span className="font-mono text-[#0055FF] mr-2">{account.account_number}</span>{account.account_name}</span>
                  <Badge variant="outline" className={`font-mono ${account.balance >= 0 ? 'text-blue-700' : 'text-red-700'}`}>Solde: {account.balance.toFixed(2)} EUR</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead className="w-24">Date</TableHead><TableHead className="w-16">Journal</TableHead><TableHead className="w-24">Ref</TableHead>
                    <TableHead>Description</TableHead><TableHead className="text-right w-28">Debit</TableHead><TableHead className="text-right w-28">Credit</TableHead><TableHead className="text-right w-28">Solde</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {account.movements.map((m, i) => (
                      <TableRow key={i} className="hover:bg-slate-50/50">
                        <TableCell className="font-mono text-xs">{m.date}</TableCell>
                        <TableCell><Badge variant="outline" className="text-[10px]">{m.journal}</Badge></TableCell>
                        <TableCell className="text-xs">{m.reference}</TableCell>
                        <TableCell className="text-sm">{m.description}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{m.debit > 0 ? m.debit.toFixed(2) : ''}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{m.credit > 0 ? m.credit.toFixed(2) : ''}</TableCell>
                        <TableCell className={`text-right font-mono text-sm font-semibold ${m.running_balance >= 0 ? 'text-slate-900' : 'text-red-700'}`}>{m.running_balance.toFixed(2)}</TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="bg-slate-50 font-bold">
                      <TableCell colSpan={4}>Totaux</TableCell>
                      <TableCell className="text-right font-mono">{account.total_debit.toFixed(2)}</TableCell>
                      <TableCell className="text-right font-mono">{account.total_credit.toFixed(2)}</TableCell>
                      <TableCell className="text-right font-mono">{account.balance.toFixed(2)}</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
