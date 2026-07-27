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
import { Plus, Pencil, Trash2, Home, Search, Archive, RotateCcw, Landmark, PlusCircle, X, Eraser, Wand2, Upload, UserPlus, FileText, Download, Image as ImageIcon, CheckCircle2, ClipboardCheck, AlertTriangle, Users } from 'lucide-react';
import BulkCsvImportDialog from '@/components/BulkCsvImportDialog';
import PdfImportDialog from '@/components/PdfImportDialog';
import ImportSummary from '@/components/ImportSummary';
import { useDirtyGuard } from '@/hooks/useDirtyGuard';

const emptyBank = { iban: '', bic: '', account_type: 'vue', is_default: false, label: '' };
const emptyLot = { number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, parent_lot_number: '' };
const emptyForm = {
  name: '', bce: '', address: '', postal_code: '', city: '', country: 'Belgique',
  description: '', bank_accounts: [], quarterly_closing: true, default_provisions: true,
  lots: [],
  // iter90gg : periode de l'exercice fiscal courant (obligatoire au Step 2)
  fy_start: '', fy_end: '', fy_name: '',
  // iter90gg : au Step 3, le syndic declare s'il y a eu des ventes intra-FY
  _has_intra_fy_sales: false,
  // iter90kz : mode promoteur
  _is_promoter: false,
  _promoter_owner_id: '',
};

export default function CoproprietesPage() {
  const { user, isAdmin, isManager, isSuperadmin } = useAuth();
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
  // iter90gk : dialog interactif pour les homonymes owner detectes en batch
  // (PDF Optipro). Structure : {rows: [{row, message}], resolved: {rowIdx: 'force'|'skip'}}
  const [ownerHomonymsDialog, setOwnerHomonymsDialog] = useState(null);
  const [step, setStep] = useState(1);
  // iter93e : sous-etapes sequentielles pour la CREATION d'ACP (Step 2).
  // Ordre : fy (periode) -> owners (import proprios) -> promoter (oui/non) ->
  // lots (import lots) -> assign (revue + validation en un clic).
  // L'edition d'ACP existante ne l'utilise PAS (garde l'ancien flow libre).
  const [substep, setSubstep] = useState('fy');
  // iter93f : proprios crees/reutilises pendant la session de creation en cours.
  // Ils DOIVENT rester visibles dans le recap meme s'ils sont deja lies a
  // d'autres ACPs (donc filtres par `unassigned_only=true`).
  const [sessionOwners, setSessionOwners] = useState([]);
  const [ownerSearchByLot, setOwnerSearchByLot] = useState({});  // {lotIdx: 'query'}
  const [ownerFocusLot, setOwnerFocusLot] = useState(null);  // lotIdx currently focused or null
  // iter90if : recap des imports par ACP (id -> summary)
  const [importSummaries, setImportSummaries] = useState({});
  const [summaryDialog, setSummaryDialog] = useState(null);  // {acp, summary} or null
  const dirty = useDirtyGuard(form, dialogOpen);

  const load = useCallback(async () => {
    const { data } = await api.get('/coproprietes', { params: { show_archived: showArchived } });
    setCoproprietes(data);
    // Fetch import summaries in parallel (best-effort, ignore failures)
    Promise.all(
      data.filter(c => c.status !== 'archived').map(c =>
        api.get(`/import-wizard/coproprietes/${c.id}/import-summary`)
          .then(r => [c.id, r.data])
          .catch(() => [c.id, null])
      )
    ).then(entries => {
      const map = {};
      entries.forEach(([id, s]) => { if (s) map[id] = s; });
      setImportSummaries(map);
    });
  }, [showArchived]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!dialogOpen) return;
    // iter93d/93f : sur la CREATION on affiche uniquement les orphelins
    // (unassigned_only), MAIS on merge aussi les sessionOwners qui sont les
    // proprios crees/reutilises pendant l'import en cours (ils peuvent
    // etre deja rattaches a d'autres ACPs). En mode edition, on garde le
    // comportement historique (tous les proprios du syndic + orphelins).
    const params = editing
      ? { include_unassigned: true, copropriete_id: 'all' }
      : { unassigned_only: true };
    api.get('/owners', { params }).then(r => {
      const fetched = r.data || [];
      if (editing) {
        setOwners(fetched);
      } else {
        const byId = new Map();
        for (const o of fetched) if (o?.id) byId.set(o.id, o);
        for (const o of sessionOwners) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
        setOwners(Array.from(byId.values()));
      }
    }).catch(() => {});
  }, [dialogOpen, editing, sessionOwners]);

  const filtered = coproprietes.filter(c => c.name.toLowerCase().includes(search.toLowerCase()) || (c.reference || '').toLowerCase().includes(search.toLowerCase()) || (c.bce || '').includes(search));

  const openCreate = () => { setEditing(null); setForm({...emptyForm, bank_accounts: [], lots: []}); setStep(1); setSubstep('fy'); setSessionOwners([]); setDialogOpen(true); };
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
    // iter90gh : dedup UI par nom normalise. Si plusieurs fiches partagent
    // le meme nom (cas legacy Optipro imports repetes), on ne garde QUE
    // celle qui a le plus d'attributs "identifiants" (VCS + auxiliary +
    // email). Le tri prefere : vcs > aux > email > id lexico (stable).
    const _score = (o) => {
      let s = 0;
      if ((o.vcs_code || '').trim()) s += 4;
      if ((o.auxiliary_code || '').trim()) s += 2;
      if ((o.email || '').trim()) s += 1;
      return s;
    };
    const _norm = (s) => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');
    const bestByName = new Map();
    for (const o of available) {
      const key = _norm(o.name);
      if (!key) continue;
      const cur = bestByName.get(key);
      if (!cur || _score(o) > _score(cur)) bestByName.set(key, o);
    }
    const deduped = Array.from(bestByName.values());
    if (!q) {
      if (ownerFocusLot === i) return deduped.slice(0, 50);
      return [];
    }
    return deduped.filter(o =>
      (o.name || '').toLowerCase().includes(q) ||
      (o.email || '').toLowerCase().includes(q) ||
      (o.auxiliary_code || '').toLowerCase().includes(q) ||
      (o.vcs_code || '').includes(q)
    ).slice(0, 12);
  };

  // Refetch owners on focus to ensure freshly-imported owners are visible.
  // iter93d/93f : sur la creation, on merge fetch(orphelins) + sessionOwners
  // (proprios importes lors de cette creation, potentiellement deja rattaches
  // a d'autres ACPs); sur l'edition, on garde le comportement historique.
  const refreshOwnersIfStale = async () => {
    try {
      const params = editing
        ? { include_unassigned: true, copropriete_id: 'all' }
        : { unassigned_only: true };
      const r = await api.get('/owners', { params });
      const fetched = r.data || [];
      if (editing) {
        setOwners(fetched);
      } else {
        const byId = new Map();
        for (const o of fetched) if (o?.id) byId.set(o.id, o);
        for (const o of sessionOwners) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
        setOwners(Array.from(byId.values()));
      }
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
        // iter90gg BLOC A : validation - la periode d'exercice est obligatoire
        if (!form.fy_start || !form.fy_end) {
          toast.error('Periode de l\'exercice fiscal requise (Etape 2)');
          setStep(2);
          return;
        }
        // Nettoie les champs UI-only avant envoi
        const payload = { ...form };
        const hadIntraFySales = !!payload._has_intra_fy_sales;
        const promoterOwnerId = payload._is_promoter ? (payload._promoter_owner_id || '') : '';
        delete payload._has_intra_fy_sales;
        delete payload._is_promoter;
        delete payload._promoter_owner_id;
        // iter90kz : envoyer TOUS les owner_ids connus (pas seulement ceux des lots)
        // pour que create_copropriete les rattache a l'ACP + cree leurs comptes tiers.
        payload.owner_ids_to_link = owners.map(o => o.id);
        // iter90kz : mode promoteur
        payload.promoter_owner_id = promoterOwnerId;
        // iter92b : persiste sur l'ACP le flag de ventes intra-exercice pour
        // afficher un banner permanent sur /lots (survit aux refresh).
        payload.has_intra_fy_sales = hadIntraFySales;
        const r = await api.post('/coproprietes', payload);
        const newCopro = r.data;
        const nLots = (form.lots || []).filter(l => l.number && l.number.trim()).length;
        toast.success(nLots > 0 ? `ACP creee avec ${nLots} lot(s)` : 'Copropriete creee');
        // iter92c : afficher un message informatif discret si des owners
        // n'ont pas ete rattaches (leur code auxiliaire est deja associe a
        // un autre owner ayant deja cette combinaison en base - typiquement
        // parce que l'import a ete rejoue). Les LOTS eux-memes sont crees
        // correctement, seule l'association owner<->ACP est skippee.
        if (Array.isArray(newCopro?._warning_skipped_owners) && newCopro._warning_skipped_owners.length > 0) {
          const n = newCopro._warning_skipped_owners.length;
          toast.info(
            `Info : ${n} proprietaire(s) doublon(s) detecte(s) - deja rattache(s) via un import precedent. Aucune action requise, vos ${nLots} lot(s) sont crees.`,
            { duration: 8000 }
          );
        }
        setDialogOpen(false); load();

        // iter90gi : ne PLUS skipper le wizard. Toujours proposer l'import
        // Optipro (fournisseurs, natures, budget, factures, journaux, OD).
        // Si le syndic a declare des ventes intra-exercice, on passe le flag
        // `pending_mutations=1` : le wizard le propagera a /lots a la fin.
        setTimeout(() => {
          if (!newCopro?.id) return;
          const msg = hadIntraFySales
            ? `L'ACP "${newCopro.name}" a ete creee avec ${nLots} lot(s).\n\n`
              + `Vous avez declare des VENTES intra-exercice. Le workflow recommande :\n\n`
              + `[OK] : Lancer le wizard d'import Optipro (fournisseurs, natures, budget, factures, journaux...). Les mutations seront saisies A LA FIN.\n`
              + `[Annuler] : Passer directement a la saisie des mutations (les autres imports pourront etre faits plus tard).`
            : `L'ACP "${newCopro.name}" a ete creee.\n\n`
              + `S'agit-il d'une REPRISE depuis Optipro / Sogis ?\n\n`
              + `[OK] : Lancer le wizard d'import (fournisseurs, natures, budget, factures...).\n`
              + `[Annuler] : Continuer normalement.`;
          const goWizard = window.confirm(msg);
          if (goWizard) {
            const suffix = hadIntraFySales ? '&pending_mutations=1' : '';
            navigate(`/import-wizard?copropriete_id=${newCopro.id}${suffix}`);
          } else if (hadIntraFySales) {
            // Fallback historique : le syndic veut ne saisir que les mutations
            navigate(`/lots?post_import_mutations=1&copropriete_id=${newCopro.id}`);
          }
        }, 200);
      }
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const handleDelete = async (id) => {
    // iter92g : message d'avertissement explicite - la suppression cascade
    // est autorisee (superadmin + whitelist) mais NON recommandee. L'archivage
    // preserve les donnees pour l'obligation legale (10 ans art. III.86 CDE).
    const acp = coproprietes.find(c => c.id === id);
    const nom = acp?.name || 'cette ACP';
    const msg = `⚠ Suppression NON RECOMMANDEE\n\nVous allez supprimer definitivement "${nom}" avec cascade complete :\n- Tous les lots, factures, ecritures comptables\n- L'historique bancaire et le grand livre\n- Les documents et communications\n\nCette action est IRREVERSIBLE et enfreint l'obligation legale de conservation 10 ans (art. III.86 CDE).\n\nPreferez l'archivage (bouton Archive) qui conserve tout en masquant l'ACP.\n\nContinuer la suppression malgre tout ?`;
    if (!window.confirm(msg)) return;
    try {
      await api.delete(`/coproprietes/${id}`);
      toast.success('ACP supprimee (cascade)');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
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

  // iter90hz : Lie explicitement le syndic connecte a l'ACP -> son logo et
  // ses mentions legales seront utilises dans TOUS les PDFs et emails de
  // cette ACP (source de verite deterministe, remplace l'heuristique).
  const handleLinkSyndicConfig = async (c) => {
    const msg = `Lier VOTRE logo et VOTRE identite syndic (mentions legales, cabinet, IPI, TVA)\n`
              + `a l'ACP "${c.name}" ?\n\n`
              + `Tous les documents (PDF, emails, rappels, decomptes) generes pour cette ACP\n`
              + `utiliseront desormais votre configuration syndic.`;
    if (!window.confirm(msg)) return;
    try {
      const { data } = await api.post(`/coproprietes/${c.id}/link-syndic-config`);
      if (data?.has_logo) {
        toast.success(`Logo et identite syndic "${data.legal_name || ''}" lies a "${c.name}"`);
      } else {
        toast.info(
          `L'ACP est liee a votre profil syndic, mais aucun logo n'est encore uploade. ` +
          `Rendez-vous dans "Profil" pour ajouter votre logo.`,
          { duration: 6000 },
        );
      }
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur : impossible de lier la config');
    }
  };

  const isLinkedToMe = (c) => {
    if (!user || !user.id) return false;
    return String(c.syndic_user_id || '') === String(user.id);
  };

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
                <TableCell><div className="flex flex-col gap-1">
                  <Badge variant="outline" className={c.status === 'archived' ? 'bg-slate-100 text-slate-500' : 'bg-green-50 text-green-700 border-green-200'}>{c.status === 'archived' ? 'Archive' : 'Active'}</Badge>
                  {c.status !== 'archived' && importSummaries[c.id] && (
                    importSummaries[c.id].is_complete ? (
                      <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200 text-[10px] gap-1" data-testid={`import-status-${c.id}`}>
                        <CheckCircle2 size={10} /> Import complet
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200 text-[10px] gap-1" data-testid={`import-status-${c.id}`}>
                        <AlertTriangle size={10} /> Import incomplet
                      </Badge>
                    )
                  )}
                </div></TableCell>
                <TableCell><div className="flex gap-0">
                  {isManager && <Button variant="outline" size="sm" onClick={() => openEdit(c)} title="Modifier l'ACP (nom, adresse, banques, parametres)" data-testid={`edit-copro-${c.id}`} className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10 mr-1"><Pencil size={13} className="mr-1" /> Modifier</Button>}
                  {isManager && importSummaries[c.id] && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSummaryDialog({ acp: c, summary: importSummaries[c.id] })}
                      title="Voir le recap de l'import et reprendre si besoin"
                      data-testid={`import-recap-${c.id}`}
                      className="text-slate-700 mr-1"
                    >
                      <ClipboardCheck size={13} className="mr-1" /> Recap
                    </Button>
                  )}
                  {isManager && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleLinkSyndicConfig(c)}
                      title={isLinkedToMe(c)
                        ? "ACP deja liee a votre profil syndic - cliquez pour re-synchroniser"
                        : "Lier mon logo, mon cabinet, mes mentions legales a cette ACP"}
                      data-testid={`link-syndic-${c.id}`}
                      className={`mr-1 ${isLinkedToMe(c)
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-300 hover:bg-emerald-100'
                        : 'text-amber-700 border-amber-300 hover:bg-amber-50'}`}
                    >
                      {isLinkedToMe(c) ? <CheckCircle2 size={13} className="mr-1" /> : <ImageIcon size={13} className="mr-1" />}
                      {isLinkedToMe(c) ? 'Logo lie' : 'Lier mon logo'}
                    </Button>
                  )}
                  {isSuperadmin && <Button variant="ghost" size="sm" onClick={() => handleCleanupOrphans(c)} className="text-[#022D52] hover:text-[#01213e]" title="Nettoyer les ecritures orphelines (re-synchroniser bilan/grand livre)" data-testid={`cleanup-orphans-${c.id}`}><Wand2 size={13} /></Button>}
                  {isSuperadmin && <Button variant="outline" size="sm" onClick={() => handleResetData(c)} className="text-amber-700 border-amber-300 hover:bg-amber-50 mr-1" title="Vider TOUTES les donnees comptables (factures, ecritures, exercices, budgets...)" data-testid={`reset-data-${c.id}`}><Eraser size={13} className="mr-1" /> Vider</Button>}
                  {isManager && c.status !== 'archived' && <Button variant="ghost" size="sm" onClick={() => handleArchive(c.id)} className="text-orange-500" title="Archiver"><Archive size={13} /></Button>}
                  {isManager && c.status === 'archived' && <Button variant="ghost" size="sm" onClick={() => handleUnarchive(c.id)} className="text-green-600" title="Reactiver"><RotateCcw size={13} /></Button>}
                  {isManager && <Button variant="ghost" size="sm" onClick={() => handleDownloadArchive(c)} className="text-[#022D52]" title="Telecharger archive ZIP complete par annee" data-testid={`archive-dl-${c.id}`}><Download size={13} /></Button>}
                  {/* iter92g : suppression uniquement pour superadmin OU email whitelist. Le syndic doit archiver. */}
                  {(isSuperadmin || (user?.email || '').toLowerCase() === 'info@nextgecopro.be') && (
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-400 hover:text-red-600" title="Supprimer definitivement (NON RECOMMANDE - preferer Archiver)" data-testid={`delete-copro-${c.id}`}><Trash2 size={13} /></Button>
                  )}
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* iter90if : Dialog recap import */}
      <Dialog open={!!summaryDialog} onOpenChange={(open) => !open && setSummaryDialog(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="import-summary-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              Recap d&apos;import - {summaryDialog?.acp?.name}
            </DialogTitle>
            <p className="text-xs text-slate-500 font-mono">{summaryDialog?.acp?.reference}</p>
          </DialogHeader>
          {summaryDialog && <ImportSummary summary={summaryDialog.summary} />}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2 mt-4 pt-3 border-t border-slate-200">
            <Button variant="outline" onClick={() => setSummaryDialog(null)} data-testid="close-summary-btn">
              Fermer
            </Button>
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
              {/* iter92c : raccourci "Creer les lots" quand aucun lot */}
              {summaryDialog && (summaryDialog.summary?.counts?.lots || 0) === 0 && (
                <Button
                  variant="outline"
                  onClick={() => {
                    localStorage.setItem('selectedCopro', summaryDialog.acp.id);
                    setSummaryDialog(null);
                    navigate(`/lots?copropriete_id=${summaryDialog.acp.id}`);
                  }}
                  className="border-orange-400 text-orange-700 hover:bg-orange-50"
                  data-testid="goto-lots-from-recap-btn"
                >
                  <Users size={13} className="mr-1" /> Creer les lots
                </Button>
              )}
              {summaryDialog && !summaryDialog.summary.is_complete && (
                <Button
                  onClick={() => {
                    localStorage.setItem('selectedCopro', summaryDialog.acp.id);
                    navigate(`/import-wizard?copropriete_id=${summaryDialog.acp.id}`);
                  }}
                  className="bg-[#022D52] hover:bg-[#01213e] text-white"
                  data-testid="resume-import-btn"
                >
                  <RotateCcw size={13} className="mr-1" /> Reprendre l&apos;import
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen} hasUnsavedChanges={dirty}>
        <DialogContent
          className="max-w-3xl max-h-[85vh] overflow-y-auto"
          data-testid="copro-dialog"
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
                <div><label className="form-label">Nom de l&apos;ACP *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="copro-name-input" /></div>
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

            {/* STEP 2: sequential sub-wizard (iter93e)
                fy -> owners -> promoter -> lots -> assign
                Le formulaire libre historique reste dispo en mode EDITION.
            */}
            {!editing && step === 2 && (
              <div>
                {/* Sub-step breadcrumb */}
                <div className="flex items-center justify-center gap-2 mb-4 text-[10px] font-semibold uppercase tracking-wide" data-testid="substep-breadcrumb">
                  {[
                    { k: 'fy', label: '1. Periode' },
                    { k: 'owners', label: '2. Proprietaires' },
                    { k: 'promoter', label: '3. Promoteur' },
                    { k: 'lots', label: '4. Lots' },
                    { k: 'assign', label: '5. Affectation' },
                    { k: 'mutations', label: '6. Mutations' },
                  ].map((s, i, arr) => {
                    const order = ['fy','owners','promoter','lots','assign','mutations'];
                    const currentIdx = order.indexOf(substep);
                    const thisIdx = order.indexOf(s.k);
                    const active = thisIdx === currentIdx;
                    const done = thisIdx < currentIdx;
                    return (
                      <div key={s.k} className="flex items-center gap-1">
                        <span className={`px-2 py-0.5 rounded-full ${active ? 'bg-[#022D52] text-white' : done ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'}`} data-testid={`substep-crumb-${s.k}`}>
                          {done ? '✓ ' : ''}{s.label}
                        </span>
                        {i < arr.length - 1 && <span className={done ? 'text-emerald-400' : 'text-slate-300'}>→</span>}
                      </div>
                    );
                  })}
                </div>

                {/* SUB-STEP 1: Periode comptable */}
                {substep === 'fy' && (
                  <div className="bg-amber-50 border-2 border-amber-300 rounded-lg p-4" data-testid="fy-period-block">
                    <div className="text-sm font-bold text-amber-900 uppercase tracking-wide mb-1">Etape 1/5 : Periode de l&apos;exercice comptable</div>
                    <p className="text-xs text-amber-800 mb-3">
                      Les proprietaires et les lots que vous allez importer correspondront a l&apos;etat de l&apos;ACP <strong>au 1er jour de cet exercice</strong>. Les ventes survenues APRES seront saisies via le module Mutations.
                    </p>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="form-label text-amber-900">Debut d&apos;exercice *</label>
                        <Input type="date" value={form.fy_start} onChange={e => setForm({...form, fy_start: e.target.value})} data-testid="fy-start-input" className="h-9" />
                      </div>
                      <div>
                        <label className="form-label text-amber-900">Fin d&apos;exercice *</label>
                        <Input type="date" value={form.fy_end} onChange={e => setForm({...form, fy_end: e.target.value})} data-testid="fy-end-input" className="h-9" />
                      </div>
                      <div>
                        <label className="form-label text-amber-900">Nom exercice</label>
                        <Input value={form.fy_name} onChange={e => setForm({...form, fy_name: e.target.value})} placeholder="auto (2025-2026)" data-testid="fy-name-input" className="h-9" />
                      </div>
                    </div>
                  </div>
                )}

                {/* SUB-STEP 2: Import des proprietaires */}
                {substep === 'owners' && (
                  <div className="bg-emerald-50 border-2 border-emerald-300 rounded-lg p-4" data-testid="owners-import-block">
                    <div className="text-sm font-bold text-emerald-900 uppercase tracking-wide mb-1">Etape 2/5 : Importer la liste des proprietaires</div>
                    <p className="text-xs text-emerald-800 mb-3">
                      Importez la <em>Liste des coproprietaires</em> depuis un export PDF (Optipro/Sogis) ou CSV. Les proprietaires seront crees dans la base de donnees de votre syndic et lies a cette ACP.
                    </p>
                    <div className="flex flex-wrap gap-2 mb-3">
                      <Button variant="outline" size="sm" onClick={() => setPdfOwnersOpen(true)} className="border-emerald-400 text-emerald-800 hover:bg-emerald-100" data-testid="import-owners-pdf-btn">
                        <FileText size={14} className="mr-1" /> Import PDF (recommande)
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setBulkOwnersOpen(true)} className="border-emerald-400 text-emerald-800 hover:bg-emerald-100" data-testid="import-owners-csv-btn">
                        <UserPlus size={14} className="mr-1" /> Import CSV
                      </Button>
                    </div>
                    <div className="bg-white rounded-md border border-emerald-200 p-3" data-testid="owners-list-recap">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-semibold text-slate-700">{owners.length} proprietaire(s) charge(s)</span>
                        {owners.length === 0 && (
                          <span className="text-[11px] text-amber-700 italic">Aucun - importez un PDF/CSV pour continuer</span>
                        )}
                      </div>
                      {owners.length > 0 && (
                        <div className="max-h-64 overflow-y-auto text-xs">
                          <table className="w-full">
                            <thead className="text-[10px] uppercase text-slate-500 border-b">
                              <tr>
                                <th className="text-left py-1">Nom</th>
                                <th className="text-left py-1">Code aux.</th>
                                <th className="text-left py-1">Email</th>
                              </tr>
                            </thead>
                            <tbody>
                              {owners.slice(0, 100).map(o => (
                                <tr key={o.id} className="border-b border-slate-100" data-testid={`owner-recap-${o.id}`}>
                                  <td className="py-1 font-medium">{o.name || `${o.last_name || ''} ${o.first_name || ''}`.trim()}</td>
                                  <td className="py-1 font-mono text-[10px] text-slate-500">{o.auxiliary_code || '-'}</td>
                                  <td className="py-1 text-slate-500">{o.email || '-'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          {owners.length > 100 && (
                            <div className="text-[10px] text-slate-400 italic mt-2 text-center">... et {owners.length - 100} autres</div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* SUB-STEP 3: Promoteur */}
                {substep === 'promoter' && (
                  <div className="bg-orange-50 border-2 border-orange-300 rounded-lg p-4" data-testid="promoter-block">
                    <div className="text-sm font-bold text-orange-900 uppercase tracking-wide mb-1">Etape 3/5 : Promoteur immobilier ?</div>
                    <p className="text-xs text-orange-800 mb-3">
                      Si tous les lots appartiennent initialement a un promoteur (ex: MATEXI), selectionnez-le. Tous les lots lui seront affectes au <strong>{form.fy_start}</strong>. Les ventes individuelles se feront ensuite via Mutations.
                    </p>
                    <div className="flex gap-4 mb-3">
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input type="radio" name="is_promoter" checked={!form._is_promoter} onChange={() => setForm({...form, _is_promoter: false, _promoter_owner_id: ''})} data-testid="promoter-no" />
                        <span>Non, pas de promoteur</span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input type="radio" name="is_promoter" checked={form._is_promoter} onChange={() => setForm({...form, _is_promoter: true})} data-testid="promoter-yes" />
                        <span>Oui, c&apos;est un promoteur</span>
                      </label>
                    </div>
                    {form._is_promoter && (
                      <div data-testid="promoter-picker" className="space-y-2">
                        <label className="text-xs font-medium text-slate-700 block">Selectionnez le promoteur parmi les proprietaires importes :</label>
                        {owners.length > 0 ? (() => {
                          const _norm = s => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');
                          const _score = o => {
                            let s = 0;
                            if ((o.vcs_code || '').trim()) s += 4;
                            if ((o.auxiliary_code || '').trim()) s += 2;
                            if ((o.email || '').trim()) s += 1;
                            return s;
                          };
                          const bestByName = new Map();
                          for (const o of owners) {
                            const key = _norm(o.name);
                            if (!key) continue;
                            const cur = bestByName.get(key);
                            if (!cur || _score(o) > _score(cur)) bestByName.set(key, o);
                          }
                          const deduped = Array.from(bestByName.values()).sort((a, b) =>
                            (a.name || '').localeCompare(b.name || '')
                          );
                          return (
                            <select
                              className="w-full h-9 text-sm border rounded-md px-2 bg-white"
                              data-testid="promoter-select"
                              value={form._promoter_owner_id}
                              onChange={e => setForm({...form, _promoter_owner_id: e.target.value})}
                            >
                              <option value="">-- Choisir un proprietaire --</option>
                              {deduped.map(o => (
                                <option key={o.id} value={o.id}>
                                  {o.name || `${o.last_name || ''} ${o.first_name || ''}`.trim()}
                                  {o.auxiliary_code ? ` (${o.auxiliary_code})` : ''}
                                </option>
                              ))}
                            </select>
                          );
                        })() : (
                          <p className="text-xs text-orange-700 italic">Aucun proprietaire disponible - revenez a l&apos;etape precedente pour en importer.</p>
                        )}
                        <div className="flex items-center gap-2 mt-2">
                          <span className="text-[10px] text-slate-500">Le promoteur n&apos;est pas dans la liste ?</span>
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 text-[11px] border-orange-400 text-orange-700 hover:bg-orange-100"
                            data-testid="create-promoter-inline-btn"
                            onClick={async () => {
                              const name = window.prompt('Nom du promoteur (societe) :');
                              if (!name || !name.trim()) return;
                              try {
                                const { data } = await api.post('/owners?reuse_on_duplicate=true', {
                                  name: name.trim(), first_name: '', last_name: '',
                                  address: '', postal_code: '', city: '', country: 'Belgique',
                                  email: '', phone: '', auxiliary_code: '',
                                });
                                toast.success(`Promoteur "${data.name}" cree`);
                                await refreshOwnersIfStale();
                                setForm(f => ({...f, _promoter_owner_id: data.id}));
                              } catch (err) {
                                toast.error(err.response?.data?.detail || 'Erreur creation promoteur');
                              }
                            }}
                          >
                            <Plus size={11} className="mr-1" /> Creer un nouveau promoteur
                          </Button>
                        </div>
                        {form._promoter_owner_id && (
                          <p className="text-xs text-green-700 mt-2 flex items-center gap-1">
                            <CheckCircle2 className="w-3 h-3" />
                            Tous les lots seront affectes a ce promoteur au {form.fy_start}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* SUB-STEP 4: Import des lots */}
                {substep === 'lots' && (
                  <div className="bg-blue-50 border-2 border-blue-300 rounded-lg p-4" data-testid="lots-import-block">
                    <div className="text-sm font-bold text-[#01213e] uppercase tracking-wide mb-1">Etape 4/5 : Importer les lots</div>
                    <p className="text-xs text-[#01213e] mb-3">
                      Importez la <em>Liste des lots</em> depuis un export PDF Optipro (recommande), CSV, ou ajoutez-les manuellement. Les lots seront ensuite automatiquement pre-affectes a leurs proprietaires importes a l&apos;etape 2.
                    </p>
                    <div className="flex flex-wrap gap-2 mb-3">
                      <Button variant="outline" size="sm" onClick={() => setPdfLotsOpen(true)} className="border-blue-400 text-[#01213e] hover:bg-blue-100" data-testid="import-lots-pdf-btn">
                        <FileText size={14} className="mr-1" /> Import PDF (recommande)
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setBulkLotsOpen(true)} className="border-blue-400 text-[#01213e] hover:bg-blue-100" data-testid="import-lots-csv-btn">
                        <Upload size={14} className="mr-1" /> Import CSV
                      </Button>
                      <Button variant="outline" size="sm" onClick={addLot} data-testid="add-lot-btn">
                        <PlusCircle size={14} className="mr-1" /> Ajouter manuellement
                      </Button>
                    </div>
                    <div className="bg-white rounded-md border border-blue-200 p-3" data-testid="lots-list-recap">
                      <span className="text-xs font-semibold text-slate-700">
                        {(form.lots || []).length} lot(s) charge(s)
                      </span>
                      {(form.lots || []).length === 0 && (
                        <span className="ml-2 text-[11px] text-amber-700 italic">Aucun - importez un PDF/CSV ou ajoutez manuellement</span>
                      )}
                    </div>
                  </div>
                )}

                {/* SUB-STEP 5: Revue + validation des affectations */}
                {substep === 'assign' && (
                  <div className="bg-purple-50 border-2 border-purple-300 rounded-lg p-4" data-testid="assign-review-block">
                    <div className="text-sm font-bold text-purple-900 uppercase tracking-wide mb-1">Etape 5/5 : Revue des affectations</div>
                    <p className="text-xs text-purple-800 mb-3">
                      Verifiez les affectations proprietaire &harr; lot ci-dessous, puis cliquez sur <strong>&quot;Valider toutes les affectations&quot;</strong>. Vous pouvez ajuster individuellement chaque lot avant de valider.
                    </p>
                    {(() => {
                      const total = (form.lots || []).length;
                      const matched = (form.lots || []).filter(l => (l.owner_ids || []).length > 0).length;
                      const orphans = (form.lots || []).filter(l => (l.owner_ids || []).length === 0 && (l._imported_owner_name || l._imported_owner_aux)).length;
                      const empty = total - matched - orphans;
                      return (
                        <div className="mb-3 flex items-center gap-3 bg-white/70 border border-purple-200 rounded-md px-3 py-2 text-[11px]" data-testid="lots-summary">
                          <span><strong>{total}</strong> lot(s)</span>
                          {form._is_promoter && form._promoter_owner_id ? (
                            <span className="text-orange-700 font-medium">
                              <strong>{total}</strong> affectes au promoteur au {form.fy_start}
                            </span>
                          ) : (
                            <>
                              {matched > 0 && <span className="text-emerald-700"><strong>{matched}</strong> auto-affectes</span>}
                              {orphans > 0 && <span className="text-amber-700"><strong>{orphans}</strong> orphelins</span>}
                              {empty > 0 && <span className="text-slate-500"><strong>{empty}</strong> sans contrepartie</span>}
                            </>
                          )}
                          {orphans > 0 && !form._is_promoter && (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 px-2 text-[11px] border-amber-400 text-amber-700 hover:bg-amber-50 ml-auto"
                              data-testid="lots-retry-match-btn"
                              onClick={async () => {
                                let nextOwners = owners;
                                try {
                                  const r = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
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
                                else toast.info('Aucun nouveau rattachement');
                              }}
                            >
                              Reessayer auto-affectation
                            </Button>
                          )}
                        </div>
                      );
                    })()}
                    <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
                      {(form.lots || []).map((lot, i) => (
                        <div key={i} className="border rounded-md p-2 bg-white relative" data-testid={`lot-row-${i}`}>
                          <button onClick={() => removeLot(i)} className="absolute top-1 right-1 text-red-400 hover:text-red-600"><X size={12} /></button>
                          <div className="flex items-center gap-2 text-xs">
                            <span className="font-mono font-semibold text-[#022D52]">Lot {lot.number}</span>
                            <span className="text-slate-600">{lot.description || lot.lot_type}</span>
                          </div>
                          {form._is_promoter && form._promoter_owner_id ? (() => {
                            // iter93f : fallback sur sessionOwners si les owners
                            // state ont ete rechargus sans le promoteur (cas
                            // owner reutilise deja lie a une autre ACP).
                            const promoter = owners.find(x => x.id === form._promoter_owner_id)
                              || sessionOwners.find(x => x.id === form._promoter_owner_id);
                            return (
                              <div className="mt-1" data-testid={`lot-${i}-promoter-override`}>
                                <Badge variant="outline" className="bg-orange-50 border-orange-400 text-orange-900 gap-1 pl-1.5 pr-1.5 py-0 text-[10px]">
                                  Promoteur : {promoter?.name || '(inconnu)'}
                                </Badge>
                              </div>
                            );
                          })() : (
                            <div className="mt-1 flex flex-wrap items-center gap-1">
                              {(lot.owner_ids || []).length > 0 ? (
                                lot.owner_ids.map(oid => {
                                  const o = owners.find(x => x.id === oid)
                                    || sessionOwners.find(x => x.id === oid);
                                  return (
                                    <Badge key={oid} variant="outline" className="bg-emerald-50 border-emerald-300 text-emerald-800 gap-1 pl-1.5 pr-1 py-0 text-[10px]" data-testid={`lot-${i}-owner-${oid}`}>
                                      {o?.name || '(inconnu)'}
                                      <button onClick={() => removeOwnerFromLot(i, oid)} className="text-emerald-500 hover:text-red-500"><X size={9} /></button>
                                    </Badge>
                                  );
                                })
                              ) : (lot._imported_owner_name || lot._imported_owner_aux) ? (
                                <Badge variant="outline" className="bg-amber-50 border-amber-300 text-amber-800 py-0 text-[10px]" data-testid={`lot-${i}-orphan`}>
                                  Orphelin : {lot._imported_owner_aux ? <span className="font-mono">{lot._imported_owner_aux} </span> : null}{lot._imported_owner_name}
                                </Badge>
                              ) : (
                                <span className="text-[10px] text-slate-400 italic">Aucun proprietaire</span>
                              )}
                              <div className="relative ml-auto min-w-[180px]">
                                <Search size={10} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
                                <Input
                                  value={ownerSearchByLot[i] || ''}
                                  onChange={e => setOwnerSearchByLot({...ownerSearchByLot, [i]: e.target.value})}
                                  onFocus={() => { setOwnerFocusLot(i); refreshOwnersIfStale(); }}
                                  onBlur={() => { setTimeout(() => setOwnerFocusLot(prev => prev === i ? null : prev), 180); }}
                                  placeholder="Rechercher proprio..."
                                  className="pl-6 h-6 text-[11px]"
                                  data-testid={`lot-${i}-owner-search`}
                                />
                                {getOwnerSuggestions(i).length > 0 && (
                                  <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-40 overflow-y-auto" data-testid={`lot-${i}-suggestions`}>
                                    {getOwnerSuggestions(i).map(o => (
                                      <button key={o.id} type="button" onMouseDown={e => e.preventDefault()} onClick={() => { addOwnerToLot(i, o.id); setOwnerSearchByLot({...ownerSearchByLot, [i]: ''}); }} className="w-full text-left px-2 py-1 hover:bg-[#022D52]/5 border-b last:border-b-0 border-slate-100 text-[11px]" data-testid={`lot-${i}-suggestion-${o.id}`}>
                                        {o.name} {o.auxiliary_code && <span className="font-mono text-[9px] text-slate-500">({o.auxiliary_code})</span>}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* SUB-STEP 6 : Ventes intra-exercice */}
                {substep === 'mutations' && (
                  <div className="bg-orange-50 border-2 border-orange-300 rounded-lg p-4" data-testid="intra-fy-sales-block">
                    <div className="text-sm font-bold text-orange-900 uppercase tracking-wide mb-1">Etape 6/6 : Ventes intra-exercice</div>
                    <p className="text-xs text-orange-800 mb-3">
                      Depuis le debut de l&apos;exercice (<strong>{form.fy_start}</strong>), y a-t-il eu des <strong>mutations</strong> (ventes de lot) sur cette ACP ? Si oui, elles seront saisies via le module Mutations juste apres la creation.
                    </p>
                    <div className="flex flex-col gap-2">
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="radio"
                          name="intra_fy_sales"
                          checked={!form._has_intra_fy_sales}
                          onChange={() => setForm({...form, _has_intra_fy_sales: false})}
                          data-testid="intra-fy-no"
                        />
                        <span>Non, aucune vente intra-exercice</span>
                      </label>
                      <label className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="radio"
                          name="intra_fy_sales"
                          checked={form._has_intra_fy_sales}
                          onChange={() => setForm({...form, _has_intra_fy_sales: true})}
                          data-testid="intra-fy-yes"
                        />
                        <span>Oui, il y a eu au moins une vente</span>
                      </label>
                    </div>
                    {form._has_intra_fy_sales && (
                      <p className="text-xs text-orange-900 mt-3 italic bg-orange-100 rounded p-2">
                        A la creation de l&apos;ACP, vous serez automatiquement redirige vers la page <strong>Lots</strong> (ou le wizard d&apos;import Optipro) pour saisir chaque mutation avec sa date, l&apos;ancien et le nouveau proprietaire.
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* MODE EDITION : ancien flow libre (Step 2 sans sous-etapes) */}
            {editing && step === 2 && (
              <div>
                <div className="bg-amber-50 border-2 border-amber-300 rounded-lg p-3 mb-4" data-testid="fy-period-block-edit">
                  <div className="text-xs font-bold text-amber-900 uppercase tracking-wide mb-1">Periode de l&apos;exercice en cours</div>
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="form-label text-amber-900">Debut *</label>
                      <Input type="date" value={form.fy_start} onChange={e => setForm({...form, fy_start: e.target.value})} className="h-9" />
                    </div>
                    <div>
                      <label className="form-label text-amber-900">Fin *</label>
                      <Input type="date" value={form.fy_end} onChange={e => setForm({...form, fy_end: e.target.value})} className="h-9" />
                    </div>
                    <div>
                      <label className="form-label text-amber-900">Nom</label>
                      <Input value={form.fy_name} onChange={e => setForm({...form, fy_name: e.target.value})} placeholder="auto" className="h-9" />
                    </div>
                  </div>
                </div>
                <p className="text-xs text-slate-500 italic">Les proprietaires, lots et affectations existants sont geres depuis leurs menus dedies (Lots, Proprietaires). Cet ecran modifie uniquement l&apos;identite et la periode.</p>
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
              {(() => {
                // iter93e/93g : navigation sub-step aware en mode CREATION Step 2
                const subOrder = ['fy','owners','promoter','lots','assign','mutations'];
                const subIdx = subOrder.indexOf(substep);
                const inCreateStep2 = !editing && step === 2;
                const canGoBack = !editing && (step > 1 || (inCreateStep2 && subIdx > 0));
                return canGoBack ? (
                  <Button
                    variant="outline"
                    data-testid="wizard-prev-btn"
                    onClick={() => {
                      if (inCreateStep2 && subIdx > 0) setSubstep(subOrder[subIdx - 1]);
                      else setStep(step - 1);
                    }}
                  >
                    Precedent
                  </Button>
                ) : <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>;
              })()}
              <div className="flex gap-2">
                {(() => {
                  const subOrder = ['fy','owners','promoter','lots','assign','mutations'];
                  const subIdx = subOrder.indexOf(substep);
                  const inCreateStep2 = !editing && step === 2;
                  const isLastSub = inCreateStep2 && substep === 'mutations';

                  // Gating logique par sous-etape
                  let disabled = false;
                  let title = '';
                  let btnLabel = 'Suivant';
                  if (step === 1 && !form.name.trim()) { disabled = true; title = 'Renseignez le nom'; }
                  if (inCreateStep2) {
                    if (substep === 'fy' && (!form.fy_start || !form.fy_end)) { disabled = true; title = 'Renseignez la periode de l\'exercice fiscal'; }
                    if (substep === 'owners' && owners.length === 0) { disabled = true; title = 'Importez au moins 1 proprietaire (PDF, CSV ou manuel)'; }
                    if (substep === 'promoter' && form._is_promoter && !form._promoter_owner_id) { disabled = true; title = 'Selectionnez ou creez un promoteur'; }
                    if (substep === 'lots' && (form.lots || []).length === 0) { disabled = true; title = 'Importez ou ajoutez au moins 1 lot'; }
                    if (substep === 'assign') btnLabel = 'Valider les affectations';
                    if (isLastSub) btnLabel = 'Etape suivante';
                  }

                  const onClickNext = () => {
                    if (inCreateStep2 && !isLastSub) {
                      // Interception speciale pour l'etape 'assign' -> confirme les affectations
                      if (substep === 'assign') {
                        const total = (form.lots || []).length;
                        const orphans = (form.lots || []).filter(l => (l.owner_ids || []).length === 0 && !form._is_promoter).length;
                        if (orphans > 0) {
                          const ok = window.confirm(
                            `${orphans} lot(s) sur ${total} n'ont pas de proprietaire assigne. ` +
                            `Continuer et les creer comme orphelins (a completer plus tard) ?`
                          );
                          if (!ok) return;
                        }
                        toast.success(`Affectations validees : ${total - orphans}/${total} lot(s) rattaches`);
                      }
                      setSubstep(subOrder[subIdx + 1]);
                    } else if (inCreateStep2 && isLastSub) {
                      setStep(3);
                    } else {
                      setStep(step + 1);
                    }
                  };

                  if (!editing && step < 3) {
                    return (
                      <Button
                        onClick={onClickNext}
                        className="bg-[#022D52] hover:bg-[#1D4ED8]"
                        data-testid="wizard-next-btn"
                        disabled={disabled}
                        title={title}
                      >
                        {btnLabel}
                      </Button>
                    );
                  }
                  return (
                    <Button onClick={handleSave} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="copro-save-btn">
                      {editing ? 'Modifier' : 'Creer l\'ACP'}
                    </Button>
                  );
                })()}
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
          const created = [];
          let ok = 0, reused = 0, ko = 0;
          // iter90gk : accumule les homonymes detectes pour un dialog de review
          const homonyms = [];
          for (const r of rows) {
            try {
              const last = r.last_name || '';
              const first = r.first_name || '';
              const name = (last + ' ' + first).trim();
              if (!last) { ko++; continue; }
              const resp = await api.post('/owners?reuse_on_duplicate=true', {
                first_name: first, last_name: last, name,
                address: r.address || '', postal_code: r.postal_code || '', city: r.city || '',
                country: 'Belgique', email: r.email || '', phone: r.phone || '', iban: r.iban || '',
              });
              if (resp?.data) created.push(resp.data);
              if (resp?.data?._reused) reused++;
              else ok++;
            } catch (err) {
              const msg = err.response?.data?.detail || '';
              if (err.response?.status === 409 && msg.toLowerCase().includes('homonyme detecte')) {
                homonyms.push({ row: r, message: msg });
              } else {
                ko++;
              }
            }
          }
          // iter93f : persist session owners (crees + reutilises) pour le recap
          if (created.length > 0) {
            setSessionOwners(prev => {
              const byId = new Map();
              for (const o of prev) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id) byId.set(o.id, o);
              return Array.from(byId.values());
            });
          }
          // Reload owners so the lot autocomplete sees them.
          // iter93f : sur la CREATION, merge fetch(orphelins) + created.
          let nextOwners = owners;
          try {
            const r = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
            const fetched = r.data || [];
            if (!editing) {
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              nextOwners = Array.from(byId.values());
            } else {
              nextOwners = fetched;
            }
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
            `${ok} propr. crees${reused ? ` + ${reused} reutilises` : ''}${ko ? ` (${ko} echec(s))` : ''}` +
            (retroMatched ? ` - ${retroMatched} lot(s) auto-affectes` : '')
          );
          // iter90gk : ouvre le dialog interactif pour les homonymes en attente
          if (homonyms.length > 0) {
            setOwnerHomonymsDialog({ rows: homonyms });
          }
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
          let ok = 0, reused = 0, ko = 0;
          // iter90gk : accumule les homonymes detectes pour un dialog de review
          const homonyms = [];
          for (const r of rows) {
            try {
              const last = r.last_name || '';
              const first = r.first_name || '';
              const civ = r.civility ? `${r.civility} ` : '';
              const name = (civ + last + ' ' + first).trim() || (r.name || '');
              if (!last && !r.name) { ko++; continue; }
              const resp = await api.post('/owners?reuse_on_duplicate=true', {
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
              if (resp?.data?._reused) reused++;
              else ok++;
            } catch (err) {
              // iter90gk : detecte les homonymes (409 non-strict) pour review batch
              const msg = err.response?.data?.detail || '';
              if (err.response?.status === 409 && msg.toLowerCase().includes('homonyme detecte')) {
                homonyms.push({ row: r, message: msg });
              } else {
                ko++;
              }
            }
          }
          // iter93f : accumule les proprios de la session (crees + reutilises)
          // pour qu'ils restent visibles dans le recap Etape 2, meme si deja
          // rattaches a d'autres ACPs.
          if (created.length > 0) {
            setSessionOwners(prev => {
              const byId = new Map();
              for (const o of prev) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id) byId.set(o.id, o);
              return Array.from(byId.values());
            });
          }
          // Re-fetch the full list so the local state is consistent.
          // iter93f : sur la CREATION, `unassigned_only=true` exclut les
          // proprios reutilises (deja rattaches a d'autres ACPs). On merge
          // manuellement les `created` (contenant les nouveaux + reutilises)
          // avec le fetch pour ne pas les perdre du recap.
          let nextOwners = owners;
          try {
            const rr = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
            const fetched = rr.data || [];
            if (!editing) {
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              nextOwners = Array.from(byId.values());
            } else {
              nextOwners = fetched;
            }
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
            `${ok} propr. crees${reused ? ` + ${reused} reutilises` : ''}${ko ? ` (${ko} echec(s))` : ''}` +
            (retroMatched ? ` - ${retroMatched} lot(s) auto-affectes` : '')
          );
          // iter90gk : ouvre le dialog interactif pour les homonymes PDF
          if (homonyms.length > 0) {
            setOwnerHomonymsDialog({ rows: homonyms });
          }
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
          // iter93f : merge sessionOwners pour ne pas perdre les proprios
          // reutilises (deja lies a d'autres ACPs) - notamment le promoteur.
          let availableOwners = owners;
          try {
            const rr = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
            const fetched = rr.data || [];
            if (!editing) {
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of sessionOwners) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              availableOwners = Array.from(byId.values());
            } else {
              availableOwners = fetched;
            }
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

      {/* iter90gk : Dialog interactif review homonymes owner (batch PDF import) */}
      <Dialog open={!!ownerHomonymsDialog} onOpenChange={(o) => { if (!o) setOwnerHomonymsDialog(null); }}>
        <DialogContent className="max-w-3xl" data-testid="owner-homonyms-batch-dialog">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Chivo,sans-serif' }}>
              Homonymes detectes ({ownerHomonymsDialog?.rows?.length || 0})
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="p-3 bg-yellow-50 border border-yellow-300 rounded text-xs">
              <strong>Regle stricte anti-doublon :</strong> les {ownerHomonymsDialog?.rows?.length || 0} proprietaires
              suivants ont un HOMONYME dans une autre ACP mais leur email/telephone est DIFFERENT.
              Verifiez qu&apos;il s&apos;agit bien d&apos;autres personnes avant de forcer la creation.
            </div>
            <div className="border rounded max-h-80 overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nom import</TableHead>
                    <TableHead>Email / Tel</TableHead>
                    <TableHead className="w-64">Homonyme existant</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(ownerHomonymsDialog?.rows || []).map((h, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs">
                        {[h.row.civility, h.row.last_name, h.row.first_name].filter(Boolean).join(' ')}
                      </TableCell>
                      <TableCell className="text-xs text-slate-600">
                        <div>{h.row.email || '(sans email)'}</div>
                        <div className="text-slate-400">{h.row.phone || '(sans tel)'}</div>
                      </TableCell>
                      <TableCell className="text-xs text-slate-500 italic max-w-md break-words">
                        {h.message.split('. ')[0]}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setOwnerHomonymsDialog(null)} data-testid="homonym-skip-all">
                Ignorer (ne pas creer)
              </Button>
              <Button
                className="bg-yellow-600 hover:bg-yellow-700 text-white"
                data-testid="homonym-force-all"
                onClick={async () => {
                  const rows = ownerHomonymsDialog?.rows || [];
                  let ok = 0, ko = 0;
                  for (const h of rows) {
                    try {
                      const r = h.row;
                      const last = r.last_name || '';
                      const first = r.first_name || '';
                      const civ = r.civility ? `${r.civility} ` : '';
                      const name = (civ + last + ' ' + first).trim() || (r.name || '');
                      await api.post('/owners?force_create_despite_homonym=true', {
                        first_name: first, last_name: last || r.name, name,
                        civility: r.civility || '',
                        email: r.email || '', phone: r.phone || '',
                        vcs_code: r.vcs_code || '', vcs_digits: r.vcs_digits || '',
                        auxiliary_code: r.auxiliary_code || '', identifier: r.identifier || '',
                        address: r.address || '', postal_code: r.postal_code || '', city: r.city || '',
                        country: 'Belgique', iban: r.iban || '',
                      });
                      ok++;
                    } catch (_e) { ko++; }
                  }
                  toast.success(`${ok} homonyme(s) confirme(s) et cree(s)${ko ? ` (${ko} echec(s))` : ''}`);
                  setOwnerHomonymsDialog(null);
                  // Refetch owners
                  try {
                    const rr = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
                    setOwners(rr.data || []);
                  } catch { /* silent */ }
                }}
              >
                Creer TOUS (homonymes confirmes)
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
