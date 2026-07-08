import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Link2 } from 'lucide-react';

/**
 * Dialog de rattachement manuel d'un fournisseur orphelin (nom sur facture
 * sans fiche correspondante) a un fournisseur existant en base.
 */
export default function LettrerDialog({
  open, onOpenChange,
  lettrerOrphan, lettrerSuppliers,
  lettrerSearch, setLettrerSearch,
  lettrerLoading, commitLettrer,
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" data-testid="lettrer-dialog">
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            Lettrer manuellement
          </DialogTitle>
          <p className="text-sm text-slate-600 mt-1">
            Selectionnez le fournisseur en base auquel rattacher les factures orphelines de
            <strong className="ml-1 text-amber-700">&laquo;{lettrerOrphan?.supplier_name}&raquo;</strong>
            {lettrerOrphan?.invoice_count > 0 && (
              <span className="ml-1 text-slate-500">({lettrerOrphan.invoice_count} facture(s))</span>
            )}
          </p>
        </DialogHeader>
        <div className="mt-3">
          <Input
            placeholder="Rechercher un fournisseur (nom, BCE, F0XXX)..."
            value={lettrerSearch}
            onChange={e => setLettrerSearch(e.target.value)}
            className="mb-3"
            data-testid="lettrer-search-input"
            autoFocus
          />
          <div className="max-h-[400px] overflow-y-auto border rounded-md divide-y divide-slate-100">
            {lettrerSuppliers
              .filter(sp => {
                if (!lettrerSearch) return true;
                const q = lettrerSearch.toLowerCase();
                return ((sp.name || '').toLowerCase().includes(q)
                  || (sp.auxiliary_code || '').toLowerCase().includes(q)
                  || (sp.vat_number || '').toLowerCase().includes(q)
                  || (sp.bce_number || '').toLowerCase().includes(q));
              })
              .slice(0, 60)
              .map(sp => (
                <button
                  key={sp.id}
                  onClick={() => commitLettrer(sp.id)}
                  disabled={lettrerLoading}
                  className="w-full text-left px-3 py-2 hover:bg-amber-50 flex items-center justify-between gap-2 text-sm disabled:opacity-50"
                  data-testid={`lettrer-pick-${sp.id}`}
                >
                  <div>
                    <div className="font-medium">{sp.name}</div>
                    <div className="text-xs text-slate-500">{sp.bce_number || sp.vat_number || '—'}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {sp.auxiliary_code && <Badge variant="outline" className="font-mono text-[10px] bg-slate-50">{sp.auxiliary_code}</Badge>}
                    <Link2 size={14} className="text-amber-600" />
                  </div>
                </button>
              ))}
            {lettrerSuppliers.length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-slate-500">
                Chargement des fournisseurs...
              </div>
            )}
            {lettrerSuppliers.length > 0 && lettrerSuppliers.filter(sp => {
              if (!lettrerSearch) return true;
              const q = lettrerSearch.toLowerCase();
              return ((sp.name || '').toLowerCase().includes(q));
            }).length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-slate-500">
                Aucun fournisseur trouve pour &laquo;{lettrerSearch}&raquo;
              </div>
            )}
          </div>
          <div className="mt-3 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800">
            <strong>Effet du lettrage :</strong> les factures de <em>&laquo;{lettrerOrphan?.supplier_name}&raquo;</em> seront
            rattachees au fournisseur selectionne ; les ecritures comptables AC manquantes (debit charge / credit 4400xxx)
            seront automatiquement creees pour que le solde apparaisse dans la balance de tiers.
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
