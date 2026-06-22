import { useState, useMemo, useEffect } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { FileText, Check, Plus, X, AlertCircle, ChevronDown, ChevronRight, FolderInput } from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import { fmtDate } from '@/lib/dateFmt';

/**
 * Dialog d'import d'un PDF "Regroupement de documents" Optipro.
 * Affiche les factures detectees, le matching propose, et permet la correction
 * manuelle (attacher a une facture existante / creer une nouvelle facture / ignorer).
 */
export default function BundleImportDialog({
  open, onOpenChange,
  invoices: invoicesProp, distKeys, categories, accounts,
  onSuccess,
}) {
  const [step, setStep] = useState('idle'); // idle | analyzing | review | committing | done
  const [bundleResult, setBundleResult] = useState(null);
  const [assignments, setAssignments] = useState({}); // block_id -> {mode, invoice_id, invoice_data, expanded}
  const [commitResult, setCommitResult] = useState(null);
  const [allInvoices, setAllInvoices] = useState([]);

  // Load ALL invoices for the ACP when dialog opens (ignore fiscal year filter)
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get('/invoices', { params: { all: true } });
        if (!cancelled) setAllInvoices(data || []);
      } catch {
        // Fallback to prop
        if (!cancelled) setAllInvoices(invoicesProp || []);
      }
    })();
    return () => { cancelled = true; };
  }, [open, invoicesProp]);

  const reset = () => {
    setStep('idle'); setBundleResult(null); setAssignments({}); setCommitResult(null);
  };

  const handleClose = (next) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const handleFileChange = async (e) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    setStep('analyzing');
    try {
      const fd = new FormData();
      fd.append('file', f);
      const copro = localStorage.getItem('copropriete_id') || '';
      if (copro) fd.append('copropriete_id', copro);
      const { data } = await api.post('/invoices/bundle-analyze', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setBundleResult(data);
      // Initialise assignments from suggested matches
      const init = {};
      (data.blocks || []).forEach(b => {
        const sm = b.suggested_match;
        // auto-attach only if confidence >= 0.65 AND target invoice has no attachment yet
        if (sm && sm.confidence >= 0.65 && !sm.has_attachment) {
          init[b.block_id] = { mode: 'attach', invoice_id: sm.invoice_id };
        } else if (sm && sm.confidence >= 0.65) {
          // match found but already has attachment - default to skip, let user decide
          init[b.block_id] = { mode: 'skip', invoice_id: sm.invoice_id };
        } else {
          init[b.block_id] = { mode: 'skip', invoice_id: '' };
        }
      });
      setAssignments(init);
      setStep('review');
      toast.success(`${data.invoice_count} facture(s) detectee(s) sur ${data.total_pages} page(s)`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec analyse PDF');
      setStep('idle');
    }
  };

  const updateAssignment = (blockId, patch) => {
    setAssignments(a => ({ ...a, [blockId]: { ...(a[blockId] || {}), ...patch } }));
  };

  const startCreateMode = (block) => {
    // Pre-fill from extracted metadata
    const invData = {
      number: block.invoice_number || '',
      date: block.date_iso || new Date().toISOString().split('T')[0],
      due_date: '',
      supplier: block.supplier_hint || '',
      description: '',
      total_amount: block.total_amount || 0,
      vat_amount: 0,
      account_number: '',
      expense_category_id: '',
      distribution_key_id: '',
      status: 'unpaid',
      copropriete_id: localStorage.getItem('copropriete_id') || '',
      is_private_fee: false,
      private_fee_owner_id: '',
    };
    updateAssignment(block.block_id, { mode: 'create', invoice_data: invData, expanded: true });
  };

  const commit = async () => {
    if (!bundleResult) return;
    const list = (bundleResult.blocks || []).map(b => {
      const a = assignments[b.block_id] || { mode: 'skip' };
      const base = { block_id: b.block_id, page_range: b.page_range, mode: a.mode };
      if (a.mode === 'attach') base.invoice_id = a.invoice_id;
      if (a.mode === 'create') base.invoice_data = a.invoice_data;
      return base;
    });
    const toProcess = list.filter(l => l.mode !== 'skip');
    if (toProcess.length === 0) {
      toast.error('Aucun bloc selectionne (tous en "Ignorer")');
      return;
    }
    // Validate attach has invoice_id, create has full data
    for (const l of toProcess) {
      if (l.mode === 'attach' && !l.invoice_id) {
        toast.error(`Bloc ${l.block_id}: aucune facture selectionnee`);
        return;
      }
      if (l.mode === 'create') {
        const d = l.invoice_data || {};
        if (!d.number || !d.date || !d.supplier || !(Number(d.total_amount) > 0)) {
          toast.error(`Bloc ${l.block_id}: completer numero, date, fournisseur, montant`);
          return;
        }
      }
    }
    setStep('committing');
    try {
      const { data } = await api.post('/invoices/bundle-commit', {
        session_id: bundleResult.session_id,
        assignments: list,
      });
      setCommitResult(data);
      setStep('done');
      const msg = `${data.attached} attachee(s), ${data.created} creee(s), ${data.skipped} ignoree(s)`;
      if ((data.errors || []).length > 0) {
        toast.error(`${msg} - ${data.errors.length} erreur(s)`);
      } else {
        toast.success(msg);
      }
      if (onSuccess) onSuccess();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec import');
      setStep('review');
    }
  };

  const confColor = (conf) => {
    if (conf >= 0.85) return 'bg-green-50 text-green-700 border-green-300';
    if (conf >= 0.65) return 'bg-amber-50 text-amber-700 border-amber-300';
    return 'bg-orange-50 text-orange-700 border-orange-300';
  };

  const sortedInvoices = useMemo(() => {
    return [...(allInvoices || [])].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  }, [allInvoices]);

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="max-w-7xl w-[97vw] max-h-[94vh] overflow-y-auto"
        data-testid="bundle-import-dialog"
      >
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="flex items-center gap-2">
            <FolderInput size={20} className="text-[#0055FF]" />
            Regroupement de factures PDF
          </DialogTitle>
        </DialogHeader>

        {step === 'idle' && (
          <div className="space-y-4 py-6">
            <div className="border-2 border-dashed border-slate-300 rounded-lg p-8 text-center">
              <FolderInput size={40} className="mx-auto text-slate-400 mb-3" />
              <p className="text-sm text-slate-600 mb-4">
                Importez un PDF Regroupement de documents Optipro contenant plusieurs factures<br />
                concatenees (separees par des pages blanches).
              </p>
              <p className="text-xs text-slate-400 mb-4">
                Le systeme detecte les factures, extrait les metadonnees (fournisseur, date, montant)<br />
                et propose un matching avec les factures existantes de l ACP.
              </p>
              <input
                type="file"
                accept="application/pdf"
                className="hidden"
                id="bundle-pdf-input"
                onChange={handleFileChange}
              />
              <Button
                onClick={() => document.getElementById('bundle-pdf-input').click()}
                className="bg-[#0055FF] hover:bg-[#0040CC]"
                data-testid="bundle-select-file-btn"
              >
                <FileText size={16} className="mr-2" /> Selectionner un PDF
              </Button>
            </div>
          </div>
        )}

        {step === 'analyzing' && (
          <div className="py-12 text-center" data-testid="bundle-analyzing">
            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-[#0055FF] mb-4"></div>
            <p className="text-sm text-slate-600">Analyse du PDF en cours...</p>
            <p className="text-xs text-slate-400 mt-1">Cette operation peut prendre quelques secondes pour les fichiers volumineux.</p>
          </div>
        )}

        {step === 'review' && bundleResult && (
          <div className="space-y-3" data-testid="bundle-review">
            <div className="flex items-center justify-between text-sm bg-blue-50 border border-blue-200 rounded p-3">
              <div>
                <b>{bundleResult.invoice_count}</b> facture(s) detectee(s) sur <b>{bundleResult.total_pages}</b> page(s)
                <span className="text-slate-500 ml-2">({bundleResult.filename})</span>
              </div>
              <div className="text-xs text-slate-500">
                Verifiez chaque correspondance avant de confirmer.
              </div>
            </div>

            <div className="border rounded overflow-hidden">
              <table className="w-full text-xs" data-testid="bundle-blocks-table">
                <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-600">
                  <tr>
                    <th className="p-2 text-left w-14">Pages</th>
                    <th className="p-2 text-left">Donnees extraites</th>
                    <th className="p-2 text-left w-28">Action</th>
                    <th className="p-2 text-left">Cible</th>
                  </tr>
                </thead>
                <tbody>
                  {(bundleResult.blocks || []).map((b) => {
                    const a = assignments[b.block_id] || {};
                    const sm = b.suggested_match;
                    return (
                      <tr key={b.block_id} className="border-t border-slate-100 align-top" data-testid={`bundle-block-${b.block_id}`}>
                        <td className="p-2 font-mono text-[11px] text-slate-700">
                          p{b.page_range[0]}{b.page_range.length > 1 ? `-${b.page_range[b.page_range.length-1]}` : ''}
                          <div className="text-[10px] text-slate-400">{b.page_count} pg</div>
                        </td>
                        <td className="p-2">
                          <div className="text-[11px]">
                            <div><b>{b.supplier_hint || <i className="text-slate-400">Fournisseur ?</i>}</b></div>
                            <div className="text-slate-500">
                              {b.date_iso && <>Date : {fmtDate(b.date_iso)} - </>}
                              {b.invoice_number && <>N : <span className="font-mono">{b.invoice_number}</span> - </>}
                              <span className="font-mono text-slate-700">{Number(b.total_amount || 0).toFixed(2)} EUR</span>
                            </div>
                            {b.supplier_tva && <div className="text-[10px] text-slate-400 font-mono">{b.supplier_tva}</div>}
                            {sm && (
                              <div className="mt-1">
                                <Badge variant="outline" className={confColor(sm.confidence) + ' text-[10px]'} data-testid={`bundle-match-${b.block_id}`}>
                                  Match {Math.round(sm.confidence * 100)}% ({sm.method}) :
                                  <span className="ml-1 font-medium">{sm.supplier}</span>
                                  {sm.has_attachment && <span className="ml-1 text-red-600">[deja PJ]</span>}
                                </Badge>
                              </div>
                            )}
                            {!sm && (
                              <div className="mt-1">
                                <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200 text-[10px]">
                                  <AlertCircle size={10} className="inline mr-1" /> Aucune correspondance
                                </Badge>
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="p-2 w-28">
                          <Select
                            value={a.mode || 'skip'}
                            onValueChange={v => {
                              if (v === 'create') startCreateMode(b);
                              else updateAssignment(b.block_id, { mode: v });
                            }}
                          >
                            <SelectTrigger className="h-7 text-[11px]" data-testid={`bundle-mode-${b.block_id}`}>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="attach">Attacher</SelectItem>
                              <SelectItem value="create">Creer facture</SelectItem>
                              <SelectItem value="skip">Ignorer</SelectItem>
                            </SelectContent>
                          </Select>
                        </td>
                        <td className="p-2">
                          {a.mode === 'attach' && (
                            <div className="space-y-1">
                              {a.invoice_id && (() => {
                                const cur = sortedInvoices.find(inv => inv.id === a.invoice_id);
                                return cur ? (
                                  <div className="text-[11px] text-emerald-700 font-medium">
                                    {fmtDate(cur.date)} - {cur.number} - {cur.supplier}
                                    {' '}<span className="text-slate-400">({Number(cur.total_amount || 0).toFixed(2)}E)</span>
                                  </div>
                                ) : null;
                              })()}
                              <Select
                                value={a.invoice_id || ''}
                                onValueChange={v => updateAssignment(b.block_id, { invoice_id: v })}
                              >
                                <SelectTrigger className="h-7 text-[11px]" data-testid={`bundle-invoice-select-${b.block_id}`}>
                                  <SelectValue placeholder="-- Changer de facture --" />
                                </SelectTrigger>
                                <SelectContent>
                                  {sortedInvoices.length === 0 && (
                                    <div className="p-2 text-xs text-slate-400">Aucune facture en base</div>
                                  )}
                                  {sortedInvoices.map(inv => (
                                    <SelectItem key={inv.id} value={inv.id}>
                                      {fmtDate(inv.date)} - {inv.number} - {inv.supplier} ({Number(inv.total_amount || 0).toFixed(2)}E){(inv.attachments?.length || 0) > 0 ? ' [PJ]' : ''}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          )}
                          {a.mode === 'create' && (
                            <CreateInvoiceMini
                              data={a.invoice_data || {}}
                              accounts={accounts}
                              distKeys={distKeys}
                              categories={categories}
                              expanded={!!a.expanded}
                              onToggle={() => updateAssignment(b.block_id, { expanded: !a.expanded })}
                              onChange={patch => updateAssignment(b.block_id, { invoice_data: { ...a.invoice_data, ...patch } })}
                              testId={`bundle-create-${b.block_id}`}
                            />
                          )}
                          {a.mode === 'skip' && (
                            <span className="text-[11px] text-slate-400 italic">Aucune action</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between pt-2 border-t">
              <div className="text-xs text-slate-500">
                {Object.values(assignments).filter(a => a.mode === 'attach').length} attachement(s),
                {' '}{Object.values(assignments).filter(a => a.mode === 'create').length} creation(s),
                {' '}{Object.values(assignments).filter(a => a.mode === 'skip').length} ignoree(s)
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => handleClose(false)} data-testid="bundle-cancel-btn">Annuler</Button>
                <Button
                  onClick={commit}
                  className="bg-[#0055FF] hover:bg-[#0040CC]"
                  data-testid="bundle-commit-btn"
                >
                  <Check size={16} className="mr-2" /> Confirmer l import
                </Button>
              </div>
            </div>
          </div>
        )}

        {step === 'committing' && (
          <div className="py-12 text-center" data-testid="bundle-committing">
            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-[#0055FF] mb-4"></div>
            <p className="text-sm text-slate-600">Decoupage et attachement en cours...</p>
          </div>
        )}

        {step === 'done' && commitResult && (
          <div className="space-y-3 py-4" data-testid="bundle-done">
            <div className="bg-green-50 border border-green-200 rounded p-4">
              <div className="font-semibold text-green-800 mb-2 flex items-center gap-2">
                <Check size={16} /> Import termine
              </div>
              <ul className="text-sm text-green-700 space-y-0.5">
                <li>{commitResult.attached} piece(s) jointe(s) attachees a des factures existantes</li>
                <li>{commitResult.created} facture(s) creee(s) avec PJ</li>
                <li>{commitResult.skipped} bloc(s) ignore(s)</li>
              </ul>
            </div>
            {(commitResult.errors || []).length > 0 && (
              <div className="bg-red-50 border border-red-200 rounded p-3">
                <div className="font-semibold text-red-800 mb-1 text-sm">
                  <X size={14} className="inline mr-1" /> {commitResult.errors.length} erreur(s)
                </div>
                <ul className="text-xs text-red-700 space-y-0.5 max-h-40 overflow-y-auto">
                  {commitResult.errors.map((e, i) => (
                    <li key={i}><span className="font-mono">{e.block_id}</span> : {e.error}</li>
                  ))}
                </ul>
              </div>
            )}
            <div className="flex justify-end">
              <Button onClick={() => handleClose(false)} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="bundle-close-btn">
                Fermer
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}


function CreateInvoiceMini({ data, accounts, distKeys, categories, expanded, onToggle, onChange, testId }) {
  return (
    <div className="border border-blue-200 bg-blue-50/40 rounded p-2 space-y-2">
      <div className="grid grid-cols-2 gap-1">
        <Input
          placeholder="N facture *"
          value={data.number || ''}
          onChange={e => onChange({ number: e.target.value })}
          className="h-7 text-[11px]"
          data-testid={`${testId}-number`}
        />
        <Input
          type="date"
          value={data.date || ''}
          onChange={e => onChange({ date: e.target.value })}
          className="h-7 text-[11px]"
          data-testid={`${testId}-date`}
        />
      </div>
      <Input
        placeholder="Fournisseur *"
        value={data.supplier || ''}
        onChange={e => onChange({ supplier: e.target.value })}
        className="h-7 text-[11px]"
        data-testid={`${testId}-supplier`}
      />
      <div className="grid grid-cols-2 gap-1">
        <Input
          type="number"
          step="0.01"
          placeholder="Montant TTC *"
          value={data.total_amount || ''}
          onChange={e => onChange({ total_amount: parseFloat(e.target.value) || 0 })}
          className="h-7 text-[11px]"
          data-testid={`${testId}-amount`}
        />
        <Input
          type="number"
          step="0.01"
          placeholder="TVA"
          value={data.vat_amount || ''}
          onChange={e => onChange({ vat_amount: parseFloat(e.target.value) || 0 })}
          className="h-7 text-[11px]"
        />
      </div>
      <button
        type="button"
        onClick={onToggle}
        className="text-[10px] text-blue-700 hover:underline flex items-center gap-0.5"
      >
        {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        {expanded ? 'Moins d\'options' : 'Plus d\'options...'}
      </button>
      {expanded && (
        <div className="space-y-1 pt-1 border-t border-blue-200">
          <Input
            placeholder="Description"
            value={data.description || ''}
            onChange={e => onChange({ description: e.target.value })}
            className="h-7 text-[11px]"
          />
          <Select
            value={data.expense_category_id || 'none'}
            onValueChange={v => {
              if (v === 'none') { onChange({ expense_category_id: '' }); return; }
              const cat = (categories || []).find(c => c.id === v);
              onChange({
                expense_category_id: v,
                account_number: cat?.account_number || data.account_number || '',
              });
            }}
          >
            <SelectTrigger className="h-7 text-[11px]" data-testid={`${testId}-category`}>
              <SelectValue placeholder="Nature de depense" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">— Aucune —</SelectItem>
              {(categories || []).map(c => (
                <SelectItem key={c.id} value={c.id}>{c.name} ({c.account_number})</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <AccountSearchSelect
            accounts={accounts}
            value={data.account_number || ''}
            onChange={v => onChange({ account_number: v })}
            placeholder="Compte PCMN..."
            classFilter={6}
            allowClear
            testId={`${testId}-account`}
          />
          <Select
            value={data.distribution_key_id || 'none'}
            onValueChange={v => onChange({ distribution_key_id: v === 'none' ? '' : v })}
          >
            <SelectTrigger className="h-7 text-[11px]" data-testid={`${testId}-key`}>
              <SelectValue placeholder="Cle de repartition" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">— Aucune —</SelectItem>
              {(distKeys || []).map(k => (
                <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
