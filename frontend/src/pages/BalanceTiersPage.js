import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Users, Truck, Eye, ArrowUpRight, ArrowDownRight } from 'lucide-react';

export default function BalanceTiersPage() {
  const [tab, setTab] = useState('owners');
  const [ownersData, setOwnersData] = useState(null);
  const [suppliersData, setSuppliersData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailType, setDetailType] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, s] = await Promise.all([api.get('/reports/balance-tiers/owners'), api.get('/reports/balance-tiers/suppliers')]);
      setOwnersData(o.data); setSuppliersData(s.data);
    } catch { toast.error('Erreur de chargement'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const viewOwnerDetail = async (ownerId) => {
    try { const { data } = await api.get(`/reports/balance-tiers/owners/${ownerId}`); setDetail(data); setDetailType('owner'); } catch { toast.error('Erreur'); }
  };
  const viewSupplierDetail = async (supplierId) => {
    try { const { data } = await api.get(`/reports/balance-tiers/suppliers/${supplierId}`); setDetail(data); setDetailType('supplier'); } catch { toast.error('Erreur'); }
  };

  if (loading) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div>;

  return (
    <div data-testid="balance-tiers-page">
      <div className="page-header"><h1 className="page-title">Balance de Tiers</h1><p className="page-subtitle">Situation de compte des proprietaires et fournisseurs</p></div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="mb-4" data-testid="tiers-tabs">
          <TabsTrigger value="owners"><Users size={14} className="mr-2" /> Proprietaires</TabsTrigger>
          <TabsTrigger value="suppliers"><Truck size={14} className="mr-2" /> Fournisseurs</TabsTrigger>
        </TabsList>

        <TabsContent value="owners" className="mt-0">
          {ownersData && (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                <Card className="border-red-200 bg-red-50"><CardContent className="p-4 flex items-center gap-3">
                  <ArrowUpRight size={20} className="text-red-600" />
                  <div><div className="text-[10px] uppercase tracking-wider text-red-600 font-semibold">Total debiteurs</div>
                    <div className="text-xl font-black text-red-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{ownersData.total_debiteurs.toFixed(2)} EUR</div>
                    <div className="text-[10px] text-red-500">Proprietaires qui doivent a la copropriete</div>
                  </div>
                </CardContent></Card>
                <Card className="border-green-200 bg-green-50"><CardContent className="p-4 flex items-center gap-3">
                  <ArrowDownRight size={20} className="text-green-600" />
                  <div><div className="text-[10px] uppercase tracking-wider text-green-600 font-semibold">Total crediteurs</div>
                    <div className="text-xl font-black text-green-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{ownersData.total_crediteurs.toFixed(2)} EUR</div>
                    <div className="text-[10px] text-green-500">Copropriete doit rembourser</div>
                  </div>
                </CardContent></Card>
              </div>
              <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Proprietaire</TableHead><TableHead>VCS</TableHead>
                    <TableHead className="text-right">Appele</TableHead><TableHead className="text-right">Paye</TableHead>
                    <TableHead className="text-right">Solde</TableHead><TableHead>Statut</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {ownersData.owners.map(o => (
                      <TableRow key={o.owner_id} className="hover:bg-slate-50/50">
                        <TableCell className="font-medium">{o.owner_name}</TableCell>
                        <TableCell className="font-mono text-xs text-[#0055FF]">{o.vcs_code}</TableCell>
                        <TableCell className="text-right font-mono">{o.total_called.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{o.total_paid.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${o.balance > 0 ? 'text-red-700' : o.balance < 0 ? 'text-green-700' : 'text-slate-500'}`}>{o.balance.toFixed(2)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={o.status === 'debiteur' ? 'bg-red-50 text-red-700 border-red-200' : o.status === 'crediteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50 text-slate-500'}>
                            {o.status === 'debiteur' ? 'Debiteur' : o.status === 'crediteur' ? 'Crediteur' : 'Solde'}
                          </Badge>
                        </TableCell>
                        <TableCell><Button variant="ghost" size="sm" onClick={() => viewOwnerDetail(o.owner_id)} data-testid={`view-owner-${o.owner_id}`}><Eye size={14} /></Button></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </TabsContent>

        <TabsContent value="suppliers" className="mt-0">
          {suppliersData && (
            <>
              <Card className="border-orange-200 bg-orange-50 mb-4"><CardContent className="p-4 flex items-center gap-3">
                <Truck size={20} className="text-orange-600" />
                <div><div className="text-[10px] uppercase tracking-wider text-orange-600 font-semibold">Total a payer aux fournisseurs</div>
                  <div className="text-xl font-black text-orange-700 font-mono" style={{fontFamily:'Chivo,sans-serif'}}>{suppliersData.total_a_payer.toFixed(2)} EUR</div>
                </div>
              </CardContent></Card>
              <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Fournisseur</TableHead><TableHead>N TVA</TableHead>
                    <TableHead className="text-right">Facture</TableHead><TableHead className="text-right">Paye</TableHead>
                    <TableHead className="text-right">Solde</TableHead><TableHead>Statut</TableHead><TableHead className="w-16"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {suppliersData.suppliers.map(s => (
                      <TableRow key={s.supplier_id} className="hover:bg-slate-50/50">
                        <TableCell className="font-medium">{s.supplier_name}</TableCell>
                        <TableCell className="font-mono text-xs">{s.vat_number || '-'}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_invoiced.toFixed(2)}</TableCell>
                        <TableCell className="text-right font-mono">{s.total_paid.toFixed(2)}</TableCell>
                        <TableCell className={`text-right font-mono font-bold ${s.balance > 0 ? 'text-orange-700' : s.balance < 0 ? 'text-green-700' : 'text-slate-500'}`}>{s.balance.toFixed(2)}</TableCell>
                        <TableCell>
                          <Badge variant="outline" className={s.status === 'crediteur' ? 'bg-orange-50 text-orange-700 border-orange-200' : s.status === 'debiteur' ? 'bg-green-50 text-green-700 border-green-200' : 'bg-slate-50 text-slate-500'}>
                            {s.status === 'crediteur' ? 'A payer' : s.status === 'debiteur' ? 'Trop-paye' : 'Solde'}
                          </Badge>
                        </TableCell>
                        <TableCell><Button variant="ghost" size="sm" onClick={() => viewSupplierDetail(s.supplier_id)} data-testid={`view-supplier-${s.supplier_id}`}><Eye size={14} /></Button></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>

      {/* Detail Dialog */}
      <Dialog open={!!detail} onOpenChange={() => setDetail(null)}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto" data-testid="tiers-detail-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              Situation de compte: {detailType === 'owner' ? detail?.owner?.name : detail?.supplier?.name}
            </DialogTitle>
            {detailType === 'owner' && detail?.owner?.vcs_code && (
              <p className="font-mono text-sm text-[#0055FF]">{detail.owner.vcs_code}</p>
            )}
          </DialogHeader>
          <div className="mt-2">
            <div className="flex gap-4 mb-4 text-sm">
              <div><span className="text-slate-500">Total debit:</span> <span className="font-mono font-bold">{detail?.total_debit?.toFixed(2)} EUR</span></div>
              <div><span className="text-slate-500">Total credit:</span> <span className="font-mono font-bold">{detail?.total_credit?.toFixed(2)} EUR</span></div>
              <div>
                <span className="text-slate-500">Solde:</span>
                <span className={`font-mono font-bold ml-1 ${detail?.status === 'debiteur' ? 'text-red-700' : detail?.status === 'crediteur' ? (detailType === 'owner' ? 'text-green-700' : 'text-orange-700') : 'text-slate-600'}`}>
                  {detail?.balance?.toFixed(2)} EUR
                </span>
                <Badge variant="outline" className="ml-2 text-[10px]">{detail?.status === 'debiteur' ? (detailType === 'owner' ? 'Doit payer' : 'Trop-paye') : detail?.status === 'crediteur' ? (detailType === 'owner' ? 'A rembourser' : 'A payer') : 'Solde'}</Badge>
              </div>
            </div>
            <div className="border rounded-md overflow-hidden">
              <Table>
                <TableHeader><TableRow>
                  <TableHead className="w-24">Date</TableHead><TableHead>Description</TableHead><TableHead>Ref</TableHead>
                  <TableHead className="text-right w-28">Debit</TableHead><TableHead className="text-right w-28">Credit</TableHead><TableHead className="text-right w-28">Solde</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {(detail?.movements || []).map((m, i) => (
                    <TableRow key={i} className="hover:bg-slate-50/50">
                      <TableCell className="font-mono text-xs">{m.date}</TableCell>
                      <TableCell className="text-sm">{m.description}</TableCell>
                      <TableCell className="text-xs text-slate-400">{m.reference}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{m.debit > 0 ? m.debit.toFixed(2) : ''}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{m.credit > 0 ? m.credit.toFixed(2) : ''}</TableCell>
                      <TableCell className={`text-right font-mono text-sm font-semibold ${m.running_balance > 0 ? 'text-red-700' : m.running_balance < 0 ? 'text-green-700' : ''}`}>{m.running_balance?.toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
