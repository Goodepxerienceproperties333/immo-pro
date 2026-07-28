import { useState, useMemo, useEffect, useRef } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import {
  FileText, Check, X, AlertCircle, FolderInput,
  ArrowLeft, Search, Building2, ExternalLink, Loader2,
} from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import { fmtDate } from '@/lib/dateFmt';

import { fmtEUR } from '@/lib/format';
/**
 * Dialog d'import d'un PDF "Regroupement de documents" Optipro.
 *
 * Flux :
 * 1. idle        -> upload PDF
 * 2. analyzing   -> spinner
 * 3. review      -> tableau des blocs detectes (attach / creer / ignorer)
 *    3b. createFull -> formulaire complet + apercu PDF du bloc
 * 4. committing  -> spinner
 * 5. done        -> resume
 */
export default function BundleImportDialog({
  open, onOpenChange,
  invoices: invoicesProp, distKeys, categories, accounts,
  suppliers = [],
  usedSupplierNames = [],
  onSuccess,
}) {
  const [step, setStep] = useState('idle');
  const [bundleResult, setBundleResult] = useState(null);
  const [assignments, setAssignments] = useState({});
  const [commitResult, setCommitResult] = useState(null);
  const [allInvoices, setAllInvoices] = useState([]);

  // Full creation view state
  const [activeBlock, setActiveBlock] = useState(null);
  const [blockPdfUrl, setBlockPdfUrl] = useState(null);
  const [blockForm, setBlockForm] = useState({});
  const [blockCreating, setBlockCreating] = useState(false);
  const [createdBlocks, setCreatedBlocks] = useState({});

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get('/invoices', { params: { all: true } });
        if (!cancelled) setAllInvoices(data || []);
      } catch {
        if (!cancelled) setAllInvoices(invoicesProp || []);
      }
    })();
    return () => { cancelled = true; };
  }, [open, invoicesProp]);

  // Load PDF preview when activeBlock is set
  useEffect(() => {
    if (!activeBlock || !bundleResult) { setBlockPdfUrl(null); return; }
    let blobUrl = null;
    let cancelled = false;
    (async () => {
      try {
        const pages = activeBlock.page_range.join(',');
        const { data } = await api.get('/invoices/bundle-preview-block', {
          params: { session_id: bundleResult.session_id, pages },
          responseType: 'blob',
        });
        if (cancelled) return;
        blobUrl = URL.createObjectURL(data);
        setBlockPdfUrl(blobUrl);
      } catch { /* silent */ }
    })();
    return () => { cancelled = true; if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [activeBlock, bundleResult]);

  const reset = () => {
    setStep('idle'); setBundleResult(null); setAssignments({});
    setCommitResult(null); setActiveBlock(null); setBlockPdfUrl(null);
    setBlockForm({}); setCreatedBlocks({});
  };

  const handleClose = (next) => { if (!next) reset(); onOpenChange(next); };

  const handleFileChange = async (e) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f) return;
    setStep('analyzing');
    try {
      const fd = new FormData();
      fd.append('file', f);
      const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
      if (copro) fd.append('copropriete_id', copro);
      const { data } = await api.post('/invoices/bundle-analyze', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setBundleResult(data);
      const init = {};
      (data.blocks || []).forEach(b => {
        const sm = b.suggested_match;
        if (sm && sm.confidence >= 0.65 && !sm.has_attachment) {
          init[b.block_id] = { mode: 'attach', invoice_id: sm.invoice_id };
        } else if (sm && sm.confidence >= 0.65) {
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

  // -- Full creation form --
  const openBlockCreation = (block) => {
    setBlockForm({
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
    });
    setActiveBlock(block);
  };

  const submitBlockCreation = async () => {
    if (!activeBlock || !bundleResult) return;
    const f = blockForm;
    if (!f.number?.trim()) { toast.error('Numero de facture requis'); return; }
    if (!f.date) { toast.error('Date requise'); return; }
    if (!f.supplier?.trim()) { toast.error('Fournisseur requis'); return; }
    if (!(Number(f.total_amount) > 0)) { toast.error('Montant TTC requis'); return; }
    if (!f.description?.trim()) { toast.error('Description obligatoire'); return; }

    setBlockCreating(true);
    try {
      const { data } = await api.post('/invoices/bundle-commit', {
        session_id: bundleResult.session_id,
        cleanup: false,
        assignments: [{
          block_id: activeBlock.block_id,
          page_range: activeBlock.page_range,
          mode: 'create',
          invoice_data: {
            number: f.number.trim(),
            date: f.date,
            due_date: f.due_date || '',
            supplier: f.supplier.trim(),
            supplier_confirmed: true,
            description: f.description.trim(),
            total_amount: Number(f.total_amount) || 0,
            vat_amount: Number(f.vat_amount) || 0,
            account_number: f.account_number || '',
            expense_category_id: f.expense_category_id || '',
            distribution_key_id: f.distribution_key_id || '',
            status: f.status || 'unpaid',
            copropriete_id: localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '',
          },
        }],
      });
      if ((data.errors || []).length > 0) {
        toast.error(data.errors[0]?.error || 'Erreur lors de la creation');
      } else {
        toast.success('Facture creee et PDF attache');
        setCreatedBlocks(prev => ({ ...prev, [activeBlock.block_id]: data.results?.[0]?.invoice_id || true }));
        updateAssignment(activeBlock.block_id, { mode: 'done' });
        setActiveBlock(null);
        if (onSuccess) onSuccess();
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec creation');
    } finally {
      setBlockCreating(false);
    }
  };

  // -- Batch commit (for attach + skip) --
  const commit = async () => {
    if (!bundleResult) return;
    const list = (bundleResult.blocks || []).map(b => {
      const a = assignments[b.block_id] || { mode: 'skip' };
      if (a.mode === 'done') return { block_id: b.block_id, page_range: b.page_range, mode: 'skip' };
      const base = { block_id: b.block_id, page_range: b.page_range, mode: a.mode };
      if (a.mode === 'attach') base.invoice_id = a.invoice_id;
      return base;
    });
    const toProcess = list.filter(l => l.mode === 'attach');
    if (toProcess.length === 0) {
      toast.info('Aucun attachement restant a traiter');
      handleClose(false);
      return;
    }
    for (const l of toProcess) {
      if (!l.invoice_id) { toast.error(`Bloc ${l.block_id}: aucune facture selectionnee`); return; }
    }
    setStep('committing');
    try {
      const { data } = await api.post('/invoices/bundle-commit', {
        session_id: bundleResult.session_id,
        cleanup: true,
        assignments: list,
      });
      setCommitResult({
        ...data,
        created: (data.created || 0) + Object.keys(createdBlocks).length,
      });
      setStep('done');
      const msg = `${data.attached} attachee(s), ${Object.keys(createdBlocks).length + (data.created || 0)} creee(s), ${data.skipped} ignoree(s)`;
      if ((data.errors || []).length > 0) toast.error(`${msg} - ${data.errors.length} erreur(s)`);
      else toast.success(msg);
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

  const sortedInvoices = useMemo(() =>
    [...(allInvoices || [])].sort((a, b) => (b.date || '').localeCompare(a.date || '')),
  [allInvoices]);

  // Stats for footer
  const stats = useMemo(() => {
    const vals = Object.values(assignments);
    return {
      attach: vals.filter(a => a.mode === 'attach').length,
      create: vals.filter(a => a.mode === 'create').length,
      done: vals.filter(a => a.mode === 'done').length,
      skip: vals.filter(a => a.mode === 'skip').length,
    };
  }, [assignments]);

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent
        className="max-w-7xl w-[97vw] max-h-[94vh] flex flex-col overflow-hidden"
        data-testid="bundle-import-dialog"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Chivo,sans-serif' }} className="flex items-center gap-2">
            <FolderInput size={20} className="text-[#022D52]" />
            Regroupement de factures PDF
          </DialogTitle>
        </DialogHeader>

        {/* IDLE */}
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
              <input type="file" accept="application/pdf" className="hidden" id="bundle-pdf-input" onChange={handleFileChange} />
              <Button onClick={() => document.getElementById('bundle-pdf-input').click()} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="bundle-select-file-btn">
                <FileText size={16} className="mr-2" /> Selectionner un PDF
              </Button>
            </div>
          </div>
        )}

        {/* ANALYZING */}
        {step === 'analyzing' && (
          <div className="py-12 text-center" data-testid="bundle-analyzing">
            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-[#022D52] mb-4" />
            <p className="text-sm text-slate-600">Analyse du PDF en cours...</p>
            <p className="text-xs text-slate-400 mt-1">Cette operation peut prendre quelques secondes pour les fichiers volumineux.</p>
          </div>
        )}

        {/* REVIEW TABLE (when no activeBlock) */}
        {step === 'review' && bundleResult && !activeBlock && (
          <div className="space-y-3 flex flex-col" style={{ maxHeight: 'calc(94vh - 5rem)' }} data-testid="bundle-review">
            <div className="flex items-center justify-between text-sm bg-blue-50 border border-blue-200 rounded p-3 shrink-0">
              <div>
                <b>{bundleResult.invoice_count}</b> facture(s) detectee(s) sur <b>{bundleResult.total_pages}</b> page(s)
                <span className="text-slate-500 ml-2">({bundleResult.filename})</span>
              </div>
              <div className="text-xs text-slate-500">Verifiez chaque correspondance avant de confirmer.</div>
            </div>

            <div className="border rounded overflow-auto flex-1 min-h-0">
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
                    const isDone = a.mode === 'done' || !!createdBlocks[b.block_id];
                    return (
                      <tr key={b.block_id} className="border-t border-slate-100 align-top" data-testid={`bundle-block-${b.block_id}`}>
                        <td className="p-2 font-mono text-[11px] text-slate-700">
                          p{b.page_range[0]}{b.page_range.length > 1 ? `-${b.page_range[b.page_range.length - 1]}` : ''}
                          <div className="text-[10px] text-slate-400">{b.page_count} pg</div>
                        </td>
                        <td className="p-2">
                          <div className="text-[11px]">
                            <div><b>{b.supplier_hint || <i className="text-slate-400">Fournisseur ?</i>}</b></div>
                            <div className="text-slate-500">
                              {b.date_iso && <>Date : {fmtDate(b.date_iso)} - </>}
                              {b.invoice_number && <>N : <span className="font-mono">{b.invoice_number}</span> - </>}
                              <span className="font-mono text-slate-700">{fmtEUR(Number(b.total_amount || 0))} EUR</span>
                            </div>
                            {b.supplier_tva && <div className="text-[10px] text-slate-400 font-mono">{b.supplier_tva}</div>}
                            {sm && !isDone && (
                              <div className="mt-1">
                                <Badge variant="outline" className={confColor(sm.confidence) + ' text-[10px]'}>
                                  Match {Math.round(sm.confidence * 100)}% ({sm.method}) :
                                  <span className="ml-1 font-medium">{sm.supplier}</span>
                                  {sm.has_attachment && <span className="ml-1 text-red-600">[deja PJ]</span>}
                                </Badge>
                              </div>
                            )}
                            {!sm && !isDone && (
                              <div className="mt-1">
                                <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200 text-[10px]">
                                  <AlertCircle size={10} className="inline mr-1" /> Aucune correspondance
                                </Badge>
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="p-2 w-28">
                          {isDone ? (
                            <Badge className="bg-emerald-100 text-emerald-800 border-emerald-300 text-[10px]">
                              <Check size={10} className="mr-1" /> Creee
                            </Badge>
                          ) : (
                            <Select
                              value={a.mode || 'skip'}
                              onValueChange={v => updateAssignment(b.block_id, { mode: v })}
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
                          )}
                        </td>
                        <td className="p-2">
                          {isDone && (
                            <span className="text-[11px] text-emerald-700 font-medium">Facture creee avec PJ</span>
                          )}
                          {!isDone && a.mode === 'attach' && (
                            <div className="space-y-1">
                              {a.invoice_id && (() => {
                                const cur = sortedInvoices.find(inv => inv.id === a.invoice_id);
                                return cur ? (
                                  <div className="text-[11px] text-emerald-700 font-medium">
                                    {fmtDate(cur.date)} - {cur.number} - {cur.supplier}
                                    {' '}<span className="text-slate-400">({fmtEUR(Number(cur.total_amount || 0))}E)</span>
                                  </div>
                                ) : null;
                              })()}
                              <Select value={a.invoice_id || ''} onValueChange={v => updateAssignment(b.block_id, { invoice_id: v })}>
                                <SelectTrigger className="h-7 text-[11px]" data-testid={`bundle-invoice-select-${b.block_id}`}>
                                  <SelectValue placeholder="-- Changer de facture --" />
                                </SelectTrigger>
                                <SelectContent>
                                  {sortedInvoices.length === 0 && <div className="p-2 text-xs text-slate-400">Aucune facture en base</div>}
                                  {sortedInvoices.map(inv => (
                                    <SelectItem key={inv.id} value={inv.id}>
                                      {fmtDate(inv.date)} - {inv.number} - {inv.supplier} ({fmtEUR(Number(inv.total_amount || 0))}E){(inv.attachments?.length || 0) > 0 ? ' [PJ]' : ''}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </div>
                          )}
                          {!isDone && a.mode === 'create' && (
                            <Button
                              size="sm"
                              className="h-7 text-[11px] bg-[#022D52] hover:bg-[#1D4ED8]"
                              onClick={() => openBlockCreation(b)}
                              data-testid={`bundle-open-create-${b.block_id}`}
                            >
                              <ExternalLink size={12} className="mr-1.5" /> Ouvrir le formulaire
                            </Button>
                          )}
                          {!isDone && a.mode === 'skip' && (
                            <span className="text-[11px] text-slate-400 italic">Aucune action</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between pt-3 border-t shrink-0 sticky bottom-0 bg-white -mx-6 px-6 pb-1">
              <div className="text-xs text-slate-500">
                <span className="font-semibold text-[#01213e]">{stats.attach}</span> attachement(s),{' '}
                <span className="font-semibold text-emerald-700">{stats.done}</span> creee(s),{' '}
                <span className="font-semibold text-slate-500">{stats.skip + stats.create}</span> ignoree(s)
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => handleClose(false)} data-testid="bundle-cancel-btn">Annuler</Button>
                <Button onClick={commit} className="bg-[#022D52] hover:bg-[#1D4ED8] font-semibold shadow-md" data-testid="bundle-commit-btn">
                  <Check size={16} className="mr-2" /> Confirmer l import
                </Button>
              </div>
            </div>
          </div>
        )}

        {/* FULL CREATION VIEW (form left + PDF right) */}
        {step === 'review' && activeBlock && (
          <div className="flex flex-col" style={{ height: 'calc(94vh - 5rem)' }} data-testid="bundle-create-full">
            {/* Header */}
            <div className="flex items-center justify-between pb-3 border-b border-slate-200 shrink-0">
              <button
                type="button"
                onClick={() => { setActiveBlock(null); setBlockPdfUrl(null); }}
                className="flex items-center gap-1.5 text-sm text-slate-600 hover:text-[#022D52] transition"
                data-testid="bundle-create-back-btn"
              >
                <ArrowLeft size={16} /> Retour a la liste
              </button>
              <div className="text-sm text-slate-700 font-medium">
                Bloc p{activeBlock.page_range[0]}
                {activeBlock.page_range.length > 1 ? `-${activeBlock.page_range[activeBlock.page_range.length - 1]}` : ''}
                {' '}({activeBlock.page_count} page{activeBlock.page_count > 1 ? 's' : ''})
                {activeBlock.supplier_hint && <span className="ml-2 text-slate-500">- {activeBlock.supplier_hint}</span>}
              </div>
            </div>

            {/* Content: Form left + PDF right */}
            <div className="flex gap-0 flex-1 min-h-0 pt-3">
              {/* LEFT: Form */}
              <div className="w-[55%] shrink-0 pr-4 overflow-y-auto" data-testid="bundle-create-form-panel">
                <BlockCreationForm
                  form={blockForm}
                  onChange={patch => setBlockForm(f => ({ ...f, ...patch }))}
                  suppliers={suppliers}
                  usedSupplierNames={usedSupplierNames}
                  accounts={accounts}
                  categories={categories}
                  distKeys={distKeys}
                  creating={blockCreating}
                  onSubmit={submitBlockCreation}
                />
              </div>

              {/* RIGHT: PDF preview */}
              <div className="flex-1 min-w-0 flex flex-col border-l border-slate-200 pl-4">
                <div className="text-xs font-semibold text-slate-700 flex items-center gap-1.5 mb-2 shrink-0">
                  <FileText size={13} className="text-purple-600" />
                  Apercu du document
                </div>
                {blockPdfUrl ? (
                  <iframe
                    src={blockPdfUrl}
                    title="Apercu facture"
                    className="flex-1 w-full min-h-0 rounded border border-slate-200 bg-white"
                    data-testid="bundle-create-pdf-iframe"
                  />
                ) : (
                  <div className="flex-1 flex items-center justify-center bg-slate-50 rounded border border-slate-200">
                    <div className="text-center text-slate-400">
                      <Loader2 size={24} className="mx-auto mb-2 animate-spin" />
                      <p className="text-xs">Chargement de l apercu...</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* COMMITTING */}
        {step === 'committing' && (
          <div className="py-12 text-center" data-testid="bundle-committing">
            <div className="inline-block animate-spin rounded-full h-10 w-10 border-b-2 border-[#022D52] mb-4" />
            <p className="text-sm text-slate-600">Decoupage et attachement en cours...</p>
          </div>
        )}

        {/* DONE */}
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
              <Button onClick={() => handleClose(false)} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="bundle-close-btn">
                Fermer
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}


/* ──────────────────────── Full creation form ──────────────────────── */

function BlockCreationForm({ form, onChange, suppliers, usedSupplierNames, accounts, categories, distKeys, creating, onSubmit }) {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-800">Nouvelle facture</h3>

      {/* Row: Number + Date */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[11px] font-medium text-slate-600 mb-1 block">N de facture *</label>
          <Input
            value={form.number || ''}
            onChange={e => onChange({ number: e.target.value })}
            className="h-9 text-sm"
            data-testid="bundle-full-number"
          />
        </div>
        <div>
          <label className="text-[11px] font-medium text-slate-600 mb-1 block">Date *</label>
          <Input
            type="date"
            value={form.date || ''}
            onChange={e => onChange({ date: e.target.value })}
            className="h-9 text-sm"
            data-testid="bundle-full-date"
          />
        </div>
      </div>

      {/* Supplier picker */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Fournisseur *</label>
        <SupplierPicker
          suppliers={suppliers}
          usedNames={usedSupplierNames}
          value={form.supplier || ''}
          onChange={name => onChange({ supplier: name })}
          testId="bundle-full-supplier"
        />
      </div>

      {/* Description */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Description *</label>
        <Input
          value={form.description || ''}
          onChange={e => onChange({ description: e.target.value })}
          placeholder="Objet de la facture (obligatoire)"
          className="h-9 text-sm"
          data-testid="bundle-full-description"
        />
      </div>

      {/* Row: Amount + VAT */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-[11px] font-medium text-slate-600 mb-1 block">Montant TTC *</label>
          <Input
            type="number" step="0.01"
            value={form.total_amount || ''}
            onChange={e => onChange({ total_amount: parseFloat(e.target.value) || 0 })}
            className="h-9 text-sm"
            data-testid="bundle-full-amount"
          />
        </div>
        <div>
          <label className="text-[11px] font-medium text-slate-600 mb-1 block">TVA</label>
          <Input
            type="number" step="0.01"
            value={form.vat_amount || ''}
            onChange={e => onChange({ vat_amount: parseFloat(e.target.value) || 0 })}
            className="h-9 text-sm"
            data-testid="bundle-full-vat"
          />
        </div>
      </div>

      {/* Due date */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Date echeance</label>
        <Input
          type="date"
          value={form.due_date || ''}
          onChange={e => onChange({ due_date: e.target.value })}
          className="h-9 text-sm"
          data-testid="bundle-full-due-date"
        />
      </div>

      {/* Expense category */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Nature de depense</label>
        <Select
          value={form.expense_category_id || 'none'}
          onValueChange={v => {
            if (v === 'none') { onChange({ expense_category_id: '' }); return; }
            const cat = (categories || []).find(c => c.id === v);
            onChange({
              expense_category_id: v,
              account_number: cat?.account_number || form.account_number || '',
            });
          }}
        >
          <SelectTrigger className="h-9 text-sm" data-testid="bundle-full-category">
            <SelectValue placeholder="-- Aucune --" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">-- Aucune --</SelectItem>
            {(categories || []).map(c => (
              <SelectItem key={c.id} value={c.id}>{c.name} ({c.account_number})</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* PCMN account */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Compte PCMN</label>
        <AccountSearchSelect
          accounts={accounts}
          value={form.account_number || ''}
          onChange={v => onChange({ account_number: v })}
          placeholder="Compte PCMN..."
          classFilter={6}
          allowClear
          testId="bundle-full-account"
        />
      </div>

      {/* Distribution key */}
      <div>
        <label className="text-[11px] font-medium text-slate-600 mb-1 block">Cle de repartition</label>
        <Select
          value={form.distribution_key_id || 'none'}
          onValueChange={v => onChange({ distribution_key_id: v === 'none' ? '' : v })}
        >
          <SelectTrigger className="h-9 text-sm" data-testid="bundle-full-distkey">
            <SelectValue placeholder="-- Aucune --" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">-- Aucune --</SelectItem>
            {(distKeys || []).map(k => (
              <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Submit */}
      <div className="pt-3 border-t border-slate-200 flex justify-end gap-2">
        <Button
          onClick={onSubmit}
          disabled={creating}
          className="bg-[#022D52] hover:bg-[#1D4ED8] font-semibold shadow-md"
          data-testid="bundle-full-submit"
        >
          {creating ? (
            <><Loader2 size={14} className="mr-2 animate-spin" /> Creation...</>
          ) : (
            <><Check size={14} className="mr-2" /> Creer la facture</>
          )}
        </Button>
      </div>
    </div>
  );
}


/* ──────────────────────── Supplier picker ──────────────────────── */

function SupplierPicker({ suppliers = [], usedNames = [], value, onChange, testId }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Build deduplicated supplier list
  const allSuppliers = useMemo(() => {
    const map = new Map();
    (suppliers || []).forEach(s => {
      const key = (s.name || '').trim().toLowerCase();
      if (key && !map.has(key)) map.set(key, s);
    });
    (usedNames || []).forEach(n => {
      const key = (n || '').trim().toLowerCase();
      if (key && !map.has(key)) map.set(key, { id: `free-${key}`, name: n.trim() });
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [suppliers, usedNames]);

  const q = search.trim().toLowerCase();
  const filtered = allSuppliers
    .filter(s => !q || s.name.toLowerCase().includes(q) || (s.bce_number || '').includes(q))
    .slice(0, 20);

  const handleSelect = (s) => {
    onChange(s.name);
    setSearch('');
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <div
        className="flex items-center gap-2 border border-slate-200 rounded-md bg-white hover:border-slate-300 transition cursor-text"
        onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 0); }}
        data-testid={testId}
      >
        <Search size={14} className="text-slate-400 ml-3 shrink-0" />
        <input
          ref={inputRef}
          className="flex-1 h-9 text-sm outline-none bg-transparent pr-3"
          value={open ? search : (value || '')}
          onChange={e => { setSearch(e.target.value); onChange(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          placeholder="Rechercher ou saisir un fournisseur..."
        />
        {value && (
          <button type="button" onClick={(e) => { e.stopPropagation(); onChange(''); setSearch(''); }} className="text-slate-400 hover:text-red-500 mr-2 shrink-0">
            <X size={14} />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-52 overflow-auto">
          {filtered.length === 0 ? (
            <div className="px-3 py-3 text-xs text-slate-400 text-center">
              {q ? 'Aucun fournisseur correspondant' : 'Aucun fournisseur enregistre'}
            </div>
          ) : filtered.map(s => (
            <button
              type="button"
              key={s.id}
              onClick={() => handleSelect(s)}
              className={`w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-blue-50 ${
                (s.name || '').toLowerCase() === (value || '').toLowerCase() ? 'bg-blue-50 font-medium' : ''
              }`}
              data-testid={`${testId}-opt-${s.id}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-slate-800 truncate">{s.name}</span>
                {s.bce_number && <span className="text-slate-400 text-[10px] font-mono shrink-0">{s.bce_number}</span>}
              </div>
            </button>
          ))}
          {allSuppliers.length > 20 && filtered.length === 20 && (
            <div className="px-3 py-1.5 text-[10px] text-slate-400 text-center bg-slate-50">Affinez votre recherche...</div>
          )}
        </div>
      )}
    </div>
  );
}
