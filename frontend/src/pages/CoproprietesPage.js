import { useState, useEffect, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Home, Search, Archive, RotateCcw, Landmark, PlusCircle, X, Eraser, Wand2, Upload, UserPlus, FileText, Download } from 'lucide-react';
import BulkCsvImportDialog from '@/components/BulkCsvImportDialog';
import PdfImportDialog from '@/components/PdfImportDialog';

const emptyBank = { iban: '', bic: '', account_type: 'vue', is_default: false, label: '' };
const emptyLot = { number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0 };
const emptyForm = { name: '', bce: '', address: '', postal_code: '', city: '', country: 'Belgique', description: '', bank_accounts: [], quarterly_closing: true, default_provisions: true, lots: [] };

export default function CoproprietesPage() {
  const { isAdmin, isManager, isSuperadmin } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [coproprietes, setCoproprietes] = useState([]);
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [bulkLotsOpen, setBulkLotsOpen] = useState(false);
  const [bulkOwnersOpen, setBulkOwnersOpen] = useState(false);
  const [pdfOwnersOpen, setPdfOwnersOpen] = useState(false);
  const [pdfLotsOpen, setPdfLotsOpen] = useState(false);
  const [importingOwners, setImportingOwners] = useState(false);
  const [step, setStep] = useState(1);
  const [ownerSearchByLot, setOwnerSearchByLot] = useState({});  // {lotIdx: 'query'}
  const [ownerFocusLot, setOwnerFocusLot] = useState(null);  // lotIdx currently focused or null

  const load = useCallback(async () => {
    const { data } = await api.get('/coproprietes', { params: { show_archived: showArchived } });
    setCoproprietes(data);
  }, [showArchived]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    // include_unassigned=true : also returns orphan owners (no lot yet), needed
    // for the lot-assignment dropdown to show freshly-imported owners that
    // aren't tied to any ACP/lot yet.
    api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } }).then(r => setOwners(r.data)).catch(() => {});
  }, [dialogOpen]);

  const filtered = coproprietes.filter(c => c.name.toLowerCase().includes(search.toLowerCase()) || (c.reference || '').toLowerCase().includes(search.toLowerCase()) || (c.bce || '').includes(search));

  const openCreate = () => { setEditing(null); setForm({...emptyForm, bank_accounts: [], lots: []}); setStep(1); setDialogOpen(true); };
  const openEdit = (c) => {
    setEditing(c);
    setForm({
      name: c.name || '', bce: c.bce || '', address: c.address || '', postal_code: c.postal_code || '',
      city: c.city || '', country: c.country || 'Belgique', description: c.description || '',
      bank_accounts: c.bank_accounts || [], quarterly_closing: c.quarterly_closing !== false,
      default_provisions: c.default_provisions !== false, lots: [],
    });
    setStep(1);
    setDialogOpen(true);
  };

  // Auto-open edit dialog when ?edit={id} query param is present (deep link from sidebar)
  useEffect(() => {
    const editId = searchParams.get('edit');
    if (editId && coproprietes.length > 0 && !dialogOpen) {
      const c = coproprietes.find(x => x.id === editId);
      if (c) {
        openEdit(c);
        // Clean URL so refresh doesn't re-open
        searchParams.delete('edit');
        setSearchParams(searchParams, { replace: true });
      }
    }
    // eslint-disable-next-line
  }, [coproprietes, searchParams]);

  // Lots on the fly (only used at creation time)
  const addLot = () => setForm({ ...form, lots: [...(form.lots || []), { ...emptyLot, owner_ids: [] }] });
  const removeLot = (i) => setForm({ ...form, lots: form.lots.filter((_, idx) => idx !== i) });
  const updateLot = (i, field, value) => {
    const ls = [...form.lots];
    ls[i] = { ...ls[i], [field]: value };
    setForm({ ...form, lots: ls });
  };
  const addOwnerToLot = (i, ownerId) => {
    const ls = [...form.lots];
    const current = ls[i].owner_ids || [];
    if (!current.includes(ownerId)) {
      ls[i] = { ...ls[i], owner_ids: [...current, ownerId] };
      setForm({ ...form, lots: ls });
    }
    setOwnerSearchByLot({ ...ownerSearchByLot, [i]: '' });
  };
  const removeOwnerFromLot = (i, ownerId) => {
    const ls = [...form.lots];
    ls[i] = { ...ls[i], owner_ids: (ls[i].owner_ids || []).filter(x => x !== ownerId) };
    setForm({ ...form, lots: ls });
  };
  const getOwnerSuggestions = (i) => {
    const q = (ownerSearchByLot[i] || '').trim().toLowerCase();
    const taken = form.lots[i].owner_ids || [];
    const available = owners.filter(o => !taken.includes(o.id));
    if (!q) {
      // No query yet : if the input is focused, show top N available owners
      // (so the user can BROWSE imported owners without typing). Otherwise hide.
      if (ownerFocusLot === i) return available.slice(0, 50);
      return [];
    }
    return available.filter(o =>
      (o.name || '').toLowerCase().includes(q) ||
      (o.email || '').toLowerCase().includes(q) ||
      (o.vcs_code || '').includes(q)
    ).slice(0, 12);
  };

  // Refetch owners on focus to ensure freshly-imported owners are visible.
  // include_unassigned=true to also see owners not yet linked to any lot/ACP.
  const refreshOwnersIfStale = async () => {
    try {
      const r = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
      setOwners(r.data);
    } catch { /* ignore */ }
  };

  // Bank accounts management
  const addBankAccount = () => setForm({ ...form, bank_accounts: [...form.bank_accounts, { ...emptyBank }] });
  const removeBankAccount = (i) => setForm({ ...form, bank_accounts: form.bank_accounts.filter((_, idx) => idx !== i) });
  const updateBankAccount = (i, field, value) => {
    const ba = [...form.bank_accounts];
    ba[i] = { ...ba[i], [field]: value };
    if (field === 'is_default' && value === true) {
      ba.forEach((b, idx) => { if (idx !== i) b.is_default = false; });
    }
    setForm({ ...form, bank_accounts: ba });
  };

  const handleSave = async () => {
    try {
      if (editing) { await api.put(`/coproprietes/${editing.id}`, form); toast.success('Copropriete modifiee'); setDialogOpen(false); load(); }
      else {
        const r = await api.post('/coproprietes', form);
        const newCopro = r.data;
        const nLots = (form.lots || []).filter(l => l.number && l.number.trim()).length;
        toast.success(nLots > 0 ? `ACP creee avec ${nLots} lot(s)` : 'Copropriete creee');
        setDialogOpen(false); load();
        // Reprise Optipro/Sogis ?
        setTimeout(() => {
          if (newCopro?.id && window.confirm(
            `L'ACP "${newCopro.name}" a ete creee.\n\n` +
            `S'agit-il d'une REPRISE depuis Optipro / Sogis ?\n\n` +
            `[OK] : Lancer le wizard d'import (proprietaires, fournisseurs, lots, natures, factures...).\n` +
            `[Annuler] : Continuer normalement.`
          )) {
            navigate(`/import-wizard?copropriete_id=${newCopro.id}`);
          }
        }, 200);
      }
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const handleDelete = async (id) => { if (!window.confirm('Supprimer cette copropriete ?')) return; try { await api.delete(`/coproprietes/${id}`); toast.success('Supprimee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const handleArchive = async (id) => { try { await api.post(`/coproprietes/${id}/archive`); toast.success('Archivee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const handleUnarchive = async (id) => { try { await api.post(`/coproprietes/${id}/unarchive`); toast.success('Reactivee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };

  const handleDownloadArchive = async (c) => {
    // iter90ax : Confirmation prealable + option include_pdfs
    const includePdfs = window.confirm(
      `Telecharger l'archive complete de ${c.name} (ZIP structure par annee) ?\n\n` +
      `OK = inclure les PDF (bilans...) - plus volumineux\n` +
      `Annuler = sortir sans telecharger\n\n` +
      `Note : cliquez OK pour la version complete. Pour CSV uniquement, decochez plus tard.`
    );
    if (includePdfs === false && !window.confirm('Confirmez le telechargement (version CSV uniquement, sans PDF) ?')) return;
    try {
      toast.info('Preparation de l\'archive... (peut prendre 30-60s)');
      const r = await api.post(
        `/coproprietes/${c.id}/archive-download?include_pdfs=${includePdfs}`,
        {},
        { responseType: 'blob' }
      );
      const url = URL.createObjectURL(new Blob([r.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = `archive_${(c.name || 'acp').replace(/\s+/g, '_')}_${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('Archive telechargee');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur telechargement archive');
    }
  };
  const handleCleanupOrphans = async (c) => {
    if (!window.confirm(`Nettoyer les ecritures orphelines de "${c.name}" ?\n\nSupprime les ecritures auto-generees dont la source (facture, appel, transaction bancaire) a ete supprimee. Resynchronise le grand livre, le bilan et la balance des tiers.`)) return;
    try {
      const { data } = await api.post(`/coproprietes/${c.id}/cleanup-orphan-entries`);
      const s = data.stats || {};
      toast.success(`${data.total_deleted} ecriture(s) orpheline(s) supprimee(s) (factures: ${s.invoice||0}, appels: ${s.fund_call||0}, banque: ${s.bank_txn||0})`);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const handleResetData = async (c) => {
    const msg = `⚠ VIDER TOUTES les donnees comptables de "${c.name}" ?\n\n`
                + `Seront DEFINITIVEMENT supprimes :\n`
                + ` • Factures fournisseurs + pieces jointes\n`
                + ` • Ecritures comptables (journaux) + pieces jointes\n`
                + ` • Appels de fonds + ecritures liees\n`
                + ` • Transactions bancaires + extraits\n`
                + ` • Budgets et exercices fiscaux (cloture comprise)\n`
                + ` • Regularisations, natures de depense, documents\n\n`
                + `Seront CONSERVES (structure de l'ACP) :\n`
                + ` • Lots et quotites\n`
                + ` • Proprietaires + fournisseurs (collections globales)\n`
                + ` • PCMN (plan comptable de l'ACP)\n`
                + ` • Cles de repartition\n`
                + ` • Comptes bancaires configures\n\n`
                + `Cette action est IRREVERSIBLE. Confirmer ?`;
    if (!window.confirm(msg)) return;
    try {
      const { data } = await api.post(`/coproprietes/${c.id}/reset-financial-data`);
      const s = data.stats || {};
      const total = (s.invoices||0) + (s.journal_entries||0) + (s.fund_calls||0) +
                    (s.bank_transactions||0) + (s.bank_statements||0) + (s.budgets||0) +
                    (s.fiscal_years||0) + (s.expense_categories||0) + (s.documents||0) +
                    (s.regularizations||0);
      toast.success(
        `ACP "${c.name}" videe : ${total} elements supprimes ` +
        `(${s.fiscal_years||0} exercice(s), ${s.invoices||0} facture(s), ` +
        `${s.journal_entries||0} ecriture(s), ${s.bank_transactions||0} txn bancaires, ` +
        `${s.deleted_files||0} fichier(s) disque)`,
        { duration: 6000 }
      );
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const getDefaultIban = (c) => (c.bank_accounts || []).find(b => b.is_default)?.iban || (c.bank_accounts || [])[0]?.iban || c.bank_account || '-';

  return (
    <div data-testid="coproprietes-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Home size={24} className="inline mr-2" />Coproprietes (ACP)</h1><p className="page-subtitle">Gestion des associations de coproprietaires</p></div>
        <div className="flex gap-2">
          <Button variant={showArchived ? "default" : "outline"} size="sm" onClick={() => setShowArchived(!showArchived)} data-testid="toggle-archived"><Archive size={14} className="mr-1" /> {showArchived ? 'Masquer archives' : 'Voir archives'}</Button>
          {isManager && <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-copro-btn"><Plus size={16} className="mr-2" /> Nouvelle ACP</Button>}
        </div>
      </div>
      <div className="mb-4 relative max-w-sm"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><Input placeholder="Rechercher ref, nom, BCE..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" /></div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Ref</TableHead><TableHead>Nom</TableHead><TableHead>BCE</TableHead><TableHead>Ville</TableHead><TableHead>Compte defaut</TableHead><TableHead>Statut</TableHead><TableHead className="w-40">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucune copropriete</TableCell></TableRow> : filtered.map(c => (
              <TableRow key={c.id} className="hover:bg-slate-50/50" data-testid={`copro-row-${c.id}`}>
                <TableCell className="font-mono text-xs text-[#022D52]">{c.reference || '-'}</TableCell>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="font-mono text-sm">{c.bce || '-'}</TableCell>
                <TableCell className="text-sm">{c.city}{c.postal_code ? ` (${c.postal_code})` : ''}</TableCell>
                <TableCell className="font-mono text-xs">{getDefaultIban(c)}</TableCell>
                <TableCell><Badge variant="outline" className={c.status === 'archived' ? 'bg-slate-100 text-slate-500' : 'bg-green-50 text-green-700 border-green-200'}>{c.status === 'archived' ? 'Archive' : 'Active'}</Badge></TableCell>
                <TableCell><div className="flex gap-0">
                  {isManager && <Button variant="outline" size="sm" onClick={() => openEdit(c)} title="Modifier l'ACP (nom, adresse, banques, parametres)" data-testid={`edit-copro-${c.id}`} className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10 mr-1"><Pencil size={13} className="mr-1" /> Modifier</Button>}
                  {isSuperadmin && <Button variant="ghost" size="sm" onClick={() => handleCleanupOrphans(c)} className="text-[#022D52] hover:text-[#01213e]" title="Nettoyer les ecritures orphelines (re-synchroniser bilan/grand livre)" data-testid={`cleanup-orphans-${c.id}`}><Wand2 size={13} /></Button>}
                  {isSuperadmin && <Button variant="outline" size="sm" onClick={() => handleResetData(c)} className="text-amber-700 border-amber-300 hover:bg-amber-50 mr-1" title="Vider TOUTES les donnees comptables (factures, ecritures, exercices, budgets...)" data-testid={`reset-data-${c.id}`}><Eraser size={13} className="mr-1" /> Vider</Button>}
                  {isManager && c.status !== 'archived' && <Button variant="ghost" size="sm" onClick={() => handleArchive(c.id)} className="text-orange-500" title="Archiver"><Archive size={13} /></Button>}
                  {isManager && c.status === 'archived' && <Button variant="ghost" size="sm" onClick={() => handleUnarchive(c.id)} className="text-green-600" title="Reactiver"><RotateCcw size={13} /></Button>}
                  {isManager && <Button variant="ghost" size="sm" onClick={() => handleDownloadArchive(c)} className="text-[#022D52]" title="Telecharger archive ZIP complete par annee" data-testid={`archive-dl-${c.id}`}><Download size={13} /></Button>}
                  {isAdmin && <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-500" title="Supprimer (cascade)"><Trash2 size={13} /></Button>}
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent
          className="max-w-3xl max-h-[85vh] overflow-y-auto"
          data-testid="copro-dialog"
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier ACP' : 'Assistant de creation ACP'}</DialogTitle>
            {editing?.reference && <p className="font-mono text-sm text-[#022D52]">Ref: {editing.reference}</p>}
          </DialogHeader>

          {/* Wizard step indicator (only for creation, not edit) */}
          {!editing && (
            <div className="flex items-center justify-between mb-4 mt-2 px-2">
              {[
                { n: 1, label: 'Identite & banques' },
                { n: 2, label: 'Lots & proprietaires' },
                { n: 3, label: 'Options & validation' },
              ].map((s, idx, arr) => (
                <div key={s.n} className="flex items-center flex-1">
                  <div className={`flex items-center gap-2 ${step === s.n ? 'text-[#022D52]' : step > s.n ? 'text-green-600' : 'text-slate-400'}`}>
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 ${step === s.n ? 'bg-[#022D52] text-white border-[#022D52]' : step > s.n ? 'bg-green-500 text-white border-green-500' : 'bg-white border-slate-300'}`} data-testid={`step-indicator-${s.n}`}>
                      {step > s.n ? '✓' : s.n}
                    </div>
                    <span className="text-xs font-medium hidden sm:inline">{s.label}</span>
                  </div>
                  {idx < arr.length - 1 && <div className={`flex-1 h-px mx-2 ${step > s.n ? 'bg-green-500' : 'bg-slate-200'}`} />}
                </div>
              ))}
            </div>
          )}

          <div className="space-y-5 mt-2">
            {/* STEP 1: Identification + adresse + banques (always shown in edit mode) */}
            {(editing || step === 1) && <>
            {/* Identification */}
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Identification</div>
              <div className="grid grid-cols-2 gap-4">
                <div><label className="form-label">Nom de l'ACP *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="copro-name-input" /></div>
                <div><label className="form-label">N BCE</label><Input value={form.bce} onChange={e => setForm({...form, bce: e.target.value})} placeholder="0123.456.789" data-testid="copro-bce-input" /></div>
              </div>
            </div>

            {/* Adresse */}
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Adresse postale</div>
              <div><label className="form-label">Adresse</label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} /></div>
              <div className="grid grid-cols-3 gap-4 mt-2">
                <div><label className="form-label">Code postal</label><Input value={form.postal_code} onChange={e => setForm({...form, postal_code: e.target.value})} /></div>
                <div><label className="form-label">Ville</label><Input value={form.city} onChange={e => setForm({...form, city: e.target.value})} /></div>
                <div><label className="form-label">Pays</label><Input value={form.country} onChange={e => setForm({...form, country: e.target.value})} /></div>
              </div>
            </div>

            {/* Comptes bancaires */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Comptes bancaires</div>
                <Button variant="outline" size="sm" onClick={addBankAccount}><PlusCircle size={14} className="mr-1" /> Ajouter compte</Button>
              </div>
              {form.bank_accounts.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-3 border rounded-md">Aucun compte - cliquez &quot;Ajouter compte&quot;</p>
              ) : (
                <div className="space-y-2">
                  {form.bank_accounts.map((ba, i) => (
                    <div key={i} className="border rounded-md p-3 bg-slate-50/50 relative">
                      <button onClick={() => removeBankAccount(i)} className="absolute top-2 right-2 text-red-400 hover:text-red-600"><X size={14} /></button>
                      <div className="grid grid-cols-4 gap-3">
                        <div className="col-span-2"><label className="form-label">IBAN *</label><Input value={ba.iban} onChange={e => updateBankAccount(i, 'iban', e.target.value)} placeholder="BE00 0000 0000 0000" /></div>
                        <div><label className="form-label">BIC</label><Input value={ba.bic} onChange={e => updateBankAccount(i, 'bic', e.target.value)} /></div>
                        <div><label className="form-label">Type</label>
                          <Select value={ba.account_type} onValueChange={v => updateBankAccount(i, 'account_type', v)}>
                            <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                            <SelectContent><SelectItem value="vue">Compte a vue</SelectItem><SelectItem value="epargne">Compte epargne</SelectItem></SelectContent>
                          </Select>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 mt-2">
                        <Input value={ba.label} onChange={e => updateBankAccount(i, 'label', e.target.value)} placeholder="Libelle (optionnel)" className="flex-1 h-8 text-sm" />
                        <label className="flex items-center gap-2 cursor-pointer text-sm whitespace-nowrap">
                          <Checkbox checked={ba.is_default} onCheckedChange={v => updateBankAccount(i, 'is_default', v)} />
                          <span>Compte par defaut</span>
                        </label>
                      </div>
                      {ba.iban && <div className="text-[10px] text-slate-400 mt-1 font-mono">Compte PCMN auto: {ba.account_type === 'epargne' ? '550' : '551'}{(ba.iban.replace(/\s/g, '').slice(-3) || '000')}00</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>
            </>}

            {/* STEP 2: Lots with owner autocomplete */}
            {!editing && step === 2 && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Lots et proprietaires</div>
                  <div className="flex gap-2 flex-wrap">
                    <Button variant="outline" size="sm" onClick={() => setBulkOwnersOpen(true)} className="border-emerald-300 text-emerald-700 hover:bg-emerald-50" data-testid="import-owners-csv-btn">
                      <UserPlus size={14} className="mr-1" /> Proprietaires (CSV)
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setPdfOwnersOpen(true)} className="border-emerald-300 text-emerald-700 hover:bg-emerald-50" data-testid="import-owners-pdf-btn">
                      <FileText size={14} className="mr-1" /> Proprietaires (PDF)
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setBulkLotsOpen(true)} className="border-blue-300 text-[#01213e] hover:bg-blue-50" data-testid="import-lots-csv-btn">
                      <Upload size={14} className="mr-1" /> Lots (CSV)
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => setPdfLotsOpen(true)} className="border-blue-300 text-[#01213e] hover:bg-blue-50" data-testid="import-lots-pdf-btn">
                      <FileText size={14} className="mr-1" /> Lots (PDF)
                    </Button>
                    <Button variant="outline" size="sm" onClick={addLot} data-testid="add-lot-btn"><PlusCircle size={14} className="mr-1" /> Ajouter manuellement</Button>
                  </div>
                </div>
                <div className="bg-blue-50/40 border border-blue-100 text-xs text-[#01213e] p-2 rounded mb-3">
                  <strong>Reprise Optipro/Sogis ?</strong> Importez les <em>Listes des coproprietaires</em> et <em>Liste des lots</em> directement en PDF (export Optipro), ou en CSV. Les proprietaires importes seront automatiquement suggeres lors du matching avec les lots.
                </div>
                {(form.lots || []).length === 0 ? (
                  <p className="text-sm text-slate-400 text-center py-3 border rounded-md">Aucun lot - vous pourrez en ajouter plus tard via le menu Lots</p>
                ) : (
                  <>
                    {/* Summary banner : counts of lots / matched / orphan */}
                    {(() => {
                      const total = form.lots.length;
                      const matched = form.lots.filter(l => (l.owner_ids || []).length > 0).length;
                      const orphans = form.lots.filter(l => (l.owner_ids || []).length === 0 && (l._imported_owner_name || l._imported_owner_aux)).length;
                      const empty = total - matched - orphans;
                      if (total === 0) return null;
                      return (
                        <div className="mb-3 flex items-center justify-between bg-slate-50/60 border border-slate-200 rounded-md px-3 py-2 text-[11px]" data-testid="lots-summary">
                          <div className="flex items-center gap-3">
                            <span><strong>{total}</strong> lot(s) au total</span>
                            {matched > 0 && <span className="text-emerald-700"><strong>{matched}</strong> auto-affectes</span>}
                            {orphans > 0 && <span className="text-amber-700"><strong>{orphans}</strong> orphelins (proprietaire manquant)</span>}
                            {empty > 0 && <span className="text-slate-500"><strong>{empty}</strong> sans contrepartie</span>}
                          </div>
                          {orphans > 0 && (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 px-2 text-[11px] border-amber-300 text-amber-700 hover:bg-amber-50"
                              data-testid="lots-retry-match-btn"
                              onClick={async () => {
                                let nextOwners = owners;
                                try {
                                  const r = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
                                  nextOwners = r.data || [];
                                  setOwners(nextOwners);
                                } catch (_e) { /* keep cache */ }
                                let matched2 = 0;
                                setForm(f => {
                                  const lots = (f.lots || []).map(l => {
                                    if ((l.owner_ids || []).length > 0) return l;
                                    const aux = (l._imported_owner_aux || '').trim().toUpperCase();
                                    const oname = (l._imported_owner_name || '').toLowerCase().trim();
                                    let m = null;
                                    if (aux) m = nextOwners.find(o => (o.auxiliary_code || '').toUpperCase() === aux);
                                    if (!m && oname) m = nextOwners.find(o => {
                                      const n = (o.name || '').toLowerCase().trim();
                                      return n === oname || n.includes(oname) || oname.includes(n);
                                    });
                                    if (m) { matched2++; return { ...l, owner_id: m.id, owner_ids: [m.id] }; }
                                    return l;
                                  });
                                  return { ...f, lots };
                                });
                                if (matched2 > 0) toast.success(`${matched2} lot(s) nouvellement affecte(s)`);
                                else toast.info('Aucun nouveau rattachement - importez d\'abord les proprietaires correspondants');
                              }}
                            >
                              Reessayer l&apos;auto-affectation
                            </Button>
                          )}
                        </div>
                      );
                    })()}
                  <div className="space-y-3">
                    {form.lots.map((lot, i) => (
                      <div key={i} className="border rounded-md p-3 bg-slate-50/50 relative" data-testid={`lot-row-${i}`}>
                        <button onClick={() => removeLot(i)} className="absolute top-2 right-2 text-red-400 hover:text-red-600"><X size={14} /></button>
                        <div className="grid grid-cols-5 gap-3">
                          <div><label className="form-label">N* *</label><Input value={lot.number} onChange={e => updateLot(i, 'number', e.target.value)} placeholder="A1" data-testid={`lot-number-${i}`} /></div>
                          <div className="col-span-2"><label className="form-label">Description</label><Input value={lot.description} onChange={e => updateLot(i, 'description', e.target.value)} placeholder="Appartement 2 ch" /></div>
                          <div><label className="form-label">Type</label>
                            <Select value={lot.lot_type} onValueChange={v => updateLot(i, 'lot_type', v)}>
                              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                              <SelectContent>
                                <SelectItem value="apartment">Appartement</SelectItem>
                                <SelectItem value="parking">Parking</SelectItem>
                                <SelectItem value="cave">Cave</SelectItem>
                                <SelectItem value="commerce">Commerce</SelectItem>
                                <SelectItem value="bureau">Bureau</SelectItem>
                                <SelectItem value="autre">Autre</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <div><label className="form-label">Etage</label><Input type="number" value={lot.floor} onChange={e => updateLot(i, 'floor', parseInt(e.target.value || '0'))} /></div>
                        </div>

                        {/* Owner autocomplete per lot */}
                        <div className="mt-2 pt-2 border-t border-slate-200/70">
                          <label className="form-label">Proprietaires <span className="text-slate-400 font-normal">(cliquez pour voir la liste ou tapez pour filtrer)</span></label>
                          {(lot.owner_ids || []).length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mb-2">
                              {lot.owner_ids.map(oid => {
                                const o = owners.find(x => x.id === oid);
                                return (
                                  <Badge key={oid} variant="outline" className="bg-emerald-50 border-emerald-300 text-emerald-800 gap-1 pl-2 pr-1 py-0.5" data-testid={`lot-${i}-owner-${oid}`}>
                                    <span className="text-[11px]">{o?.name || '(inconnu)'}</span>
                                    <button onClick={() => removeOwnerFromLot(i, oid)} className="text-emerald-500 hover:text-red-500"><X size={10} /></button>
                                  </Badge>
                                );
                              })}
                            </div>
                          )}
                          {/* Orphan badge : lot imported but owner not matched */}
                          {(lot.owner_ids || []).length === 0 && (lot._imported_owner_name || lot._imported_owner_aux) && (
                            <div className="mb-2 flex items-center gap-2 text-[11px]">
                              <Badge variant="outline" className="bg-amber-50 border-amber-300 text-amber-800 py-0.5" data-testid={`lot-${i}-orphan`}>
                                Non rattache: {lot._imported_owner_aux ? <span className="font-mono">{lot._imported_owner_aux}</span> : null} {lot._imported_owner_name}
                              </Badge>
                              <span className="text-slate-400">- importez les proprietaires pour auto-affecter</span>
                            </div>
                          )}
                          <div className="relative">
                            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                            <Input
                              value={ownerSearchByLot[i] || ''}
                              onChange={e => setOwnerSearchByLot({...ownerSearchByLot, [i]: e.target.value})}
                              onFocus={() => { setOwnerFocusLot(i); refreshOwnersIfStale(); }}
                              onBlur={() => { setTimeout(() => setOwnerFocusLot(prev => prev === i ? null : prev), 180); }}
                              placeholder="Cliquez pour voir la liste, ou tapez nom / email / VCS..."
                              className="pl-8 h-8 text-sm"
                              data-testid={`lot-${i}-owner-search`}
                            />
                            {getOwnerSuggestions(i).length > 0 && (
                              <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-60 overflow-y-auto" data-testid={`lot-${i}-suggestions`}>
                                {!(ownerSearchByLot[i] || '').trim() && (
                                  <div className="px-2 py-1 bg-slate-50 border-b border-slate-100 text-[10px] text-slate-500 uppercase tracking-wide">
                                    {getOwnerSuggestions(i).length} proprietaire(s) disponible(s) - tapez pour filtrer
                                  </div>
                                )}
                                {getOwnerSuggestions(i).map(o => (
                                  <button key={o.id} type="button" onMouseDown={e => e.preventDefault()} onClick={() => { addOwnerToLot(i, o.id); setOwnerSearchByLot({...ownerSearchByLot, [i]: ''}); }} className="w-full text-left px-2 py-1.5 hover:bg-[#022D52]/5 border-b last:border-b-0 border-slate-100 text-xs flex items-center justify-between" data-testid={`lot-${i}-suggestion-${o.id}`}>
                                    <span className="font-medium">{o.name}</span>
                                    <span className="flex items-center gap-2">
                                      {o.auxiliary_code && <span className="font-mono text-[9px] bg-slate-100 px-1 rounded text-slate-600">{o.auxiliary_code}</span>}
                                      {o.vcs_code && <span className="font-mono text-[9px] text-[#022D52]">{o.vcs_code}</span>}
                                    </span>
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                  </>
                )}
              </div>
            )}

            {/* STEP 3: Options + description + summary */}
            {(editing || step === 3) && <>
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Options</div>
              <div className="flex gap-6">
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <Checkbox checked={form.quarterly_closing} onCheckedChange={v => setForm({...form, quarterly_closing: v})} />
                  <span>Cloture trimestrielle</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <Checkbox checked={form.default_provisions} onCheckedChange={v => setForm({...form, default_provisions: v})} />
                  <span>Appels de provisions par defaut</span>
                </label>
              </div>
            </div>

            {/* Description */}
            <div><label className="form-label">Notes / Description</label><Textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})} rows={2} /></div>

            {/* Summary recap (only in wizard mode) */}
            {!editing && (
              <div className="bg-slate-50 border border-slate-200 rounded-md p-3 text-xs">
                <div className="font-semibold text-slate-700 mb-1">Recapitulatif</div>
                <ul className="space-y-0.5 text-slate-600">
                  <li><strong>Nom:</strong> {form.name || '(non defini)'}</li>
                  <li><strong>Adresse:</strong> {form.address}, {form.postal_code} {form.city}</li>
                  <li><strong>Comptes bancaires:</strong> {form.bank_accounts.length}</li>
                  <li><strong>Lots:</strong> {(form.lots || []).filter(l => l.number?.trim()).length}</li>
                  <li><strong>Total proprietaires affectes:</strong> {new Set(form.lots.flatMap(l => l.owner_ids || [])).size}</li>
                </ul>
                <div className="mt-2 text-[#01213e]">A la creation: 95 comptes PCMN belges + 10 categories documents seront automatiquement seedes.</div>
              </div>
            )}
            </>}

            {/* Navigation buttons */}
            <div className="flex gap-3 justify-between pt-2 border-t">
              {!editing && step > 1 ? (
                <Button variant="outline" onClick={() => setStep(step - 1)} data-testid="wizard-prev-btn">Precedent</Button>
              ) : <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>}
              <div className="flex gap-2">
                {!editing && step < 3 ? (
                  <Button onClick={() => setStep(step + 1)} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="wizard-next-btn" disabled={step === 1 && !form.name.trim()}>Suivant</Button>
                ) : (
                  <Button onClick={handleSave} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="copro-save-btn">{editing ? 'Modifier' : 'Creer l\'ACP'}</Button>
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* BULK IMPORT : LOTS via CSV */}
      <BulkCsvImportDialog
        open={bulkLotsOpen}
        onClose={() => setBulkLotsOpen(false)}
        title="Importer des lots depuis un CSV (Optipro / Sogis)"
        targetFields={[
          { key: 'number',      label: 'Numero du lot',  required: true, synonyms: ['lot', 'numero', 'n', 'reference'] },
          { key: 'description', label: 'Description',                    synonyms: ['libelle', 'designation'] },
          { key: 'lot_type',    label: 'Type',                            synonyms: ['type'] },
          { key: 'floor',       label: 'Etage',                           synonyms: ['etage', 'niveau'] },
          { key: 'area',        label: 'Surface',                         synonyms: ['surface', 'm2', 'metres'] },
          { key: 'quotity',     label: 'Quotite (millemes)',              synonyms: ['quotite', 'milliemes', 'tantieme'] },
          { key: 'owner_name',  label: 'Proprietaire (nom)',              synonyms: ['proprietaire', 'owner', 'copropriete', 'nom'] },
        ]}
        onImport={(rows) => {
          // Append parsed lots to form.lots state
          const newLots = rows.map(r => {
            // Try to match an existing owner by name (case-insensitive)
            const ownerName = (r.owner_name || '').toLowerCase().trim();
            const matched = ownerName ? owners.find(o => (o.name || '').toLowerCase().trim() === ownerName) : null;
            const lotType = (r.lot_type || '').toLowerCase();
            const typeMap = { 'appartement': 'apartment', 'apartment': 'apartment', 'parking': 'parking', 'cave': 'cave', 'commerce': 'commerce', 'bureau': 'bureau' };
            return {
              number: r.number,
              description: r.description || '',
              lot_type: typeMap[lotType] || 'apartment',
              floor: parseInt(r.floor) || 0,
              area: parseFloat((r.area || '').toString().replace(',', '.')) || 0,
              quotity: parseFloat((r.quotity || '').toString().replace(',', '.')) || 0,
              owner_id: matched?.id || '',
              owner_ids: matched ? [matched.id] : [],
              _imported_owner_name: r.owner_name || '',  // kept for later display
            };
          });
          setForm(f => ({ ...f, lots: [...(f.lots || []), ...newLots] }));
          const matchedCount = newLots.filter(l => l.owner_id).length;
          toast.success(`${newLots.length} lot(s) importes${matchedCount ? ` (${matchedCount} avec proprietaire pre-rattache)` : ''}`);
        }}
      />

      {/* BULK IMPORT : OWNERS via CSV - inserted directly via API */}
      <BulkCsvImportDialog
        open={bulkOwnersOpen}
        onClose={() => setBulkOwnersOpen(false)}
        title="Importer des proprietaires depuis un CSV (Optipro / Sogis)"
        targetFields={[
          { key: 'last_name',   label: 'Nom *',         required: true, synonyms: ['nom', 'lastname'] },
          { key: 'first_name',  label: 'Prenom',                         synonyms: ['prenom', 'firstname'] },
          { key: 'address',     label: 'Adresse',                        synonyms: ['adresse', 'rue'] },
          { key: 'postal_code', label: 'Code postal',                    synonyms: ['cp', 'codepostal', 'zip'] },
          { key: 'city',        label: 'Ville',                          synonyms: ['ville', 'commune', 'localite'] },
          { key: 'email',       label: 'Email',                          synonyms: ['email', 'mail', 'courriel'] },
          { key: 'phone',       label: 'Telephone',                      synonyms: ['tel', 'telephone', 'gsm'] },
          { key: 'iban',        label: 'IBAN',                           synonyms: ['iban', 'compte'] },
        ]}
        onImport={async (rows) => {
          if (importingOwners) return;
          setImportingOwners(true);
          let ok = 0, ko = 0;
          for (const r of rows) {
            try {
              const last = r.last_name || '';
              const first = r.first_name || '';
              const name = (last + ' ' + first).trim();
              if (!last) { ko++; continue; }
              await api.post('/owners', {
                first_name: first, last_name: last, name,
                address: r.address || '', postal_code: r.postal_code || '', city: r.city || '',
                country: 'Belgique', email: r.email || '', phone: r.phone || '', iban: r.iban || '',
              });
              ok++;
            } catch (_e) { ko++; }
          }
          // Reload owners so the lot autocomplete sees them
          let nextOwners = owners;
          try {
            const r = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
            nextOwners = r.data || [];
            setOwners(nextOwners);
          } catch (_e) {
            // ignore reload failure
          }
          // Retroactive auto-assignment : if lots were imported BEFORE owners,
          // match them now via the name kept in _imported_owner_name.
          let retroMatched = 0;
          setForm(f => {
            const lots = (f.lots || []).map(l => {
              if (l.owner_id) return l;
              const oname = (l._imported_owner_name || '').toLowerCase().trim();
              if (!oname) return l;
              const matched = nextOwners.find(o => {
                const n = (o.name || '').toLowerCase().trim();
                return n === oname || n.includes(oname) || oname.includes(n);
              });
              if (matched) {
                retroMatched++;
                return { ...l, owner_id: matched.id, owner_ids: [matched.id] };
              }
              return l;
            });
            return { ...f, lots };
          });
          toast.success(
            `${ok} proprietaire(s) crees${ko ? ` (${ko} echec(s))` : ''}` +
            (retroMatched ? ` - ${retroMatched} lot(s) auto-affectes` : '')
          );
          setImportingOwners(false);
        }}
      />

      {/* PDF IMPORT : OWNERS via PDF Optipro */}
      <PdfImportDialog
        open={pdfOwnersOpen}
        onClose={() => setPdfOwnersOpen(false)}
        title="Importer des proprietaires depuis un PDF (Optipro / Sogis)"
        kind="owners"
        dataKey="owners"
        hint="Liste des coproprietaires - export PDF Optipro/Sogis"
        columns={[
          { key: 'auxiliary_code', label: 'Code', monospace: true, width: 70 },
          { key: 'civility', label: 'Civilite', width: 100 },
          { key: 'last_name', label: 'Nom' },
          { key: 'first_name', label: 'Prenom' },
          { key: 'email', label: 'Email' },
          { key: 'phone', label: 'Telephone' },
          { key: 'vcs_code', label: 'VCS', monospace: true },
        ]}
        onImport={async (rows) => {
          if (importingOwners) return;
          setImportingOwners(true);
          const created = [];
          let ok = 0, ko = 0;
          for (const r of rows) {
            try {
              const last = r.last_name || '';
              const first = r.first_name || '';
              const civ = r.civility ? `${r.civility} ` : '';
              const name = (civ + last + ' ' + first).trim() || (r.name || '');
              if (!last && !r.name) { ko++; continue; }
              const resp = await api.post('/owners', {
                first_name: first,
                last_name: last || r.name,
                name,
                civility: r.civility || '',
                address: '',
                postal_code: '',
                city: '',
                country: 'Belgique',
                email: r.email || '',
                phone: r.phone || '',
                vcs_code: r.vcs_code || '',
                vcs_digits: r.vcs_digits || '',
                auxiliary_code: r.auxiliary_code || '',
                identifier: r.identifier || '',
              });
              if (resp?.data) created.push(resp.data);
              ok++;
            } catch (_e) { ko++; }
          }
          // Re-fetch the full list so the local state is consistent
          let nextOwners = owners;
          try {
            const rr = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
            nextOwners = rr.data || [];
            setOwners(nextOwners);
          } catch (_e) {
            // ignore
          }
          // RETROACTIVE LOT-OWNER AUTO-ASSIGNMENT : if lots were imported
          // BEFORE owners, match them now via _imported_owner_aux (C0XXX).
          let retroMatched = 0;
          setForm(f => {
            const lots = (f.lots || []).map(l => {
              if (l.owner_id) return l;  // already assigned
              const aux = (l._imported_owner_aux || '').trim().toUpperCase();
              const oname = (l._imported_owner_name || '').toLowerCase().trim();
              let matched = null;
              if (aux) {
                matched = nextOwners.find(o => (o.auxiliary_code || '').toUpperCase() === aux);
              }
              if (!matched && oname) {
                matched = nextOwners.find(o => {
                  const n = (o.name || '').toLowerCase().trim();
                  return n === oname || n.includes(oname) || oname.includes(n);
                });
              }
              if (matched) {
                retroMatched++;
                return { ...l, owner_id: matched.id, owner_ids: [matched.id] };
              }
              return l;
            });
            return { ...f, lots };
          });
          toast.success(
            `${ok} proprietaire(s) crees${ko ? ` (${ko} echec(s))` : ''}` +
            (retroMatched ? ` - ${retroMatched} lot(s) auto-affectes` : '')
          );
          setImportingOwners(false);
        }}
      />

      {/* PDF IMPORT : LOTS via PDF Optipro */}
      <PdfImportDialog
        open={pdfLotsOpen}
        onClose={() => setPdfLotsOpen(false)}
        title="Importer des lots depuis un PDF (Optipro / Sogis)"
        kind="lots"
        dataKey="lots"
        hint="Liste des lots - export PDF Optipro/Sogis"
        columns={[
          { key: 'code', label: 'Code', monospace: true, width: 70 },
          { key: 'reference', label: 'Reference', width: 100 },
          { key: 'nature', label: 'Nature' },
          { key: 'batiment', label: 'Batiment' },
          { key: 'quotities_value', label: 'Quotites' },
          { key: 'owner_auxiliary_code', label: 'Code prop.', monospace: true },
          { key: 'owner_name', label: 'Proprietaire' },
        ]}
        onImport={async (rows) => {
          // Refetch owners JUST BEFORE matching to ensure freshly-imported
          // owners (from a prior PDF/CSV import) are visible.
          let availableOwners = owners;
          try {
            const rr = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
            availableOwners = rr.data || [];
            setOwners(availableOwners);
          } catch (_e) {
            // use local cache
          }
          const natureMap = (n) => {
            const u = (n || '').toUpperCase();
            if (u.includes('APPART')) return 'apartment';
            if (u.includes('GARAGE') || u.includes('PARKING')) return 'parking';
            if (u.includes('CAVE')) return 'cave';
            if (u.includes('COMMERCE')) return 'commerce';
            if (u.includes('BUREAU')) return 'bureau';
            return 'autre';
          };
          const newLots = rows.map((r) => {
            const aux = (r.owner_auxiliary_code || '').trim().toUpperCase();
            const ownerName = (r.owner_name || '').toLowerCase().trim();
            let matched = null;
            if (aux) {
              matched = availableOwners.find((o) => (o.auxiliary_code || '').toUpperCase() === aux);
            }
            if (!matched && ownerName) {
              matched = availableOwners.find((o) => {
                const n = (o.name || '').toLowerCase().trim();
                return n === ownerName || n.includes(ownerName) || ownerName.includes(n);
              });
            }
            return {
              number: r.reference || r.code,
              description: r.nature || '',
              lot_type: natureMap(r.nature),
              floor: 0,
              area: 0,
              quotity: parseFloat(r.quotities_value || 0) || 0,
              owner_id: matched?.id || '',
              owner_ids: matched ? [matched.id] : [],
              _imported_owner_name: r.owner_name || '',
              _imported_owner_aux: r.owner_auxiliary_code || '',
            };
          });
          setForm((f) => ({ ...f, lots: [...(f.lots || []), ...newLots] }));
          const matchedCount = newLots.filter((l) => l.owner_id).length;
          toast.success(
            `${newLots.length} lot(s) importes` +
            (matchedCount ? ` - ${matchedCount} auto-affectes a leurs proprietaires` : ' (importez d\'abord les proprietaires pour l\'auto-affectation)')
          );
        }}
      />
    </div>
  );
}
