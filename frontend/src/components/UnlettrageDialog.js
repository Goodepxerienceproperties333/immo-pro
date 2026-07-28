import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Unlink, Link2, Loader2, Search, CheckCircle2, AlertCircle, ArrowRight } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

import { fmtEUR } from '@/lib/format';
/**
 * Dialog de delettrage / relettrage rapide depuis le Journal FI.
 * - Affiche la facture actuellement lettree (linked_invoice)
 * - Charge les candidates depuis /api/banking/unlettrage-candidates/{txn_id}
 * - Boutons : "Delettrer seulement" ou "Relettrer a cette facture" (1 clic)
 */
export default function UnlettrageDialog({ entry, open, onClose, onSuccess }) {
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!open || !entry?.source_id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      setData(null);
      setSearch('');
      setShowAll(false);
      try {
        const r = await api.get(`/banking/unlettrage-candidates/${entry.source_id}`);
        if (!cancelled) setData(r.data);
      } catch (e) {
        if (!cancelled) toast.error(e?.response?.data?.detail || 'Erreur chargement candidats');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, entry?.source_id]);

  const closeDialog = () => {
    if (busy) return;
    onClose?.();
  };

  const doUnlettrage = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.post(`/banking/unlettrage/${entry.source_id}`);
      toast.success('Lettrage annule. La transaction est de nouveau disponible.');
      onSuccess?.();
      onClose?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur delettrage');
    } finally {
      setBusy(false);
    }
  };

  const doRelettrage = async (invoiceId, invoiceLabel) => {
    if (busy) return;
    if (!window.confirm(`Relettrer cette transaction a la facture "${invoiceLabel}" ?`)) return;
    setBusy(true);
    try {
      await api.post(`/banking/relettrage/${entry.source_id}`, { new_invoice_id: invoiceId });
      toast.success(`Re-lettrage effectue vers "${invoiceLabel}".`);
      onSuccess?.();
      onClose?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur relettrage');
    } finally {
      setBusy(false);
    }
  };

  const linked = entry?.linked_invoice;
  const txn = data?.transaction || {};
  const candidates = data?.candidates || [];

  // Filtres : recherche libre + affichage tous / meme fournisseur
  const filtered = candidates.filter(c => {
    if (!showAll && data?.current_supplier && !c.same_supplier) return false;
    if (search) {
      const q = search.toLowerCase();
      if (!(c.invoice_number || '').toLowerCase().includes(q)
          && !(c.supplier || '').toLowerCase().includes(q)) return false;
    }
    return true;
  });

  return (
    <Dialog open={open} onOpenChange={closeDialog}>
      <DialogContent className="max-w-3xl" data-testid="unlettrage-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
            <Unlink size={18} className="text-amber-500" />
            Delettrer / Relettrer la transaction
          </DialogTitle>
        </DialogHeader>

        {/* Recap transaction + lettrage actuel */}
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 space-y-2 text-sm">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="text-[11px] text-slate-500 uppercase tracking-wide">Transaction</div>
              <div className="font-medium text-slate-800 truncate">{txn.description || entry?.description || '—'}</div>
              <div className="text-xs text-slate-500 mt-0.5">
                {txn.date ? fmtDate(txn.date) : ''} · <span className="font-mono font-semibold text-slate-700">{fmtEUR(Number(txn.amount_abs || 0))} EUR</span>
              </div>
            </div>
            {linked && (
              <div className="text-right shrink-0">
                <div className="text-[11px] text-slate-500 uppercase tracking-wide">Actuellement lettree a</div>
                <Badge variant="outline" className="bg-emerald-50 border-emerald-300 text-emerald-800">
                  {linked.invoice_number || linked.supplier_name || '(sans numero)'}
                </Badge>
                <div className="text-xs text-slate-500 mt-0.5">{linked.supplier_name}</div>
                <div className="text-xs font-mono text-slate-600">{fmtEUR(Number(linked.amount_ttc || 0))} EUR</div>
              </div>
            )}
          </div>
        </div>

        {/* Option A : Delettrer seulement */}
        <div className="border border-amber-200 bg-amber-50/50 rounded-lg p-3 flex items-center justify-between gap-3">
          <div className="flex-1">
            <div className="font-semibold text-slate-800 text-sm flex items-center gap-1.5">
              <Unlink size={13} className="text-amber-600" /> Delettrer seulement
            </div>
            <div className="text-xs text-slate-600 mt-0.5">
              La transaction redevient "a lettrer". Vous pourrez la relier plus tard depuis la page Banque.
            </div>
          </div>
          <Button
            onClick={doUnlettrage}
            disabled={busy}
            variant="outline"
            className="border-amber-400 text-amber-800 hover:bg-amber-100 shrink-0"
            data-testid="unlettrage-only-btn"
          >
            {busy ? <Loader2 size={13} className="animate-spin mr-1.5" /> : <Unlink size={13} className="mr-1.5" />}
            Delettrer
          </Button>
        </div>

        {/* Option B : Relettrer directement */}
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          <div className="bg-slate-50 border-b border-slate-200 p-3 flex items-center gap-2 flex-wrap">
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-slate-800 text-sm flex items-center gap-1.5">
                <Link2 size={13} className="text-[#022D52]" /> Relettrer directement a une autre facture
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {data?.current_supplier
                  ? `Suggestion : factures non payees du fournisseur "${data.current_supplier}" avec montant proche`
                  : 'Factures non payees compatibles'}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Search size={13} className="text-slate-400 -mr-1" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="N° / fournisseur..."
                className="h-8 text-xs w-40"
                data-testid="unlettrage-search-input"
              />
              {data?.current_supplier && (
                <button
                  onClick={() => setShowAll(v => !v)}
                  className={`text-[11px] px-2 py-1 rounded border transition-colors ${
                    showAll
                      ? 'bg-slate-800 text-white border-slate-800'
                      : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
                  }`}
                  data-testid="unlettrage-show-all-btn"
                >
                  {showAll ? 'Tous fournisseurs' : 'Meme fournisseur uniquement'}
                </button>
              )}
            </div>
          </div>

          <div className="max-h-[320px] overflow-auto">
            {loading ? (
              <div className="flex items-center justify-center gap-2 text-slate-500 text-sm py-6">
                <Loader2 size={14} className="animate-spin" /> Recherche des candidats...
              </div>
            ) : filtered.length === 0 ? (
              <div className="text-center text-sm text-slate-500 py-6">
                <AlertCircle size={16} className="inline mr-1 text-slate-400" />
                Aucune facture non payee compatible.
                {!showAll && data?.current_supplier && (
                  <button
                    onClick={() => setShowAll(true)}
                    className="ml-2 text-[#022D52] hover:underline"
                  >
                    Voir tous fournisseurs
                  </button>
                )}
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500 uppercase sticky top-0">
                  <tr>
                    <th className="p-2 text-left font-medium">N° facture</th>
                    <th className="p-2 text-left font-medium">Fournisseur</th>
                    <th className="p-2 text-left font-medium">Date</th>
                    <th className="p-2 text-right font-medium">Solde du</th>
                    <th className="p-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((c, i) => {
                    const label = c.invoice_number || c.supplier || '';
                    return (
                      <tr
                        key={c.id}
                        className={`border-t border-slate-100 hover:bg-slate-50/60 ${c.exact_match ? 'bg-emerald-50/40' : ''}`}
                        data-testid={`candidate-row-${i}`}
                      >
                        <td className="p-2 font-mono text-xs">
                          {c.invoice_number || <span className="text-slate-400 italic">—</span>}
                          {c.exact_match && (
                            <Badge variant="outline" className="ml-1.5 text-[9px] bg-emerald-100 border-emerald-300 text-emerald-700">
                              Match exact
                            </Badge>
                          )}
                          {c.same_supplier && !c.exact_match && (
                            <Badge variant="outline" className="ml-1.5 text-[9px] bg-blue-50 border-blue-300 text-[#01213e]">
                              {c.supplier ? 'Meme fournisseur' : ''}
                            </Badge>
                          )}
                        </td>
                        <td className="p-2 text-xs truncate max-w-[180px]">{c.supplier}</td>
                        <td className="p-2 text-xs text-slate-500">{c.date ? fmtDate(c.date) : '—'}</td>
                        <td className="p-2 text-right font-mono text-xs">
                          {fmtEUR(Number(c.remaining))}
                          {c.status === 'partially_paid' && (
                            <div className="text-[10px] text-amber-600">
                              paye : {fmtEUR(Number(c.amount_paid))}
                            </div>
                          )}
                        </td>
                        <td className="p-2 text-right">
                          <Button
                            size="sm"
                            className={`h-7 text-xs ${c.exact_match ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-[#022D52] hover:bg-[#1D4ED8]'} text-white`}
                            onClick={() => doRelettrage(c.id, label)}
                            disabled={busy}
                            data-testid={`relettrage-btn-${i}`}
                          >
                            {c.exact_match ? <CheckCircle2 size={11} className="mr-1" /> : <ArrowRight size={11} className="mr-1" />}
                            Relettrer
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="flex justify-end pt-2">
          <Button variant="outline" onClick={closeDialog} disabled={busy} data-testid="unlettrage-cancel-btn">
            Annuler
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
