import { useState, useMemo } from 'react';
import api from '@/lib/api';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertTriangle, CheckCircle2, Loader2, Building2, Users, Receipt, X, Filter,
} from 'lucide-react';
import { toast } from 'sonner';
import { fmtDate } from '@/lib/dateFmt';

const MATCH_LABELS = {
  vcs: { label: 'VCS detecte', color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  name_exact: { label: 'Nom exact', color: 'text-blue-700 bg-blue-50 border-blue-200' },
  name_partial: { label: 'Nom partiel', color: 'text-amber-700 bg-amber-50 border-amber-200' },
  supplier_iban: { label: 'IBAN fournisseur', color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  supplier_name: { label: 'Nom fournisseur', color: 'text-blue-700 bg-blue-50 border-blue-200' },
  invoice_number: { label: 'N facture', color: 'text-blue-700 bg-blue-50 border-blue-200' },
};

const CONFIDENCE_LABELS = {
  high: { label: 'Eleve', color: 'text-emerald-700' },
  medium: { label: 'Moyen', color: 'text-amber-700' },
  low: { label: 'Faible', color: 'text-red-700' },
};

/**
 * Dialog de mapping CODA : montre le contenu parse + suggestions de match
 * pour chaque mouvement, permet a l'utilisateur d'override avant l'import.
 *
 * Props:
 *  - open: bool
 *  - onClose: () => void
 *  - preview: object renvoye par /api/banking/coda/preview
 *  - copropriete_id: string
 *  - owners, suppliers, invoices: listes pour les dropdowns de match manuel
 *  - onSuccess: () => void  // appele apres import confirme (parent recharge)
 */
export default function CodaImportDialog({
  open, onClose, preview, copropriete_id,
  owners = [], suppliers = [], invoices = [],
  onSuccess,
}) {
  const [movements, setMovements] = useState(() =>
    (preview?.movements || []).map(m => ({
      ...m,
      manual_match_type: m.suggestion?.match_type || '',
      manual_match_id: m.suggestion?.match_id || '',
      include: true,
    }))
  );
  const [filter, setFilter] = useState('all'); // all | unmatched | matched | excluded
  const [importing, setImporting] = useState(false);

  // Recharge si preview change (nouveau fichier)
  useMemo(() => {
    setMovements(
      (preview?.movements || []).map(m => ({
        ...m,
        manual_match_type: m.suggestion?.match_type || '',
        manual_match_id: m.suggestion?.match_id || '',
        include: true,
      }))
    );
  }, [preview]);

  const stats = useMemo(() => {
    const total = movements.length;
    const included = movements.filter(m => m.include).length;
    const excluded = total - included;
    const matched = movements.filter(m => m.include && m.manual_match_type && m.manual_match_id).length;
    const unmatched = included - matched;
    const sum = movements.filter(m => m.include).reduce((s, m) => s + (Number(m.amount) || 0), 0);
    return { total, included, excluded, matched, unmatched, sum };
  }, [movements]);

  const expectedDelta = useMemo(() => {
    const oo = Number(preview?.old_balance?.balance || 0);
    const nn = Number(preview?.new_balance?.balance || 0);
    return Number((nn - oo).toFixed(2));
  }, [preview]);

  const balanceOk = Math.abs(stats.sum - expectedDelta) < 0.01;

  const updateMovement = (idx, patch) => {
    setMovements(prev => prev.map((m, i) => (i === idx ? { ...m, ...patch } : m)));
  };

  const filteredMovements = useMemo(() => {
    return movements
      .map((m, idx) => ({ ...m, _origIdx: idx }))
      .filter(m => {
        if (filter === 'unmatched') return m.include && !(m.manual_match_type && m.manual_match_id);
        if (filter === 'matched') return m.include && m.manual_match_type && m.manual_match_id;
        if (filter === 'excluded') return !m.include;
        return true;
      });
  }, [movements, filter]);

  const matchOptions = useMemo(() => ({
    owners: owners.map(o => ({ value: o.id, label: `${o.name || `${o.first_name||''} ${o.last_name||''}`.trim()}${o.vcs_code ? ` (${o.vcs_code})` : ''}` })),
    suppliers: suppliers.map(s => ({ value: s.id, label: s.name || '(sans nom)' })),
    invoices: invoices.filter(i => i.status === 'unpaid').map(i => ({ value: i.id, label: `${i.number} - ${i.supplier} (${(i.total_amount || 0).toFixed(2)} EUR)` })),
  }), [owners, suppliers, invoices]);

  const confirmImport = async () => {
    if (!preview) return;
    setImporting(true);
    try {
      const payload = {
        file_hash: preview.file_hash,
        filename: preview.filename,
        copropriete_id,
        statement_number: preview.old_balance?.statement_number || '',
        statement_date: preview.new_balance?.date || preview.old_balance?.date || '',
        account_number: preview.old_balance?.account_number || '',
        opening_balance: Number(preview.old_balance?.balance || 0),
        closing_balance: Number(preview.new_balance?.balance || 0),
        movements: movements.map(m => ({
          value_date: m.value_date || '',
          entry_date: m.entry_date || '',
          amount: Number(m.amount || 0),
          type: m.type || 'credit',
          counterparty_name: m.counterparty_name || '',
          counterparty_account: m.counterparty_account || '',
          communication: m.communication || '',
          transaction_code: m.transaction_code || '',
          reference: m.reference || '',
          manual_match_type: m.manual_match_type || '',
          manual_match_id: m.manual_match_id || '',
          include: !!m.include,
        })),
      };
      const { data } = await api.post('/banking/coda/import-confirmed', payload);
      toast.success(data.message, {
        description: `Auto-lettre: ${data.matched_auto} | Manuels: ${data.matched_manual}`,
        duration: 6000,
      });
      onSuccess?.();
      onClose?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec de l\'import');
    } finally {
      setImporting(false);
    }
  };

  if (!preview) return null;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v && !importing) onClose?.(); }}>
      <DialogContent className="max-w-[1600px] max-h-[92vh] overflow-hidden flex flex-col p-0" data-testid="coda-import-dialog">
        <DialogHeader className="px-6 py-4 border-b border-slate-200 shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <Receipt size={18} className="text-blue-600" />
            Import CODA - Mapping et controle
            <Badge variant="outline" className="ml-2 text-[10px]">{preview.filename}</Badge>
          </DialogTitle>
        </DialogHeader>

        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
          {/* HEADER : statut + balances */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3" data-testid="coda-header-account">
              <div className="text-[10px] uppercase text-slate-500 font-semibold">Compte</div>
              <div className="text-sm font-mono text-slate-800 truncate">{preview.old_balance?.account_number || '-'}</div>
              <div className="text-[10px] text-slate-500 mt-1">{preview.header?.account_holder || preview.header?.addressee || '-'}</div>
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="text-[10px] uppercase text-slate-500 font-semibold">Extrait</div>
              <div className="text-sm text-slate-800">N {preview.old_balance?.statement_number || '-'}</div>
              <div className="text-[10px] text-slate-500 mt-1">
                du {preview.old_balance?.date || '?'} au {preview.new_balance?.date || '?'}
              </div>
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="text-[10px] uppercase text-slate-500 font-semibold">Solde initial</div>
              <div className="text-sm font-mono font-semibold">{Number(preview.old_balance?.balance || 0).toFixed(2)} EUR</div>
              <div className="text-[10px] text-slate-500 mt-1">{preview.old_balance?.date || '-'}</div>
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="text-[10px] uppercase text-slate-500 font-semibold">Solde final</div>
              <div className="text-sm font-mono font-semibold">{Number(preview.new_balance?.balance || 0).toFixed(2)} EUR</div>
              <div className="text-[10px] text-slate-500 mt-1">Delta {expectedDelta.toFixed(2)} EUR</div>
            </div>
          </div>

          {/* WARNINGS */}
          {preview.duplicate_warning && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 flex items-start gap-2" data-testid="coda-dup-warning">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div>
                <b>Fichier deja importe</b> - {preview.duplicate_warning.message}
              </div>
            </div>
          )}
          {preview.account_holder_warning && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 flex items-start gap-2" data-testid="coda-account-warning">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div>{preview.account_holder_warning}</div>
            </div>
          )}

          {/* STATS bar */}
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <div className="flex items-center gap-1">
              <Badge variant="outline" className="text-[10px]">{stats.total} mouvements</Badge>
            </div>
            <div className="flex items-center gap-1">
              <CheckCircle2 size={12} className="text-emerald-600" />
              <span className="text-slate-700"><b>{stats.matched}</b> mappes</span>
            </div>
            <div className="flex items-center gap-1">
              <AlertTriangle size={12} className="text-amber-600" />
              <span className="text-slate-700"><b>{stats.unmatched}</b> non mappes</span>
            </div>
            {stats.excluded > 0 && (
              <div className="flex items-center gap-1">
                <X size={12} className="text-red-600" />
                <span className="text-slate-700"><b>{stats.excluded}</b> exclus</span>
              </div>
            )}
            <div className="flex items-center gap-1 ml-auto">
              <span className="text-slate-500">Somme inclus :</span>
              <span className={`font-mono font-semibold ${balanceOk ? 'text-emerald-700' : 'text-red-700'}`}>
                {stats.sum.toFixed(2)} EUR
              </span>
              {balanceOk ? (
                <Badge variant="outline" className="text-[10px] border-emerald-400 text-emerald-700">= delta</Badge>
              ) : (
                <Badge variant="outline" className="text-[10px] border-red-400 text-red-700">
                  ecart {(stats.sum - expectedDelta).toFixed(2)}
                </Badge>
              )}
            </div>
          </div>

          {/* FILTRES */}
          <div className="flex items-center gap-2 text-xs">
            <Filter size={12} className="text-slate-400" />
            {[
              { v: 'all', l: `Tous (${stats.total})` },
              { v: 'matched', l: `Mappes (${stats.matched})` },
              { v: 'unmatched', l: `Non mappes (${stats.unmatched})` },
              { v: 'excluded', l: `Exclus (${stats.excluded})` },
            ].map(f => (
              <button
                key={f.v}
                onClick={() => setFilter(f.v)}
                className={`px-2.5 py-1 rounded border transition ${
                  filter === f.v
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
                data-testid={`coda-filter-${f.v}`}
              >
                {f.l}
              </button>
            ))}
          </div>

          {/* TABLE MOUVEMENTS */}
          <div className="rounded-md border border-slate-200 overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-slate-100 text-slate-600 uppercase text-[10px] tracking-wider">
                <tr>
                  <th className="px-2 py-2 text-left w-[80px]">Date</th>
                  <th className="px-2 py-2 text-right w-[100px]">Montant</th>
                  <th className="px-2 py-2 text-left">Contrepartie</th>
                  <th className="px-2 py-2 text-left">IBAN / Comm.</th>
                  <th className="px-2 py-2 text-left w-[140px]">Suggestion</th>
                  <th className="px-2 py-2 text-left w-[340px]">Match (override possible)</th>
                  <th className="px-2 py-2 text-center w-[60px]">Inclure</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredMovements.length === 0 ? (
                  <tr><td colSpan="7" className="px-3 py-6 text-center text-slate-400 italic">Aucun mouvement dans ce filtre.</td></tr>
                ) : filteredMovements.map(m => {
                  const idx = m._origIdx;
                  const sugg = m.suggestion || {};
                  const matchKind = MATCH_LABELS[sugg.match_reason];
                  const conf = CONFIDENCE_LABELS[sugg.confidence];
                  const isCredit = (m.amount || 0) >= 0;
                  return (
                    <tr key={idx} className={`hover:bg-slate-50 ${!m.include ? 'opacity-40' : ''}`} data-testid={`coda-mov-row-${idx}`}>
                      <td className="px-2 py-2 font-mono text-[11px]">{fmtDate(m.value_date) || '-'}</td>
                      <td className={`px-2 py-2 font-mono font-semibold text-right ${isCredit ? 'text-emerald-700' : 'text-red-700'}`}>
                        {isCredit ? '+' : ''}{Number(m.amount).toFixed(2)}
                      </td>
                      <td className="px-2 py-2">
                        <div className="text-slate-800 truncate max-w-[200px]" title={m.counterparty_name}>{m.counterparty_name || '-'}</div>
                      </td>
                      <td className="px-2 py-2 text-[10px] text-slate-500">
                        {m.counterparty_account && <div className="font-mono truncate max-w-[180px]">{m.counterparty_account}</div>}
                        {m.communication && <div className="truncate max-w-[200px]" title={m.communication}>{m.communication}</div>}
                      </td>
                      <td className="px-2 py-2">
                        {matchKind ? (
                          <div className={`text-[10px] inline-flex items-center gap-1 px-1.5 py-0.5 rounded border ${matchKind.color}`}>
                            {matchKind.label}
                            {conf && <span className={`text-[9px] ${conf.color}`}>({conf.label})</span>}
                          </div>
                        ) : (
                          <span className="text-[10px] text-slate-400 italic">Aucune</span>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <div className="flex gap-1 items-center">
                          <Select
                            value={m.manual_match_type || '__none__'}
                            onValueChange={(v) => updateMovement(idx, {
                              manual_match_type: v === '__none__' ? '' : v,
                              manual_match_id: v === '__none__' ? '' : (v === m.suggestion?.match_type ? m.suggestion.match_id : ''),
                            })}
                          >
                            <SelectTrigger className="h-7 text-[11px] w-[110px]" data-testid={`coda-mov-type-${idx}`}>
                              <SelectValue placeholder="Type" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__none__">Aucun</SelectItem>
                              <SelectItem value="owner_payment"><Users size={10} className="inline mr-1" />Proprietaire</SelectItem>
                              <SelectItem value="supplier_payment"><Building2 size={10} className="inline mr-1" />Fournisseur</SelectItem>
                              <SelectItem value="invoice"><Receipt size={10} className="inline mr-1" />Facture</SelectItem>
                            </SelectContent>
                          </Select>
                          {m.manual_match_type && (
                            <Select
                              value={m.manual_match_id || ''}
                              onValueChange={(v) => updateMovement(idx, { manual_match_id: v })}
                            >
                              <SelectTrigger className="h-7 text-[11px] flex-1" data-testid={`coda-mov-id-${idx}`}>
                                <SelectValue placeholder="Selectionner..." />
                              </SelectTrigger>
                              <SelectContent>
                                {(m.manual_match_type === 'owner_payment' ? matchOptions.owners
                                  : m.manual_match_type === 'supplier_payment' ? matchOptions.suppliers
                                  : matchOptions.invoices
                                ).map(opt => (
                                  <SelectItem key={opt.value} value={opt.value}>{opt.label}</SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          )}
                        </div>
                        {m.manual_match_type && m.manual_match_id && sugg.match_id === m.manual_match_id && (
                          <div className="text-[9px] text-emerald-700 mt-0.5">= suggestion auto</div>
                        )}
                      </td>
                      <td className="px-2 py-2 text-center">
                        <input
                          type="checkbox"
                          checked={!!m.include}
                          onChange={(e) => updateMovement(idx, { include: e.target.checked })}
                          className="cursor-pointer"
                          data-testid={`coda-mov-include-${idx}`}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* FOOTER : actions */}
        <div className="border-t border-slate-200 px-6 py-3 flex items-center justify-between gap-3 bg-white shrink-0">
          <div className="text-xs text-slate-500">
            <b>{stats.included}</b> mouvement(s) seront importes
            {stats.matched > 0 && <span> - <b>{stats.matched}</b> avec match</span>}
            {stats.unmatched > 0 && <span className="text-amber-700"> - <b>{stats.unmatched}</b> sans match (auto-lettrage tente)</span>}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose} disabled={importing} data-testid="coda-cancel-btn">
              Annuler
            </Button>
            <Button
              onClick={confirmImport}
              disabled={importing || stats.included === 0 || !!preview.duplicate_warning}
              className="bg-blue-600 hover:bg-blue-700 text-white"
              data-testid="coda-confirm-btn"
            >
              {importing ? (
                <><Loader2 size={14} className="mr-1 animate-spin" />Import...</>
              ) : (
                <>Confirmer l&apos;import ({stats.included} mouvement(s))</>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
