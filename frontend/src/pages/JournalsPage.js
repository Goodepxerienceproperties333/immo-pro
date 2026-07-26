import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Eye, Paperclip, Download, Pencil, Unlink, ShieldAlert, X } from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import UnlettrageDialog from '@/components/UnlettrageDialog';
import { fmtDate } from '@/lib/dateFmt';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';
import { useAuth } from '@/contexts/AuthContext';
import { useDirtyGuard } from '@/hooks/useDirtyGuard';

const API = process.env.REACT_APP_BACKEND_URL;

const JOURNAL_TYPES = [
  { value: 'OD', label: 'Operations Diverses', desc: 'Ecritures manuelles' },
  { value: 'AC', label: 'Achats', desc: 'Factures fournisseurs' },
  { value: 'VE', label: 'Ventes', desc: 'Appels de fonds proprietaires' },
  { value: 'FI', label: 'Financier', desc: 'Mouvements bancaires' },
  { value: 'AN', label: 'A-Nouveau', desc: 'Ouverture exercice' },
];

export default function JournalsPage() {
  const { user } = useAuth();
  // iter90en : suppression bulk d'ecritures (admin/superadmin only)
  const isSuperadmin = user && (user.role === 'superadmin' || user.role === 'admin');
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkDeleteReason, setBulkDeleteReason] = useState('');
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [entries, setEntries] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [journalType, setJournalType] = useState('OD');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [viewEntry, setViewEntry] = useState(null);
  const [attachDialogEntry, setAttachDialogEntry] = useState(null);
  const [form, setForm] = useState({ journal_type: 'OD', date: '', reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
  const [pendingAttachment, setPendingAttachment] = useState(null);
  // iter90bx : par defaut afficher les contre-passations (audit trail legal PCMN)
  const [includeReversals, setIncludeReversals] = useState(true);
  // iter90bt : filtres de recherche journal (periode, montant, tiers)
  const [filters, setFilters] = useState({
    date_from: '', date_to: '', search: '',
    amount_min: '', amount_max: '', third_party_name: '',
  });
  const fyParams = useFiscalYearParams();
  // iter90et : dirty guard sur le dialog de creation/edition d'ecriture
  const entryDirty = useDirtyGuard(form, dialogOpen);

  const load = useCallback(async () => {
    // Compose les params : filtres locaux + defaut fiscal + journal type
    const params = { journal_type: journalType, include_reversals: includeReversals, ...fyParams };
    // Priorite aux filtres locaux (date/montant/tiers) s'ils sont remplis
    if (filters.date_from) params.date_from = filters.date_from;
    if (filters.date_to) params.date_to = filters.date_to;
    if (filters.search.trim()) params.search = filters.search.trim();
    if (filters.amount_min) params.amount_min = filters.amount_min;
    if (filters.amount_max) params.amount_max = filters.amount_max;
    if (filters.third_party_name.trim()) params.third_party_name = filters.third_party_name.trim();
    const [e, a, c] = await Promise.all([
      api.get('/accounting/entries', { params }),
      api.get('/accounting/pcmn'),
      api.get('/expense-categories').catch(() => ({ data: [] })),
    ]);
    setEntries(e.data);
    setAccounts(a.data);
    setCategories(c.data);
  }, [journalType, includeReversals, fyParams.date_from, fyParams.date_to,
      filters.date_from, filters.date_to, filters.search,
      filters.amount_min, filters.amount_max, filters.third_party_name]);

  useEffect(() => { load(); }, [load]);

  const resetFilters = () => setFilters({
    date_from: '', date_to: '', search: '',
    amount_min: '', amount_max: '', third_party_name: '',
  });
  const hasActiveFilters = Object.values(filters).some(v => v && String(v).trim());

  // iter90fr : export CSV / PDF des ecritures (respecte les filtres periode + journal_type)
  const [exporting, setExporting] = useState(null); // 'csv' | 'pdf' | null
  const downloadJournalsExport = async (format) => {
    setExporting(format);
    try {
      const params = { journal_type: journalType, include_reversals: includeReversals };
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      const path = format === 'csv' ? '/exports/journals.csv' : '/exports/journals.pdf';
      const r = await api.get(path, { params, responseType: 'blob' });
      const mime = format === 'csv' ? 'text/csv' : 'application/pdf';
      const url = URL.createObjectURL(new Blob([r.data], { type: mime }));
      const a = document.createElement('a');
      a.href = url;
      const stamp = new Date().toISOString().slice(0, 10);
      const range = [filters.date_from, filters.date_to].filter(Boolean).join('_') || 'toutes';
      a.download = `journaux_${journalType.toLowerCase()}_${range}_${stamp}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(`Export ${format.toUpperCase()} genere`);
    } catch (err) {
      toast.error(err.response?.data?.detail || `Erreur export ${format.toUpperCase()}`);
    } finally {
      setExporting(null);
    }
  };

  const [editingEntry, setEditingEntry] = useState(null);

  const openCreate = () => {
    setEditingEntry(null);
    setForm({ journal_type: journalType, date: new Date().toISOString().split('T')[0], reference: '', description: '', lines: [{ account_number: '', account_name: '', debit: 0, credit: 0 }, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
    setPendingAttachment(null);
    setDialogOpen(true);
  };

  const openEdit = (entry) => {
    setEditingEntry(entry);
    setForm({
      journal_type: entry.journal_type, date: entry.date, reference: entry.reference || '',
      description: entry.description || '',
      lines: (entry.lines || []).map(l => ({
        account_number: l.account_number,
        account_name: l.account_name,
        debit: l.debit,
        credit: l.credit,
        occupant_pct: l.occupant_pct,
        proprietaire_pct: l.proprietaire_pct,
      })),
    });
    setPendingAttachment(null);
    setDialogOpen(true);
  };

  const addLine = () => setForm({ ...form, lines: [...form.lines, { account_number: '', account_name: '', debit: 0, credit: 0 }] });
  const removeLine = (i) => setForm({ ...form, lines: form.lines.filter((_, idx) => idx !== i) });
  const updateLine = (i, field, value) => {
    const lines = [...form.lines];
    lines[i] = { ...lines[i], [field]: value };
    if (field === 'account_number') {
      const acc = accounts.find(a => a.number === value);
      if (acc) lines[i].account_name = acc.name;
      // Auto-pre-rempli les % occupant/proprio depuis la categorie de depense du compte
      const cat = (categories || []).find(c => c.account_number === value);
      if (cat && cat.default_occupant_pct != null) {
        lines[i].occupant_pct = Number(cat.default_occupant_pct);
        lines[i].proprietaire_pct = +(100 - Number(cat.default_occupant_pct)).toFixed(2);
      } else if (value && (value.startsWith('6') || value.startsWith('7'))) {
        // Compte de charge sans categorie -> 0% occupant par defaut
        if (lines[i].occupant_pct == null) {
          lines[i].occupant_pct = 0;
          lines[i].proprietaire_pct = 100;
        }
      } else {
        // Compte non-charge : pas de repartition
        lines[i].occupant_pct = null;
        lines[i].proprietaire_pct = null;
      }
    }
    if (field === 'occupant_pct') {
      const v = Math.max(0, Math.min(100, parseFloat(value) || 0));
      lines[i].occupant_pct = v;
      lines[i].proprietaire_pct = +(100 - v).toFixed(2);
    }
    if (field === 'proprietaire_pct') {
      const v = Math.max(0, Math.min(100, parseFloat(value) || 0));
      lines[i].proprietaire_pct = v;
      lines[i].occupant_pct = +(100 - v).toFixed(2);
    }
    setForm({ ...form, lines });
  };

  const totalDebit = form.lines.reduce((s, l) => s + Number(l.debit || 0), 0);
  const totalCredit = form.lines.reduce((s, l) => s + Number(l.credit || 0), 0);
  const isBalanced = Math.abs(totalDebit - totalCredit) < 0.01;

  const handleSave = async () => {
    if (!isBalanced) { toast.error('Ecriture non equilibree'); return; }
    try {
      const payload = { ...form, lines: form.lines.map(l => ({ ...l, debit: Number(l.debit), credit: Number(l.credit) })) };
      let entryId;
      if (editingEntry) {
        await api.put(`/accounting/entries/${editingEntry.id}`, payload);
        entryId = editingEntry.id;
        toast.success(editingEntry.auto_generated ? 'Ecriture modifiee (marquee manuel)' : 'Ecriture modifiee');
      } else {
        const { data: created } = await api.post('/accounting/entries', payload);
        entryId = created?.id;
        toast.success('Ecriture creee');
      }
      if (pendingAttachment && entryId) {
        try {
          const fd = new FormData();
          fd.append('file', pendingAttachment);
          await api.post(`/accounting/entries/${entryId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
        } catch (e) { console.warn(e); }
      }
      setDialogOpen(false); setEditingEntry(null);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const uploadEntryAttachment = async (entryId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/accounting/entries/${entryId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success('Piece jointe ajoutee');
      const { data } = await api.get(`/accounting/entries/${entryId}`);
      setAttachDialogEntry(data); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Echec'); }
  };

  const deleteEntryAttachment = async (entryId, attachmentId) => {
    if (!window.confirm('Supprimer cette piece jointe ?')) return;
    await api.delete(`/accounting/entries/${entryId}/attachments/${attachmentId}`);
    const { data } = await api.get(`/accounting/entries/${entryId}`);
    setAttachDialogEntry(data); load();
  };

  const handleDelete = async (id) => {
    // iter90bx : ne supprime PAS - genere une contre-passation traceable
    const reason = window.prompt(
      "Contre-passer cette ecriture ?\n\n" +
      "L'ecriture originale sera conservee (audit trail legal PCMN) et une " +
      "ecriture inverse sera creee pour l'annuler comptablement.\n\n" +
      "Motif (optionnel) :",
      ""
    );
    if (reason === null) return;  // annule
    try {
      const { data } = await api.delete(`/accounting/entries/${id}`, {
        params: reason ? { reason } : {},
      });
      toast.success(data.message || 'Contre-passation creee');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleUnlettrage = (entry) => {
    // Ouvre le dialog qui permet de choisir : Delettrer seulement OU Relettrer
    setUnlettrageEntry(entry);
  };
  const [unlettrageEntry, setUnlettrageEntry] = useState(null);

  // iter90en/iter90ep : selection multi + suppression bulk (superadmin only).
  // Une contre-passation ou une ecriture extournee ne peut jamais etre
  // supprimee (integrite audit trail PCMN).
  const isDeletableEntry = (e) => !e?.is_reversal && !e?.reversed;
  const deletableEntries = entries.filter(isDeletableEntry);
  const toggleSelect = (id) => {
    const entry = entries.find(e => e.id === id);
    if (!isDeletableEntry(entry)) {
      toast.error("Impossible : les contre-passations et extournes ne sont pas supprimables (audit trail legal)");
      return;
    }
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === deletableEntries.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(deletableEntries.map(e => e.id)));
    }
  };
  const clearSelection = () => setSelectedIds(new Set());
  const openBulkDelete = (selectAll = false) => {
    if (selectAll) {
      setSelectedIds(new Set(deletableEntries.map(e => e.id)));
    }
    setBulkDeleteReason('');
    setBulkDeleteDialogOpen(true);
  };
  const confirmBulkDelete = async () => {
    if (bulkDeleteReason.trim().length < 10) {
      toast.error('Justification detaillee requise (10 caracteres minimum)');
      return;
    }
    setBulkDeleting(true);
    try {
      const ids = Array.from(selectedIds);
      const { data } = await api.post('/admin/journal-entries/bulk-force-delete', {
        entry_ids: ids,
        reason: bulkDeleteReason.trim(),
      });
      toast.success(`${data.deleted_count} ecriture(s) supprimee(s) definitivement`);
      setBulkDeleteDialogOpen(false);
      setBulkDeleteReason('');
      clearSelection();
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur suppression');
    } finally {
      setBulkDeleting(false);
    }
  };

  return (
    <div data-testid="journals-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Journaux Comptables</h1><p className="page-subtitle">Ecritures comptables par journal</p></div>
        <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-entry-btn"><Plus size={16} className="mr-2" /> Nouvelle ecriture</Button>
      </div>

      <Tabs value={journalType} onValueChange={setJournalType}>
        <div className="flex items-center justify-between mb-4">
          <TabsList data-testid="journal-type-tabs">
            {JOURNAL_TYPES.map(j => <TabsTrigger key={j.value} value={j.value}>{j.label}</TabsTrigger>)}
          </TabsList>
          <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none" data-testid="include-reversals-toggle">
            <input
              type="checkbox"
              checked={includeReversals}
              onChange={e => setIncludeReversals(e.target.checked)}
              className="h-4 w-4 accent-[#022D52]"
            />
            <span>Inclure les contre-passations</span>
          </label>
        </div>
        <TabsContent value={journalType} className="mt-0">
          {/* iter90bt : barre de filtres de recherche */}
          <div className="bg-white rounded-2xl border border-slate-200/70 shadow-card p-3 mb-3 flex flex-wrap items-end gap-2" data-testid="journal-filters">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Du</label>
              <Input
                type="date"
                value={filters.date_from}
                onChange={e => setFilters(f => ({ ...f, date_from: e.target.value }))}
                className="w-36 h-9 text-xs"
                data-testid="filter-date-from"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Au</label>
              <Input
                type="date"
                value={filters.date_to}
                onChange={e => setFilters(f => ({ ...f, date_to: e.target.value }))}
                className="w-36 h-9 text-xs"
                data-testid="filter-date-to"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Montant min</label>
              <Input
                type="number"
                step="0.01"
                value={filters.amount_min}
                onChange={e => setFilters(f => ({ ...f, amount_min: e.target.value }))}
                className="w-28 h-9 text-xs"
                placeholder="0.00"
                data-testid="filter-amount-min"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Montant max</label>
              <Input
                type="number"
                step="0.01"
                value={filters.amount_max}
                onChange={e => setFilters(f => ({ ...f, amount_max: e.target.value }))}
                className="w-28 h-9 text-xs"
                placeholder="9999.99"
                data-testid="filter-amount-max"
              />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Tiers (fournisseur / proprietaire)</label>
              <Input
                type="text"
                value={filters.third_party_name}
                onChange={e => setFilters(f => ({ ...f, third_party_name: e.target.value }))}
                className="h-9 text-xs"
                placeholder="Ex : Finlead, Dupont..."
                data-testid="filter-third-party"
              />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Recherche libre</label>
              <Input
                type="text"
                value={filters.search}
                onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
                className="h-9 text-xs"
                placeholder="Reference, description, compte..."
                data-testid="filter-search"
              />
            </div>
            {hasActiveFilters && (
              <Button
                variant="outline"
                size="sm"
                onClick={resetFilters}
                className="h-9 text-xs"
                data-testid="filter-reset"
              >
                Reinitialiser
              </Button>
            )}
            <div className="flex items-end gap-2 ml-auto">
              <Button
                variant="outline"
                size="sm"
                onClick={() => downloadJournalsExport('csv')}
                disabled={exporting !== null}
                className="h-9 text-xs"
                data-testid="export-journals-csv-btn"
                title="Exporter les ecritures du journal courant en CSV (respecte les filtres de date)"
              >
                <Download size={13} className="mr-1" />
                {exporting === 'csv' ? 'Export...' : 'Export CSV'}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => downloadJournalsExport('pdf')}
                disabled={exporting !== null}
                className="h-9 text-xs"
                data-testid="export-journals-pdf-btn"
                title="Exporter les ecritures du journal courant en PDF (respecte les filtres de date)"
              >
                <Download size={13} className="mr-1" />
                {exporting === 'pdf' ? 'Export...' : 'Export PDF'}
              </Button>
            </div>
          </div>

          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            {/* iter90en : barre d'actions bulk (admin only) */}
            {isSuperadmin && entries.length > 0 && (
              <div className="flex items-center gap-3 px-4 py-2 bg-slate-50 border-b border-slate-200 text-sm">
                {selectedIds.size > 0 ? (
                  <>
                    <span className="text-slate-700 font-medium">
                      {selectedIds.size} ecriture(s) selectionnee(s)
                    </span>
                    <Button
                      size="sm"
                      onClick={() => openBulkDelete(false)}
                      className="bg-red-600 hover:bg-red-700 text-white h-7 text-xs"
                      data-testid="bulk-delete-selected-btn"
                    >
                      <Trash2 size={13} className="mr-1" /> Supprimer selection ({selectedIds.size})
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={clearSelection}
                      className="h-7 text-xs"
                    >
                      <X size={13} className="mr-1" /> Deselectionner
                    </Button>
                  </>
                ) : (
                  <span className="text-slate-500 italic">
                    <ShieldAlert size={13} className="inline mr-1" />
                    Mode admin : cochez les ecritures a supprimer definitivement
                  </span>
                )}
                <div className="ml-auto">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => openBulkDelete(true)}
                    className="border-red-300 text-red-700 hover:bg-red-50 h-7 text-xs"
                    data-testid="bulk-delete-all-btn"
                    disabled={deletableEntries.length === 0}
                  >
                    <Trash2 size={13} className="mr-1" /> Supprimer TOUT ({deletableEntries.length})
                  </Button>
                </div>
              </div>
            )}
            <Table>
              <TableHeader><TableRow>
                {isSuperadmin && (
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      checked={deletableEntries.length > 0 && selectedIds.size === deletableEntries.length}
                      onChange={toggleSelectAll}
                      className="cursor-pointer"
                      data-testid="bulk-select-all-checkbox"
                      title="Tout selectionner (hors contre-passations)"
                      disabled={deletableEntries.length === 0}
                    />
                  </TableHead>
                )}
                <TableHead>Date</TableHead><TableHead>Reference</TableHead><TableHead>Description</TableHead>
                <TableHead className="text-right">Debit</TableHead><TableHead className="text-right">Credit</TableHead><TableHead className="w-24">Actions</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {entries.length === 0 ? (
                  <TableRow><TableCell colSpan={isSuperadmin ? 7 : 6} className="text-center py-8 text-slate-400">Aucune ecriture</TableCell></TableRow>
                ) : entries.map(e => (
                  <TableRow key={e.id} className={`hover:bg-slate-50/50 ${e.reversed ? 'bg-red-50/30 line-through opacity-70' : ''} ${e.is_reversal ? 'bg-amber-50/40' : ''} ${selectedIds.has(e.id) ? 'bg-red-50/50' : ''}`}>
                    {isSuperadmin && (
                      <TableCell>
                        {isDeletableEntry(e) ? (
                          <input
                            type="checkbox"
                            checked={selectedIds.has(e.id)}
                            onChange={() => toggleSelect(e.id)}
                            className="cursor-pointer"
                            data-testid={`bulk-select-${e.id}`}
                          />
                        ) : (
                          <span
                            className="inline-block w-3 h-3 rounded-sm bg-slate-200"
                            title="Contre-passation / extournee : suppression interdite (audit legal)"
                            data-testid={`bulk-select-blocked-${e.id}`}
                          />
                        )}
                      </TableCell>
                    )}
                    <TableCell className="font-mono text-sm">{fmtDate(e.date)}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.reference}
                      {e.auto_generated && !e.manually_edited && <Badge variant="outline" className="ml-2 text-[10px] bg-blue-50 border-blue-200 text-[#01213e]" data-testid={`auto-badge-${e.id}`}>Auto</Badge>}
                      {e.manually_edited && <Badge variant="outline" className="ml-2 text-[10px] bg-orange-50 border-orange-200 text-orange-700" data-testid={`manual-edit-badge-${e.id}`}>Modifie</Badge>}
                      {e.is_reversal && <Badge variant="outline" className="ml-2 text-[10px] bg-amber-100 border-amber-300 text-amber-800" data-testid={`reversal-badge-${e.id}`}>Contre-passation</Badge>}
                      {e.reversed && <Badge variant="outline" className="ml-2 text-[10px] bg-red-100 border-red-300 text-red-700" data-testid={`reversed-badge-${e.id}`}>Extournee</Badge>}
                    </TableCell>
                    <TableCell className="font-medium">
                      {e.description}
                      {e.linked_invoice && (
                        <Badge variant="outline" className="ml-2 text-[10px] bg-emerald-50 border-emerald-300 text-emerald-800" data-testid={`linked-invoice-${e.id}`} title={`Facture liee : ${e.linked_invoice.invoice_number || ''} - ${e.linked_invoice.supplier_name || ''} (${(e.linked_invoice.amount_ttc || 0).toFixed(2)} EUR)`}>
                          Facture {e.linked_invoice.invoice_number || e.linked_invoice.supplier_name || ''}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono">{e.total_debit?.toFixed(2)}</TableCell>
                    <TableCell className="text-right font-mono">{e.total_credit?.toFixed(2)}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setViewEntry(e)}><Eye size={14} /></Button>
                        {!e.is_reversal && !e.reversed && <Button variant="ghost" size="sm" onClick={() => openEdit(e)} data-testid={`edit-entry-${e.id}`} title="Modifier"><Pencil size={14} /></Button>}
                        <Button variant="ghost" size="sm" onClick={() => setAttachDialogEntry(e)} data-testid={`entry-attach-${e.id}`} title="Pieces jointes">
                          <Paperclip size={14} />{(e.attachments?.length || 0) > 0 && <span className="ml-1 text-xs">{e.attachments.length}</span>}
                        </Button>
                        {e.source_type === 'bank_txn' && e.bank_txn_matched && !e.is_reversal && !e.reversed && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleUnlettrage(e)}
                            className="text-amber-600 hover:bg-amber-50"
                            data-testid={`unlettrage-btn-${e.id}`}
                            title={e.linked_invoice
                              ? `Delettrer de la facture "${e.linked_invoice.invoice_number || e.linked_invoice.supplier_name}"`
                              : 'Delettrer la transaction bancaire'}
                          >
                            <Unlink size={14} />
                          </Button>
                        )}
                        {(!e.auto_generated || e.manually_edited) && !e.is_reversal && !e.reversed && <Button variant="ghost" size="sm" onClick={() => handleDelete(e.id)} className="text-red-500"><Trash2 size={14} /></Button>}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </TabsContent>
      </Tabs>

      {/* Create Dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) setEditingEntry(null); setDialogOpen(open); }} hasUnsavedChanges={entryDirty}>
        <DialogContent className="max-w-6xl w-[min(96vw,1400px)] max-h-[90vh] overflow-y-auto" data-testid="entry-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            {editingEntry ? `Modifier ecriture ${editingEntry.reference || ''}` : 'Nouvelle ecriture comptable'}
            {editingEntry?.auto_generated && <Badge variant="outline" className="ml-2 text-[10px] bg-orange-50 border-orange-200 text-orange-700">Auto -&gt; sera marquee comme modifiee</Badge>}
          </DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Date *</label><Input type="date" value={form.date} onChange={e => setForm({...form, date: e.target.value})} data-testid="entry-date" /></div>
              <div><label className="form-label">Reference</label><Input value={form.reference} onChange={e => setForm({...form, reference: e.target.value})} /></div>
              <div><label className="form-label">Journal</label>
                <Select value={form.journal_type} onValueChange={v => setForm({...form, journal_type: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{JOURNAL_TYPES.map(j => <SelectItem key={j.value} value={j.value}>{j.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div><label className="form-label">Description</label><Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} data-testid="entry-desc" /></div>

            <div>
              <label className="form-label mb-2">Lignes d'ecriture</label>
              <div className="border rounded-md overflow-x-auto">
                <table className="w-full text-sm min-w-[900px]">
                  <thead><tr className="bg-slate-50 text-xs text-slate-600 uppercase">
                    <th className="p-2 text-left" style={{ minWidth: 260 }}>Compte</th>
                    <th className="p-2 text-left">Libelle</th>
                    <th className="p-2 text-right" style={{ minWidth: 110 }}>Debit</th>
                    <th className="p-2 text-right" style={{ minWidth: 110 }}>Credit</th>
                    <th className="p-2 text-right" style={{ minWidth: 80 }} title="Pourcentage occupant (decompte locataire)">%Occ.</th>
                    <th className="p-2 text-right" style={{ minWidth: 80 }} title="Pourcentage proprietaire">%Prop.</th>
                    <th className="p-2 w-10"></th>
                  </tr></thead>
                  <tbody>
                    {form.lines.map((line, i) => {
                      const isCharge = line.account_number && (line.account_number.startsWith('6') || line.account_number.startsWith('7'));
                      return (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="p-1" style={{ minWidth: 260 }}>
                          <AccountSearchSelect
                            accounts={accounts}
                            value={line.account_number}
                            onChange={v => updateLine(i, 'account_number', v)}
                            placeholder="Choisir un compte..."
                            testId={`journal-line-${i}-account`}
                          />
                        </td>
                        <td className="p-1 text-xs text-slate-500">{line.account_name}</td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8 font-mono w-full" value={line.debit} onChange={e => updateLine(i, 'debit', e.target.value)} /></td>
                        <td className="p-1"><Input type="number" step="0.01" className="text-right text-sm h-8 font-mono w-full" value={line.credit} onChange={e => updateLine(i, 'credit', e.target.value)} /></td>
                        <td className="p-1">
                          {isCharge ? (
                            <Input type="number" min={0} max={100} step={1} className="text-right text-sm h-8 bg-amber-50/40 font-mono w-full"
                              value={line.occupant_pct ?? 0}
                              onChange={e => updateLine(i, 'occupant_pct', e.target.value)}
                              data-testid={`journal-line-${i}-occupant`}
                            />
                          ) : <span className="text-slate-300 text-xs">—</span>}
                        </td>
                        <td className="p-1">
                          {isCharge ? (
                            <Input type="number" min={0} max={100} step={1} className="text-right text-sm h-8 bg-blue-50/40 font-mono w-full"
                              value={line.proprietaire_pct ?? 100}
                              onChange={e => updateLine(i, 'proprietaire_pct', e.target.value)}
                              data-testid={`journal-line-${i}-proprio`}
                            />
                          ) : <span className="text-slate-300 text-xs">—</span>}
                        </td>
                        <td className="p-1">{form.lines.length > 2 && <button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button>}</td>
                      </tr>
                    );})}
                  </tbody>
                  <tfoot><tr className="border-t-2 border-slate-200 bg-slate-50 font-semibold text-sm">
                    <td colSpan={2} className="p-2">
                      <Button variant="ghost" size="sm" onClick={addLine} className="text-xs"><Plus size={12} className="mr-1" /> Ajouter ligne</Button>
                    </td>
                    <td className="p-2 text-right font-mono">{totalDebit.toFixed(2)}</td>
                    <td className="p-2 text-right font-mono">{totalCredit.toFixed(2)}</td>
                    <td colSpan={3}></td>
                  </tr></tfoot>
                </table>
              </div>
              {!isBalanced && <p className="text-red-500 text-xs mt-1">Ecart: {Math.abs(totalDebit - totalCredit).toFixed(2)} EUR</p>}
            </div>

            <div className="border-t pt-3">
              <label className="form-label mb-1">Piece justificative (PDF / Image)</label>
              <div className="flex items-center gap-3">
                <input id="entry-attach-input" type="file" accept="application/pdf,image/*" className="hidden"
                  onChange={(e) => setPendingAttachment(e.target.files?.[0] || null)} />
                <Button type="button" variant="outline" size="sm" onClick={() => document.getElementById('entry-attach-input').click()} data-testid="entry-attach-pick">
                  <Paperclip size={14} className="mr-2" /> Choisir un fichier
                </Button>
                {pendingAttachment && (
                  <span className="text-xs text-slate-600 flex items-center gap-2">
                    {pendingAttachment.name}
                    <button type="button" className="text-red-500" onClick={() => setPendingAttachment(null)}><Trash2 size={12} /></button>
                  </span>
                )}
              </div>
              <p className="text-[11px] text-slate-400 mt-1">Optionnel - documente l'ecriture (justificatif, contrat, devis...)</p>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} disabled={!isBalanced} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="entry-save-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* View Dialog */}
      <Dialog open={!!viewEntry} onOpenChange={() => setViewEntry(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Detail de l'ecriture</DialogTitle></DialogHeader>
          {viewEntry && (
            <div className="space-y-4 mt-2">
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div><span className="text-slate-500">Date:</span> <span className="font-medium">{fmtDate(viewEntry.date)}</span></div>
                <div><span className="text-slate-500">Ref:</span> <span className="font-medium">{viewEntry.reference}</span></div>
                <div><span className="text-slate-500">Journal:</span> <Badge variant="outline">{viewEntry.journal_type}</Badge></div>
              </div>
              <div className="text-sm"><span className="text-slate-500">Description:</span> {viewEntry.description}</div>
              <div className="border rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead><tr className="bg-slate-50"><th className="p-2 text-left text-xs text-slate-600">Compte</th><th className="p-2 text-left text-xs text-slate-600">Libelle</th><th className="p-2 text-right text-xs text-slate-600">Debit</th><th className="p-2 text-right text-xs text-slate-600">Credit</th></tr></thead>
                  <tbody>
                    {viewEntry.lines?.map((l, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="p-2 font-mono">{l.account_number}</td>
                        <td className="p-2">{l.account_name}</td>
                        <td className="p-2 text-right font-mono">{l.debit > 0 ? l.debit.toFixed(2) : ''}</td>
                        <td className="p-2 text-right font-mono">{l.credit > 0 ? l.credit.toFixed(2) : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
      {/* Attachments Dialog (per existing entry) */}
      <Dialog open={!!attachDialogEntry} onOpenChange={() => setAttachDialogEntry(null)}>
        <DialogContent className="max-w-2xl w-[min(92vw,720px)]" data-testid="entry-attach-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="truncate pr-8">
              Pieces jointes - {attachDialogEntry?.reference || attachDialogEntry?.description}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="border-2 border-dashed rounded p-4 text-center">
              <input type="file" accept="application/pdf,image/*" className="hidden" id="att-input-entry"
                onChange={(e) => { const f = e.target.files?.[0]; if (f && attachDialogEntry) uploadEntryAttachment(attachDialogEntry.id, f); e.target.value=''; }} />
              <Button variant="outline" onClick={() => document.getElementById('att-input-entry').click()} data-testid="upload-attachment-entry-btn">
                <Paperclip size={14} className="mr-2" /> Ajouter un PDF / Image
              </Button>
            </div>
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {(attachDialogEntry?.attachments || []).length === 0 ? (
                <div className="text-sm text-slate-400 text-center py-3">Aucune piece jointe</div>
              ) : attachDialogEntry.attachments.map((a) => (
                <div key={a.id} className="flex items-center gap-2 border rounded px-2 py-1.5 text-sm min-w-0">
                  <span
                    className="flex-1 min-w-0 truncate flex items-center gap-2"
                    title={a.filename}
                  >
                    <Paperclip size={12} className="flex-shrink-0" />
                    <span className="truncate">{a.filename}</span>
                  </span>
                  <div className="flex gap-1 flex-shrink-0">
                    <a href={`${API}/api/accounting/entries/${attachDialogEntry.id}/attachments/${a.id}/download`} target="_blank" rel="noreferrer" title="Telecharger">
                      <Button variant="ghost" size="sm"><Download size={14} /></Button>
                    </a>
                    <Button variant="ghost" size="sm" className="text-red-500" onClick={() => deleteEntryAttachment(attachDialogEntry.id, a.id)} title="Supprimer"><Trash2 size={14} /></Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter90x' : Delettrer / Relettrer directement */}
      <UnlettrageDialog
        entry={unlettrageEntry}
        open={!!unlettrageEntry}
        onClose={() => setUnlettrageEntry(null)}
        onSuccess={load}
      />

      {/* iter90en : Dialog de confirmation bulk delete (admin only) */}
      <Dialog open={bulkDeleteDialogOpen} onOpenChange={setBulkDeleteDialogOpen}>
        <DialogContent className="max-w-lg" data-testid="bulk-delete-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-700" style={{fontFamily:'Chivo,sans-serif'}}>
              <ShieldAlert size={20} /> Suppression definitive
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-900">
              <p className="font-semibold mb-1">
                Vous etes sur le point de supprimer <b>{selectedIds.size}</b> ecriture(s) DEFINITIVEMENT.
              </p>
              <p className="text-xs text-red-800">
                Cette action est irreversible. Contrairement au bouton &laquo;extourne&raquo; habituel
                qui cree une contre-passation traceable, ce bouton ADMIN supprime
                completement les ecritures. Une copie est conservee dans <code>deleted_entries</code>
                pour l&apos;audit trail.
              </p>
              <p className="text-xs text-red-800 mt-1">
                <b>Impact bilan :</b> Les provisions/charges concernees disparaissent des
                balances et bilan. Assurez-vous que ces ecritures sont bien des doublons
                ou erreurs de saisie.
              </p>
            </div>
            <div>
              <label className="form-label">Justification (10 caracteres minimum) *</label>
              <Textarea
                value={bulkDeleteReason}
                onChange={e => setBulkDeleteReason(e.target.value)}
                placeholder="Ex : suppression doublons VE crees par regularisation 2026 relancee 3 fois"
                rows={3}
                data-testid="bulk-delete-reason"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                La justification est loguee dans l&apos;audit trail avec votre identifiant admin.
              </p>
            </div>
            <div className="flex gap-3 justify-end">
              <Button
                variant="outline"
                onClick={() => setBulkDeleteDialogOpen(false)}
                disabled={bulkDeleting}
              >
                Annuler
              </Button>
              <Button
                onClick={confirmBulkDelete}
                className="bg-red-600 hover:bg-red-700 text-white"
                disabled={bulkDeleting || bulkDeleteReason.trim().length < 10}
                data-testid="bulk-delete-confirm-btn"
              >
                <Trash2 size={14} className="mr-1.5" />
                {bulkDeleting ? 'Suppression...' : `Supprimer ${selectedIds.size} ecriture(s)`}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
