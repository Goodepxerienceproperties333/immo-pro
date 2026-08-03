import { useState, useMemo } from 'react';
import api from '@/lib/api';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command';
import {
  AlertTriangle, CheckCircle2, Loader2, Building2, Users, Receipt, X, Filter, Search, Tag, Landmark, ChevronsUpDown,
} from 'lucide-react';
import { toast } from 'sonner';
import { fmtDate } from '@/lib/dateFmt';

import { fmtEUR } from '@/lib/format';
const MATCH_LABELS = {
  vcs: { label: 'VCS detecte', color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  name_exact: { label: 'Nom exact', color: 'text-[#01213e] bg-blue-50 border-blue-200' },
  name_partial: { label: 'Nom partiel', color: 'text-amber-700 bg-amber-50 border-amber-200' },
  supplier_iban: { label: 'IBAN fournisseur', color: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  supplier_name: { label: 'Nom fournisseur', color: 'text-[#01213e] bg-blue-50 border-blue-200' },
  invoice_number: { label: 'N facture', color: 'text-[#01213e] bg-blue-50 border-blue-200' },
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
 *  - owners, suppliers, invoices, expenseCategories, pcmnAccounts: listes
 *    utilisees pour les dropdowns de match manuel (avec recherche).
 *  - onSuccess: () => void  // appele apres import confirme (parent recharge)
 */
export default function CodaImportDialog({
  open, onClose, preview, copropriete_id,
  owners = [], suppliers = [], invoices = [],
  expenseCategories = [], pcmnAccounts = [],
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
  // Popover ouvert pour le combobox recherchable (une seule ligne a la fois)
  const [openPopover, setOpenPopover] = useState(null);

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
    return Number(fmtEUR((nn - oo)));
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
    owner_payment: owners.map(o => ({
      value: o.id,
      label: `${o.name || `${o.first_name||''} ${o.last_name||''}`.trim()}${o.vcs_code ? ` (${o.vcs_code})` : ''}`,
      search: [o.name, o.first_name, o.last_name, o.vcs_code, o.auxiliary_code, o.email].filter(Boolean).join(' ').toLowerCase(),
    })),
    supplier_payment: suppliers.map(s => ({
      value: s.id,
      label: s.name || '(sans nom)',
      search: [s.name, s.vat_number, s.iban, s.bce_number].filter(Boolean).join(' ').toLowerCase(),
    })),
    invoice: invoices.filter(i => i.status === 'unpaid').map(i => ({
      value: i.id,
      label: `${i.number} - ${i.supplier} (${fmtEUR((i.total_amount || 0))} EUR)`,
      search: [i.number, i.supplier, i.description].filter(Boolean).join(' ').toLowerCase(),
    })),
    expense_category: expenseCategories.map(c => ({
      value: c.id,
      label: `${c.code ? c.code + ' - ' : ''}${c.name}${c.account_number ? ` (${c.account_number})` : ''}`,
      search: [c.name, c.code, c.account_number, c.account_name].filter(Boolean).join(' ').toLowerCase(),
    })),
    pcmn_account: pcmnAccounts.map(a => ({
      value: a.number,
      label: `${a.number} - ${a.name}`,
      search: [a.number, a.name].filter(Boolean).join(' ').toLowerCase(),
    })),
  }), [owners, suppliers, invoices, expenseCategories, pcmnAccounts]);

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
            <Receipt size={18} className="text-[#022D52]" />
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
              <div className="text-sm font-mono font-semibold">{fmtEUR(Number(preview.old_balance?.balance || 0))} EUR</div>
              <div className="text-[10px] text-slate-500 mt-1">{preview.old_balance?.date || '-'}</div>
            </div>
            <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
              <div className="text-[10px] uppercase text-slate-500 font-semibold">Solde final</div>
              <div className="text-sm font-mono font-semibold">{fmtEUR(Number(preview.new_balance?.balance || 0))} EUR</div>
              <div className="text-[10px] text-slate-500 mt-1">Delta {fmtEUR(expectedDelta)} EUR</div>
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
                {fmtEUR(stats.sum)} EUR
              </span>
              {balanceOk ? (
                <Badge variant="outline" className="text-[10px] border-emerald-400 text-emerald-700">= delta</Badge>
              ) : (
                <Badge variant="outline" className="text-[10px] border-red-400 text-red-700">
                  ecart {fmtEUR((stats.sum - expectedDelta))}
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
                    ? 'bg-[#022D52] text-white border-[#022D52]'
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
                        {isCredit ? '+' : ''}{fmtEUR(Number(m.amount))}
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
                            <SelectTrigger className="h-7 text-[11px] w-[130px]" data-testid={`coda-mov-type-${idx}`}>
                              <SelectValue placeholder="Type" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="__none__">Aucun</SelectItem>
                              <SelectItem value="owner_payment"><Users size={10} className="inline mr-1" />Proprietaire</SelectItem>
                              <SelectItem value="supplier_payment"><Building2 size={10} className="inline mr-1" />Fournisseur</SelectItem>
                              <SelectItem value="invoice"><Receipt size={10} className="inline mr-1" />Facture</SelectItem>
                              <SelectItem value="expense_category"><Tag size={10} className="inline mr-1" />Categorie de depense</SelectItem>
                              <SelectItem value="pcmn_account"><Landmark size={10} className="inline mr-1" />Compte PCMN</SelectItem>
                            </SelectContent>
                          </Select>
                          {m.manual_match_type && (() => {
                            const opts = matchOptions[m.manual_match_type] || [];
                            const selected = opts.find(o => o.value === m.manual_match_id);
                            const popKey = `pop-${idx}`;
                            const isOpen = openPopover === popKey;
                            return (
                              <Popover open={isOpen} onOpenChange={(o) => setOpenPopover(o ? popKey : null)}>
                                <PopoverTrigger asChild>
                                  <Button
                                    variant="outline"
                                    role="combobox"
                                    aria-expanded={isOpen}
                                    className="h-7 text-[11px] flex-1 justify-between font-normal px-2"
                                    data-testid={`coda-mov-id-${idx}`}
                                  >
                                    <span className="truncate text-left">
                                      {selected ? selected.label : <span className="text-slate-400 italic">Rechercher / selectionner...</span>}
                                    </span>
                                    <ChevronsUpDown size={11} className="ml-1 opacity-50 shrink-0" />
                                  </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-[420px] p-0" align="start">
                                  <Command
                                    filter={(value, search) => {
                                      // value = option.value; on cherche dans le champ "search" via la map
                                      const opt = opts.find(o => o.value === value);
                                      if (!opt) return 0;
                                      const q = (search || '').toLowerCase();
                                      if (!q) return 1;
                                      return (opt.search || opt.label.toLowerCase()).includes(q) ? 1 : 0;
                                    }}
                                  >
                                    <CommandInput placeholder="Rechercher (nom, IBAN, VCS, compte, ...)" data-testid={`coda-mov-search-${idx}`} />
                                    <CommandList className="max-h-64">
                                      <CommandEmpty>Aucun resultat</CommandEmpty>
                                      <CommandGroup>
                                        {opts.map(opt => (
                                          <CommandItem
                                            key={opt.value}
                                            value={opt.value}
                                            onSelect={(v) => {
                                              updateMovement(idx, { manual_match_id: v });
                                              setOpenPopover(null);
                                            }}
                                            data-testid={`coda-mov-opt-${idx}-${opt.value}`}
                                          >
                                            <span className="text-[11px]">{opt.label}</span>
                                          </CommandItem>
                                        ))}
                                      </CommandGroup>
                                    </CommandList>
                                  </Command>
                                </PopoverContent>
                              </Popover>
                            );
                          })()}
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
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
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
