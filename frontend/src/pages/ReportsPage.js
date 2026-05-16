import { useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { BarChart3, Download, FileText } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ReportsPage() {
  const [tab, setTab] = useState('balance');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [balance, setBalance] = useState(null);
  const [bilan, setBilan] = useState(null);
  const [resultat, setResultat] = useState(null);
  const [decomptes, setDecomptes] = useState(null);
  const [years, setYears] = useState([]);
  const [loading, setLoading] = useState(false);

  useState(() => { api.get('/fiscal/years').then(r => setYears(r.data)).catch(() => {}); });

  const loadBalance = async () => { setLoading(true); try { const params = {}; if (dateFrom) params.date_from = dateFrom; if (dateTo) params.date_to = dateTo; const { data } = await api.get('/reports/balance', { params }); setBalance(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };
  const loadBilan = async () => { setLoading(true); try { const params = {}; if (dateTo) params.date_to = dateTo; const { data } = await api.get('/reports/bilan', { params }); setBilan(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };
  const loadResultat = async () => { setLoading(true); try { const params = {}; if (dateFrom) params.date_from = dateFrom; if (dateTo) params.date_to = dateTo; const { data } = await api.get('/reports/resultat', { params }); setResultat(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };
  const loadDecomptes = async () => { setLoading(true); try { const params = {}; if (dateFrom) params.date_from = dateFrom; if (dateTo) params.date_to = dateTo; const { data } = await api.get('/reports/decompte', { params }); setDecomptes(data); } catch { toast.error('Erreur'); } finally { setLoading(false); } };

  const downloadPdf = (ownerId) => { window.open(`${API}/api/reports/decompte/pdf/${ownerId}?date_from=${dateFrom || '2024-01-01'}&date_to=${dateTo || '2024-12-31'}`, '_blank'); };

  const DateFilters = ({ onLoad, label }) => (
    <Card className="border-slate-200 mb-6"><CardContent className="p-4"><div className="flex flex-wrap gap-4 items-end">
      <div><label className="form-label">Du</label><Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="w-40" /></div>
      <div><label className="form-label">Au</label><Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="w-40" /></div>
      <Button onClick={onLoad} className="bg-[#0055FF] hover:bg-[#0040CC]" disabled={loading}><BarChart3 size={16} className="mr-2" />{label}</Button>
    </div></CardContent></Card>
  );

  return (
    <div data-testid="reports-page">
      <div className="page-header"><h1 className="page-title"><BarChart3 size={24} className="inline mr-2" />Rapports Financiers</h1><p className="page-subtitle">Bilan, compte de resultats, balance et decomptes</p></div>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4"><TabsTrigger value="balance">Balance</TabsTrigger><TabsTrigger value="bilan">Bilan</TabsTrigger><TabsTrigger value="resultat">Resultat</TabsTrigger><TabsTrigger value="decomptes">Decomptes</TabsTrigger></TabsList>

        <TabsContent value="balance" className="mt-0">
          <DateFilters onLoad={loadBalance} label="Charger balance" />
          {balance && (<div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table><TableHeader><TableRow><TableHead>Compte</TableHead><TableHead>Libelle</TableHead><TableHead className="text-right">Total Debit</TableHead><TableHead className="text-right">Total Credit</TableHead><TableHead className="text-right">Solde Debit</TableHead><TableHead className="text-right">Solde Credit</TableHead></TableRow></TableHeader>
              <TableBody>
                {balance.accounts.map((a, i) => (<TableRow key={i} className="hover:bg-slate-50/50"><TableCell className="font-mono text-sm">{a.account_number}</TableCell><TableCell className="text-sm">{a.account_name}</TableCell><TableCell className="text-right font-mono">{a.total_debit.toFixed(2)}</TableCell><TableCell className="text-right font-mono">{a.total_credit.toFixed(2)}</TableCell><TableCell className="text-right font-mono">{a.solde_debit > 0 ? a.solde_debit.toFixed(2) : ''}</TableCell><TableCell className="text-right font-mono">{a.solde_credit > 0 ? a.solde_credit.toFixed(2) : ''}</TableCell></TableRow>))}
                <TableRow className="bg-slate-50 font-bold"><TableCell colSpan={2}>TOTAUX</TableCell><TableCell className="text-right font-mono">{balance.totals.total_debit.toFixed(2)}</TableCell><TableCell className="text-right font-mono">{balance.totals.total_credit.toFixed(2)}</TableCell><TableCell className="text-right font-mono">{balance.totals.solde_debit.toFixed(2)}</TableCell><TableCell className="text-right font-mono">{balance.totals.solde_credit.toFixed(2)}</TableCell></TableRow>
              </TableBody>
            </Table>
          </div>)}
        </TabsContent>

        <TabsContent value="bilan" className="mt-0">
          <DateFilters onLoad={loadBilan} label="Charger bilan" />
          {bilan && (<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card className="border-slate-200"><CardHeader className="bg-blue-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>ACTIF</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>{bilan.actif.map((a, i) => (<TableRow key={i}><TableCell className="font-mono text-sm">{a.account_number}</TableCell><TableCell>{a.account_name}</TableCell><TableCell className="text-right font-mono">{a.amount.toFixed(2)}</TableCell></TableRow>))}<TableRow className="bg-blue-50 font-bold"><TableCell colSpan={2}>TOTAL ACTIF</TableCell><TableCell className="text-right font-mono">{bilan.total_actif.toFixed(2)} EUR</TableCell></TableRow></TableBody></Table>
            </CardContent></Card>
            <Card className="border-slate-200"><CardHeader className="bg-green-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>PASSIF</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>{bilan.passif.map((p, i) => (<TableRow key={i}><TableCell className="font-mono text-sm">{p.account_number}</TableCell><TableCell>{p.account_name}</TableCell><TableCell className="text-right font-mono">{p.amount.toFixed(2)}</TableCell></TableRow>))}<TableRow className="bg-green-50 font-bold"><TableCell colSpan={2}>TOTAL PASSIF</TableCell><TableCell className="text-right font-mono">{bilan.total_passif.toFixed(2)} EUR</TableCell></TableRow></TableBody></Table>
            </CardContent></Card>
          </div>)}
        </TabsContent>

        <TabsContent value="resultat" className="mt-0">
          <DateFilters onLoad={loadResultat} label="Charger resultat" />
          {resultat && (<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card className="border-slate-200"><CardHeader className="bg-red-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>CHARGES (Classe 6)</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>{resultat.charges.map((c, i) => (<TableRow key={i}><TableCell className="font-mono text-sm">{c.account_number}</TableCell><TableCell className="text-sm">{c.account_name}</TableCell><TableCell className="text-right font-mono">{c.amount.toFixed(2)}</TableCell></TableRow>))}<TableRow className="bg-red-50 font-bold"><TableCell colSpan={2}>TOTAL CHARGES</TableCell><TableCell className="text-right font-mono">{resultat.total_charges.toFixed(2)} EUR</TableCell></TableRow></TableBody></Table>
            </CardContent></Card>
            <Card className="border-slate-200"><CardHeader className="bg-green-50 rounded-t-md"><CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>PRODUITS (Classe 7)</CardTitle></CardHeader><CardContent className="p-0">
              <Table><TableBody>{resultat.produits.map((p, i) => (<TableRow key={i}><TableCell className="font-mono text-sm">{p.account_number}</TableCell><TableCell className="text-sm">{p.account_name}</TableCell><TableCell className="text-right font-mono">{p.amount.toFixed(2)}</TableCell></TableRow>))}<TableRow className="bg-green-50 font-bold"><TableCell colSpan={2}>TOTAL PRODUITS</TableCell><TableCell className="text-right font-mono">{resultat.total_produits.toFixed(2)} EUR</TableCell></TableRow></TableBody></Table>
            </CardContent></Card>
            <Card className={`border-2 col-span-full ${resultat.resultat >= 0 ? 'border-green-300 bg-green-50' : 'border-red-300 bg-red-50'}`}><CardContent className="p-6 text-center">
              <div className="text-sm text-slate-600 mb-1">Resultat de l'exercice</div>
              <div className={`text-3xl font-black tracking-tight ${resultat.resultat >= 0 ? 'text-green-700' : 'text-red-700'}`} style={{fontFamily:'Chivo,sans-serif'}}>{resultat.resultat.toFixed(2)} EUR</div>
            </CardContent></Card>
          </div>)}
        </TabsContent>

        <TabsContent value="decomptes" className="mt-0">
          <DateFilters onLoad={loadDecomptes} label="Generer decomptes" />
          {decomptes && (<div className="space-y-4">
            <div className="text-sm text-slate-500 mb-2">Periode: {decomptes.period.from} au {decomptes.period.to} - {decomptes.decomptes.length} proprietaires</div>
            {decomptes.decomptes.map(d => (
              <Card key={d.owner_id} className="border-slate-200"><CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <span className="font-semibold text-slate-900">{d.owner_name}</span>
                    {d.vcs_code && <span className="ml-2 font-mono text-xs text-[#0055FF]">{d.vcs_code}</span>}
                    <div className="text-xs text-slate-500">Lots: {d.lots.map(l => l.number).join(', ')} - Quote-part: {d.share_pct}%</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono font-bold text-lg">{d.total_charges.toFixed(2)} EUR</span>
                    <Button variant="outline" size="sm" onClick={() => downloadPdf(d.owner_id)} data-testid={`download-pdf-${d.owner_id}`}><Download size={14} className="mr-1" /> PDF</Button>
                  </div>
                </div>
                {d.charges.length > 0 && (
                  <div className="border rounded overflow-hidden">
                    <table className="w-full text-xs"><thead><tr className="bg-slate-50"><th className="p-2 text-left">Date</th><th className="p-2 text-left">Description</th><th className="p-2">Facture</th><th className="p-2 text-right">Montant</th></tr></thead>
                      <tbody>{d.charges.map((c, i) => (<tr key={i} className="border-t border-slate-100"><td className="p-2 font-mono">{c.date}</td><td className="p-2">{c.description}</td><td className="p-2 font-mono">{c.invoice_number}</td><td className="p-2 text-right font-mono">{c.amount.toFixed(2)}</td></tr>))}</tbody>
                    </table>
                  </div>
                )}
              </CardContent></Card>
            ))}
          </div>)}
        </TabsContent>
      </Tabs>
    </div>
  );
}
