import { useEffect, useMemo, useState } from 'react';
import api from '@/lib/api';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Plus, Save, Trash2, FileText, Download, Wallet } from 'lucide-react';

const FREQ_LABEL = { monthly: 'Mensuel', quarterly: 'Trimestriel', annual: 'Annuel' };

function fmtEUR(n) {
  const v = Number(n || 0);
  return v.toLocaleString('fr-BE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
}

export default function AdminBillingPage() {
  const [cfg, setCfg] = useState(null);
  const [rows, setRows] = useState([]);
  const [totals, setTotals] = useState({ annual_ht: 0, period_ht: 0, period_ttc: 0, lot_count: 0 });
  const [loading, setLoading] = useState(true);
  const [savingCfg, setSavingCfg] = useState(false);
  const [dlg, setDlg] = useState(null); // syndic row en edition
  const [invoiceRow, setInvoiceRow] = useState(null);
  const [invoicePeriod, setInvoicePeriod] = useState(String(new Date().getFullYear()));

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/admin/billing/syndics');
      setRows(r.data.rows || []);
      setTotals(r.data.totals || {});
      setCfg(r.data.config || null);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement echoue');
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const saveCfg = async () => {
    if (!cfg) return;
    setSavingCfg(true);
    try {
      await api.put('/admin/billing/config', {
        tiers: cfg.tiers || [],
        vat_rate: Number(cfg.vat_rate) || 21,
        default_frequency: cfg.default_frequency || 'annual',
        admin_info: cfg.admin_info || {},
      });
      toast.success('Configuration enregistree');
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setSavingCfg(false); }
  };

  const setTier = (i, key, val) => {
    const tiers = [...(cfg.tiers || [])];
    const v = val === '' ? null : Number(val);
    tiers[i] = { ...tiers[i], [key]: v };
    setCfg({ ...cfg, tiers });
  };
  const addTier = () => {
    const last = (cfg.tiers || [])[cfg.tiers.length - 1];
    const nextMin = last ? (last.max_lots || 0) + 1 : 1;
    setCfg({ ...cfg, tiers: [...(cfg.tiers || []), { min_lots: nextMin, max_lots: nextMin + 49, price_per_lot: 0 }] });
  };
  const removeTier = (i) => {
    const tiers = [...(cfg.tiers || [])];
    tiers.splice(i, 1);
    setCfg({ ...cfg, tiers });
  };

  const saveSyndic = async () => {
    if (!dlg) return;
    try {
      await api.put(`/admin/billing/syndics/${dlg.syndic_user_id}`, {
        frequency: dlg.frequency,
        negotiated_flat_fee: dlg.negotiated_flat_fee === '' || dlg.negotiated_flat_fee == null ? null : Number(dlg.negotiated_flat_fee),
        notes: dlg.notes || '',
      });
      toast.success('Enregistre');
      setDlg(null);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    }
  };

  const downloadInvoice = async () => {
    if (!invoiceRow || !invoicePeriod) return;
    const url = `${process.env.REACT_APP_BACKEND_URL}/api/admin/billing/invoice/${invoiceRow.syndic_user_id}/pdf?period_label=${encodeURIComponent(invoicePeriod)}`;
    window.open(url, '_blank');
    setInvoiceRow(null);
  };

  const exportCsv = () => {
    const header = ['Syndic', 'Email', 'ACPs', 'Lots actifs', 'Frequence', 'Mode', 'Montant HT periode', 'TVA', 'TTC', 'Annuel HT'];
    const lines = [header.join(';')];
    for (const r of rows) {
      lines.push([
        r.syndic_name, r.syndic_email, r.acp_count, r.lot_count,
        FREQ_LABEL[r.frequency] || r.frequency, r.pricing_mode,
        r.period_amount_ht.toFixed(2), r.period_amount_tva.toFixed(2),
        r.period_amount_ttc.toFixed(2), r.annual_amount_ht.toFixed(2),
      ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(';'));
    }
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `facturation-syndics-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const tiersPreview = useMemo(() => {
    if (!cfg?.tiers) return '';
    return cfg.tiers.map(t =>
      `${t.min_lots}-${t.max_lots ?? '+'} : ${Number(t.price_per_lot || 0).toFixed(2)}€/lot`
    ).join(' → ');
  }, [cfg]);

  if (loading || !cfg) {
    return <div className="p-8 text-slate-500">Chargement...</div>;
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto" data-testid="admin-billing-page">
      <div className="flex items-center gap-3">
        <Wallet className="w-6 h-6 text-[#022D52]" />
        <h1 className="text-2xl font-bold text-slate-900" style={{ fontFamily: 'Chivo,sans-serif' }}>
          Facturation des syndics
        </h1>
      </div>
      <p className="text-sm text-slate-500 -mt-4">
        Espace interne. Definit le bareme applicable et genere les factures a envoyer aux cabinets syndics.
      </p>

      {/* Bareme */}
      <Card className="border-slate-200">
        <CardHeader>
          <CardTitle className="text-base">Bareme degressif par tranche</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="text-xs text-slate-500">
            Pour chaque nouveau lot au-dela d&apos;une tranche, on applique le prix de la tranche suivante.
            Un forfait <b>negocie</b> par syndic prend le pas sur ce bareme.
          </div>
          <div className="space-y-2">
            <div className="grid grid-cols-12 gap-2 text-[11px] uppercase text-slate-500 font-semibold">
              <div className="col-span-3">Lots min</div>
              <div className="col-span-3">Lots max (vide = illimite)</div>
              <div className="col-span-4">Prix par lot (EUR / an)</div>
              <div className="col-span-2"></div>
            </div>
            {(cfg.tiers || []).map((t, i) => (
              <div key={i} className="grid grid-cols-12 gap-2 items-center">
                <Input className="col-span-3" type="number" min={0} value={t.min_lots ?? ''}
                       onChange={e => setTier(i, 'min_lots', e.target.value)}
                       data-testid={`tier-min-${i}`} />
                <Input className="col-span-3" type="number" min={0} value={t.max_lots ?? ''}
                       placeholder="Illimite" onChange={e => setTier(i, 'max_lots', e.target.value)}
                       data-testid={`tier-max-${i}`} />
                <Input className="col-span-4" type="number" step="0.01" min={0} value={t.price_per_lot ?? ''}
                       onChange={e => setTier(i, 'price_per_lot', e.target.value)}
                       data-testid={`tier-price-${i}`} />
                <Button variant="ghost" size="sm" className="col-span-2 text-red-500"
                        onClick={() => removeTier(i)} data-testid={`tier-remove-${i}`}>
                  <Trash2 size={14} className="mr-1" /> Retirer
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={addTier} data-testid="tier-add">
              <Plus size={14} className="mr-1" /> Ajouter une tranche
            </Button>
          </div>
          <div className="grid grid-cols-3 gap-3 pt-3 border-t border-slate-200">
            <div>
              <Label className="text-xs">TVA (%)</Label>
              <Input type="number" step="0.1" value={cfg.vat_rate ?? 21}
                     onChange={e => setCfg({ ...cfg, vat_rate: Number(e.target.value) })}
                     data-testid="cfg-vat" />
            </div>
            <div>
              <Label className="text-xs">Frequence par defaut</Label>
              <select value={cfg.default_frequency || 'annual'} onChange={e => setCfg({ ...cfg, default_frequency: e.target.value })}
                      className="w-full h-9 border border-slate-300 rounded-md px-2 text-sm bg-white"
                      data-testid="cfg-default-freq">
                <option value="monthly">Mensuel</option>
                <option value="quarterly">Trimestriel</option>
                <option value="annual">Annuel</option>
              </select>
            </div>
            <div className="flex items-end">
              <Button onClick={saveCfg} disabled={savingCfg} className="w-full bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="cfg-save">
                <Save size={14} className="mr-1" /> {savingCfg ? 'Enregistrement...' : 'Enregistrer le bareme'}
              </Button>
            </div>
          </div>
          <details className="text-xs text-slate-500 pt-2 border-t border-slate-100">
            <summary className="cursor-pointer text-[#022D52] font-medium">Coordonnees cabinet (pour PDF)</summary>
            <div className="grid grid-cols-2 gap-3 mt-3">
              <div><Label className="text-xs">Raison sociale</Label>
                <Input value={cfg.admin_info?.name || ''} onChange={e => setCfg({ ...cfg, admin_info: { ...(cfg.admin_info || {}), name: e.target.value } })} data-testid="admin-name" />
              </div>
              <div><Label className="text-xs">N&deg; TVA</Label>
                <Input value={cfg.admin_info?.vat_number || ''} onChange={e => setCfg({ ...cfg, admin_info: { ...(cfg.admin_info || {}), vat_number: e.target.value } })} data-testid="admin-vat" />
              </div>
              <div className="col-span-2"><Label className="text-xs">Adresse</Label>
                <Input value={cfg.admin_info?.address || ''} onChange={e => setCfg({ ...cfg, admin_info: { ...(cfg.admin_info || {}), address: e.target.value } })} data-testid="admin-address" />
              </div>
              <div><Label className="text-xs">IBAN</Label>
                <Input value={cfg.admin_info?.iban || ''} onChange={e => setCfg({ ...cfg, admin_info: { ...(cfg.admin_info || {}), iban: e.target.value } })} data-testid="admin-iban" />
              </div>
            </div>
          </details>
        </CardContent>
      </Card>

      {/* Totaux */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Total lots actifs</div>
          <div className="text-2xl font-bold text-slate-900" data-testid="total-lots">{totals.lot_count}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Total HT / periode</div>
          <div className="text-2xl font-bold text-[#022D52]" data-testid="total-period-ht">{fmtEUR(totals.period_ht)}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Total TTC / periode</div>
          <div className="text-2xl font-bold text-emerald-700" data-testid="total-period-ttc">{fmtEUR(totals.period_ttc)}</div>
        </CardContent></Card>
        <Card><CardContent className="p-4">
          <div className="text-xs text-slate-500">Total annuel HT</div>
          <div className="text-2xl font-bold text-slate-900" data-testid="total-annual-ht">{fmtEUR(totals.annual_ht)}</div>
        </CardContent></Card>
      </div>

      {/* Table syndics */}
      <Card className="border-slate-200">
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">Syndics ({rows.length})</CardTitle>
          <Button variant="outline" size="sm" onClick={exportCsv} data-testid="btn-export-csv">
            <Download size={14} className="mr-1" /> Exporter CSV
          </Button>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-50 text-left text-[11px] uppercase text-slate-500">
                  <th className="px-3 py-2">Syndic</th>
                  <th className="px-3 py-2 text-right">ACPs</th>
                  <th className="px-3 py-2 text-right">Lots</th>
                  <th className="px-3 py-2">Frequence</th>
                  <th className="px-3 py-2">Mode</th>
                  <th className="px-3 py-2 text-right">HT / periode</th>
                  <th className="px-3 py-2 text-right">TTC / periode</th>
                  <th className="px-3 py-2 text-right">Annuel HT</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.syndic_user_id} className="border-t hover:bg-slate-50" data-testid={`row-${r.syndic_user_id}`}>
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-900">{r.syndic_name}</div>
                      <div className="text-[11px] text-slate-500 font-mono">{r.syndic_email}</div>
                    </td>
                    <td className="px-3 py-2 text-right">{r.acp_count}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold">{r.lot_count}</td>
                    <td className="px-3 py-2">{FREQ_LABEL[r.frequency] || r.frequency}</td>
                    <td className="px-3 py-2">
                      {r.pricing_mode === 'negocie' ? (
                        <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">Negocie</Badge>
                      ) : (
                        <Badge variant="outline">Bareme</Badge>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{fmtEUR(r.period_amount_ht)}</td>
                    <td className="px-3 py-2 text-right font-mono font-semibold">{fmtEUR(r.period_amount_ttc)}</td>
                    <td className="px-3 py-2 text-right font-mono text-slate-500">{fmtEUR(r.annual_amount_ht)}</td>
                    <td className="px-3 py-2 flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => setDlg({ ...r })} data-testid={`btn-edit-${r.syndic_user_id}`}>Editer</Button>
                      <Button size="sm" variant="ghost" onClick={() => setInvoiceRow(r)} data-testid={`btn-invoice-${r.syndic_user_id}`}>
                        <FileText size={13} className="mr-1" /> Facture
                      </Button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400">Aucun syndic</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {tiersPreview && (
            <div className="text-[11px] text-slate-500 mt-3">Bareme actif : {tiersPreview}</div>
          )}
        </CardContent>
      </Card>

      {/* Dialog edition syndic */}
      <Dialog open={!!dlg} onOpenChange={o => !o && setDlg(null)}>
        <DialogContent data-testid="edit-syndic-dialog">
          <DialogHeader>
            <DialogTitle>Facturation - {dlg?.syndic_name}</DialogTitle>
          </DialogHeader>
          {dlg && (
            <div className="space-y-3">
              <div>
                <Label className="text-xs">Frequence</Label>
                <select value={dlg.frequency} onChange={e => setDlg({ ...dlg, frequency: e.target.value })}
                        className="w-full h-9 border border-slate-300 rounded-md px-2 text-sm bg-white"
                        data-testid="edit-freq">
                  <option value="monthly">Mensuel</option>
                  <option value="quarterly">Trimestriel</option>
                  <option value="annual">Annuel</option>
                </select>
              </div>
              <div>
                <Label className="text-xs">Forfait negocie annuel (EUR HT) - vide = utilise le bareme</Label>
                <Input type="number" step="0.01" min={0} value={dlg.negotiated_flat_fee ?? ''}
                       onChange={e => setDlg({ ...dlg, negotiated_flat_fee: e.target.value })}
                       placeholder="Vide = bareme"
                       data-testid="edit-nego" />
              </div>
              <div>
                <Label className="text-xs">Notes</Label>
                <Input value={dlg.notes || ''} onChange={e => setDlg({ ...dlg, notes: e.target.value })}
                       placeholder="Contrat, remarques..." data-testid="edit-notes" />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDlg(null)}>Annuler</Button>
            <Button onClick={saveSyndic} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="edit-save">
              <Save size={14} className="mr-1" /> Enregistrer
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Dialog generation facture PDF */}
      <Dialog open={!!invoiceRow} onOpenChange={o => !o && setInvoiceRow(null)}>
        <DialogContent data-testid="invoice-dialog">
          <DialogHeader>
            <DialogTitle>Generer la facture</DialogTitle>
          </DialogHeader>
          {invoiceRow && (
            <div className="space-y-3">
              <div className="text-sm text-slate-600">
                Client : <strong>{invoiceRow.syndic_name}</strong>
                <br />Montant TTC : <strong>{fmtEUR(invoiceRow.period_amount_ttc)}</strong>
              </div>
              <div>
                <Label className="text-xs">Libelle de la periode</Label>
                <Input value={invoicePeriod} onChange={e => setInvoicePeriod(e.target.value)}
                       placeholder="Ex: 2026, 2026-Q3, 2026-11"
                       data-testid="invoice-period" />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setInvoiceRow(null)}>Annuler</Button>
            <Button onClick={downloadInvoice} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="invoice-download">
              <Download size={14} className="mr-1" /> Telecharger PDF
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
