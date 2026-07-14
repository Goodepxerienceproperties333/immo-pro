import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Check, ChevronsUpDown } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Receipt, Sparkles, Paperclip, Download, X, Pencil, Filter, FolderInput, Eye, Loader2, ArrowUp, ArrowDown, ArrowUpDown, SkipForward, PanelRightClose, PanelRightOpen, FileText, ShieldAlert } from 'lucide-react';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import SupplierSearchSelect from '@/components/SupplierSearchSelect';
import BundleImportDialog from '@/components/BundleImportDialog';
import { fmtDate } from '@/lib/dateFmt';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';
import { useDirtyGuard } from '@/hooks/useDirtyGuard';

const API = process.env.REACT_APP_BACKEND_URL;

export default function InvoicesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [invoices, setInvoices] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [invFilters, setInvFilters] = useState({ startDate: '', endDate: '', supplier: '', reference: '', status: '' });
  // iter86 : tri cliquable par colonne (alphabetique / chronologique / numerique)
  const [invSort, setInvSort] = useState({ key: 'date', dir: 'desc' });
  const [distKeys, setDistKeys] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [lots, setLots] = useState([]);
  const [invoiceDialog, setInvoiceDialog] = useState(false);
  const [invForm, setInvForm] = useState({ number: '', date: '', due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', expense_category_id: '', distribution_key_id: '', status: 'unpaid', is_private_fee: false, private_fee_owner_id: '', private_fee_allocations: [], occupant_pct: 0, proprietaire_pct: 100, lines: [] });
  const [owners, setOwners] = useState([]);
  const [ownerSearch, setOwnerSearch] = useState('');
  const [suggestCreateSupplier, setSuggestCreateSupplier] = useState(null); // {name, vat, bce, iban}
  const [aiExtracting, setAiExtracting] = useState(false);
  const [aiHint, setAiHint] = useState('');
  // iter90aq : conservees pour le "learn" post-save (envoi diff aux templates)
  const [lastAiRawText, setLastAiRawText] = useState('');
  const [lastAiSupplierIdGuess, setLastAiSupplierIdGuess] = useState('');
  const [lastAiValues, setLastAiValues] = useState(null);
  const [lastAiExtractionSource, setLastAiExtractionSource] = useState('');
  const [pendingPdf, setPendingPdf] = useState(null); // {file, filename} captured for later attach
  // Iter90di : queue multi-fichiers pour import IA sequentiel (facture par facture)
  const [pendingAiFiles, setPendingAiFiles] = useState([]);
  const [aiBatchTotal, setAiBatchTotal] = useState(0);
  const [aiBatchIndex, setAiBatchIndex] = useState(0);
  const [attachDialogInv, setAttachDialogInv] = useState(null); // invoice being managed
  const [newCatDialog, setNewCatDialog] = useState(false);
  const [newCatForm, setNewCatForm] = useState({ name: '', account_number: '', description: '' });
  const [bundleDialog, setBundleDialog] = useState(false);
  // iter90et : dirty guard sur le dialog facture
  const invDirty = useDirtyGuard(invForm, invoiceDialog);
  const newCatDirty = useDirtyGuard(newCatForm, newCatDialog);
  const [dragActive, setDragActive] = useState(false);
  const [viewerAttachment, setViewerAttachment] = useState(null); // {url, filename}
  // iter90ez : side panel PDF viewer inside invoice dialog
  // (minimizable pour ne pas encombrer). Auto-open des qu'un pendingPdf
  // existe.
  const [pdfPanelOpen, setPdfPanelOpen] = useState(true);
  const [pdfPanelUrl, setPdfPanelUrl] = useState(null);
  useEffect(() => {
    if (pendingPdf && pendingPdf.file) {
      const url = URL.createObjectURL(pendingPdf.file);
      setPdfPanelUrl(url);
      setPdfPanelOpen(true);
      return () => URL.revokeObjectURL(url);
    } else {
      setPdfPanelUrl(null);
    }
  }, [pendingPdf]);
  // iter85g : dialog de confirmation homonymes lors de la creation supplier
  // depuis InvoicesPage. State : { payload, similar, onConfirm } ou null.
  const [supplierHomonymsDialog, setSupplierHomonymsDialog] = useState(null);
  // iter90fj : dialog BLOQUANT lors de l'enregistrement d'une facture si le
  // fournisseur saisi/extrait par l'IA n'a pas de fiche exacte mais ressemble
  // a une fiche existante (homonyme). Le syndic DOIT choisir avant que la
  // facture ne soit persistee. State : { typedName, similar, onUseExisting,
  // onCreateNew, onCancel } ou null.
  const [invSupplierGate, setInvSupplierGate] = useState(null);
  const fyParams = useFiscalYearParams();

  // iter85g : helper de creation supplier avec pre-check homonymes.
  // Renvoie le supplier cree (ou null si l'utilisateur annule).
  // Si exact match -> erreur (toast). Si similaires -> dialog confirmation.
  // Si force=true (apres confirmation utilisateur) -> POST avec force_create_despite_similar.
  const createSupplierWithHomonymCheck = (payload) => new Promise((resolve) => {
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    const doActualPost = async (forceDespite) => {
      try {
        const { data: created } = await api.post('/suppliers', {
          ...payload, copropriete_id: copro,
          force_create_despite_similar: !!forceDespite,
        });
        setSuppliers(prev => [...prev, created].sort((a, b) => (a.name || '').localeCompare(b.name || '')));
        toast.success(`Fiche fournisseur creee : ${created.name}`);
        resolve(created);
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Erreur creation fournisseur');
        resolve(null);
      }
    };
    (async () => {
      try {
        const { data: check } = await api.post('/suppliers/check-duplicate', {
          name: payload.name || '',
          vat_number: payload.vat_number || '',
          bce_number: payload.bce_number || '',
          iban: payload.iban || '',
          copropriete_id: copro,
        });
        if (check.exact) {
          const e = check.exact;
          const label = ({ bce_number: 'numero BCE', vat_number: 'numero TVA', iban: 'IBAN', name: 'nom' })[e.field] || e.field;
          toast.error(`Doublon strict : un fournisseur avec le meme ${label} existe deja (${e.supplier?.name || ''}).`);
          // Pre-remplit le champ Fournisseur de la facture avec le sup existant
          if (e.supplier?.name) {
            setInvForm(f => ({ ...f, supplier: e.supplier.name }));
          }
          resolve(null);
          return;
        }
        if (check.similar && check.similar.length > 0) {
          // Ouvre le dialog de confirmation
          setSupplierHomonymsDialog({
            payload,
            similar: check.similar,
            onConfirm: (forceDespite) => {
              setSupplierHomonymsDialog(null);
              doActualPost(forceDespite);
            },
            onCancel: () => {
              setSupplierHomonymsDialog(null);
              resolve(null);
            },
            onUseExisting: (existing) => {
              // Pre-remplit le champ Fournisseur de la facture
              setInvForm(f => ({ ...f, supplier: existing.name }));
              setSupplierHomonymsDialog(null);
              toast.success(`Fournisseur existant reutilise : ${existing.name}`);
              resolve(existing);
            },
          });
          return;
        }
        // Aucune similitude -> creation directe
        await doActualPost(false);
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Erreur verification doublon');
        resolve(null);
      }
    })();
  });

  const load = useCallback(async () => {
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    const supplierParams = copro ? { copropriete_id: copro } : {};
    // iter90d : pour le combobox d'allocation des frais privatifs, on doit
    // voir TOUS les proprietaires du syndic (pas juste ceux ayant un lot dans
    // l'ACP courante), sinon l'utilisateur ne retrouve pas un proprio existant
    // et finit par creer un doublon -> nouveau compte auxiliaire 40000XXX au
    // lieu de reutiliser le compte principal du proprio. Le backend
    // `assign_owner_accounts` recree idempotemment les comptes 4000/4001 dans
    // l'ACP cible quand le proprio est selectionne, donc aucun risque de fuite.
    const ownersConfig = { params: { syndic_wide: true } };
    const [inv, dk, acc, lt, cat, ow, sup] = await Promise.all([
      api.get('/invoices', { params: fyParams }), api.get('/distribution-keys'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }), api.get('/lots'),
      api.get('/expense-categories'), api.get('/owners', ownersConfig),
      api.get('/suppliers', { params: supplierParams }),
    ]);
    setInvoices(inv.data); setDistKeys(dk.data); setAccounts(acc.data); setLots(lt.data);
    setCategories(cat.data); setOwners(ow.data); setSuppliers(sup.data);
  }, [fyParams.date_from, fyParams.date_to]);

  useEffect(() => { load(); }, [load]);

  // Cle de repartition par defaut (marquee is_default=true, sinon premiere cle)
  const defaultKeyId = useMemo(() => {
    if (!distKeys.length) return '';
    return (distKeys.find(k => k.is_default) || distKeys[0]).id;
  }, [distKeys]);

  // Open edit dialog if ?edit=<invoice_id> in URL (deep-link from Expenses page)
  useEffect(() => {
    const editId = searchParams.get('edit');
    if (editId && invoices.length > 0) {
      const inv = invoices.find(i => i.id === editId);
      if (inv) {
        openEditInvoice(inv);
        searchParams.delete('edit');
        setSearchParams(searchParams, { replace: true });
      }
    }
  }, [invoices, searchParams]);

  const [editingInvoice, setEditingInvoice] = useState(null);

  // Invoice handlers
  // iter90ff : export PDF/CSV de la liste filtree
  const exportInvoices = (format) => {
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    const params = new URLSearchParams();
    if (copro) params.set('copropriete_id', copro);
    if (invFilters.startDate) params.set('start_date', invFilters.startDate);
    if (invFilters.endDate) params.set('end_date', invFilters.endDate);
    if (invFilters.supplier) params.set('supplier', invFilters.supplier);
    if (invFilters.status) params.set('status', invFilters.status);
    if (invFilters.reference) params.set('reference', invFilters.reference);
    const url = `${process.env.REACT_APP_BACKEND_URL}/api/invoices/export.${format}?${params.toString()}`;
    // Ouvre dans un nouvel onglet -> le navigateur declenche le download
    // avec la Content-Disposition envoyee par le backend.
    window.open(url, '_blank');
  };

  const openCreateInvoice = () => {
    setEditingInvoice(null);
    setInvForm({ number: `F-${Date.now().toString().slice(-6)}`, date: new Date().toISOString().split('T')[0], due_date: '', supplier: '', description: '', total_amount: 0, vat_amount: 0, account_number: '', expense_category_id: '', distribution_key_id: defaultKeyId, status: 'unpaid', is_private_fee: false, private_fee_owner_id: '', private_fee_allocations: [], occupant_pct: 0, proprietaire_pct: 100, lines: [] });
    setAiHint(''); setPendingPdf(null); setOwnerSearch('');
    setInvoiceDialog(true);
  };

  const openEditInvoice = (inv) => {
    setEditingInvoice(inv);
    // P1 fix (iter90fo) : bug "nature de depense grisee, impossible de
    // changer / modification non enregistree". Root cause : de nombreuses
    // factures (import CODA/Optipro, extraction IA) sont stockees avec un
    // tableau `lines` contenant UNE SEULE ligne (meme si conceptuellement
    // c'est une facture a "1 seule nature"). En edition, le simple fait que
    // `lines.length > 0` activait le mode "lignes multiples" : le bloc
    // Nature/Compte/Cle du haut devenait `pointer-events-none` (grise et
    // non-cliquable), forcant l'utilisateur a utiliser un second selecteur
    // (bloc "Lignes multiples") qu'il ne voyait/comprenait pas -> impression
    // que le champ est bloque et que la modification ne "prend" jamais.
    // Fix : si la facture n'a qu'UNE seule ligne, on "aplatit" cette ligne
    // dans les champs de premier niveau (mode normal, editable) et on vide
    // `lines`. Les VRAIES factures multi-lignes (>= 2 lignes, ex: relance
    // assurance avec plusieurs primes) restent en mode "lignes multiples".
    const rawLines = inv.lines || [];
    const singleLine = rawLines.length === 1 ? rawLines[0] : null;
    setInvForm({
      number: inv.number || '', date: inv.date || '', due_date: inv.due_date || '',
      supplier: inv.supplier || '', description: inv.description || '',
      total_amount: inv.total_amount || 0, vat_amount: inv.vat_amount || 0,
      account_number: singleLine ? (singleLine.account_number || inv.account_number || '') : (inv.account_number || ''),
      expense_category_id: singleLine ? (singleLine.expense_category_id || inv.expense_category_id || '') : (inv.expense_category_id || ''),
      distribution_key_id: singleLine ? (singleLine.distribution_key_id || inv.distribution_key_id || '') : (inv.distribution_key_id || ''),
      status: inv.status || 'unpaid',
      is_private_fee: !!inv.is_private_fee,
      private_fee_owner_id: inv.private_fee_owner_id || '',
      // iter90bn : _key stable pour React (chaque ligne/allocation charge en
      // edition recoit un identifiant client, evite index-as-key collisions).
      private_fee_allocations: Array.isArray(inv.private_fee_allocations)
        ? inv.private_fee_allocations.map(a => ({
            _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`),
            owner_id: a.owner_id, amount: a.amount,
          }))
        : [],
      occupant_pct: singleLine ? (singleLine.occupant_pct ?? inv.occupant_pct ?? 0) : (inv.occupant_pct ?? 0),
      proprietaire_pct: singleLine ? (singleLine.proprietaire_pct ?? inv.proprietaire_pct ?? 100) : (inv.proprietaire_pct ?? 100),
      lines: singleLine ? [] : rawLines.map(l => ({
        _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`),
        account_number: l.account_number || '',
        expense_category_id: l.expense_category_id || '',
        distribution_key_id: l.distribution_key_id || '',
        amount: l.amount || 0,
        description: l.description || '',
      })),
    });
    setAiHint(''); setPendingPdf(null); setOwnerSearch('');
    setInvoiceDialog(true);
  };

  // Auto-apprentissage fournisseur -> nature : quand le user selectionne un
  // fournisseur, on interroge l'historique et pre-remplit la nature de
  // depense la plus utilisee pour ce fournisseur au sein de l'ACP.
  // Chinese walls STRICT : uniquement pour l'ACP courante.
  // Non-destructif : n'ecrase JAMAIS un choix manuel du user.
  const applySupplierSuggestion = async (supplierName) => {
    const name = (supplierName || '').trim();
    if (!name) return;
    const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
    if (!copro || copro === 'all') return;
    try {
      const { data } = await api.get('/invoices/supplier-suggestion', {
        params: { supplier: name, copropriete_id: copro },
      });
      const sug = data?.suggestion;
      if (!sug) return;
      setInvForm(f => {
        // Mode multi-lignes : ne pre-remplir que si la 1ere ligne est vide
        // (pas de nature ET pas de compte). Sinon l'utilisateur a deja
        // saisi quelque chose, on ne l'ecrase pas.
        if (f.lines && f.lines.length > 0) {
          const first = f.lines[0];
          if (!first.expense_category_id && !first.account_number) {
            const newLines = [...f.lines];
            newLines[0] = {
              ...first,
              expense_category_id: sug.expense_category_id,
              account_number: sug.account_number || first.account_number,
              distribution_key_id: sug.distribution_key_id || first.distribution_key_id,
              // iter90fj : la repartition Occ/Prop apprise pour la nature suit
              // la meme regle que la selection manuelle (non-destructif : ligne
              // sans pct explicite uniquement).
              occupant_pct: (first.occupant_pct == null && sug.occupant_pct != null) ? Number(sug.occupant_pct) : first.occupant_pct,
              proprietaire_pct: (first.proprietaire_pct == null && sug.occupant_pct != null) ? +(100 - Number(sug.occupant_pct)).toFixed(2) : first.proprietaire_pct,
            };
            toast.success(`Nature apprise : ${sug.expense_category_name} (${sug.usage_count} facture${sug.usage_count > 1 ? 's' : ''} de ${name})`);
            return { ...f, lines: newLines };
          }
          return f;
        }
        // Mode 1-nature : ne pre-remplir que si nature ET compte sont vides
        if (!f.expense_category_id && !f.account_number) {
          toast.success(`Nature apprise : ${sug.expense_category_name} (${sug.usage_count} facture${sug.usage_count > 1 ? 's' : ''} de ${name})`);
          return {
            ...f,
            expense_category_id: sug.expense_category_id,
            account_number: sug.account_number || f.account_number,
            distribution_key_id: sug.distribution_key_id || f.distribution_key_id,
            // iter90fj : repartition Occ/Prop apprise, au meme titre que la
            // selection manuelle de la nature (cf. onValueChange plus bas).
            occupant_pct: sug.occupant_pct != null ? Number(sug.occupant_pct) : f.occupant_pct,
            proprietaire_pct: sug.occupant_pct != null ? +(100 - Number(sug.occupant_pct)).toFixed(2) : f.proprietaire_pct,
          };
        }
        return f;
      });
    } catch {
      // Silencieux : la suggestion est un bonus, pas un bloqueur
    }
  };

  const aiExtractFromPdf = async (file) => {
    setAiExtracting(true);
    setAiHint('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
      if (copro) fd.append('copropriete_id', copro);
      const { data } = await api.post('/invoices-ai/extract', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      const ext = data.extracted || {};
      // iter90aq : conserve raw_text + supplier_id_guess + ai_values pour post-save learning
      setLastAiRawText(data.raw_text || '');
      setLastAiSupplierIdGuess(data.supplier_id_guess || data.supplier_match?.id || '');
      setLastAiValues({
        number: ext.number || '',
        date: ext.date || '',
        due_date: ext.due_date || '',
        total_amount: ext.total_amount || 0,
        vat_amount: ext.vat_amount || 0,
        net_amount: ext.net_amount || 0,
        vat_rate: ext.vat_rate || 0,
        iban: ext.iban || '',
        communication: ext.communication || '',
      });
      setLastAiExtractionSource(ext._extraction_source || 'ai');
      // Si le backend retourne un warning explicite (PDF illisible, pas de cle LLM, etc.)
      if (ext._warning) {
        setAiHint(ext._warning);
        toast.warning(ext._warning, { duration: 8000 });
      } else if (!ext.number && !ext.supplier_name && !ext.total_amount) {
        // Aucun champ utile rempli : afficher un warning au lieu d'un faux success
        setAiHint('IA n\'a pas pu extraire de donnees utiles - completez manuellement.');
        toast.warning('Aucune donnee extraite, completez manuellement', { duration: 5000 });
      } else {
        setInvForm(f => {
          // Si l'IA a detecte 2+ lignes, on pre-remplit le mode multi-lignes
          const aiLines = Array.isArray(ext.lines) ? ext.lines.filter(ln => Number(ln.amount) > 0) : [];
          const useMulti = aiLines.length >= 2;
          return {
            ...f,
            number: ext.number || f.number,
            date: ext.date || f.date,
            due_date: ext.due_date || f.due_date,
            supplier: ext.supplier_name || f.supplier,
            description: ext.description || f.description,
            total_amount: ext.total_amount || f.total_amount,
            vat_amount: ext.vat_amount || f.vat_amount,
            account_number: useMulti ? '' : (ext.suggested_pcmn_account || f.account_number),
            lines: useMulti ? aiLines.map(ln => ({
              account_number: ln.suggested_pcmn_account || '',
              expense_category_id: '',
              distribution_key_id: defaultKeyId,
              amount: Number(ln.amount) || 0,
              description: ln.description || '',
            })) : [],
          };
        });
        const parts = [];
        const aiLinesCount = Array.isArray(ext.lines) ? ext.lines.filter(ln => Number(ln.amount) > 0).length : 0;
        if (aiLinesCount >= 2) parts.push(`${aiLinesCount} lignes detectees - mode multi-lignes pre-rempli`);
        if (ext.vat_number) parts.push(`TVA fourn.: ${ext.vat_number}`);
        if (ext.bce_number) parts.push(`BCE: ${ext.bce_number}`);
        if (ext.iban) parts.push(`IBAN: ${ext.iban}`);
        if (ext.vat_rate) parts.push(`Taux TVA: ${ext.vat_rate}%`);
        // iter90ap : avertissement dates suspectes signale par le backend
        if (ext._date_warning) {
          parts.push(`⚠️ ${ext._date_warning}`);
          toast.warning(`Dates a verifier : ${ext._date_warning}`, { duration: 10000 });
        }
        if (data.supplier_match) {
          const m = data.supplier_match_method === 'bce' ? 'par BCE' : 'par nom';
          parts.push(`Fournisseur reconnu (${m}): ${data.supplier_match.name}`);
          setSuggestCreateSupplier(null);
        } else if (data.supplier_suggest_create) {
          setSuggestCreateSupplier({
            name: ext.supplier_name || '',
            vat_number: ext.vat_number || '',
            bce_number: ext.bce_number || '',
            iban: ext.iban || '',
            bic: ext.bic || '',
          });
          parts.push(`Nouveau fournisseur a creer: ${ext.supplier_name}`);
        }
        setAiHint(parts.join(' - '));
        // iter90aq : feedback visuel selon source d'extraction
        if (ext._extraction_source === 'template') {
          toast.success('⚡ Extraction template : donnees reprises du profil fournisseur (rapide, sans IA)');
        } else {
          toast.success('Donnees extraites par IA - verifiez avant enregistrement.');
        }
      }
      setPendingPdf({ file, filename: file.name });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec extraction IA');
    } finally { setAiExtracting(false); }
  };

  const saveInvoice = async () => {
    try {
      // Validation cote front : si mode multi-lignes, somme = total
      const usesMultiLines = (invForm.lines || []).length > 0;
      if (usesMultiLines) {
        if (invForm.is_private_fee) {
          toast.error('Frais privatif incompatible avec lignes multiples'); return;
        }
        const sum = invForm.lines.reduce((s, l) => s + (Number(l.amount) || 0), 0);
        if (Math.abs(sum - Number(invForm.total_amount || 0)) > 0.01) {
          toast.error(`Somme des lignes (${sum.toFixed(2)}) different du total facture (${Number(invForm.total_amount).toFixed(2)})`);
          return;
        }
        // Verifie que chaque ligne a un compte + montant > 0
        for (let i = 0; i < invForm.lines.length; i++) {
          const ln = invForm.lines[i];
          if (!ln.account_number) { toast.error(`Ligne ${i + 1} : compte PCMN requis`); return; }
          if (!Number(ln.amount) || Number(ln.amount) <= 0) { toast.error(`Ligne ${i + 1} : montant > 0 requis`); return; }
        }
      }
      // iter85e : validation allocations frais privatif (centimes pour eviter
      // les erreurs d'arrondi flottants)
      let cleanedAllocs = null;
      if (invForm.is_private_fee) {
        const allocs = (invForm.private_fee_allocations || []).filter(a => a.owner_id && Number(a.amount) > 0);
        if (allocs.length === 0) {
          toast.error('Frais privatif : au moins un proprietaire avec un montant > 0 requis');
          return;
        }
        const sumCents = allocs.reduce((s, a) => s + Math.round(Number(a.amount) * 100), 0);
        const totalCents = Math.round(Number(invForm.total_amount || 0) * 100);
        if (sumCents !== totalCents) {
          const diff = (totalCents - sumCents) / 100;
          toast.error(`Somme des allocations (${(sumCents/100).toFixed(2)}) different du total facture (${(totalCents/100).toFixed(2)}). Ecart : ${diff.toFixed(2)} EUR`);
          return;
        }
        // Pas de doublons d'owner
        const ids = allocs.map(a => a.owner_id);
        if (new Set(ids).size !== ids.length) {
          toast.error('Frais privatif : un meme proprietaire est present plusieurs fois');
          return;
        }
        cleanedAllocs = allocs.map(a => ({ owner_id: a.owner_id, amount: Number(a.amount) }));
      }
      const payload = {
        ...invForm,
        total_amount: Number(invForm.total_amount),
        vat_amount: Number(invForm.vat_amount),
        private_fee_allocations: cleanedAllocs,
        lines: usesMultiLines ? invForm.lines.map(l => ({
          account_number: l.account_number,
          expense_category_id: l.expense_category_id || '',
          distribution_key_id: (l.distribution_key_id && l.distribution_key_id !== 'none') ? l.distribution_key_id : '',
          amount: Number(l.amount),
          description: l.description || '',
          // iter90ey : repartition Occ/Prop specifique a la ligne (null = herite)
          occupant_pct: (l.occupant_pct === '' || l.occupant_pct === null || l.occupant_pct === undefined) ? null : Number(l.occupant_pct),
          proprietaire_pct: (l.proprietaire_pct === '' || l.proprietaire_pct === null || l.proprietaire_pct === undefined) ? null : Number(l.proprietaire_pct),
        })) : null,
      };
      let invoiceId;
      // iter90fj : une fois le syndic a tranche sur un homonyme (utiliser
      // l'existant ou creer un nouveau), on envoie supplier_confirmed=true
      // pour que le backend n'exige plus de confirmation sur ce meme nom.
      let supplierConfirmed = false;
      // iter90ay : retry avec ?force=true si SOFT_DUPLICATE (montant+date proche mais numero different)
      const saveOnce = async (force = false) => {
        const suffix = force ? '?force=true' : '';
        const body = { ...payload, supplier_confirmed: supplierConfirmed };
        if (editingInvoice) {
          await api.put(`/invoices/${editingInvoice.id}${suffix}`, body);
          return editingInvoice.id;
        }
        const { data: created } = await api.post(`/invoices${suffix}`, body);
        return created?.id;
      };
      try {
        invoiceId = await saveOnce(false);
      } catch (err) {
        const detail = err.response?.data?.detail || '';
        if (err.response?.status === 409 && detail && typeof detail === 'object' && detail.code === 'SUPPLIER_HOMONYM') {
          // iter90fj : ne JAMAIS enregistrer sans accord explicite du syndic
          // en cas d'homonyme/nom proche detecte.
          const decision = await new Promise((resolve) => {
            setInvSupplierGate({
              typedName: detail.typed_name,
              similar: detail.similar || [],
              onUseExisting: (name) => { setInvSupplierGate(null); resolve({ action: 'use', name }); },
              onCreateNew: () => { setInvSupplierGate(null); resolve({ action: 'new' }); },
              onCancel: () => { setInvSupplierGate(null); resolve(null); },
            });
          });
          if (!decision) return; // annule : facture NON enregistree
          supplierConfirmed = true;
          if (decision.action === 'use') {
            payload.supplier = decision.name;
            setInvForm(f => ({ ...f, supplier: decision.name }));
            // iter90fj : le fournisseur canonique choisi peut avoir un
            // historique de nature/repartition. Si la nature n'a pas encore
            // ete renseignee sur cette facture, on tente la suggestion avant
            // le retry de sauvegarde (meme comportement que la saisie manuelle).
            const noCategoryYet = (payload.lines && payload.lines.length > 0)
              ? !payload.lines[0].expense_category_id
              : !payload.expense_category_id;
            if (noCategoryYet) {
              try {
                const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
                if (copro && copro !== 'all') {
                  const { data: sugData } = await api.get('/invoices/supplier-suggestion', { params: { supplier: decision.name, copropriete_id: copro } });
                  const sug = sugData?.suggestion;
                  if (sug) {
                    const occ = sug.occupant_pct != null ? Number(sug.occupant_pct) : null;
                    if (payload.lines && payload.lines.length > 0) {
                      payload.lines[0] = {
                        ...payload.lines[0],
                        expense_category_id: sug.expense_category_id,
                        account_number: sug.account_number || payload.lines[0].account_number,
                        distribution_key_id: sug.distribution_key_id || payload.lines[0].distribution_key_id,
                        occupant_pct: occ,
                        proprietaire_pct: occ != null ? +(100 - occ).toFixed(2) : payload.lines[0].proprietaire_pct,
                      };
                      setInvForm(f => {
                        if (!f.lines || f.lines.length === 0) return f;
                        const newLines = [...f.lines];
                        newLines[0] = { ...newLines[0], ...payload.lines[0] };
                        return { ...f, lines: newLines };
                      });
                    } else {
                      payload.expense_category_id = sug.expense_category_id;
                      payload.account_number = sug.account_number || payload.account_number;
                      payload.distribution_key_id = sug.distribution_key_id || payload.distribution_key_id;
                      if (occ != null) { payload.occupant_pct = occ; payload.proprietaire_pct = +(100 - occ).toFixed(2); }
                      setInvForm(f => ({
                        ...f,
                        expense_category_id: sug.expense_category_id,
                        account_number: sug.account_number || f.account_number,
                        distribution_key_id: sug.distribution_key_id || f.distribution_key_id,
                        occupant_pct: occ != null ? occ : f.occupant_pct,
                        proprietaire_pct: occ != null ? +(100 - occ).toFixed(2) : f.proprietaire_pct,
                      }));
                    }
                    toast.success(`Nature apprise : ${sug.expense_category_name} (${sug.usage_count} facture${sug.usage_count > 1 ? 's' : ''} de ${decision.name})`);
                  }
                }
              } catch { /* suggestion best-effort, non bloquante */ }
            }
          }
          try {
            invoiceId = await saveOnce(false);
          } catch (err2) {
            throw err2;
          }
        } else if (err.response?.status === 409 && typeof detail === 'string' && detail.includes('[SOFT_DUPLICATE]')) {
          const cleanMsg = detail.replace('[SOFT_DUPLICATE] ', '');
          const ok = window.confirm(`${cleanMsg}\n\nEnregistrer quand meme ?`);
          if (!ok) return;
          invoiceId = await saveOnce(true);
        } else if (err.response?.status === 409 && (pendingAiFiles.length > 0 || aiBatchTotal > 1)) {
          // iter90ee : Doublon strict en mode batch import IA -> proposer de
          // passer a la facture suivante au lieu de bloquer toute la queue.
          const remaining = pendingAiFiles.length;
          const msg = typeof detail === 'string'
            ? detail
            : 'Facture deja existante (doublon strict)';
          const skip = window.confirm(
            `${msg}\n\n` +
            `Facture ${aiBatchIndex}/${aiBatchTotal} en doublon.\n` +
            (remaining > 0
              ? `Passer a la facture suivante ? (${remaining} restante${remaining > 1 ? 's' : ''} en attente)`
              : `Il s'agit de la derniere facture du batch. Cliquez OK pour ignorer.`)
          );
          if (!skip) return;
          // Skip = passer a la suivante SANS enregistrer cette facture
          toast.warning(`Facture ${aiBatchIndex}/${aiBatchTotal} ignoree (doublon)`);
          if (remaining > 0) {
            const [next, ...rest] = pendingAiFiles;
            setPendingAiFiles(rest);
            setAiBatchIndex(prev => prev + 1);
            setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
            setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
            setTimeout(() => {
              openCreateInvoice();
              aiExtractFromPdf(next);
            }, 300);
          } else {
            // Fin de queue
            if (aiBatchTotal > 1) {
              toast.success(`Batch termine (${aiBatchTotal} traitees, dont doublons ignores)`, { duration: 5000 });
            }
            setAiBatchTotal(0); setAiBatchIndex(0);
            setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
            setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
          }
          return;
        } else {
          throw err;
        }
      }
      toast.success(editingInvoice ? 'Facture modifiee' : 'Facture creee');
      // If we have a pending PDF, attach it to the invoice
      if (pendingPdf && pendingPdf.file && invoiceId) {
        try {
          const fd = new FormData();
          fd.append('file', pendingPdf.file);
          await api.post(`/invoices/${invoiceId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
        } catch (e) { console.warn('Attachment failed', e); }
      }
      // iter90aq : apprendre le template fournisseur si l'IA/template a ete utilise
      // et qu'on a le texte brut + supplier_id resolu.
      if (lastAiRawText && lastAiSupplierIdGuess && invoiceId && !editingInvoice) {
        try {
          const copro = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
          await api.post('/invoice-templates/learn', {
            supplier_id: lastAiSupplierIdGuess,
            supplier_name: invForm.supplier || '',
            copropriete_id: copro,
            raw_text: lastAiRawText,
            user_values: {
              number: invForm.number || '',
              date: invForm.date || '',
              due_date: invForm.due_date || '',
              total_amount: Number(invForm.total_amount) || 0,
              vat_amount: Number(invForm.vat_amount) || 0,
            },
          });
        } catch (e) { console.warn('Template learn failed', e); }
      }
      // Iter90di : si des fichiers PDF restent dans la queue -> traiter suivant
      const queue = pendingAiFiles;
      if (queue.length > 0) {
        const [next, ...rest] = queue;
        setPendingAiFiles(rest);
        setAiBatchIndex(prev => prev + 1);
        setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
        setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
        toast.info(`Facture ${aiBatchIndex}/${aiBatchTotal} enregistree - passage a la suivante (reste ${queue.length})...`, { duration: 3000 });
        // Petit delay pour laisser le dialog se fermer avant reouverture
        setTimeout(() => {
          openCreateInvoice();
          aiExtractFromPdf(next);
        }, 300);
        load();
        return;
      }
      // Fin de queue : reset batch state
      if (aiBatchTotal > 1) {
        toast.success(`Batch termine : ${aiBatchTotal} factures traitees`, { duration: 5000 });
      }
      setAiBatchTotal(0); setAiBatchIndex(0);
      setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
      // Reset des state IA
      setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const deleteInvoice = async (id) => {
    if (!window.confirm('Supprimer cette facture ?')) return;
    await api.delete(`/invoices/${id}`); toast.success('Facture supprimee'); load();
  };

  const uploadInvoiceAttachment = async (invoiceId, file) => {
    const fd = new FormData();
    fd.append('file', file);
    try {
      await api.post(`/invoices/${invoiceId}/attachments`, fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      toast.success('Piece jointe ajoutee');
      const { data } = await api.get(`/invoices/${invoiceId}`);
      setAttachDialogInv(data); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Echec upload'); }
  };

  const deleteInvoiceAttachment = async (invoiceId, attachmentId) => {
    if (!window.confirm('Supprimer cette piece jointe ?')) return;
    try {
      await api.delete(`/invoices/${invoiceId}/attachments/${attachmentId}`);
      const { data } = await api.get(`/invoices/${invoiceId}`);
      setAttachDialogInv(data); load();
    } catch { toast.error('Erreur'); }
  };

  // Distribution key handlers deplaces vers /app/frontend/src/pages/DistributionKeysPage.js
  // (iter90da : lien direct depuis le menu Comptabilite)

  return (
    <div data-testid="invoices-page">
      <div className="page-header"><h1 className="page-title">Facturation</h1><p className="page-subtitle">Factures et pieces jointes</p></div>

      {/* iter90dt : zone drag-and-drop dediee multi-upload PDF. Rend evident
          qu'on peut deposer plusieurs fichiers a la fois. */}
      <div
        className={`mb-4 border-2 border-dashed rounded-lg p-4 transition-colors ${
          dragActive ? 'border-purple-500 bg-purple-50' : 'border-slate-300 bg-slate-50'
        }`}
        onDragOver={(e) => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          const files = Array.from(e.dataTransfer?.files || []).filter(f =>
            f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')
          );
          if (files.length === 0) { toast.warning('Deposez des fichiers PDF uniquement'); return; }
          if (files.length === 1) {
            setPendingAiFiles([]); setAiBatchTotal(1); setAiBatchIndex(1);
            openCreateInvoice(); aiExtractFromPdf(files[0]);
          } else {
            const [first, ...rest] = files;
            setPendingAiFiles(rest); setAiBatchTotal(files.length); setAiBatchIndex(1);
            toast.info(`${files.length} factures a traiter - facture 1/${files.length} en cours...`, { duration: 4000 });
            openCreateInvoice(); aiExtractFromPdf(first);
          }
        }}
        data-testid="pdf-drop-zone"
      >
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-purple-100 flex items-center justify-center text-purple-700 flex-shrink-0">
              <Sparkles size={20} />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>
                Deposez vos factures PDF ici pour extraction IA
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                1 ou plusieurs fichiers a la fois - chaque facture sera analysee puis validee etape par etape.
              </p>
            </div>
          </div>
          <div className="flex gap-2 flex-wrap justify-end">
            <Button type="button" variant="outline" className="border-emerald-300 text-emerald-700 hover:bg-emerald-50" data-testid="bundle-import-btn"
              onClick={() => setBundleDialog(true)}>
              <FolderInput size={14} className="mr-1.5" /> Regroupement Optipro (1 PDF concat.)
            </Button>
            <label className="inline-flex">
              <Button type="button" variant="outline" className="border-purple-300 text-purple-700 hover:bg-purple-50" data-testid="ai-extract-btn"
                onClick={() => document.getElementById('ai-pdf-input').click()} disabled={aiExtracting}>
                {aiExtracting ? (
                  <><Loader2 size={14} className="mr-1.5 animate-spin" /> Extraction IA...</>
                ) : aiBatchTotal > 1 ? (
                  <><Sparkles size={14} className="mr-1.5" /> Facture {aiBatchIndex}/{aiBatchTotal}</>
                ) : (
                  <><Sparkles size={14} className="mr-1.5" /> Choisir des factures (1+)</>
                )}
              </Button>
              {/* Iter90di : multiple - permet d'uploader plusieurs factures en une fois.
                  Chaque fichier est traite individuellement, sequentiellement. */}
              <input id="ai-pdf-input" type="file" accept="application/pdf" multiple className="hidden" onChange={(e) => {
                const files = Array.from(e.target.files || []);
                if (files.length === 0) return;
                if (files.length === 1) {
                  setPendingAiFiles([]);
                  setAiBatchTotal(1);
                  setAiBatchIndex(1);
                  openCreateInvoice();
                  aiExtractFromPdf(files[0]);
                } else {
                  const [first, ...rest] = files;
                  setPendingAiFiles(rest);
                  setAiBatchTotal(files.length);
                  setAiBatchIndex(1);
                  toast.info(`${files.length} factures a traiter - facture 1/${files.length} en cours...`, { duration: 4000 });
                  openCreateInvoice();
                  aiExtractFromPdf(first);
                }
                e.target.value = '';
              }} />
            </label>
            <Button onClick={openCreateInvoice} className="bg-[#022D52] hover:bg-[#01213e]" data-testid="create-invoice-btn"><Plus size={14} className="mr-1.5" /> Manuel</Button>
          </div>
        </div>

        {/* Batch progress banner - visible durant tout le traitement multi-fichier */}
        {aiBatchTotal > 1 && (
          <div className="mt-3 pt-3 border-t border-slate-200 flex items-center justify-between gap-3" data-testid="batch-progress-banner">
            <div className="flex items-center gap-2 text-sm">
              <Loader2 size={14} className="text-purple-600 animate-spin" />
              <span className="font-semibold text-slate-900">
                Traitement en cours : <span className="text-purple-700">{aiBatchIndex}/{aiBatchTotal}</span>
              </span>
              <span className="text-xs text-slate-500">
                {pendingAiFiles.length > 0 ? `(${pendingAiFiles.length} en file d'attente)` : '(dernier fichier)'}
              </span>
            </div>
            <div className="flex-1 max-w-md">
              <div className="h-2 bg-slate-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-purple-600 transition-all"
                  style={{width: `${(aiBatchIndex - 1) / aiBatchTotal * 100}%`}}
                />
              </div>
            </div>
          </div>
        )}
      </div>

          {/* ---- Filter bar invoices ---- */}
          <div className="mb-3 p-3 bg-slate-50 border border-slate-200 rounded-md flex flex-wrap items-end gap-2" data-testid="invoices-filter-bar">
            <div className="flex items-center gap-1.5">
              <Filter size={14} className="text-slate-500" />
              <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Filtres</span>
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Du</label>
              <Input type="date" value={invFilters.startDate} onChange={e => setInvFilters({...invFilters, startDate: e.target.value})} className="h-8 text-xs w-36" data-testid="inv-filter-start" />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Au</label>
              <Input type="date" value={invFilters.endDate} onChange={e => setInvFilters({...invFilters, endDate: e.target.value})} className="h-8 text-xs w-36" data-testid="inv-filter-end" />
            </div>
            <div className="flex-1 min-w-[140px]">
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Fournisseur</label>
              <Select value={invFilters.supplier || '__all__'} onValueChange={v => setInvFilters({...invFilters, supplier: v === '__all__' ? '' : v})}>
                <SelectTrigger className="h-8 text-xs" data-testid="inv-filter-supplier"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">Tous</SelectItem>
                  {[...new Set(invoices.map(i => (i.supplier || '').trim()).filter(Boolean))].sort().map(s => (
                    <SelectItem key={s} value={s}>{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex-1 min-w-[140px]">
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Reference / description</label>
              <Input value={invFilters.reference} onChange={e => setInvFilters({...invFilters, reference: e.target.value})} placeholder="Numero..." className="h-8 text-xs" data-testid="inv-filter-ref" />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Statut</label>
              <Select value={invFilters.status || '__all__'} onValueChange={v => setInvFilters({...invFilters, status: v === '__all__' ? '' : v})}>
                <SelectTrigger className="h-8 text-xs w-28" data-testid="inv-filter-status"><SelectValue placeholder="Tous" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">Tous</SelectItem>
                  <SelectItem value="unpaid">Impayee</SelectItem>
                  <SelectItem value="paid">Payee</SelectItem>
                  <SelectItem value="draft">Brouillon</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {(invFilters.startDate || invFilters.endDate || invFilters.supplier || invFilters.reference || invFilters.status) && (
              <Button size="sm" variant="ghost" className="h-8 text-red-600" onClick={() => setInvFilters({ startDate: '', endDate: '', supplier: '', reference: '', status: '' })} data-testid="inv-filter-reset">
                Reset
              </Button>
            )}
            {/* iter90ff : exports CSV / PDF de la liste filtree */}
            <div className="ml-auto flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                onClick={() => exportInvoices('csv')}
                data-testid="inv-export-csv-btn"
                title="Exporter la liste filtree en CSV (Excel)"
              >
                <Download size={13} className="mr-1" /> CSV
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                onClick={() => exportInvoices('pdf')}
                data-testid="inv-export-pdf-btn"
                title="Exporter la liste filtree en PDF"
              >
                <Download size={13} className="mr-1" /> PDF
              </Button>
            </div>
          </div>

          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader><TableRow>
                {(() => {
                  const toggleSort = (key) => setInvSort(s => s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' });
                  const sortIcon = (k) => {
                    if (invSort.key !== k) return <ArrowUpDown size={12} className="ml-1 opacity-40" />;
                    return invSort.dir === 'asc'
                      ? <ArrowUp size={12} className="ml-1 text-[#022D52]" />
                      : <ArrowDown size={12} className="ml-1 text-[#022D52]" />;
                  };
                  const renderTh = (k, label, className = '', align = 'left') => (
                    <TableHead key={k} className={className}>
                      <button
                        type="button"
                        onClick={() => toggleSort(k)}
                        className={`inline-flex items-center font-semibold hover:text-[#022D52] transition-colors ${align === 'right' ? 'justify-end w-full' : ''}`}
                        data-testid={`inv-sort-${k}`}
                        title="Cliquez pour trier"
                      >
                        {label}{sortIcon(k)}
                      </button>
                    </TableHead>
                  );
                  return (
                    <>
                      {renderTh('internal_reference', 'Ref. interne')}
                      {renderTh('number', 'N fournisseur')}
                      {renderTh('date', 'Date')}
                      {renderTh('supplier', 'Fournisseur')}
                      {renderTh('description', 'Description')}
                      {renderTh('total_amount', 'Montant', 'text-right whitespace-nowrap min-w-[110px]', 'right')}
                      <TableHead>Cle</TableHead>
                      {renderTh('status', 'Statut')}
                      <TableHead className="w-20">Actions</TableHead>
                    </>
                  );
                })()}
              </TableRow></TableHeader>
              <TableBody>
                {(() => {
                  const filtered = invoices.filter(inv => {
                    if (invFilters.startDate && (inv.date || '') < invFilters.startDate) return false;
                    if (invFilters.endDate && (inv.date || '') > invFilters.endDate) return false;
                    if (invFilters.supplier && inv.supplier !== invFilters.supplier) return false;
                    if (invFilters.reference && !(inv.number || '').toLowerCase().includes(invFilters.reference.toLowerCase()) && !(inv.description || '').toLowerCase().includes(invFilters.reference.toLowerCase())) return false;
                    if (invFilters.status && inv.status !== invFilters.status) return false;
                    return true;
                  });
                  // iter86 : tri stable par colonne
                  const sorted = [...filtered].sort((a, b) => {
                    const k = invSort.key;
                    if (!k) return 0;
                    let va = a[k];
                    let vb = b[k];
                    if (k === 'total_amount') {
                      va = Number(va) || 0;
                      vb = Number(vb) || 0;
                    } else if (k === 'date') {
                      va = (va || '');
                      vb = (vb || '');
                    } else {
                      va = String(va || '').toLowerCase();
                      vb = String(vb || '').toLowerCase();
                    }
                    if (va < vb) return invSort.dir === 'asc' ? -1 : 1;
                    if (va > vb) return invSort.dir === 'asc' ? 1 : -1;
                    return 0;
                  });
                  if (sorted.length === 0) return <TableRow><TableCell colSpan={9} className="text-center py-8 text-slate-400">Aucune facture</TableCell></TableRow>;
                  return sorted.map(inv => (
                  <TableRow key={inv.id} className="hover:bg-slate-50/50">
                    <TableCell className="font-mono text-xs text-[#022D52] font-semibold">{inv.internal_reference || '-'}</TableCell>
                    <TableCell className="font-mono text-sm">{inv.number}</TableCell>
                    <TableCell>{fmtDate(inv.date)}</TableCell>
                    <TableCell className="font-medium">{inv.supplier}</TableCell>
                    <TableCell className="max-w-[200px] truncate">{inv.description}</TableCell>
                    <TableCell className="text-right font-mono whitespace-nowrap">{inv.total_amount?.toFixed(2)} EUR</TableCell>
                    <TableCell className="text-xs">{(() => {
                      // iter85e : affichage cascade de la cle de repartition
                      //   1. invoice.distribution_key_id direct
                      //   2. distinct keys utilisees dans invoice.lines
                      //   3. default_distribution_key_id de la nature de depense
                      //   4. '-' si vraiment aucune cle
                      const directKey = distKeys.find(k => k.id === inv.distribution_key_id);
                      if (directKey) return directKey.name;
                      const lineKeys = Array.from(new Set(
                        (inv.lines || []).map(l => l.distribution_key_id).filter(Boolean)
                      )).map(kid => distKeys.find(k => k.id === kid)?.name).filter(Boolean);
                      if (lineKeys.length === 1) return lineKeys[0];
                      if (lineKeys.length > 1) return `${lineKeys.length} cles`;
                      const cat = categories.find(c => c.id === inv.expense_category_id);
                      if (cat?.default_distribution_key_id) {
                        const defKey = distKeys.find(k => k.id === cat.default_distribution_key_id);
                        if (defKey) {
                          return (
                            <span className="text-slate-500 italic" title="Cle par defaut (via nature de depense)">
                              {defKey.name}
                            </span>
                          );
                        }
                      }
                      return '-';
                    })()}</TableCell>
                    <TableCell>
                      <Badge className={inv.status === 'paid' ? 'bg-green-50 text-green-700 border-green-200' : inv.status === 'unpaid' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-slate-50 text-slate-600'} variant="outline">
                        {inv.status === 'paid' ? 'Payee' : inv.status === 'unpaid' ? 'Impayee' : 'Brouillon'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-1 items-center">
                        <Button variant="ghost" size="sm" onClick={() => openEditInvoice(inv)} data-testid={`edit-invoice-${inv.id}`} title="Modifier"><Pencil size={14} /></Button>
                        <Button variant="ghost" size="sm" onClick={() => setAttachDialogInv(inv)} data-testid={`inv-attach-${inv.id}`} title="Pieces jointes">
                          <Paperclip size={14} />{(inv.attachments?.length || 0) > 0 && <span className="ml-1 text-xs">{inv.attachments.length}</span>}
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => deleteInvoice(inv.id)} className="text-red-500"><Trash2 size={14} /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ));
                })()}
              </TableBody>
            </Table>
          </div>

      {/* Invoice Dialog */}
      <Dialog open={invoiceDialog} onOpenChange={(open) => { if (!open) { setEditingInvoice(null); setPendingPdf(null); } setInvoiceDialog(open); }} hasUnsavedChanges={invDirty}>
        <DialogContent className="max-w-[1600px] w-[97vw] max-h-[92vh] overflow-hidden flex flex-col" data-testid="invoice-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingInvoice ? 'Modifier la facture' : 'Nouvelle facture'}</DialogTitle></DialogHeader>
          <div className="flex gap-3 mt-2 flex-1 min-h-0">
            {/* LEFT COLUMN : form (scrollable) */}
            <div className={`space-y-4 min-w-0 overflow-y-auto pr-2 ${pdfPanelUrl && pdfPanelOpen ? 'flex-1 max-w-[62%]' : 'flex-1'}`}>
            {/* Loading banner during AI extraction */}
            {aiExtracting && (
              <div
                className="relative overflow-hidden rounded-md border border-purple-300 bg-gradient-to-r from-purple-50 via-fuchsia-50 to-purple-50 px-4 py-3"
                data-testid="ai-extracting-banner"
              >
                {/* Animated progress bar (indeterminate) */}
                <div className="absolute top-0 left-0 h-0.5 w-full bg-purple-100 overflow-hidden">
                  <div className="h-full w-1/3 bg-gradient-to-r from-purple-400 via-fuchsia-500 to-purple-400 ai-progress-bar" />
                </div>
                <div className="flex items-center gap-3">
                  <div className="relative flex items-center justify-center w-9 h-9 rounded-full bg-purple-100">
                    <Loader2 size={20} className="text-purple-700 animate-spin" />
                    <Sparkles size={10} className="absolute top-1.5 right-1.5 text-fuchsia-500 animate-pulse" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-semibold text-purple-900 flex items-center gap-2">
                      Analyse de la facture par IA
                      <span className="inline-flex gap-1">
                        <span className="ai-dot ai-dot-1">.</span>
                        <span className="ai-dot ai-dot-2">.</span>
                        <span className="ai-dot ai-dot-3">.</span>
                      </span>
                    </div>
                    <div className="text-[11px] text-purple-700 mt-0.5">
                      Lecture du PDF, extraction du numero, fournisseur, montants et detection du compte PCMN...
                    </div>
                  </div>
                </div>
              </div>
            )}
            {aiHint && (
              <div className="text-xs px-3 py-2 rounded bg-purple-50 border border-purple-200 text-purple-800" data-testid="ai-hint">
                <Sparkles size={12} className="inline mr-1" /> {aiHint}
              </div>
            )}
            {suggestCreateSupplier && (
              <div className="text-xs px-3 py-2 rounded bg-amber-50 border border-amber-200 text-amber-900 flex items-center justify-between gap-3" data-testid="suggest-create-supplier">
                <div>
                  <Sparkles size={12} className="inline mr-1 text-amber-700" />
                  <b>Fournisseur introuvable dans la base globale du syndic.</b>
                  <span className="ml-1">Voulez-vous creer la fiche pour <b>{suggestCreateSupplier.name}</b>
                  {suggestCreateSupplier.bce_number && ` (BCE ${suggestCreateSupplier.bce_number})`} ?</span>
                </div>
                <div className="flex gap-2 shrink-0">
                  <Button type="button" size="sm" variant="outline" onClick={() => setSuggestCreateSupplier(null)} data-testid="suggest-supplier-dismiss">Ignorer</Button>
                  <Button type="button" size="sm" className="bg-amber-600 hover:bg-amber-700 text-white" onClick={async () => {
                    const created = await createSupplierWithHomonymCheck(suggestCreateSupplier);
                    if (created) setSuggestCreateSupplier(null);
                  }} data-testid="suggest-supplier-create">Creer la fiche</Button>
                </div>
              </div>
            )}
            {pendingPdf && (
              <div className="text-xs px-3 py-2 rounded bg-blue-50 border border-blue-200 text-blue-800 flex items-center justify-between gap-2" data-testid="pending-pdf-row">
                <span className="truncate"><Paperclip size={12} className="inline mr-1" /> PDF a attacher: <b>{pendingPdf.filename}</b></span>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    title="Apercu de la facture"
                    onClick={() => {
                      const url = URL.createObjectURL(pendingPdf.file);
                      setViewerAttachment({ url, filename: pendingPdf.filename, isBlob: true });
                    }}
                    className="p-1.5 rounded hover:bg-blue-100 text-[#01213e]"
                    data-testid="preview-pending-pdf-btn"
                  >
                    <Eye size={14} />
                  </button>
                  <button type="button" onClick={() => setPendingPdf(null)} className="p-1.5 rounded hover:bg-blue-100 text-[#022D52]" title="Retirer la piece jointe">
                    <X size={14} />
                  </button>
                </div>
              </div>
            )}
            {!pendingPdf && (
              <div className="text-xs">
                <input id="manual-pdf-input" type="file" accept="application/pdf,image/*" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) setPendingPdf({ file: f, filename: f.name }); e.target.value = ''; }} />
                <button type="button" className="text-slate-500 hover:text-[#022D52] underline" onClick={() => document.getElementById('manual-pdf-input').click()} data-testid="manual-attach-btn">
                  <Paperclip size={11} className="inline mr-1" /> Joindre la facture PDF / image (optionnel)
                </button>
              </div>
            )}
            {/* Pieces jointes existantes (mode edition) : apercu rapide */}
            {editingInvoice && (editingInvoice.attachments || []).length > 0 && (
              <div className="rounded-md border border-slate-200 bg-slate-50 p-2 space-y-1" data-testid="existing-attachments-block">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-600 px-1">
                  Pieces jointes ({editingInvoice.attachments.length})
                </div>
                {editingInvoice.attachments.map((a) => {
                  const coproId = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
                  const inlineUrl = `${API}/api/invoices/${editingInvoice.id}/attachments/${a.id}/download?disposition=inline&copropriete_id=${coproId}`;
                  return (
                    <div key={a.id} className="flex items-center justify-between gap-2 text-xs bg-white rounded border px-2 py-1">
                      <span className="truncate flex items-center gap-1 text-slate-700">
                        <Paperclip size={11} /> {a.filename}
                      </span>
                      <button
                        type="button"
                        title="Apercu de la facture"
                        onClick={() => setViewerAttachment({ url: inlineUrl, filename: a.filename })}
                        className="p-1 rounded hover:bg-blue-50 text-[#01213e]"
                        data-testid={`preview-attachment-${a.id}`}
                      >
                        <Eye size={14} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="form-label">N facture fournisseur *</label>
                <Input value={invForm.number} onChange={e => setInvForm({...invForm, number: e.target.value})} placeholder="Ex: V-260114" data-testid="inv-number" />
                {editingInvoice?.internal_reference && (
                  <p className="text-[10px] text-slate-500 mt-1">Ref. interne : <span className="font-mono text-[#022D52] font-semibold">{editingInvoice.internal_reference}</span></p>
                )}
                {!editingInvoice && (
                  <p className="text-[10px] text-slate-400 mt-1">Une reference interne <span className="font-mono">FA-AAAA-NNNN</span> sera auto-generee a la creation.</p>
                )}
              </div>
              <div><label className="form-label">Date *</label><Input type="date" value={invForm.date} onChange={e => setInvForm({...invForm, date: e.target.value})} /></div>
              <div><label className="form-label">Echeance</label><Input type="date" value={invForm.due_date} onChange={e => setInvForm({...invForm, due_date: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="form-label">Fournisseur *</label>
                <SupplierSearchSelect
                  suppliers={suppliers}
                  usedNames={[...new Set(invoices.map(i => (i.supplier || '').trim()).filter(Boolean))]}
                  value={invForm.supplier}
                  onChange={(name) => {
                    setInvForm(f => ({ ...f, supplier: name }));
                    // Auto-apprentissage : pre-remplit la nature de depense
                    // la plus utilisee pour ce fournisseur (non-destructif).
                    if (name) applySupplierSuggestion(name);
                  }}
                  onCreateSupplier={async (data) => {
                    const created = await createSupplierWithHomonymCheck(data);
                    return created;
                  }}
                  testId="inv-supplier"
                />
              </div>
              <div><label className="form-label">Description</label><Input value={invForm.description} onChange={e => setInvForm({...invForm, description: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Montant TTC *</label><Input type="number" step="0.01" value={invForm.total_amount} onChange={e => setInvForm({...invForm, total_amount: e.target.value})} data-testid="inv-amount" /></div>
              <div><label className="form-label">TVA</label><Input type="number" step="0.01" value={invForm.vat_amount} onChange={e => setInvForm({...invForm, vat_amount: e.target.value})} /></div>
              <div><label className="form-label">Statut</label>
                <Select value={invForm.status} onValueChange={v => setInvForm({...invForm, status: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Brouillon</SelectItem>
                    <SelectItem value="unpaid">Impayee</SelectItem>
                    <SelectItem value="paid">Payee</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="rounded-md border border-amber-200 bg-amber-50/50 p-3">
              <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-amber-900">
                <input
                  type="checkbox"
                  checked={!!invForm.is_private_fee}
                  onChange={e => setInvForm(f => ({
                    ...f,
                    is_private_fee: e.target.checked,
                    // Cocher Frais privatif vide les lignes multiples (incompatibles)
                    // et la cle de repartition
                    lines: e.target.checked ? [] : f.lines,
                    distribution_key_id: e.target.checked ? '' : f.distribution_key_id,
                  }))}
                  className="rounded border-amber-400 text-amber-600 focus:ring-amber-500"
                  data-testid="inv-private-fee"
                />
                Frais privatif (a charge d&apos;un seul proprietaire)
                {(invForm.lines && invForm.lines.length > 0) && !invForm.is_private_fee && (
                  <span className="ml-auto text-[10px] text-amber-700 italic">
                    (videra les {invForm.lines.length} lignes multiples)
                  </span>
                )}
              </label>
              {invForm.is_private_fee && (() => {
                // iter85e : tableau dynamique d'allocations (owner + montant)
                // iter85j : comparaison en CENTIMES (entiers) pour eviter les
                //          erreurs d'arrondi flottants (ex. 90.02 - 90.01 != 0.01)
                const allocs = invForm.private_fee_allocations || [];
                const total = Number(invForm.total_amount || 0);
                const sumCents = allocs.reduce((s, a) => s + Math.round((Number(a.amount) || 0) * 100), 0);
                const totalCents = Math.round(total * 100);
                const diffCents = totalCents - sumCents;
                const sum = sumCents / 100;
                const diff = diffCents / 100;
                const balanced = diffCents === 0;
                const addAlloc = () => {
                  setInvForm(f => ({
                    ...f,
                    private_fee_owner_id: '',
                    // iter90bn : _key stable pour eviter les collisions React
                    // (index-as-key -> perte de state / focus lors add/remove).
                    private_fee_allocations: [...(f.private_fee_allocations || []),
                      { _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`), owner_id: '', amount: 0 }]
                  }));
                };
                const updateAlloc = (idx, patch) => {
                  setInvForm(f => ({
                    ...f,
                    private_fee_allocations: (f.private_fee_allocations || []).map((a, i) => i === idx ? { ...a, ...patch } : a)
                  }));
                };
                const removeAlloc = (idx) => {
                  setInvForm(f => ({
                    ...f,
                    private_fee_allocations: (f.private_fee_allocations || []).filter((_, i) => i !== idx)
                  }));
                };
                return (
                  <div className="mt-3 space-y-2" data-testid="private-fee-allocations">
                    <div className="flex items-center justify-between">
                      <label className="form-label text-xs">Repartition par proprietaire * (somme = total facture)</label>
                      <button
                        type="button"
                        onClick={addAlloc}
                        className="text-[11px] text-amber-700 hover:text-amber-900 underline"
                        data-testid="add-private-fee-allocation"
                      >+ Ajouter un proprietaire</button>
                    </div>
                    {allocs.length === 0 && (
                      <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                        Aucun proprietaire selectionne. Cliquez sur &laquo;+ Ajouter un proprietaire&raquo; pour repartir la facture.
                      </div>
                    )}
                    {allocs.map((a, idx) => {
                      const selOwner = owners.find(o => o.id === a.owner_id);
                      // iter85i : combobox avec recherche (nom, prenom, VCS, email)
                      // au lieu du Select shadcn (inutilisable au-dela de 20 owners).
                      const excludedIds = allocs.filter((b, j) => j !== idx && b.owner_id).map(b => b.owner_id);
                      return (
                        <div key={a._key || idx} className="flex gap-2 items-start bg-white border border-amber-100 rounded p-2">
                          <div className="flex-1">
                            <OwnerComboboxAlloc
                              owners={owners}
                              value={a.owner_id || ''}
                              excludeIds={excludedIds}
                              onChange={(oid) => updateAlloc(idx, { owner_id: oid })}
                              testId={`alloc-owner-combo-${idx}`}
                            />
                            {selOwner?.email && <div className="text-[10px] text-slate-400 mt-1">{selOwner.email}</div>}
                          </div>
                          <div className="w-32">
                            <Input
                              type="number" step="0.01"
                              value={a.amount}
                              onChange={(e) => updateAlloc(idx, { amount: e.target.value })}
                              placeholder="Montant EUR"
                              className="text-xs h-8 text-right"
                              data-testid={`alloc-amount-${idx}`}
                            />
                          </div>
                          <button
                            type="button"
                            onClick={() => removeAlloc(idx)}
                            className="text-slate-400 hover:text-red-600 mt-1.5"
                            data-testid={`alloc-remove-${idx}`}
                            title="Supprimer cette ligne"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      );
                    })}
                    {allocs.length > 0 && (
                      <div className={`flex justify-between items-center text-xs px-2 py-1.5 rounded border ${balanced ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-rose-50 border-rose-200 text-rose-700'}`}>
                        <span>Somme allocations : <b>{sum.toFixed(2)} EUR</b> / Total facture : <b>{total.toFixed(2)} EUR</b></span>
                        <span className="flex items-center gap-2" data-testid="alloc-balance-status">
                          {balanced ? <>OK - equilibre</> : (
                            <>
                              <span>Ecart : <b>{diff.toFixed(2)} EUR</b></span>
                              <button
                                type="button"
                                onClick={() => {
                                  // Ajoute / retire le cent manquant sur la DERNIERE ligne
                                  setInvForm(f => {
                                    const arr = [...(f.private_fee_allocations || [])];
                                    if (arr.length === 0) return f;
                                    const last = arr.length - 1;
                                    const newAmt = Math.round((Number(arr[last].amount) || 0) * 100 + diffCents) / 100;
                                    arr[last] = { ...arr[last], amount: newAmt };
                                    return { ...f, private_fee_allocations: arr };
                                  });
                                }}
                                className="text-[11px] underline text-rose-700 hover:text-rose-900"
                                data-testid="alloc-auto-balance"
                                title="Ajuste le cent manquant sur la derniere ligne"
                              >
                                Equilibrer
                              </button>
                            </>
                          )}
                        </span>
                      </div>
                    )}
                    <p className="text-[10px] text-amber-700">
                      Chaque proprietaire sera debite de son montant via une OD (Dr 4100XXX owner / Cr 643).
                      Le fournisseur est credite du total via une AC (Dr 643 / Cr 44000XXX).
                    </p>
                  </div>
                );
              })()}
            </div>
            <div className={`grid grid-cols-3 gap-4 ${invForm.is_private_fee || (invForm.lines && invForm.lines.length > 0) ? 'opacity-50 pointer-events-none' : ''}`}>
              <div><label className="form-label">Nature de depense</label>
                <Select
                  value={invForm.expense_category_id || 'none'}
                  onValueChange={v => {
                    if (v === '__create__') { setNewCatDialog(true); return; }
                    if (v === 'none') { setInvForm(f => ({...f, expense_category_id: ''})); return; }
                    const cat = categories.find(c => c.id === v);
                    // Auto-pre-rempli les % occupant/proprietaire + la cle de repartition depuis la categorie selectionnee
                    const occ = cat?.default_occupant_pct ?? null;
                    const defKey = cat?.default_distribution_key_id || '';
                    setInvForm(f => ({
                      ...f,
                      expense_category_id: v,
                      account_number: cat?.account_number || f.account_number,
                      occupant_pct: occ != null ? Number(occ) : f.occupant_pct,
                      proprietaire_pct: occ != null ? +(100 - Number(occ)).toFixed(2) : f.proprietaire_pct,
                      distribution_key_id: defKey || f.distribution_key_id,
                    }));
                  }}
                >
                  <SelectTrigger data-testid="invoice-category-select"><SelectValue placeholder="Aucune" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Aucune —</SelectItem>
                    {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name} <span className="text-slate-400 ml-2 font-mono text-xs">({c.account_number})</span></SelectItem>)}
                    <SelectItem value="__create__" className="text-[#022D52] font-semibold">+ Creer une nature de depense...</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-slate-400 mt-1">Pre-rempli le compte PCMN + repartition occupant/proprio</p>
              </div>
              <div><label className="form-label">Compte PCMN</label>
                <AccountSearchSelect
                  accounts={accounts}
                  value={invForm.account_number}
                  onChange={v => setInvForm({...invForm, account_number: v})}
                  placeholder="Rechercher un compte..."
                  classFilter={6}
                  allowClear
                  testId="inv-account-search"
                />
              </div>
              <div><label className="form-label">Cle de repartition</label>
                <Select value={invForm.distribution_key_id} onValueChange={v => setInvForm({...invForm, distribution_key_id: v})}>
                  <SelectTrigger data-testid="inv-dist-key"><SelectValue placeholder={distKeys.length ? "Selectionner une cle" : "Aucune cle - creez-en une"} /></SelectTrigger>
                  <SelectContent>
                    {distKeys.map(k => (
                      <SelectItem key={k.id} value={k.id}>
                        {k.name}{k.is_default ? ' (defaut)' : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Bouton bascule vers le mode lignes multiples */}
            {!invForm.is_private_fee && (!invForm.lines || invForm.lines.length === 0) && (
              <div className="-mt-2">
                <button
                  type="button"
                  onClick={() => {
                    // Initialise avec la ligne courante (si compte rempli) + une ligne vide
                    const firstLine = invForm.account_number ? [{
                      account_number: invForm.account_number,
                      expense_category_id: invForm.expense_category_id || '',
                      distribution_key_id: invForm.distribution_key_id || defaultKeyId,
                      amount: Number(invForm.total_amount) || 0,
                      description: '',
                    }] : [];
                    setInvForm(f => ({ ...f, lines: [...firstLine, { _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`), account_number: '', expense_category_id: '', distribution_key_id: defaultKeyId, amount: 0, description: '' }] }));
                  }}
                  className="text-xs text-[#022D52] hover:text-[#1D4ED8] underline"
                  data-testid="enable-multi-lines-btn"
                >
                  <Plus size={11} className="inline mr-1" /> Splitter en plusieurs natures de depense
                </button>
              </div>
            )}

            {/* Bloc lignes multiples */}
            {!invForm.is_private_fee && invForm.lines && invForm.lines.length > 0 && (() => {
              const linesSum = invForm.lines.reduce((s, l) => s + (Number(l.amount) || 0), 0);
              const totalAmt = Number(invForm.total_amount) || 0;
              const diff = +(linesSum - totalAmt).toFixed(2);
              const ok = Math.abs(diff) < 0.01;
              return (
                <div className="rounded-md border-2 border-[#022D52]/30 bg-[#022D52]/5 p-3 space-y-2" data-testid="multi-lines-block">
                  <div className="flex items-center justify-between">
                    <div className="text-xs font-bold text-[#022D52] uppercase tracking-wider">
                      Lignes multiples ({invForm.lines.length})
                    </div>
                    <button
                      type="button"
                      onClick={() => setInvForm(f => ({ ...f, lines: [] }))}
                      className="text-[11px] text-slate-500 hover:text-red-600 underline"
                      data-testid="disable-multi-lines-btn"
                    >
                      Revenir au mode 1 nature
                    </button>
                  </div>
                  <div className="space-y-1.5">
                    {invForm.lines.map((ln, idx) => (
                      <div key={ln._key || idx} className="grid grid-cols-[repeat(15,minmax(0,1fr))] gap-2 items-end bg-white rounded border border-slate-200 px-2 py-1.5" data-testid={`invoice-line-${idx}`}>
                        <div className="col-span-3">
                          {idx === 0 && <label className="form-label text-[10px]">Nature</label>}
                          <Select
                            value={ln.expense_category_id || 'none'}
                            onValueChange={v => {
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                if (v === 'none') {
                                  newLines[idx] = { ...newLines[idx], expense_category_id: '' };
                                } else {
                                  const cat = categories.find(c => c.id === v);
                                  newLines[idx] = {
                                    ...newLines[idx],
                                    expense_category_id: v,
                                    account_number: cat?.account_number || newLines[idx].account_number,
                                    // Auto-pre-rempli la cle de repartition par defaut de la nature
                                    distribution_key_id: cat?.default_distribution_key_id || newLines[idx].distribution_key_id || '',
                                    // iter90ey : pre-remplir %Occ/%Prop depuis la nature
                                    // si l'utilisateur ne les a pas encore explicitement fixes.
                                    occupant_pct: (newLines[idx].occupant_pct == null && cat?.default_occupant_pct != null)
                                      ? cat.default_occupant_pct
                                      : newLines[idx].occupant_pct,
                                    proprietaire_pct: (newLines[idx].proprietaire_pct == null && cat?.default_proprietaire_pct != null)
                                      ? cat.default_proprietaire_pct
                                      : newLines[idx].proprietaire_pct,
                                  };
                                }
                                return { ...f, lines: newLines };
                              });
                            }}
                          >
                            <SelectTrigger className="h-8 text-xs" data-testid={`invoice-line-cat-${idx}`}><SelectValue placeholder="Categorie" /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="none">—</SelectItem>
                              {categories.map(c => <SelectItem key={c.id} value={c.id}>{c.name} <span className="text-slate-400 ml-1 font-mono text-[10px]">({c.account_number})</span></SelectItem>)}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="col-span-3">
                          {idx === 0 && <label className="form-label text-[10px]">Compte PCMN *</label>}
                          <AccountSearchSelect
                            accounts={accounts}
                            value={ln.account_number}
                            onChange={v => {
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = { ...newLines[idx], account_number: v };
                                return { ...f, lines: newLines };
                              });
                            }}
                            placeholder="Compte..."
                            classFilter={6}
                            allowClear
                            testId={`invoice-line-acc-${idx}`}
                          />
                        </div>
                        <div className="col-span-2">
                          {idx === 0 && <label className="form-label text-[10px]">Cle</label>}
                          <Select
                            value={ln.distribution_key_id || ''}
                            onValueChange={v => {
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = { ...newLines[idx], distribution_key_id: v };
                                return { ...f, lines: newLines };
                              });
                            }}
                          >
                            <SelectTrigger className="h-8 text-xs" data-testid={`invoice-line-key-${idx}`}><SelectValue placeholder={distKeys.length ? "Cle" : "—"} /></SelectTrigger>
                            <SelectContent>
                              {distKeys.map(k => (
                                <SelectItem key={k.id} value={k.id}>
                                  {k.name}{k.is_default ? ' (defaut)' : ''}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="col-span-2">
                          {idx === 0 && (
                            <label
                              className="form-label text-[10px]"
                              title="Optionnel. Si vide, la description generale de la facture est utilisee pour cette ligne. Sinon, ce commentaire la remplace uniquement pour cette ligne dans la liste des depenses."
                            >
                              Commentaire
                            </label>
                          )}
                          <Input
                            value={ln.description || ''}
                            onChange={e => {
                              const v = e.target.value;
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = { ...newLines[idx], description: v };
                                return { ...f, lines: newLines };
                              });
                            }}
                            className="h-8 text-xs"
                            placeholder={invForm.description ? `« ${invForm.description.slice(0, 24)}${invForm.description.length > 24 ? '…' : ''} »` : 'Optionnel'}
                            title="Vide = herite de la description generale. Rempli = remplace pour cette ligne."
                            data-testid={`invoice-line-desc-${idx}`}
                          />
                        </div>
                        {/* iter90ey : repartition Occupant / Proprietaire par ligne */}
                        <div className="col-span-1">
                          {idx === 0 && (
                            <label
                              className="form-label text-[10px]"
                              title="Pourcentage occupant sur cette ligne. Si laisse vide, la repartition globale de la facture (bas du formulaire) s'applique."
                            >
                              % Occ
                            </label>
                          )}
                          <Input
                            type="number" step="0.01" min="0" max="100"
                            value={ln.occupant_pct ?? ''}
                            onChange={e => {
                              const raw = e.target.value;
                              const v = raw === '' ? null : Math.max(0, Math.min(100, parseFloat(raw) || 0));
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = {
                                  ...newLines[idx],
                                  occupant_pct: v,
                                  proprietaire_pct: v == null ? null : Math.max(0, 100 - v),
                                };
                                return { ...f, lines: newLines };
                              });
                            }}
                            className="h-8 text-xs text-right font-mono"
                            placeholder={ln.expense_category_id ? '(nature)' : '(global)'}
                            data-testid={`invoice-line-occ-${idx}`}
                          />
                        </div>
                        <div className="col-span-1">
                          {idx === 0 && <label className="form-label text-[10px]">% Prop</label>}
                          <Input
                            type="number" step="0.01" min="0" max="100"
                            value={ln.proprietaire_pct ?? ''}
                            onChange={e => {
                              const raw = e.target.value;
                              const v = raw === '' ? null : Math.max(0, Math.min(100, parseFloat(raw) || 0));
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = {
                                  ...newLines[idx],
                                  proprietaire_pct: v,
                                  occupant_pct: v == null ? null : Math.max(0, 100 - v),
                                };
                                return { ...f, lines: newLines };
                              });
                            }}
                            className="h-8 text-xs text-right font-mono"
                            placeholder={ln.expense_category_id ? '(nature)' : '(global)'}
                            data-testid={`invoice-line-prop-${idx}`}
                          />
                        </div>
                        <div className="col-span-2">
                          {idx === 0 && <label className="form-label text-[10px]">Montant *</label>}
                          <Input
                            type="number" step="0.01"
                            value={ln.amount}
                            onChange={e => {
                              const v = e.target.value;
                              setInvForm(f => {
                                const newLines = [...f.lines];
                                newLines[idx] = { ...newLines[idx], amount: v };
                                return { ...f, lines: newLines };
                              });
                            }}
                            className="h-8 text-xs text-right font-mono"
                            data-testid={`invoice-line-amount-${idx}`}
                          />
                        </div>
                        <div className="col-span-1 flex items-center justify-end">
                          <button
                            type="button"
                            onClick={() => setInvForm(f => ({ ...f, lines: f.lines.filter((_, i) => i !== idx) }))}
                            className="p-1 rounded hover:bg-red-50 text-red-500"
                            title="Supprimer la ligne"
                            data-testid={`invoice-line-remove-${idx}`}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between pt-2 border-t border-[#022D52]/20">
                    <button
                      type="button"
                      onClick={() => setInvForm(f => ({ ...f, lines: [...f.lines, { _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`), account_number: '', expense_category_id: '', distribution_key_id: defaultKeyId, amount: 0, description: '' }] }))}
                      className="text-xs text-[#022D52] hover:text-[#1D4ED8] font-semibold"
                      data-testid="invoice-line-add"
                    >
                      <Plus size={12} className="inline mr-1" /> Ajouter une ligne
                    </button>
                    <div className="text-xs flex items-center gap-3">
                      <span className="text-slate-600">Somme :</span>
                      <span className={`font-mono font-bold ${ok ? 'text-emerald-700' : 'text-red-600'}`} data-testid="invoice-lines-sum">
                        {linesSum.toFixed(2)} EUR
                      </span>
                      <span className="text-slate-500">/ Total :</span>
                      <span className="font-mono">{totalAmt.toFixed(2)} EUR</span>
                      {!ok && (
                        <span className="text-[10px] text-red-600 font-semibold">
                          {diff > 0 ? `(+${diff.toFixed(2)})` : `(${diff.toFixed(2)})`}
                        </span>
                      )}
                      {ok && <span className="text-[10px] text-emerald-600">OK</span>}
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* Repartition occupant / proprietaire (decompte locataire) */}
            <div className="rounded-md border border-amber-200 bg-amber-50/40 p-3 space-y-2" data-testid="invoice-occupant-section">
              <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
                Repartition occupant / proprietaire (decompte locataire)
              </div>
              <div className="grid grid-cols-4 gap-3 items-end">
                <div>
                  <label className="form-label text-xs">% Occupant</label>
                  <Input type="number" min={0} max={100} step={1}
                    value={invForm.occupant_pct}
                    onChange={e => {
                      const v = Math.max(0, Math.min(100, parseFloat(e.target.value) || 0));
                      setInvForm(f => ({ ...f, occupant_pct: v, proprietaire_pct: +(100 - v).toFixed(2) }));
                    }}
                    data-testid="inv-occupant-pct"
                  />
                </div>
                <div>
                  <label className="form-label text-xs">% Proprietaire</label>
                  <Input type="number" min={0} max={100} step={1}
                    value={invForm.proprietaire_pct}
                    onChange={e => {
                      const v = Math.max(0, Math.min(100, parseFloat(e.target.value) || 0));
                      setInvForm(f => ({ ...f, proprietaire_pct: v, occupant_pct: +(100 - v).toFixed(2) }));
                    }}
                    data-testid="inv-proprietaire-pct"
                  />
                </div>
                <div className="text-xs">
                  <div className="text-slate-500 uppercase tracking-wide text-[10px]">Part occupant</div>
                  <div className="font-mono font-semibold text-amber-700">{((Number(invForm.total_amount) || 0) * (Number(invForm.occupant_pct) || 0) / 100).toFixed(2)} EUR</div>
                </div>
                <div className="text-xs">
                  <div className="text-slate-500 uppercase tracking-wide text-[10px]">Part proprietaire</div>
                  <div className="font-mono font-semibold text-[#01213e]">{((Number(invForm.total_amount) || 0) * (Number(invForm.proprietaire_pct) || 0) / 100).toFixed(2)} EUR</div>
                </div>
              </div>
              <p className="text-[11px] text-slate-500">Total doit etre 100%. Pre-rempli depuis la nature de depense si selectionnee.</p>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => {
                // Iter90di : si queue de fichiers active, demander confirmation
                if (pendingAiFiles.length > 0 || aiBatchTotal > 1) {
                  const remaining = pendingAiFiles.length + 1;
                  if (!window.confirm(`Il reste ${remaining} facture(s) a traiter dans le batch. Abandonner tout le batch ?`)) {
                    return;
                  }
                  setPendingAiFiles([]);
                  setAiBatchTotal(0);
                  setAiBatchIndex(0);
                  toast.info(`Batch abandonne (${remaining} fichier(s) non traites)`);
                }
                setInvoiceDialog(false);
              }}>Annuler</Button>
              {/* iter90ee : bouton "Ignorer" en mode batch pour passer la facture courante */}
              {(pendingAiFiles.length > 0 || aiBatchTotal > 1) && !editingInvoice && (
                <Button
                  variant="outline"
                  className="border-amber-300 text-amber-700 hover:bg-amber-50"
                  onClick={() => {
                    const remaining = pendingAiFiles.length;
                    toast.warning(`Facture ${aiBatchIndex}/${aiBatchTotal} ignoree`);
                    if (remaining > 0) {
                      const [next, ...rest] = pendingAiFiles;
                      setPendingAiFiles(rest);
                      setAiBatchIndex(prev => prev + 1);
                      setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
                      setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
                      setTimeout(() => {
                        openCreateInvoice();
                        aiExtractFromPdf(next);
                      }, 300);
                    } else {
                      if (aiBatchTotal > 1) {
                        toast.success(`Batch termine (${aiBatchTotal} traitees)`, { duration: 5000 });
                      }
                      setAiBatchTotal(0); setAiBatchIndex(0);
                      setInvoiceDialog(false); setPendingPdf(null); setEditingInvoice(null);
                      setLastAiRawText(''); setLastAiSupplierIdGuess(''); setLastAiValues(null); setLastAiExtractionSource('');
                    }
                  }}
                  data-testid="inv-skip-btn"
                  title="Ignorer cette facture et passer a la suivante"
                >
                  <SkipForward size={14} className="mr-1.5" /> Ignorer et suivant
                </Button>
              )}
              <Button
                onClick={saveInvoice}
                disabled={aiExtracting}
                className="bg-[#022D52] hover:bg-[#1D4ED8] disabled:opacity-50 disabled:cursor-not-allowed"
                title={aiExtracting ? "Extraction IA en cours - patientez..." : ""}
                data-testid="inv-save-btn"
              >
                {aiExtracting ? (
                  <><Loader2 size={14} className="mr-2 animate-spin" /> Extraction en cours...</>
                ) : 'Enregistrer'}
              </Button>
            </div>
            </div>
            {/* RIGHT COLUMN : PDF/image side panel - iter90ez */}
            {pdfPanelUrl && (
              pdfPanelOpen ? (
                <div className="w-[38%] shrink-0 flex flex-col border-l border-slate-200 pl-3" data-testid="invoice-pdf-panel">
                  <div className="flex items-center justify-between mb-2 shrink-0">
                    <div className="text-xs font-semibold text-slate-700 flex items-center gap-1 min-w-0">
                      <FileText size={13} className="shrink-0 text-purple-600" />
                      <span className="truncate" title={pendingPdf?.filename || 'Facture'}>{pendingPdf?.filename || 'Facture'}</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setPdfPanelOpen(false)}
                      title="Reduire le volet"
                      className="p-1 rounded hover:bg-slate-100 text-slate-600 shrink-0"
                      data-testid="pdf-panel-collapse-btn"
                    >
                      <PanelRightClose size={16} />
                    </button>
                  </div>
                  {(() => {
                    const isImg = /\.(jpe?g|png|webp|gif|bmp)$/i.test(pendingPdf?.filename || '');
                    return isImg ? (
                      <img
                        src={pdfPanelUrl}
                        alt="Facture"
                        className="flex-1 w-full min-h-0 object-contain rounded border border-slate-200 bg-slate-50"
                      />
                    ) : (
                      <iframe
                        src={pdfPanelUrl}
                        title="Facture"
                        className="flex-1 w-full min-h-0 rounded border border-slate-200 bg-white"
                        data-testid="pdf-panel-iframe"
                      />
                    );
                  })()}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setPdfPanelOpen(true)}
                  title="Ouvrir l'apercu de la facture"
                  className="w-9 shrink-0 border-l border-slate-200 pl-2 flex flex-col items-center gap-2 pt-2 text-slate-600 hover:bg-slate-50"
                  data-testid="pdf-panel-expand-btn"
                >
                  <PanelRightOpen size={16} />
                  <span className="[writing-mode:vertical-rl] rotate-180 text-[10px] font-medium tracking-wide text-slate-500">Apercu facture</span>
                </button>
              )
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Attachments Dialog */}
      <Dialog open={!!attachDialogInv} onOpenChange={() => setAttachDialogInv(null)}>
        <DialogContent className="max-w-2xl w-[min(92vw,720px)]" data-testid="invoice-attach-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="truncate pr-8">
              Pieces jointes - {attachDialogInv?.number}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="border-2 border-dashed rounded p-4 text-center">
              <input type="file" accept="application/pdf,image/*" className="hidden" id="att-input-inv"
                onChange={(e) => { const f = e.target.files?.[0]; if (f && attachDialogInv) uploadInvoiceAttachment(attachDialogInv.id, f); e.target.value=''; }} />
              <Button variant="outline" onClick={() => document.getElementById('att-input-inv').click()} data-testid="upload-attachment-inv-btn">
                <Paperclip size={14} className="mr-2" /> Ajouter un PDF / Image
              </Button>
              <div className="text-xs text-slate-500 mt-1">PDF, PNG ou JPG</div>
            </div>
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {(attachDialogInv?.attachments || []).length === 0 ? (
                <div className="text-sm text-slate-400 text-center py-3">Aucune piece jointe</div>
              ) : attachDialogInv.attachments.map((a) => {
                const coproId = localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || '';
                const inlineUrl = `${API}/api/invoices/${attachDialogInv.id}/attachments/${a.id}/download?disposition=inline&copropriete_id=${coproId}`;
                const downloadUrl = `${API}/api/invoices/${attachDialogInv.id}/attachments/${a.id}/download?copropriete_id=${coproId}`;
                return (
                  <div key={a.id} className="flex items-center gap-2 border rounded px-2 py-1.5 text-sm min-w-0">
                    <button
                      type="button"
                      className="flex-1 min-w-0 truncate flex items-center gap-2 text-left text-[#022D52] hover:text-blue-800 hover:underline cursor-pointer"
                      onClick={() => setViewerAttachment({ url: inlineUrl, filename: a.filename })}
                      data-testid={`view-attachment-${a.id}`}
                      title={a.filename}
                    >
                      <Paperclip size={12} className="flex-shrink-0" />
                      <span className="truncate">{a.filename}</span>
                    </button>
                    <div className="flex gap-1 flex-shrink-0">
                      <a href={downloadUrl} target="_blank" rel="noreferrer" title="Telecharger">
                        <Button variant="ghost" size="sm"><Download size={14} /></Button>
                      </a>
                      <Button variant="ghost" size="sm" className="text-red-500" onClick={() => deleteInvoiceAttachment(attachDialogInv.id, a.id)} title="Supprimer"><Trash2 size={14} /></Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* PDF / Image viewer modal */}
      <Dialog open={!!viewerAttachment} onOpenChange={(o) => {
        if (!o) {
          // Revoke blob URL to free memory when previewing a pending file
          if (viewerAttachment?.isBlob && viewerAttachment?.url) {
            try { URL.revokeObjectURL(viewerAttachment.url); } catch { /* noop */ }
          }
          setViewerAttachment(null);
        }
      }}>
        <DialogContent className="max-w-6xl w-[95vw] h-[92vh] p-0 flex flex-col overflow-hidden" data-testid="attachment-viewer">
          <DialogHeader className="px-4 py-2 border-b">
            <DialogTitle className="text-sm font-medium flex items-center justify-between gap-2 pr-8">
              <span className="truncate flex items-center gap-2"><Paperclip size={14} />{viewerAttachment?.filename}</span>
              {viewerAttachment && (
                <a
                  href={viewerAttachment.isBlob ? viewerAttachment.url : viewerAttachment.url.replace('disposition=inline', 'disposition=attachment')}
                  download={viewerAttachment.isBlob ? viewerAttachment.filename : undefined}
                  target="_blank" rel="noreferrer"
                >
                  <Button variant="outline" size="sm" data-testid="viewer-download-btn"><Download size={14} className="mr-1" /> Télécharger</Button>
                </a>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 bg-slate-100 overflow-hidden">
            {viewerAttachment && (
              <iframe
                src={viewerAttachment.url}
                title={viewerAttachment.filename}
                className="w-full h-full border-0"
                data-testid="viewer-iframe"
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Bundle PDF import dialog */}
      <BundleImportDialog
        open={bundleDialog}
        onOpenChange={setBundleDialog}
        invoices={invoices}
        distKeys={distKeys}
        categories={categories}
        accounts={accounts}
        onSuccess={load}
      />

      {/* Create Expense Category inline dialog */}
      <Dialog open={newCatDialog} hasUnsavedChanges={newCatDirty} onOpenChange={async (open) => {
        setNewCatDialog(open);
        // iter90ec : refetch les comptes PCMN a l'ouverture pour inclure les
        // comptes tout juste crees (custom) dans une autre page.
        if (open) {
          try {
            const { data } = await api.get('/accounting/pcmn', { params: { class_num: 6 } });
            setAccounts(data);
          } catch (err) { /* silent : fallback sur la liste existante */ }
        }
      }}>
        <DialogContent className="max-w-md" data-testid="new-category-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Nouvelle nature de depense</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <label className="form-label">Nom *</label>
              <Input
                value={newCatForm.name}
                onChange={e => setNewCatForm({...newCatForm, name: e.target.value})}
                placeholder="Ex: Entretien chaudiere"
                data-testid="new-cat-name"
              />
            </div>
            <div>
              <label className="form-label">Compte PCMN (classe 6) *</label>
              <AccountSearchSelect
                accounts={accounts.filter(a => (a.number || '').startsWith('6'))}
                value={newCatForm.account_number}
                onChange={v => setNewCatForm({...newCatForm, account_number: v})}
                placeholder="6XXX..."
                testId="new-cat-account"
              />
              <p className="text-[10px] text-slate-400 mt-1">Plusieurs natures peuvent partager le meme compte PCMN.</p>
            </div>
            <div>
              <label className="form-label">Description</label>
              <Input
                value={newCatForm.description}
                onChange={e => setNewCatForm({...newCatForm, description: e.target.value})}
                placeholder="Optionnel"
                data-testid="new-cat-description"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setNewCatDialog(false)}>Annuler</Button>
              <Button
                className="bg-[#022D52] hover:bg-[#1D4ED8]"
                disabled={!newCatForm.name.trim() || !newCatForm.account_number.trim()}
                onClick={async () => {
                  try {
                    const { data } = await api.post('/expense-categories', {
                      name: newCatForm.name.trim(),
                      account_number: newCatForm.account_number.trim(),
                      description: newCatForm.description.trim(),
                    });
                    toast.success('Nature de depense creee');
                    setCategories(prev => [...prev, data].sort((a, b) => (a.name || '').localeCompare(b.name || '')));
                    setInvForm(f => ({...f, expense_category_id: data.id, account_number: data.account_number}));
                    setNewCatForm({ name: '', account_number: '', description: '' });
                    setNewCatDialog(false);
                  } catch (err) {
                    toast.error(err.response?.data?.detail || 'Erreur creation');
                  }
                }}
                data-testid="new-cat-submit"
              >
                Creer et selectionner
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {/* iter85g : Dialog de confirmation homonymes lors de creation supplier
          depuis le dialog facture (suggestion IA ou autocomplete) */}
      <Dialog open={!!supplierHomonymsDialog} onOpenChange={(o) => { if (!o && supplierHomonymsDialog) supplierHomonymsDialog.onCancel(); }}>
        <DialogContent className="max-w-2xl" data-testid="invoice-supplier-homonyms-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="flex items-center gap-2 text-amber-700">
              <Sparkles size={18} /> Homonymes potentiels detectes
            </DialogTitle>
          </DialogHeader>
          {supplierHomonymsDialog && (
            <div className="space-y-4 mt-2">
              <p className="text-sm text-slate-700">
                Vous etes sur le point de creer le fournisseur <b>&quot;{supplierHomonymsDialog.payload.name}&quot;</b>.
                Des fournisseurs existent deja avec un nom similaire :
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded p-3 space-y-2 max-h-72 overflow-auto">
                {supplierHomonymsDialog.similar.map((m, i) => (
                  <div key={i} className="flex items-center justify-between bg-white border border-amber-100 rounded p-2" data-testid={`inv-similar-supplier-${i}`}>
                    <div className="flex-1">
                      <div className="font-medium text-sm">{m.supplier.name}</div>
                      <div className="text-[11px] text-slate-500 space-x-3">
                        {m.supplier.vat_number && <span>TVA: {m.supplier.vat_number}</span>}
                        {m.supplier.city && <span>{m.supplier.city}</span>}
                        {m.supplier.iban && <span className="font-mono">{m.supplier.iban}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-amber-700">{Math.round(m.score * 100)}% similarite</span>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-[#01213e] border-blue-200 h-7 text-[11px]"
                        onClick={() => supplierHomonymsDialog.onUseExisting(m.supplier)}
                        data-testid={`inv-use-existing-supplier-${i}`}
                      >
                        Utiliser celui-ci
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-slate-500 italic">
                Le fait de cliquer &laquo;Utiliser celui-ci&raquo; remplit automatiquement le champ Fournisseur de la facture.
              </p>
              <div className="flex gap-3 justify-end">
                <Button variant="outline" onClick={() => supplierHomonymsDialog.onCancel()} data-testid="inv-similar-cancel-btn">
                  Annuler
                </Button>
                <Button
                  onClick={() => supplierHomonymsDialog.onConfirm(true)}
                  className="bg-amber-600 hover:bg-amber-700 text-white"
                  data-testid="inv-similar-force-create-btn"
                >
                  Creer quand meme
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
      {/* iter90fj : dialog BLOQUANT lors de l'enregistrement d'une facture -
          homonyme detecte sur le champ Fournisseur (pas de fiche exacte,
          mais nom proche d'une fiche existante). La facture n'est PAS
          enregistree tant que le syndic n'a pas explicitement choisi. */}
      <Dialog open={!!invSupplierGate} onOpenChange={(o) => { if (!o && invSupplierGate) invSupplierGate.onCancel(); }}>
        <DialogContent className="max-w-2xl" data-testid="invoice-supplier-gate-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="flex items-center gap-2 text-amber-700">
              <ShieldAlert size={18} /> Fournisseur a confirmer avant enregistrement
            </DialogTitle>
          </DialogHeader>
          {invSupplierGate && (
            <div className="space-y-4 mt-2">
              <p className="text-sm text-slate-700">
                Le nom saisi <b>&quot;{invSupplierGate.typedName}&quot;</b> ne correspond a aucune fiche
                fournisseur exacte, mais ressemble a {invSupplierGate.similar.length > 1 ? 'des fournisseurs' : 'un fournisseur'} deja enregistre(s).
                La facture ne sera PAS enregistree tant que vous n'avez pas choisi une option ci-dessous.
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded p-3 space-y-2 max-h-72 overflow-auto">
                {invSupplierGate.similar.map((m, i) => (
                  <div key={i} className="flex items-center justify-between bg-white border border-amber-100 rounded p-2" data-testid={`inv-gate-similar-supplier-${i}`}>
                    <div className="flex-1">
                      <div className="font-medium text-sm">{m.name}</div>
                      <div className="text-[11px] text-slate-500 space-x-3">
                        {m.vat_number && <span>TVA: {m.vat_number}</span>}
                        {m.bce_number && <span>BCE: {m.bce_number}</span>}
                        {m.city && <span>{m.city}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-amber-700">{Math.round(m.score * 100)}% similarite</span>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-[#01213e] border-blue-200 h-7 text-[11px]"
                        onClick={() => invSupplierGate.onUseExisting(m.name)}
                        data-testid={`inv-gate-use-existing-${i}`}
                      >
                        Utiliser celui-ci
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex gap-3 justify-between items-center">
                <Button variant="outline" onClick={() => invSupplierGate.onCancel()} data-testid="inv-gate-cancel-btn">
                  Annuler
                </Button>
                <Button
                  onClick={() => invSupplierGate.onCreateNew()}
                  className="bg-amber-600 hover:bg-amber-700 text-white"
                  data-testid="inv-gate-create-new-btn"
                >
                  Confirmer nouveau fournisseur &quot;{invSupplierGate.typedName}&quot;
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

// iter85i : Combobox proprietaire avec recherche pour le tableau d'allocations
// frais privatifs. Filtre par nom, prenom, VCS code, email. Exclut les owners
// deja selectionnes dans d'autres lignes (excludeIds).
function OwnerComboboxAlloc({ owners, value, excludeIds = [], onChange, testId = '' }) {
  const [open, setOpen] = useState(false);
  const selected = owners.find(o => o.id === value);
  const available = owners.filter(o => !excludeIds.includes(o.id));
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between text-xs h-8 font-normal"
          data-testid={testId || 'alloc-owner-combo'}
        >
          {selected ? (
            <span className="truncate flex items-center gap-1">
              {selected.name}
              {selected.vcs_code && <span className="text-slate-400 font-mono text-[10px]">{selected.vcs_code}</span>}
            </span>
          ) : (
            <span className="text-slate-400">Rechercher un proprietaire (nom, VCS, email)...</span>
          )}
          <ChevronsUpDown size={14} className="ml-2 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[380px] p-0" align="start">
        <Command
          filter={(itemValue, search) => {
            // itemValue contient le nom + vcs + email concatenes en lowercase
            return itemValue.toLowerCase().includes(search.toLowerCase()) ? 1 : 0;
          }}
        >
          <CommandInput placeholder="Tapez le nom, VCS ou email..." className="text-xs h-9" data-testid={`${testId || 'alloc-owner-combo'}-input`} />
          <CommandList className="max-h-72">
            <CommandEmpty className="text-xs text-slate-500 py-4 text-center">Aucun proprietaire trouve.</CommandEmpty>
            <CommandGroup>
              {available.map(o => {
                const haystack = `${o.name || ''} ${o.vcs_code || ''} ${o.email || ''}`.toLowerCase();
                return (
                  <CommandItem
                    key={o.id}
                    value={haystack}
                    onSelect={() => { onChange(o.id); setOpen(false); }}
                    className="text-xs cursor-pointer"
                    data-testid={`${testId || 'alloc-owner-combo'}-option-${o.id}`}
                  >
                    <Check size={12} className={`mr-2 ${value === o.id ? 'opacity-100' : 'opacity-0'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="truncate">{o.name}</div>
                      <div className="text-[10px] text-slate-400 truncate space-x-2">
                        {o.vcs_code && <span className="font-mono">{o.vcs_code}</span>}
                        {o.email && <span>{o.email}</span>}
                      </div>
                    </div>
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

