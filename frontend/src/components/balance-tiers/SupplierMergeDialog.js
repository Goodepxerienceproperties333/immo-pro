import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import api from '@/lib/api';
import { toast } from 'sonner';

/**
 * Dialog de fusion multi-fournisseurs.
 * Choix radio du fournisseur a conserver + confirmation.
 */
export default function SupplierMergeDialog({
  open, onOpenChange,
  suppliersData, selectedSupplierIds,
  mergeKeepId, setMergeKeepId,
  onSuccess,
}) {
  const confirm = async () => {
    const removeIds = Array.from(selectedSupplierIds).filter(id => id !== mergeKeepId);
    if (!mergeKeepId || removeIds.length === 0) {
      toast.error('Selection invalide');
      return;
    }
    try {
      const r = await api.post('/suppliers/merge', { keep_id: mergeKeepId, remove_ids: removeIds });
      toast.success(`Fusion OK : ${r.data.invoices_migrated} facture(s), ${r.data.bank_transactions_migrated} transaction(s) reassociees, ${r.data.removed_ids.length} fournisseur(s) absorbe(s)`);
      onSuccess();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur fusion');
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-2xl p-0 overflow-hidden"
        data-testid="merge-suppliers-dialog"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <div className="bg-gradient-to-r from-orange-600 to-orange-500 text-white px-5 py-4">
          <DialogTitle className="text-base font-semibold m-0">Fusionner {selectedSupplierIds.size} fournisseurs</DialogTitle>
          <div className="mt-1 text-xs opacity-90">
            Choisissez le fournisseur a conserver. Les autres seront absorbes (factures, transactions reassociees, infos manquantes copiees), puis supprimes.
          </div>
        </div>
        <div className="p-5 space-y-3">
          <p className="text-xs text-slate-700 font-medium">Conserver ce fournisseur :</p>
          <div className="space-y-1.5 max-h-[360px] overflow-y-auto">
            {suppliersData && suppliersData.suppliers
              .filter(s => selectedSupplierIds.has(s.supplier_id))
              .map(s => {
                const isKept = s.supplier_id === mergeKeepId;
                return (
                  <label
                    key={s.supplier_id}
                    className={`flex items-center gap-3 border rounded-md px-3 py-2 cursor-pointer ${isKept ? 'border-orange-400 bg-orange-50' : 'border-slate-200 hover:border-slate-300'}`}
                  >
                    <input
                      type="radio"
                      name="merge-keep"
                      checked={isKept}
                      onChange={() => setMergeKeepId(s.supplier_id)}
                      data-testid={`merge-keep-${s.supplier_id}`}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm">{s.supplier_name}</span>
                        {s.vat_number && <Badge variant="outline" className="text-[10px] font-mono bg-blue-50 text-[#01213e] border-blue-200">TVA {s.vat_number}</Badge>}
                        <Badge variant="outline" className="text-[10px] font-mono">{s.tier_account || '—'}</Badge>
                      </div>
                      <div className="text-[11px] text-slate-500 mt-0.5">
                        Facture: {s.total_invoiced.toFixed(2)} EUR &middot; Paye: {s.total_paid.toFixed(2)} EUR &middot; Solde: {s.balance.toFixed(2)} EUR
                      </div>
                    </div>
                    {isKept && <Badge className="bg-orange-600 text-white text-[10px]">A CONSERVER</Badge>}
                  </label>
                );
              })}
          </div>
          <div className="mt-3 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
            <strong>Attention :</strong> les fournisseurs absorbes seront supprimes apres reassociation des factures et transactions. Les ecritures comptables historiques restent inchangees.
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} data-testid="merge-cancel-btn">Annuler</Button>
            <Button
              onClick={confirm}
              className="bg-orange-600 hover:bg-orange-700 text-white"
              data-testid="merge-confirm-btn"
            >Confirmer la fusion</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
