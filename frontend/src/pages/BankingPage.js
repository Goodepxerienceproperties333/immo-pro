import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from 'sonner';
import { Plus, Trash2, Upload, Link2, Unlink, Search, Landmark, PlusCircle, Save, Pencil, X, CheckCircle2, AlertTriangle, Eye, Tag, Zap } from 'lucide-react';
import { useFiscalYearParams } from '@/hooks/useFiscalYearParams';
import { useAuth } from '@/contexts/AuthContext';
import CounterpartySearchSelect from '@/components/CounterpartySearchSelect';
import AccountSearchSelect from '@/components/AccountSearchSelect';
import CodaImportDialog from '@/components/CodaImportDialog';
import { fmtDate } from '@/lib/dateFmt';

export default function BankingPage() {
  const { selectedCopro, selectedFiscalYear, selectedFiscalYearId, setSelectedFiscalYearId } = useAuth();
  const navigate = useNavigate();
  const fyParams = useFiscalYearParams();
  const [statements, setStatements] = useState([]);
  // iter90gr : nombre TOTAL d'extraits toutes periodes confondues (sans filtre FY).
  // Sert a detecter le cas "l'utilisateur a des extraits en base mais le filtre
  // FY courant les masque tous" - on l'aide alors a comprendre la situation.
  const [totalStatementsAllPeriods, setTotalStatementsAllPeriods] = useState(0);
  const [transactions, setTransactions] = useState([]);
  const [selectedStmt, setSelectedStmt] = useState(null);
  const [stmtDialog, setStmtDialog] = useState(false);
  const [editingStmtId, setEditingStmtId] = useState(null);
  const [codaUploading, setCodaUploading] = useState(false);
  const [lettrageDialog, setLettrageDialog] = useState(false);
  const [lettrageTarget, setLettrageTarget] = useState(null);
  const [editingTxn, setEditingTxn] = useState(null);
  const [owners, setOwners] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [lookupResults, setLookupResults] = useState(null);
  const [lookupQuery, setLookupQuery] = useState('');
  const codaRef = useRef(null);
  const [codaPreview, setCodaPreview] = useState(null);
  const [codaDialogOpen, setCodaDialogOpen] = useState(false);
  // iter90l : import PDF/CSV multi-fichiers d'extraits (IA + regex CSV)
  const importRef = useRef(null);
  const [importUploading, setImportUploading] = useState(false);
  const [stmtForm, setStmtForm] = useState({ number: '', date: '', account_number: '', opening_balance: 0, closing_balance: 0 });
  const [bankAccounts, setBankAccounts] = useState([]);
  const [inlineLines, setInlineLines] = useState([]);
  const [editForm, setEditForm] = useState({});
  // ----- Multi-selection lettrage state -----
  const [selectedTxnIds, setSelectedTxnIds] = useState(new Set());
  const [batchLettrageDialog, setBatchLettrageDialog] = useState(false);
  const [batchInvoiceSearch, setBatchInvoiceSearch] = useState('');
  // 1 txn -> N invoices (multi-selection des factures dans le dialog lettrage de transaction)
  const [selectedInvoiceIds, setSelectedInvoiceIds] = useState(new Set());
  // iter90eb : Liens deja lettres sur la transaction en cours d'ouverture
  //   { matched, match_type, lettrage_code, invoices[], owner, supplier, sibling_transactions[] }
  const [letteredLinks, setLetteredLinks] = useState(null);
  const [letteredLinksLoading, setLetteredLinksLoading] = useState(false);
  // ----- iter90k : Categorisation par nature de depense/revenu -----
  const [expenseCategories, setExpenseCategories] = useState([]);
  const [distributionKeys, setDistributionKeys] = useState([]);
  const [pcmnAccounts, setPcmnAccounts] = useState([]);
  const [categorizeDialog, setCategorizeDialog] = useState(false);
  const [categorizeTarget, setCategorizeTarget] = useState(null);
  const [categorizeSplits, setCategorizeSplits] = useState([]);
  // iter90ja : filtres cascade + readiness summary
  const [readiness, setReadiness] = useState({ total: 0, draft: 0, posted: 0, ready_to_post: 0, needs_review: 0, draft_ids_ready: [], draft_ids_needs_review: [] });
  const [filterAccount, setFilterAccount] = useState('__all__'); // account_number (IBAN)
  const [filterYear, setFilterYear] = useState('__all__'); // 'YYYY'
  const [filterMonth, setFilterMonth] = useState('__all__'); // 'MM'
  const [batchPosting, setBatchPosting] = useState(false);

  const [deletePreview, setDeletePreview] = useState(null);
  const [deletePreviewLoading, setDeletePreviewLoading] = useState(false);
  // iter90bg : mapping compte -> badge visuel distinctif
  //   Recherche le bank_account correspondant au statement (via
  //   account_number matchant iban ou pcmn_number). Retourne { label, color }.
  //   Les couleurs sont deterministes (hash du pcmn_number) pour rester
  //   stables entre les rechargements et coherentes visuellement.
  const _BA_COLORS = [
    { bg: 'bg-blue-100',    text: 'text-blue-800',    border: 'border-blue-300' },
    { bg: 'bg-emerald-100', text: 'text-emerald-800', border: 'border-emerald-300' },
    { bg: 'bg-purple-100',  text: 'text-purple-800',  border: 'border-purple-300' },
    { bg: 'bg-amber-100',   text: 'text-amber-800',   border: 'border-amber-300' },
    { bg: 'bg-rose-100',    text: 'text-rose-800',    border: 'border-rose-300' },
    { bg: 'bg-cyan-100',    text: 'text-cyan-800',    border: 'border-cyan-300' },
    { bg: 'bg-fuchsia-100', text: 'text-fuchsia-800', border: 'border-fuchsia-300' },
    { bg: 'bg-indigo-100',  text: 'text-indigo-800',  border: 'border-indigo-300' },
  ];
  const getBankAccountBadge = (stmt) => {
    const accNum = (stmt.account_number || '').trim();
    if (!accNum) return null;
    // iter90jb : matching par IBAN normalise (source of truth = sans separateur).
    // Evite de louper une correspondance a cause d'un espace legacy dans l'un des deux.
    const normAcc = accNum.toUpperCase().replace(/[\s.-]+/g, '');
    const ba = bankAccounts.find(b => {
      const nib = (b.iban || '').toUpperCase().replace(/[\s.-]+/g, '');
      return nib === normAcc || b.pcmn_number === accNum;
    });
    // Label : label defini ou "vue/epargne + IBAN 4 derniers" en fallback
    let label = ba?.label?.trim() || '';
    if (!label) {
      const type = ba?.account_type || '';
      const last4 = (ba?.iban || accNum).slice(-4);
      label = (type ? `${type.charAt(0).toUpperCase()}${type.slice(1)}` : 'Compte') + ' *' + last4;
    }
    // Couleur deterministe : hash simple sur l'IBAN normalise
    const key = ba?.pcmn_number || normAcc;
    let h = 0;
    for (let i = 0; i < key.length; i++) h = ((h << 5) - h + key.charCodeAt(i)) | 0;
    const c = _BA_COLORS[Math.abs(h) % _BA_COLORS.length];
    return { label, ...c };
  };

  const load = useCallback(async () => {
    const promises = [
      api.get('/banking/statements', { params: fyParams }),
      api.get('/banking/transactions', { params: fyParams }),
      selectedCopro
        ? api.get('/owners', { params: { copropriete_id: selectedCopro } })
        : Promise.resolve({ data: [] }),
      api.get('/invoices', { params: fyParams }),
      api.get('/suppliers'),
      api.get('/expense-categories').catch(() => ({ data: [] })),
      api.get('/distribution-keys').catch(() => ({ data: [] })),
      api.get('/accounting/pcmn').catch(() => ({ data: [] })),
    ];
    // iter90iy-v2 : tolerance sur /coproprietes/{id} - si l'ACP a ete supprimee
    // ou n'existe pas dans le scope, on ne veut pas casser tout le Promise.all
    // (sinon overlay "Uncaught runtime errors" sur la page Banque).
    if (selectedCopro) promises.push(api.get(`/coproprietes/${selectedCopro}`).catch(() => ({ data: null })));
    const [s, t, o, inv, sup, cats, dks, pcmn, c] = await Promise.all(promises);
    setStatements(s.data); setTransactions(t.data); setOwners(o.data); setInvoices(inv.data); setSuppliers(sup.data);
    setExpenseCategories(cats.data || []);
    setDistributionKeys(dks.data || []);
    setPcmnAccounts(pcmn.data || []);
    setBankAccounts(c?.data?.bank_accounts || []);
    // iter90gr : quand le filtre FY masque tous les extraits, il faut savoir
    // s'il en existe HORS de ce filtre pour orienter l'utilisateur. Un appel
    // supplementaire sans date_from/date_to donne le total reel. On ne le fait
    // que si le filtre FY est actif ET que le resultat filtre est vide - sinon
    // les 2 comptes sont deja identiques.
    if ((s.data?.length || 0) === 0 && selectedFiscalYearId) {
      try {
        const rAll = await api.get('/banking/statements');  // pas de fyParams
        setTotalStatementsAllPeriods((rAll.data || []).length);
      } catch {
        setTotalStatementsAllPeriods(0);
      }
    } else {
      setTotalStatementsAllPeriods(s.data?.length || 0);
    }
    // iter90ja : fetch readiness summary (compteur "A comptabiliser" + ids ready/review)
    if (selectedCopro) {
      try {
        const params = { copropriete_id: selectedCopro, ...fyParams };
        const rs = await api.get('/banking/statements/readiness-summary', { params });
        setReadiness(rs.data);
      } catch {
        setReadiness({ total: 0, draft: 0, posted: 0, ready_to_post: 0, needs_review: 0, draft_ids_ready: [], draft_ids_needs_review: [] });
      }
    }
  }, [selectedCopro, fyParams.date_from, fyParams.date_to, selectedFiscalYearId]);
  useEffect(() => { load(); }, [load]);

  const loadStmtTxns = async (stmt) => {
    setSelectedStmt(stmt);
    const { data } = await api.get(`/banking/statements/${stmt.id}`);
    setTransactions(data.transactions || []);
    setInlineLines([]); setEditingTxn(null);
  };

  // iter90eh : refresh instantane apres lettrage/delettrage.
  // - Recharge transactions (matched flag) ET invoices (status paid/unpaid) en parallele.
  // - Si le dialog de lettrage est encore ouvert, refetch aussi letteredLinks
  //   pour synchroniser l'en-tete "Deja lettree a" sans clignotement.
  const refreshAfterLettrage = useCallback(async (opts = {}) => {
    const invPromise = api.get('/invoices', { params: fyParams }).then(r => setInvoices(r.data));
    let txnPromise;
    if (selectedStmt?.id) {
      txnPromise = api.get(`/banking/statements/${selectedStmt.id}`)
        .then(r => setTransactions(r.data.transactions || []));
    } else {
      txnPromise = api.get('/banking/transactions', { params: fyParams })
        .then(r => setTransactions(r.data));
    }
    const linksPromise = (opts.refetchLinks && lettrageTarget?.id)
      ? api.get(`/banking/transactions/${lettrageTarget.id}/lettered-links`)
          .then(r => setLetteredLinks(r.data))
          .catch(() => {})
      : Promise.resolve();
    await Promise.all([invPromise, txnPromise, linksPromise]);
  }, [selectedStmt, lettrageTarget, fyParams]);

  // iter90ja : options + filtrage cascade des extraits.
  // Sources : statements charges depuis /banking/statements (deja filtres par
  // fiscal year cote back). Les options se cascadent : choisir un compte
  // reduit les annees ; choisir une annee reduit les mois.
  // iter90jb : normalisation stricte des IBAN pour dedup les entrees affichees.
  //   Ex : "BE04 0019 5208 9331" et "BE04001952089331" -> meme entree "Compte * 9331".
  const _normIban = (s) => (s || '').toString().toUpperCase().replace(/[\s.-]+/g, '');
  const filterCascade = (() => {
    // Accounts distincts par IBAN normalise (source of truth = sans separateur)
    const accSet = new Map(); // norm_iban -> label
    for (const s of statements) {
      const raw = (s.account_number || '').trim();
      const norm = _normIban(raw);
      if (!norm) continue;
      const ba = bankAccounts.find(b => _normIban(b.iban) === norm || b.pcmn_number === raw);
      const nice = ba?.label?.trim()
        || `${ba?.account_type ? ba.account_type[0].toUpperCase() + ba.account_type.slice(1) : 'Compte'} * ${(ba?.iban || norm).slice(-4)}`;
      if (!accSet.has(norm)) accSet.set(norm, `${nice} (${norm})`);
    }
    const accountOptions = Array.from(accSet.entries()).map(([v, label]) => ({ v, label }));
    // Statements filtres par compte (comparaison normalisee)
    const afterAcc = filterAccount === '__all__'
      ? statements
      : statements.filter(s => _normIban(s.account_number) === filterAccount);
    const yearSet = new Set();
    for (const s of afterAcc) {
      const d = s.date || '';
      if (d.length >= 4) yearSet.add(d.slice(0, 4));
    }
    const yearOptions = Array.from(yearSet).sort().reverse();
    const afterYear = filterYear === '__all__' ? afterAcc : afterAcc.filter(s => (s.date || '').startsWith(filterYear));
    const monthSet = new Set();
    for (const s of afterYear) {
      const d = s.date || '';
      if (d.length >= 7) monthSet.add(d.slice(5, 7));
    }
    const monthOptions = Array.from(monthSet).sort().reverse();
    const afterMonth = filterMonth === '__all__' ? afterYear : afterYear.filter(s => (s.date || '').slice(5, 7) === filterMonth);
    // Tri final : plus recent en premier au sein du mois selectionne
    const sorted = [...afterMonth].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
    return { accountOptions, yearOptions, monthOptions, filtered: sorted };
  })();
  const filteredStatements = filterCascade.filtered;

  // iter90ja : Set des ids d'extraits necessitant review (transactions orphelines).
  // Sert a afficher une pastille rouge + fond ambre alerte sur la card.
  const needsReviewSet = new Set(readiness.draft_ids_needs_review || []);
  const readyIdsSet = new Set(readiness.draft_ids_ready || []);

  // iter90ja : "Tout comptabiliser" - batch-post les extraits prets (draft &
  // sans orphelin) du filtre courant. Signale les extraits sautes.
  const handleBatchPost = async () => {
    // On ne passe que les IDs draft du scope filtre courant + qui sont "ready"
    const draftInScope = filteredStatements.filter(s => s.status !== 'posted').map(s => s.id);
    const readyInScope = draftInScope.filter(id => readyIdsSet.has(id));
    const reviewInScope = draftInScope.filter(id => needsReviewSet.has(id));
    if (readyInScope.length === 0) {
      if (reviewInScope.length > 0) {
        toast.warning(`${reviewInScope.length} extrait(s) a verifier`, {
          description: 'Assignez une contrepartie ou lettrez leurs transactions orphelines avant de pouvoir les comptabiliser.',
          duration: 8000,
        });
      } else {
        toast.info('Aucun extrait pret a comptabiliser dans le filtre courant.');
      }
      return;
    }
    if (!window.confirm(`Comptabiliser ${readyInScope.length} extrait(s) ?${reviewInScope.length ? `\n${reviewInScope.length} extrait(s) avec orphelin(s) seront SAUTES.` : ''}`)) return;
    setBatchPosting(true);
    try {
      const { data } = await api.post('/banking/statements/batch-post', { statement_ids: readyInScope });
      const { counts, posted, skipped } = data;
      toast.success(`${counts.posted} extrait(s) comptabilise(s)`, {
        description: `${counts.skipped} sautes • ${counts.errors} erreurs`,
        duration: 6000,
      });
      if (skipped?.length) {
        // Ligne d'alerte pour chaque skipped
        const msg = skipped.slice(0, 5).map(s => `#${s.reference || s.id?.slice(0, 6)} : ${s.message || s.reason}`).join(' • ');
        toast.warning(`${skipped.length} extrait(s) sautes`, { description: msg, duration: 10000 });
      }
      // Log complet pour debug
      console.log('[batch-post]', { posted, skipped, errors: data.errors });
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur batch-post');
    } finally {
      setBatchPosting(false);
    }
  };

  const handleCodaImport = async (e) => {
    const file = e.target.files[0]; if (!file) return; setCodaUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const coproId = selectedCopro || '';
      if (coproId) fd.append('copropriete_id', coproId);
      // Step 1 : preview only - no DB write yet
      const { data } = await api.post('/banking/coda/preview', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      setCodaPreview(data);
      setCodaDialogOpen(true);
      if (data.duplicate_warning) {
        toast.warning('Fichier deja importe', {
          description: data.duplicate_warning.message,
          duration: 8000,
        });
      } else {
        toast.success(`${data.movements?.length || 0} mouvement(s) detecte(s) - reviewez et confirmez`);
      }
    }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur CODA'); }
    finally { setCodaUploading(false); if (codaRef.current) codaRef.current.value = ''; }
  };

  // iter90l : Import PDF/CSV multi-fichiers (IA Vision + regex CSV)
  const handleImportFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    if (!selectedCopro) { toast.error('Selectionnez d\'abord une copropriete'); return; }
    setImportUploading(true);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      fd.append('copropriete_id', selectedCopro);
      const { data } = await api.post('/banking/statements/import-files', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 300000,
      });
      const okCount = data.results.filter(r => r.status === 'ok').length;
      const errCount = data.results.filter(r => r.status === 'error').length;
      // iter90bu : detecte les doublons ignores et informe l'user
      const dupCount = data.results.filter(r => r.status === 'duplicate').length;
      if (okCount > 0) {
        const extras = [];
        if (dupCount > 0) extras.push(`${dupCount} doublon${dupCount > 1 ? 's' : ''} ignore${dupCount > 1 ? 's' : ''}`);
        if (errCount > 0) extras.push(`${errCount} erreur${errCount > 1 ? 's' : ''}`);
        toast.success(
          `${okCount} extrait${okCount > 1 ? 's' : ''} importe${okCount > 1 ? 's' : ''} en brouillon`,
          {
            description: `${data.total_transactions} transaction${data.total_transactions > 1 ? 's' : ''} au total${extras.length ? ' — ' + extras.join(', ') : ''}`,
            duration: 6000,
          },
        );
      }
      if (dupCount > 0 && !okCount && !errCount) {
        // Tous doublons -> avertissement (pas d'erreur)
        toast.warning(`${dupCount} extrait${dupCount > 1 ? 's' : ''} deja importe${dupCount > 1 ? 's' : ''} — rien a ajouter`, {
          description: data.results.filter(r => r.status === 'duplicate').map(r => `${r.filename}: ${r.error || 'doublon'}`).join(' • '),
          duration: 8000,
        });
      } else if (dupCount > 0 && okCount === 0) {
        toast.warning(`${dupCount} doublon${dupCount > 1 ? 's' : ''} ignore${dupCount > 1 ? 's' : ''}`, {
          description: data.results.filter(r => r.status === 'duplicate').map(r => `${r.filename}: ${r.error || 'doublon'}`).join(' • '),
          duration: 8000,
        });
      }
      if (errCount > 0 && !okCount) {
        toast.error(`${errCount} fichier(s) en erreur`, {
          description: data.results.filter(r => r.status === 'error').map(r => `${r.filename}: ${r.error || 'inconnu'}`).join(' • '),
          duration: 8000,
        });
      }
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de l\'import');
    } finally {
      setImportUploading(false);
      if (importRef.current) importRef.current.value = '';
    }
  };

  const saveStmt = async () => {
    try {
      const payload = { ...stmtForm, opening_balance: Number(stmtForm.opening_balance), closing_balance: Number(stmtForm.closing_balance) };
      if (editingStmtId) {
        await api.put(`/banking/statements/${editingStmtId}`, payload);
        toast.success('Extrait modifie');
        // iter90p : maj locale du statement (sidebar + panel) sans reload total
        const refreshed = await api.get(`/banking/statements/${editingStmtId}`);
        if (refreshed.data) {
          patchSidebarStmt(editingStmtId, {
            number: refreshed.data.number,
            date: refreshed.data.date,
            account_number: refreshed.data.account_number,
            opening_balance: refreshed.data.opening_balance,
            closing_balance: refreshed.data.closing_balance,
          });
        }
        setEditingStmtId(null);
        setStmtDialog(false);
      } else {
        await api.post('/banking/statements', payload);
        toast.success('Extrait cree');
        setEditingStmtId(null);
        setStmtDialog(false);
        // Nouveau statement -> reload pour l'ajouter a la sidebar
        load();
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const deleteStmt = async (id) => {
    const stmt = statements.find(s => s.id === id);
    if (stmt?.status === 'posted') {
      toast.error("Extrait comptabilise", {
        description: "Devalidez-le d'abord via 'Repasser brouillon' pour contrepasser les ecritures, puis vous pourrez le supprimer.",
        duration: 6000,
      });
      return;
    }
    setDeletePreviewLoading(true);
    try {
      const { data } = await api.get(`/banking/statements/${id}/delete-preview`);
      setDeletePreview(data);
    } catch {
      if (window.confirm('Supprimer cet extrait ?')) {
        try {
          await api.delete(`/banking/statements/${id}`);
          toast.success('Supprime');
          load();
          if (selectedStmt?.id === id) { setSelectedStmt(null); setTransactions([]); }
        } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
      }
    } finally {
      setDeletePreviewLoading(false);
    }
  };

  const confirmDeleteStmt = async () => {
    if (!deletePreview) return;
    const stmtId = deletePreview.statement_id;
    try {
      await api.delete(`/banking/statements/${stmtId}`);
      toast.success('Extrait supprime');
      load();
      if (selectedStmt?.id === stmtId) { setSelectedStmt(null); setTransactions([]); }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la suppression', { duration: 6000 });
    } finally {
      setDeletePreview(null);
    }
  };

  // INLINE LINES
  const addInlineLine = () => setInlineLines([...inlineLines, { date: new Date().toISOString().split('T')[0], amount: 0, counterparty_name: '', counterparty_account: '', communication: '', transaction_type: 'credit', counterparty_id: '', counterparty_type: '' }]);
  const updateLine = (i, f, v) => { const l = [...inlineLines]; l[i] = { ...l[i], [f]: v }; setInlineLines(l); };
  const removeLine = (i) => setInlineLines(inlineLines.filter((_, idx) => idx !== i));

  const saveLines = async () => {
    if (!selectedStmt || inlineLines.length === 0) return;
    const valid = inlineLines.filter(l => Math.abs(l.amount) > 0.001);
    if (!valid.length) { toast.error('Aucune ligne valide'); return; }
    try { const { data } = await api.post(`/banking/statements/${selectedStmt.id}/add-lines`, { lines: valid.map(l => ({ ...l, amount: Number(l.amount) })) }); toast.success(data.message); setInlineLines([]); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // GLOBAL LOOKUP
  const doLookup = async (q, lineIdx) => {
    if (q.length < 2) { setLookupResults(null); return; }
    try { const { data } = await api.get('/banking/lookup', { params: { q } }); setLookupResults({ ...data, lineIdx, query: q }); } catch { setLookupResults(null); }
  };

  // EDIT EXISTING TXN
  const startEdit = (txn) => {
    setEditingTxn(txn.id);
    setEditForm({
      date: txn.date,
      amount: txn.amount,
      counterparty_name: txn.counterparty_name || '',
      counterparty_account: txn.counterparty_account || '',
      communication: txn.communication || '',
      transaction_type: txn.transaction_type || 'credit',
      counterparty_id: txn.counterparty_id || '',
      counterparty_type: txn.counterparty_type || '',
    });
  };
  const cancelEdit = () => { setEditingTxn(null); };
  const saveEdit = async () => {
    try { await api.put(`/banking/transactions/${editingTxn}`, { ...editForm, amount: Number(editForm.amount) }); toast.success('Transaction modifiee'); setEditingTxn(null); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const deleteTxn = async (id) => {
    if (!window.confirm('Supprimer cette transaction ? Cette action est irreversible.')) return;
    try { await api.delete(`/banking/transactions/${id}`); toast.success('Transaction supprimee'); loadStmtTxns(selectedStmt); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // iter90p : local sidebar update (avoid full reload after every action).
  const patchSidebarStmt = (stmtId, patch) => {
    setStatements(prev => prev.map(s => s.id === stmtId ? { ...s, ...patch } : s));
    setSelectedStmt(prev => (prev && prev.id === stmtId) ? { ...prev, ...patch } : prev);
  };

  // LETTRAGE
  const openLettrage = async (txn) => {
    setLettrageTarget(txn);
    setLettrageDialog(true);
    setLookupQuery('');
    setSelectedInvoiceIds(new Set());
    // iter90eb : charge les liens deja lettres sur cette txn
    setLetteredLinks(null);
    setLetteredLinksLoading(true);
    try {
      const { data } = await api.get(`/banking/transactions/${txn.id}/lettered-links`);
      setLetteredLinks(data);
    } catch (err) {
      // Silent : l'utilisateur peut lettrer sans le detail (fallback UI)
      setLetteredLinks(null);
    } finally {
      setLetteredLinksLoading(false);
    }
  };
  const doLettrage = async (id, type) => { try { await api.post('/banking/lettrage', { transaction_id: lettrageTarget.id, match_to_id: id, match_type: type }); toast.success('Lettre'); setLettrageDialog(false); refreshAfterLettrage(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const unlettrage = async (id) => {
    // iter90cl : optimistic patch au lieu de load() full reload pour perf batch.
    try {
      const { data } = await api.post(`/banking/unlettrage/${id}`);
      toast.success('Delettrage');
      // Patch local du transaction dans les listes
      const patchTxn = (t) => (
        t.id === id
          ? { ...t, matched: false, matched_to: '', match_type: '',
              lettrage_code: undefined, lettrage_at: undefined }
          : t
      );
      setTransactions(prev => prev.map(patchTxn));
      // Patch de la facture concernee (si delettrage etait sur invoice)
      if (data.invoice) {
        setInvoices(prev => prev.map(inv =>
          inv.id === data.invoice.id ? { ...inv, ...data.invoice } : inv
        ));
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur delettrage');
    }
  };
  const unlettrageByInvoice = async (invId) => {
    try {
      const { data } = await api.post(`/banking/unlettrage-by-invoice/${invId}`);
      toast.success('Facture delettree');
      // iter90cl : patch local des txns delettrees + facture
      const ids = new Set((data.transactions || []).map(t => t.id));
      if (ids.size) {
        setTransactions(prev => prev.map(t =>
          ids.has(t.id)
            ? { ...t, matched: false, matched_to: '', match_type: '',
                lettrage_code: undefined, lettrage_at: undefined }
            : t
        ));
      }
      if (data.invoice) {
        setInvoices(prev => prev.map(inv =>
          inv.id === data.invoice.id ? { ...inv, ...data.invoice } : inv
        ));
      }
      // iter90eh : rafraichit letteredLinks du dialog si il est encore ouvert
      // sur la txn dont on delettre une des factures (multi_invoice)
      if (lettrageTarget?.id && ids.has(lettrageTarget.id)) {
        try {
          const { data: links } = await api.get(`/banking/transactions/${lettrageTarget.id}/lettered-links`);
          setLetteredLinks(links);
        } catch { /* silent */ }
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  // ----- iter90k : CATEGORISATION -----
  const openCategorize = (txn) => {
    setCategorizeTarget(txn);
    setCategorizeSplits([{ _key: (crypto?.randomUUID?.() || `k-${Date.now()}`), expense_category_id: '', account_number: '', distribution_key_id: '', amount: Math.abs(Number(txn.amount) || 0), description: '' }]);
    setCategorizeDialog(true);
  };
  const addCatSplit = () => setCategorizeSplits([...categorizeSplits, { _key: (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`), expense_category_id: '', account_number: '', distribution_key_id: '', amount: 0, description: '' }]);
  const removeCatSplit = (i) => setCategorizeSplits(categorizeSplits.filter((_, idx) => idx !== i));
  const updateCatSplit = (i, f, v) => setCategorizeSplits(prev => {
    const s = [...prev];
    s[i] = { ...s[i], [f]: v };
    return s;
  });
  const doCategorize = async () => {
    if (!categorizeTarget) return;
    // iter90bn : on retire le champ client `_key` avant l'envoi (backend
    // n'en a pas besoin et Pydantic ignore mais restons propre).
    const payload = { splits: categorizeSplits.map(({ _key, ...s }) => ({ ...s, amount: Number(s.amount) })) };
    try {
      await api.post(`/banking/transactions/${categorizeTarget.id}/categorize`, payload);
      toast.success(`Categorisation OK (${payload.splits.length} nature${payload.splits.length > 1 ? 's' : ''})`);
      setCategorizeDialog(false);
      refreshAfterLettrage();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const uncategorize = async (id) => {
    if (!window.confirm('Retirer la nature de cette transaction ?')) return;
    try {
      await api.delete(`/banking/transactions/${id}/categorize`);
      toast.success('Categorisation retiree');
      refreshAfterLettrage();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  // 1 txn -> N factures : multi-selection dans le dialog Lettrage
  const toggleInvoiceSelected = (id) => {
    setSelectedInvoiceIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const selectedInvoicesTotal = (lettrageTarget ? invoices.filter(i => selectedInvoiceIds.has(i.id)) : [])
    .reduce((s, i) => s + Number(i.total_amount || i.amount_ttc || i.amount || 0), 0);
  const doLettrageMultiInvoices = async () => {
    if (!lettrageTarget || selectedInvoiceIds.size === 0) return;
    try {
      const r = await api.post('/banking/lettrage-multi-invoices', {
        transaction_id: lettrageTarget.id,
        invoice_ids: Array.from(selectedInvoiceIds),
      });
      const { transaction_amount, invoice_total, remaining, is_exact } = r.data;
      const msg = is_exact
        ? `Lettrage OK : ${selectedInvoiceIds.size} factures = ${invoice_total.toFixed(2)} EUR (exact)`
        : `Lettrage OK : ${selectedInvoiceIds.size} factures pour ${invoice_total.toFixed(2)} EUR / txn ${transaction_amount.toFixed(2)} EUR (ecart ${Math.abs(remaining).toFixed(2)} EUR sur compte tiers)`;
      toast.success(msg);
      setLettrageDialog(false);
      setSelectedInvoiceIds(new Set());
      refreshAfterLettrage();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lettrage multi-factures');
    }
  };

  // ---- MULTI-SELECTION LETTRAGE (N transactions -> 1 facture) ----
  const toggleTxnSelected = (id) => {
    setSelectedTxnIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const clearSelection = () => setSelectedTxnIds(new Set());
  // Multi-selection : ne s'applique qu'aux txns de l'extrait actuellement ouvert
  const selectedTxns = transactions.filter(t => selectedTxnIds.has(t.id));
  const selectedTotal = selectedTxns.reduce((s, t) => s + Math.abs(Number(t.amount) || 0), 0);
  const doBatchLettrage = async (invoiceId) => {
    try {
      const ids = Array.from(selectedTxnIds);
      const r = await api.post('/banking/lettrage-batch', {
        transaction_ids: ids,
        match_to_id: invoiceId,
        match_type: 'invoice',
      });
      const { total_paid, invoice_amount, status, remaining } = r.data;
      const msg = status === 'paid'
        ? `Lettrage OK : ${ids.length} transactions = ${total_paid.toFixed(2)} EUR / ${invoice_amount.toFixed(2)} EUR (solde)`
        : `Lettrage partiel : ${total_paid.toFixed(2)} EUR / ${invoice_amount.toFixed(2)} EUR (reste ${remaining.toFixed(2)} EUR)`;
      toast.success(msg);
      setBatchLettrageDialog(false);
      clearSelection();
      refreshAfterLettrage();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lettrage en lot');
    }
  };
  // unpaidInv (legacy) supprime - l'onglet factures affiche maintenant toutes les factures avec coloration.

  // unpaidInv (legacy) supprime - l'onglet factures affiche maintenant toutes les factures avec coloration.

  return (
    <div data-testid="banking-page">
      <div className="page-header flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title">Interface Bancaire</h1>
          <p className="page-subtitle">Extraits de compte, encodage et lettrage</p>
        </div>
        <div className="flex gap-2 flex-wrap items-center">
          {/* iter90ja : Compteur "A comptabiliser" - visible en permanence dans le header */}
          {readiness.draft > 0 && (
            <div className="flex items-center gap-2" data-testid="banking-readiness-header">
              <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-amber-50 border border-amber-300 text-amber-900" title="Extraits en brouillon prets a etre comptabilises">
                <AlertTriangle size={14} className="text-amber-700" />
                <span className="text-xs font-semibold" data-testid="readiness-count-todo">
                  {readiness.draft} a comptabiliser
                </span>
                {readiness.needs_review > 0 && (
                  <span className="text-[10px] font-medium bg-rose-100 text-rose-800 border border-rose-200 rounded-full px-1.5 py-0.5 ml-1" title="Extraits contenant des transactions orphelines">
                    {readiness.needs_review} a verifier
                  </span>
                )}
              </div>
            </div>
          )}
          <Button
            onClick={() => navigate('/reports?tab=bilan')}
            variant="outline"
            className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10"
            data-testid="view-bilan-btn"
            title="Ouvrir le bilan de la copropriete"
          >
            <Eye size={16} className="mr-2" /> Voir le bilan
          </Button>
          <input type="file" ref={codaRef} accept=".cod,.coda,.txt" onChange={handleCodaImport} className="hidden" />
          <Button onClick={() => codaRef.current?.click()} variant="outline" disabled={codaUploading} data-testid="coda-import-btn"><Upload size={16} className="mr-2" /> {codaUploading ? 'Import...' : 'Import CODA'}</Button>
          <input type="file" ref={importRef} accept=".pdf,.csv" multiple onChange={handleImportFiles} className="hidden" data-testid="import-files-input" />
          <Button onClick={() => importRef.current?.click()}
            variant="outline"
            disabled={importUploading || !selectedCopro}
            title={selectedCopro ? "Importer un ou plusieurs extraits PDF / CSV (creation auto en brouillon)" : "Selectionnez une copropriete"}
            data-testid="import-files-btn"
            className="border-purple-200 text-purple-700 hover:bg-purple-50">
            <Upload size={16} className="mr-2" /> {importUploading ? 'Extraction IA en cours...' : 'Importer PDF/CSV'}
          </Button>
          <Button onClick={() => {
            const def = bankAccounts.find(b => b.is_default) || bankAccounts[0];
            setStmtForm({ number: '', date: new Date().toISOString().split('T')[0], account_number: def?.iban || '', opening_balance: 0, closing_balance: 0 });
            setStmtDialog(true);
          }} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-stmt-btn"><Plus size={16} className="mr-2" /> Nouvel extrait</Button>
          <Button
            variant="outline"
            className="border-teal-300 text-teal-700 hover:bg-teal-50"
            data-testid="auto-lettrage-vcs-btn"
            disabled={!selectedCopro}
            title={selectedCopro ? "Lettrage automatique par communication structuree VCS" : "Selectionnez une copropriete"}
            onClick={async () => {
              try {
                const { data } = await api.post('/banking/auto-lettrage-vcs', { copropriete_id: selectedCopro });
                if (data.count > 0) {
                  toast.success(`${data.count} transaction(s) lettree(s) automatiquement`);
                  load();
                } else {
                  toast.info('Aucune correspondance VCS trouvee');
                }
              } catch (err) {
                toast.error(err.response?.data?.detail || 'Erreur auto-lettrage VCS');
              }
            }}
          >
            <Zap size={16} className="mr-2" /> Auto-lettrage VCS
          </Button>
        </div>
      </div>

      {/* iter90ja : Barre de filtres cascade + bouton "Tout comptabiliser" */}
      <div className="my-3 flex items-center gap-3 flex-wrap p-3 rounded-lg bg-slate-50 border border-slate-200" data-testid="banking-filter-bar">
        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Filtrer</div>
        <Select value={filterAccount} onValueChange={v => { setFilterAccount(v); setFilterYear('__all__'); setFilterMonth('__all__'); }}>
          <SelectTrigger className="w-64 h-8 text-xs" data-testid="filter-account">
            <SelectValue placeholder="Compte bancaire" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">Tous les comptes</SelectItem>
            {filterCascade.accountOptions.map(o => (
              <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={filterYear} onValueChange={v => { setFilterYear(v); setFilterMonth('__all__'); }}>
          <SelectTrigger className="w-32 h-8 text-xs" data-testid="filter-year">
            <SelectValue placeholder="Annee" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">Toutes annees</SelectItem>
            {filterCascade.yearOptions.map(y => (
              <SelectItem key={y} value={y}>{y}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={filterMonth} onValueChange={v => setFilterMonth(v)} disabled={filterYear === '__all__'}>
          <SelectTrigger className="w-32 h-8 text-xs" data-testid="filter-month">
            <SelectValue placeholder="Mois" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">Tous mois</SelectItem>
            {filterCascade.monthOptions.map(m => {
              const monthLabels = ['','Jan','Fev','Mar','Avr','Mai','Juin','Juil','Aou','Sep','Oct','Nov','Dec'];
              return <SelectItem key={m} value={m}>{monthLabels[parseInt(m,10)] || m}</SelectItem>;
            })}
          </SelectContent>
        </Select>
        <span className="text-[11px] text-slate-500 italic ml-auto" data-testid="filter-count">
          {filteredStatements.length} extrait{filteredStatements.length > 1 ? 's' : ''} affiche{filteredStatements.length > 1 ? 's' : ''}
        </span>
        <Button
          size="sm"
          onClick={handleBatchPost}
          disabled={batchPosting || filteredStatements.filter(s => s.status !== 'posted' && readyIdsSet.has(s.id)).length === 0}
          className="bg-emerald-600 hover:bg-emerald-700 text-white h-8 text-xs"
          data-testid="batch-post-btn"
          title="Comptabilise tous les extraits en brouillon prets (skipe ceux avec orphelins)"
        >
          <CheckCircle2 size={13} className="mr-1.5" />
          {batchPosting ? 'Comptabilisation...' : `Tout comptabiliser (${filteredStatements.filter(s => s.status !== 'posted' && readyIdsSet.has(s.id)).length})`}
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Statements sidebar */}
        <div className="space-y-2 lg:col-span-1">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold px-1">
            Extraits ({filteredStatements.length})
          </div>
          {filteredStatements.length === 0 ? (
            (selectedFiscalYearId && totalStatementsAllPeriods > 0) ? (
              <div className="text-xs text-slate-600 text-center py-6 px-3 bg-amber-50 border border-amber-200 rounded" data-testid="stmt-fy-filter-empty">
                <div className="font-semibold text-amber-800 mb-1">Aucun extrait pour cet exercice</div>
                <div className="mb-2">{totalStatementsAllPeriods} extrait(s) existent hors de la periode {selectedFiscalYear?.start_date} - {selectedFiscalYear?.end_date}.</div>
                <Button
                  size="sm"
                  variant="outline"
                  className="text-[10px] h-6"
                  onClick={() => setSelectedFiscalYearId('')}
                  data-testid="stmt-clear-fy-filter"
                >Voir tous les exercices</Button>
              </div>
            ) : (
              <p className="text-sm text-slate-400 text-center py-4">Aucun extrait</p>
            )
          ) : filteredStatements.map(s => {
            const baBadge = getBankAccountBadge(s);
            // iter90ja : classification visuelle par etat de comptabilisation
            const isPosted = s.status === 'posted';
            const isDraft = s.status === 'draft';
            const needsReview = needsReviewSet.has(s.id);
            const isReady = readyIdsSet.has(s.id);
            const isSelected = selectedStmt?.id === s.id;
            // Card background : posted=neutre, draft-ready=ambre pale, draft-review=rose alerte
            const cardBg = isPosted ? 'bg-white' : (needsReview ? 'bg-rose-50/60' : (isDraft ? 'bg-amber-50/50' : 'bg-white'));
            const cardBorder = isSelected
              ? 'border-[#022D52] shadow-md'
              : (needsReview ? 'border-rose-300' : (isReady ? 'border-amber-300' : `${baBadge?.border || 'border-slate-200'}`));
            return (
            <Card key={s.id} className={`cursor-pointer transition-all border-l-4 text-sm ${cardBg} ${cardBorder} hover:border-slate-400`} onClick={() => loadStmtTxns(s)} data-testid={`stmt-card-${s.id}`}>
              <CardContent className="p-3">
                {baBadge && (
                  <div className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide mb-1.5 ${baBadge.bg} ${baBadge.text}`} title={s.account_number} data-testid={`stmt-ba-badge-${s.id}`}>
                    {baBadge.label}
                  </div>
                )}
                {needsReview && (
                  <div className="inline-flex items-center gap-1 ml-1 px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-rose-100 text-rose-800 border border-rose-200 mb-1.5" title="Cet extrait contient des transactions orphelines - non comptabilisable en batch" data-testid={`stmt-needs-review-${s.id}`}>
                    <AlertTriangle size={9} /> A verifier
                  </div>
                )}
                <div className="flex items-center justify-between gap-1">
                  <span className="font-mono font-semibold text-[11px] truncate min-w-0" title={s.number}>N {s.number}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={e => { e.stopPropagation(); deleteStmt(s.id); }}
                    className={`h-5 w-5 p-0 shrink-0 ${s.status === 'posted' ? 'text-slate-300 cursor-not-allowed' : 'text-red-400 hover:text-red-600'}`}
                    title={s.status === 'posted' ? "Devalider d'abord (Repasser brouillon) pour pouvoir supprimer" : 'Supprimer'}
                    data-testid={`stmt-del-${s.id}`}
                  ><Trash2 size={10} /></Button>
                </div>
                <div className="text-xs text-slate-500">{fmtDate(s.date)}</div>
                <div className="flex justify-between mt-1 text-[10px] font-mono"><span>O:{s.opening_balance?.toFixed(2)}</span><span>F:{s.closing_balance?.toFixed(2)}</span></div>
                <div className="flex gap-1 mt-1 flex-wrap">
                  {isPosted && <Badge className="text-[9px] bg-green-100 text-green-700 border-green-300">Comptabilise</Badge>}
                  {isDraft && !needsReview && <Badge className="text-[9px] bg-amber-100 text-amber-800 border-amber-300" variant="outline" data-testid={`stmt-badge-draft-${s.id}`}>Pret</Badge>}
                  {isDraft && needsReview && <Badge className="text-[9px] bg-rose-100 text-rose-800 border-rose-300" variant="outline" data-testid={`stmt-badge-review-${s.id}`}>Verif requise</Badge>}
                  {s.source === 'CODA' && <Badge className="text-[9px]" variant="outline">CODA</Badge>}
                  {s.source === 'PDF' && <Badge className="text-[9px] bg-purple-50 text-purple-700 border-purple-200" variant="outline">PDF IA</Badge>}
                  {s.source === 'CSV' && <Badge className="text-[9px] bg-blue-50 text-[#01213e] border-blue-200" variant="outline">CSV</Badge>}
                </div>
                {s.source_file_id && (
                  <a
                    href={`${(process.env.REACT_APP_BACKEND_URL || '') || ''}/api/banking/statements/${s.id}/source-file`}
                    onClick={e => e.stopPropagation()}
                    target="_blank" rel="noreferrer"
                    className="mt-1 text-[10px] text-purple-600 hover:text-purple-800 hover:underline inline-block"
                    data-testid={`stmt-source-${s.id}`}
                  >Voir fichier source</a>
                )}
              </CardContent>
            </Card>
          );})}
        </div>

        {/* Main panel - sticky whole panel so it stays in view while
            user scrolls the left statements list */}
        <div className="lg:col-span-4 lg:sticky lg:top-4 lg:self-start lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
          {selectedStmt ? (
            <Card className="border-slate-200 overflow-visible">
              <CardHeader
                className="pb-2 sticky top-0 z-20 bg-white/95 backdrop-blur-sm border-b border-slate-100 rounded-t-lg"
                data-testid="stmt-sticky-header"
              >
                <div className="flex flex-row items-start justify-between gap-3">
                  <div className="flex-1">
                    <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
                      Extrait N {selectedStmt.number} - {fmtDate(selectedStmt.date)}
                      {selectedStmt.status === 'posted' ? (
                        <Badge className="bg-green-100 text-green-700 border-green-300"><CheckCircle2 size={11} className="mr-1" />Comptabilise</Badge>
                      ) : (
                        <Badge variant="outline" className="bg-slate-50 text-slate-500">Brouillon</Badge>
                      )}
                    </CardTitle>
                    <p className="text-xs text-slate-500 mt-1 font-mono">{selectedStmt.account_number}</p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {selectedStmt.status !== 'posted' && (
                      <>
                        <Button size="sm" variant="outline" onClick={() => {
                          setStmtForm({
                            number: selectedStmt.number || '',
                            date: selectedStmt.date || '',
                            account_number: selectedStmt.account_number || '',
                            opening_balance: selectedStmt.opening_balance || 0,
                            closing_balance: selectedStmt.closing_balance || 0,
                          });
                          setEditingStmtId(selectedStmt.id);
                          setStmtDialog(true);
                        }} data-testid="edit-stmt-btn"><Pencil size={14} className="mr-1" /> Modifier</Button>
                        <Button size="sm" variant="outline" onClick={addInlineLine} data-testid="add-inline-line"><PlusCircle size={14} className="mr-1" /> Ajouter lignes</Button>
                      </>
                    )}
                    {(() => {
                      // Compute balance check inline
                      const opening = Number(selectedStmt.opening_balance || 0);
                      const closing = Number(selectedStmt.closing_balance || 0);
                      const mvts = transactions.reduce((s, t) => s + (t.transaction_type === 'credit' ? 1 : -1) * Math.abs(Number(t.amount || 0)), 0);
                      const computed = Math.round((opening + mvts) * 100) / 100;
                      const diff = Math.round((computed - closing) * 100) / 100;
                      const balanced = Math.abs(diff) < 0.01;
                      if (selectedStmt.status === 'posted') return (
                        <Button size="sm" variant="outline" onClick={async () => {
                          if (!window.confirm('Repasser cet extrait en brouillon ?')) return;
                          try {
                            await api.post(`/banking/statements/${selectedStmt.id}/unpost`);
                            toast.success('Extrait repasse en brouillon');
                            patchSidebarStmt(selectedStmt.id, { status: 'draft' });
                            loadStmtTxns({ ...selectedStmt, status: 'draft' });
                          } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
                        }} data-testid="unpost-stmt-btn">Repasser brouillon</Button>
                      );
                      return (
                        <Button
                          size="sm"
                          onClick={async () => {
                            try {
                              await api.post(`/banking/statements/${selectedStmt.id}/post`);
                              toast.success('Extrait comptabilise');
                              patchSidebarStmt(selectedStmt.id, { status: 'posted' });
                              loadStmtTxns({ ...selectedStmt, status: 'posted' });
                            } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
                          }}
                          disabled={!balanced || transactions.length === 0}
                          className={balanced && transactions.length > 0 ? "bg-green-600 hover:bg-green-700 text-white" : ""}
                          data-testid="post-stmt-btn"
                          title={!balanced ? `Difference: ${diff.toFixed(2)} EUR. Ajustez les mouvements ou le solde de fermeture.` : "Comptabiliser l'extrait"}
                        >
                          <CheckCircle2 size={14} className="mr-1" />
                          Comptabiliser
                        </Button>
                      );
                    })()}
                  </div>
                </div>
                {/* Bandeau equilibre */}
                {(() => {
                  const opening = Number(selectedStmt.opening_balance || 0);
                  const closing = Number(selectedStmt.closing_balance || 0);
                  const mvts = transactions.reduce((s, t) => s + (t.transaction_type === 'credit' ? 1 : -1) * Math.abs(Number(t.amount || 0)), 0);
                  const computed = Math.round((opening + mvts) * 100) / 100;
                  const diff = Math.round((computed - closing) * 100) / 100;
                  const balanced = Math.abs(diff) < 0.01;
                  return (
                    <div className={`mt-3 p-2 rounded text-xs flex items-center justify-between gap-4 ${balanced ? 'bg-green-50 border border-green-200' : 'bg-amber-50 border border-amber-200'}`} data-testid="balance-check-banner">
                      <div className="flex gap-4">
                        <span>Solde ouverture : <b className="font-mono">{opening.toFixed(2)}</b></span>
                        <span>+ Mouvements : <b className={`font-mono ${mvts >= 0 ? 'text-green-700' : 'text-red-700'}`}>{mvts >= 0 ? '+' : ''}{mvts.toFixed(2)}</b></span>
                        <span>= Solde calcule : <b className="font-mono">{computed.toFixed(2)}</b></span>
                        <span>vs. saisi : <b className="font-mono">{closing.toFixed(2)}</b></span>
                      </div>
                      {balanced ? (
                        <span className="text-green-700 flex items-center gap-1"><CheckCircle2 size={12} /> Equilibre</span>
                      ) : (
                        <span className="text-amber-700 flex items-center gap-1"><AlertTriangle size={12} /> Difference : <b className="font-mono">{diff > 0 ? '+' : ''}{diff.toFixed(2)}</b></span>
                      )}
                    </div>
                  );
                })()}
              </CardHeader>
              <CardContent className="p-0">
                {/* Inline entry */}
                {inlineLines.length > 0 && (
                  <div className="border-b-2 border-[#022D52] bg-blue-50/30 p-3">
                    <div className="text-xs font-semibold text-[#022D52] mb-2 uppercase tracking-wider">Nouvelles lignes</div>
                    <table className="w-full text-xs">
                      <thead><tr className="text-[10px] text-slate-500 uppercase"><th className="p-1 text-left w-24">Date</th><th className="p-1 text-right w-24">Montant</th><th className="p-1 w-12">+/-</th><th className="p-1 text-left">Contrepartie</th><th className="p-1 text-left">Communication</th><th className="p-1 w-6"></th></tr></thead>
                      <tbody>
                        {inlineLines.map((line, i) => (
                          <tr key={i} className="border-t border-blue-100">
                            <td className="p-1"><Input type="date" className="h-7 text-xs" value={line.date} onChange={e => updateLine(i, 'date', e.target.value)} /></td>
                            <td className="p-1"><Input type="number" step="0.01" className="h-7 text-xs text-right" value={line.amount} onChange={e => updateLine(i, 'amount', e.target.value)} /></td>
                            <td className="p-1"><select className="h-7 text-xs border rounded px-1 w-full" value={line.transaction_type} onChange={e => updateLine(i, 'transaction_type', e.target.value)}><option value="credit">+</option><option value="debit">-</option></select></td>
                            <td className="p-1 relative">
                              <CounterpartySearchSelect
                                owners={owners}
                                suppliers={suppliers}
                                value={line.counterparty_name}
                                onChange={(v) => updateLine(i, 'counterparty_name', v)}
                                onSelect={({ item, type }) => {
                                  // Enregistre l'ID + type pour que le backend utilise
                                  // cette contrepartie EXPLICITE en priorite sur le VCS.
                                  updateLine(i, 'counterparty_id', item.id);
                                  updateLine(i, 'counterparty_type', type);
                                  updateLine(i, 'counterparty_name', item.name);
                                  // Pour un encaissement proprietaire, auto-pre-remplir la communication
                                  // (VCS si vide + mention "Votre paiement au JJ/MM/AAAA")
                                  if (type === 'owner') {
                                    const dateLabel = line.date ? new Date(line.date).toLocaleDateString('fr-BE') : '';
                                    const paymentLabel = dateLabel ? `Votre paiement au ${dateLabel}` : 'Votre paiement';
                                    const vcs = item.vcs_code || '';
                                    const newComm = (line.communication || '').trim()
                                      ? line.communication
                                      : (vcs ? `${vcs} - ${paymentLabel}` : paymentLabel);
                                    updateLine(i, 'communication', newComm);
                                  }
                                }}
                                placeholder="Nom contrepartie"
                                testId={`counterparty-${i}`}
                              />
                            </td>
                            <td className="p-1 relative">
                              <Input className="h-7 text-xs" value={line.communication} onChange={e => { updateLine(i, 'communication', e.target.value); doLookup(e.target.value, `cm-${i}`); }} placeholder="Communication libre ou VCS" />
                              {lookupResults && lookupResults.lineIdx === `cm-${i}` && lookupResults.owners.length > 0 && (
                                <div className="absolute top-8 left-0 z-20 bg-white border border-[#022D52] shadow-lg rounded-md p-2 text-xs w-56">
                                  {lookupResults.owners.map(o => <div key={o.id} className="p-1 text-[#022D52]"><strong>{o.name}</strong> <span className="font-mono text-[10px]">{o.vcs_code}</span><div className="text-[10px] text-green-600">Auto-lettrage VCS</div></div>)}
                                </div>
                              )}
                            </td>
                            <td className="p-1"><button onClick={() => removeLine(i)} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="flex gap-2 mt-2 justify-end">
                      <Button size="sm" variant="ghost" onClick={addInlineLine}><Plus size={12} className="mr-1" /> Ligne</Button>
                      <Button size="sm" onClick={saveLines} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="save-inline-lines"><Save size={12} className="mr-1" /> Enregistrer</Button>
                    </div>
                  </div>
                )}

                {/* Toolbar selection multi-lettrage */}
                {selectedTxnIds.size > 0 && (
                  <div className="bg-[#022D52]/10 border border-[#022D52]/30 rounded-md px-3 py-2 mb-2 flex items-center justify-between text-sm" data-testid="batch-lettrage-toolbar">
                    <div className="flex items-center gap-4">
                      <strong className="text-[#022D52]">{selectedTxnIds.size} transaction(s) selectionnee(s)</strong>
                      <span className="font-mono text-slate-700">Total : <strong>{selectedTotal.toFixed(2)} EUR</strong></span>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={() => { setBatchInvoiceSearch(''); setBatchLettrageDialog(true); }} className="bg-[#022D52] hover:bg-[#1D4ED8] text-white" data-testid="batch-lettrage-open-btn">
                        <Link2 size={13} className="mr-1.5" /> Lettrer la selection vers une facture
                      </Button>
                      <Button size="sm" variant="outline" onClick={clearSelection} data-testid="batch-lettrage-clear-btn">Annuler</Button>
                    </div>
                  </div>
                )}

                {/* Transaction table */}
                <Table>
                  <TableHeader><TableRow>
                    <TableHead className="w-8 px-1">
                      <input
                        type="checkbox"
                        title="Tout selectionner (txns non lettrees)"
                        checked={transactions.length > 0 && transactions.filter(t => !t.matched).every(t => selectedTxnIds.has(t.id))}
                        onChange={(e) => {
                          if (e.target.checked) {
                            const next = new Set(selectedTxnIds);
                            transactions.filter(t => !t.matched).forEach(t => next.add(t.id));
                            setSelectedTxnIds(next);
                          } else {
                            clearSelection();
                          }
                        }}
                        data-testid="batch-select-all-checkbox"
                      />
                    </TableHead>
                    <TableHead className="w-24">Date</TableHead><TableHead className="min-w-[200px]">Contrepartie</TableHead><TableHead className="min-w-[200px]">Communication</TableHead>
                    <TableHead className="text-right w-28">Montant</TableHead><TableHead className="w-24">Lettrage</TableHead><TableHead className="w-24"></TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {transactions.length === 0 ? (
                      <TableRow><TableCell colSpan={7} className="text-center py-6 text-slate-400 text-sm">Cliquez &laquo;Ajouter lignes&raquo; pour encoder</TableCell></TableRow>
                    ) : transactions.map(txn => editingTxn === txn.id ? (
                      <TableRow key={txn.id} className="bg-yellow-50/50">
                        <TableCell></TableCell>
                        <TableCell><Input type="date" className="h-7 text-xs" value={editForm.date} onChange={e => setEditForm({...editForm, date: e.target.value})} /></TableCell>
                        <TableCell>
                          <CounterpartySearchSelect
                            owners={owners}
                            suppliers={suppliers}
                            value={editForm.counterparty_name}
                            onChange={(v) => setEditForm({...editForm, counterparty_name: v})}
                            onSelect={({ item, type }) => {
                              setEditForm(f => ({
                                ...f,
                                counterparty_id: item.id,
                                counterparty_type: type,
                                counterparty_name: item.name,
                                communication: (f.communication && f.communication.trim()) ? f.communication : (item.vcs_code || f.communication),
                              }));
                            }}
                            placeholder="Nom contrepartie"
                            testId={`edit-counterparty-${txn.id}`}
                          />
                        </TableCell>
                        <TableCell><Input className="h-7 text-xs" value={editForm.communication} onChange={e => setEditForm({...editForm, communication: e.target.value})} /></TableCell>
                        <TableCell><Input type="number" step="0.01" className="h-7 text-xs text-right" value={editForm.amount} onChange={e => setEditForm({...editForm, amount: e.target.value})} /></TableCell>
                        <TableCell colSpan={2}>
                          <div className="flex gap-1"><Button size="sm" variant="ghost" onClick={saveEdit} className="text-green-600 h-6 px-2" data-testid={`save-edit-${txn.id}`}><Save size={12} /></Button><Button size="sm" variant="ghost" onClick={cancelEdit} className="text-slate-400 h-6 px-2"><X size={12} /></Button></div>
                        </TableCell>
                      </TableRow>
                    ) : (
                      <TableRow key={txn.id} className={`hover:bg-slate-50/50 ${selectedTxnIds.has(txn.id) ? 'bg-blue-50/40' : ''}`}>
                        <TableCell className="px-1">
                          {!txn.matched && (
                            <input
                              type="checkbox"
                              checked={selectedTxnIds.has(txn.id)}
                              onChange={() => toggleTxnSelected(txn.id)}
                              data-testid={`select-txn-${txn.id}`}
                            />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{fmtDate(txn.date)}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.counterparty_name}</TableCell>
                        <TableCell className="text-sm break-words" style={{wordBreak: 'break-word'}}>{txn.communication}</TableCell>
                        <TableCell className={`text-right font-mono font-semibold ${txn.amount >= 0 ? 'text-green-700' : 'text-red-700'}`}>{txn.amount >= 0 ? '+' : ''}{txn.amount?.toFixed(2)}</TableCell>
                        <TableCell>{txn.matched ? <Badge className={
                            txn.match_type === 'expense_category'
                              ? "bg-purple-50 text-purple-700 border-purple-200 text-[10px]"
                              : "bg-green-50 text-green-700 border-green-200 text-[10px]"
                          } variant="outline">{
                            txn.match_type === 'owner_payment' ? 'Proprio' :
                            txn.match_type === 'supplier_payment' ? 'Fourn.' :
                            txn.match_type === 'expense_category' ? (
                              (txn.category_splits && txn.category_splits.length > 1)
                                ? `Nature (${txn.category_splits.length})`
                                : 'Nature'
                            ) :
                            'Fact.'
                          }</Badge> : <Badge variant="outline" className="text-slate-400 text-[10px]">-</Badge>}</TableCell>
                        <TableCell>
                          <div className="flex gap-0">
                            <Button variant="ghost" size="sm" onClick={() => startEdit(txn)} className="h-6 w-6 p-0 text-slate-400" title="Editer" data-testid={`edit-txn-${txn.id}`}><Pencil size={11} /></Button>
                            {txn.matched ? (
                              txn.match_type === 'expense_category'
                                ? <Button variant="ghost" size="sm" onClick={() => uncategorize(txn.id)} className="text-purple-600 h-6 w-6 p-0" title="Retirer la nature" data-testid={`uncategorize-${txn.id}`}><Unlink size={11} /></Button>
                                : <Button variant="ghost" size="sm" onClick={() => unlettrage(txn.id)} className="text-orange-500 h-6 w-6 p-0" title="Delettrer"><Unlink size={11} /></Button>
                            ) : (
                              <>
                                <Button variant="ghost" size="sm" onClick={() => openLettrage(txn)} className="text-[#022D52] h-6 w-6 p-0" title="Lettrer" data-testid={`lettrage-${txn.id}`}><Link2 size={11} /></Button>
                                <Button variant="ghost" size="sm" onClick={() => openCategorize(txn)} className="text-purple-600 h-6 w-6 p-0" title="Categoriser (nature de depense/revenu)" data-testid={`categorize-${txn.id}`}><Tag size={11} /></Button>
                              </>
                            )}
                            <Button variant="ghost" size="sm" onClick={() => deleteTxn(txn.id)} className="h-6 w-6 p-0 text-red-400" title="Supprimer" data-testid={`delete-txn-${txn.id}`}><Trash2 size={11} /></Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : (
            <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Selectionnez un extrait</div>
          )}
        </div>
      </div>

      {/* Statement Dialog */}
      <Dialog open={stmtDialog} onOpenChange={(o) => { setStmtDialog(o); if (!o) setEditingStmtId(null); }}><DialogContent data-testid="stmt-dialog"><DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingStmtId ? 'Modifier l\'extrait' : 'Nouvel extrait'}</DialogTitle></DialogHeader>
        <div className="space-y-4 mt-2">
          <div className="grid grid-cols-2 gap-4"><div><label className="form-label">Numero *</label><Input value={stmtForm.number} onChange={e => setStmtForm({...stmtForm, number: e.target.value})} /></div><div><label className="form-label">Date *</label><Input type="date" value={stmtForm.date} onChange={e => setStmtForm({...stmtForm, date: e.target.value})} /></div></div>
          <div><label className="form-label">Compte bancaire *</label>
            {bankAccounts.length > 0 ? (
              <Select value={stmtForm.account_number} onValueChange={async (v) => {
                setStmtForm(f => ({...f, account_number: v}));
                // Auto-fill opening balance via API (gere posted+draft+computed)
                try {
                  const { data } = await api.get('/banking/statements/previous-closing', {
                    params: { account_number: v },
                  });
                  setStmtForm(f => ({
                    ...f,
                    account_number: v,
                    opening_balance: Number(data.balance || 0),
                    _opening_source: data.source,
                    _previous_stmt: data.previous_statement_number || data.previous_statement_id || '',
                    _previous_date: data.previous_statement_date || '',
                  }));
                } catch {
                  // Fallback : 0
                  setStmtForm(f => ({...f, account_number: v, opening_balance: 0}));
                }
              }}>
                <SelectTrigger data-testid="stmt-account-select"><SelectValue placeholder="Selectionner un compte bancaire" /></SelectTrigger>
                <SelectContent>
                  {bankAccounts.map(b => (
                    <SelectItem key={b.iban} value={b.iban} data-testid={`stmt-account-${b.iban}`}>
                      <div className="flex items-center justify-between gap-3 w-full">
                        <div>
                          <span className="font-mono text-xs">{b.iban}</span>
                          {b.label && <span className="ml-2 text-slate-700">{b.label}</span>}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          {b.pcmn_number && <span className="text-[10px] font-mono bg-green-100 text-green-800 px-1.5 py-0.5 rounded">PCMN {b.pcmn_number}</span>}
                          {b.is_default && <span className="text-[10px] bg-blue-100 text-[#01213e] px-1.5 py-0.5 rounded">defaut</span>}
                        </div>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <>
                <Input value={stmtForm.account_number} onChange={e => setStmtForm({...stmtForm, account_number: e.target.value})} placeholder="BE00 0000 0000 0000" />
                <p className="text-[11px] text-amber-600 mt-1">Aucun compte bancaire configure sur cette ACP. Configurez-en un dans Coproprietes.</p>
              </>
            )}
            {/* Mention du compte PCMN selectionne + dernier solde */}
            {stmtForm.account_number && (() => {
              const ba = bankAccounts.find(b => b.iban === stmtForm.account_number);
              const src = stmtForm._opening_source;
              const prevNum = stmtForm._previous_stmt;
              const prevDate = stmtForm._previous_date;
              return (
                <div className="text-[11px] text-slate-500 mt-1.5 space-x-3 flex flex-wrap gap-x-3">
                  {ba?.pcmn_number && <span>Compte PCMN : <b className="font-mono text-slate-700">{ba.pcmn_number}</b></span>}
                  {src === 'posted' && prevDate && (
                    <span className="text-green-700">
                      Solde repris de l&apos;extrait <b className="font-mono">{prevNum}</b> du {fmtDate(prevDate)} (comptabilise)
                    </span>
                  )}
                  {src === 'draft_computed' && prevDate && (
                    <span className="text-amber-600">
                      Solde calcule depuis l&apos;extrait brouillon du {fmtDate(prevDate)} (mouvements non figes)
                    </span>
                  )}
                  {src === 'none' && (
                    <span className="text-[#022D52]">Aucun extrait precedent - solde d&apos;ouverture initialise a 0</span>
                  )}
                </div>
              );
            })()}
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="form-label">Solde ouverture</label>
              <Input type="number" step="0.01" value={stmtForm.opening_balance} onChange={e => setStmtForm({...stmtForm, opening_balance: e.target.value})} data-testid="stmt-opening-balance" />
            </div>
            <div>
              <label className="form-label">Solde fermeture</label>
              <Input type="number" step="0.01" value={stmtForm.closing_balance} onChange={e => setStmtForm({...stmtForm, closing_balance: e.target.value})} data-testid="stmt-closing-balance" />
            </div>
          </div>
          <div className="flex gap-3 justify-end"><Button variant="outline" onClick={() => { setStmtDialog(false); setEditingStmtId(null); }}>Annuler</Button><Button onClick={saveStmt} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="stmt-save-btn">{editingStmtId ? 'Enregistrer' : 'Creer'}</Button></div>
        </div>
      </DialogContent></Dialog>

      {/* Lettrage Dialog - layout pro avec hierarchie claire */}
      <Dialog open={lettrageDialog} onOpenChange={setLettrageDialog}>
        <DialogContent className="max-w-3xl p-0 overflow-hidden" data-testid="lettrage-dialog">
          {/* Header transaction */}
          <div className="bg-gradient-to-r from-[#022D52] to-[#1D4ED8] px-6 py-4 text-white">
            <DialogHeader className="space-y-1">
              <DialogTitle className="text-white text-base flex items-center justify-between gap-3" style={{fontFamily:'Chivo,sans-serif'}}>
                <span>Lettrage de la transaction</span>
                <span className="font-mono text-xl">{Number(lettrageTarget?.amount || 0).toFixed(2)} EUR</span>
              </DialogTitle>
            </DialogHeader>
            {lettrageTarget && (
              <div className="text-[12px] text-white/85 mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5">
                {lettrageTarget.date && <span><b>{fmtDate(lettrageTarget.date)}</b></span>}
                {lettrageTarget.counterparty_name && <span>{lettrageTarget.counterparty_name}</span>}
                {lettrageTarget.communication && (
                  <span className="font-mono text-[11px] bg-white/15 px-2 py-0.5 rounded">{lettrageTarget.communication}</span>
                )}
              </div>
            )}
            {/* iter90eb : Bandeau "Deja lettree" avec detail des cibles */}
            {letteredLinks?.matched && (
              <div
                className="mt-3 bg-white/15 border border-white/30 rounded-md px-3 py-2.5 text-[12px]"
                data-testid="lettrage-existing-links"
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <CheckCircle2 size={13} className="text-emerald-200" />
                  <span className="font-semibold text-white">
                    {(() => {
                      const mt = letteredLinks.match_type;
                      if (mt === 'invoice' && letteredLinks.invoices.length > 0) {
                        return `Cette transaction est deja lettree a ${letteredLinks.invoices.length} facture${letteredLinks.invoices.length > 1 ? 's' : ''}`;
                      }
                      if (mt === 'multi_invoice') {
                        return `Cette transaction paie ${letteredLinks.invoices.length} facture${letteredLinks.invoices.length > 1 ? 's' : ''}`;
                      }
                      if (mt === 'owner_payment' && letteredLinks.owner) return 'Deja lettree a un proprietaire';
                      if (mt === 'supplier_payment' && letteredLinks.supplier) return 'Deja lettree a un fournisseur';
                      return 'Deja lettree';
                    })()}
                  </span>
                  {letteredLinks.lettrage_code && (
                    <span className="ml-auto font-mono text-[10px] bg-white/20 px-1.5 py-0.5 rounded" title="Code lettrage groupe">
                      #{letteredLinks.lettrage_code}
                    </span>
                  )}
                </div>
                {/* Details factures */}
                {letteredLinks.invoices?.length > 0 && (
                  <div className="space-y-1 pl-4" data-testid="lettered-invoices-list">
                    {letteredLinks.invoices.map(inv => (
                      <div
                        key={inv.id}
                        className="flex items-center justify-between gap-3 text-white/95"
                        data-testid={`lettered-invoice-${inv.id}`}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <span className="font-mono text-[11px] font-semibold">{inv.number || '—'}</span>
                          <span className="truncate">{inv.supplier}</span>
                          <span className="text-[10px] bg-white/25 px-1.5 py-0.5 rounded shrink-0">{inv.status?.toUpperCase()}</span>
                        </div>
                        <span className="font-mono text-[11px] shrink-0">{Number(inv.total_amount).toFixed(2)} EUR</span>
                      </div>
                    ))}
                  </div>
                )}
                {/* Owner details */}
                {letteredLinks.owner && (
                  <div className="pl-4 text-white/95">
                    <span className="font-medium">{letteredLinks.owner.name}</span>
                    {letteredLinks.owner.vcs_code && (
                      <span className="ml-2 font-mono text-[10px] bg-white/20 px-1.5 py-0.5 rounded">{letteredLinks.owner.vcs_code}</span>
                    )}
                  </div>
                )}
                {/* Supplier details */}
                {letteredLinks.supplier && (
                  <div className="pl-4 text-white/95">
                    <span className="font-medium">{letteredLinks.supplier.name}</span>
                    {letteredLinks.supplier.vat_number && (
                      <span className="ml-2 font-mono text-[10px] bg-white/20 px-1.5 py-0.5 rounded">{letteredLinks.supplier.vat_number}</span>
                    )}
                  </div>
                )}
                {/* Sibling transactions (batch N->1) */}
                {letteredLinks.sibling_transactions?.length > 0 && (
                  <div className="mt-1.5 pl-4 text-white/80 text-[11px]" data-testid="lettered-siblings">
                    <span className="italic">+ {letteredLinks.sibling_transactions.length} autre{letteredLinks.sibling_transactions.length > 1 ? 's' : ''} transaction{letteredLinks.sibling_transactions.length > 1 ? 's' : ''} du meme lettrage</span>
                    <span className="ml-2 font-mono">
                      (total {letteredLinks.sibling_transactions.reduce((s, t) => s + Math.abs(Number(t.amount || 0)), 0).toFixed(2)} EUR)
                    </span>
                  </div>
                )}
                {/* Warning re-lettrage + bouton action rapide */}
                <div className="mt-2 pt-2 border-t border-white/20 text-[11px] text-amber-200 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <AlertTriangle size={11} />
                    <span>Delettrez d&apos;abord avant de lettrer a une nouvelle cible.</span>
                  </div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      if (!lettrageTarget) return;
                      try {
                        await api.post(`/banking/unlettrage/${lettrageTarget.id}`);
                        toast.success('Transaction delettree');
                        setLetteredLinks({ ...letteredLinks, matched: false, invoices: [], owner: null, supplier: null, sibling_transactions: [] });
                        refreshAfterLettrage();
                      } catch (err) {
                        toast.error(err.response?.data?.detail || 'Erreur delettrage');
                      }
                    }}
                    className="bg-white/10 hover:bg-white/25 border-white/40 text-white h-6 text-[11px] shrink-0"
                    data-testid="lettrage-header-unlettrage-btn"
                  >
                    <Unlink size={11} className="mr-1" /> Delettrer maintenant
                  </Button>
                </div>
              </div>
            )}
            {letteredLinksLoading && !letteredLinks && (
              <div className="mt-3 text-[11px] text-white/70 italic" data-testid="lettered-links-loading">Verification des lettrages existants...</div>
            )}
          </div>

          <div className="px-6 py-4">
            <Tabs defaultValue="owners">
              <TabsList className="grid grid-cols-3 mb-4">
                <TabsTrigger value="owners" data-testid="lettrage-tab-owners">Proprietaires</TabsTrigger>
                <TabsTrigger value="invoices" data-testid="lettrage-tab-invoices">Factures</TabsTrigger>
                <TabsTrigger value="suppliers" data-testid="lettrage-tab-suppliers">Fournisseurs</TabsTrigger>
              </TabsList>

              <TabsContent value="owners" className="mt-0">
                <Input placeholder="Rechercher par nom ou VCS..." value={lookupQuery} onChange={e => setLookupQuery(e.target.value)} className="mb-3" />
                <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                  {owners.filter(o => !lookupQuery || (o.name || '').toLowerCase().includes(lookupQuery.toLowerCase()) || (o.vcs_code || '').includes(lookupQuery)).map(o => (
                    <div key={o.id} className="flex items-center justify-between gap-3 border border-slate-200 rounded-md px-3 py-2.5 hover:border-[#022D52]/40 hover:bg-slate-50 transition-colors">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-slate-900 truncate">{o.name}</div>
                        {o.vcs_code && <div className="text-[11px] font-mono text-[#022D52] mt-0.5">{o.vcs_code}</div>}
                      </div>
                      <Button size="sm" onClick={() => doLettrage(o.id, 'owner_payment')} className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-7 text-xs shrink-0">
                        <Link2 size={11} className="mr-1" /> Lettrer
                      </Button>
                    </div>
                  ))}
                </div>
              </TabsContent>

              <TabsContent value="invoices" className="mt-0">
                <Input
                  placeholder="Filtrer par fournisseur, numero ou description..."
                  value={lookupQuery}
                  onChange={e => setLookupQuery(e.target.value)}
                  className="mb-3"
                  data-testid="lettrage-invoice-search"
                />
                <div className="text-[11px] text-slate-500 mb-3 flex items-center gap-4">
                  <span className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-full bg-red-400" /> A lettrer</span>
                  <span className="flex items-center gap-1.5"><span className="inline-block w-2.5 h-2.5 rounded-full bg-green-400" /> Deja lettree</span>
                  <span className="ml-auto text-slate-600"><b>Astuce :</b> cochez plusieurs factures pour les lettrer ensemble a cette transaction.</span>
                </div>
                {/* Barre de selection multi-factures */}
                {selectedInvoiceIds.size > 0 && (
                  <div className="bg-[#022D52]/10 border border-[#022D52]/30 rounded px-3 py-2 mb-3 flex items-center justify-between text-xs" data-testid="multi-invoice-toolbar">
                    <div>
                      <strong className="text-[#022D52]">{selectedInvoiceIds.size} factures selectionnees</strong>
                      <span className="ml-3 font-mono">Total : <strong>{selectedInvoicesTotal.toFixed(2)} EUR</strong></span>
                      {(() => {
                        const txnAmt = Math.abs(Number(lettrageTarget?.amount || 0));
                        const diff = txnAmt - selectedInvoicesTotal;
                        if (Math.abs(diff) < 0.01) return <span className="ml-2 text-green-700 font-semibold">= SOLDE EXACT</span>;
                        if (diff > 0) return <span className="ml-2 text-amber-700 font-semibold">- partiel (txn reste {diff.toFixed(2)} EUR)</span>;
                        return <span className="ml-2 text-amber-700 font-semibold">- sur-paiement ({(-diff).toFixed(2)} EUR)</span>;
                      })()}
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" onClick={doLettrageMultiInvoices} className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-7 text-xs" data-testid="multi-invoice-confirm-btn">
                        <Link2 size={11} className="mr-1" /> Lettrer ces {selectedInvoiceIds.size} factures
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setSelectedInvoiceIds(new Set())} className="h-7 text-xs">Annuler</Button>
                    </div>
                  </div>
                )}
                <div className="space-y-2 max-h-80 overflow-y-auto pr-1">
                  {(() => {
                    const cpName = (lettrageTarget?.counterparty_name || '').toLowerCase().trim();
                    const q = (lookupQuery || '').toLowerCase().trim();
                    const list = invoices.filter(inv => {
                      if (q) {
                        return (inv.supplier || '').toLowerCase().includes(q)
                            || (inv.number || '').toLowerCase().includes(q)
                            || (inv.description || '').toLowerCase().includes(q);
                      }
                      if (cpName) {
                        return (inv.supplier || '').toLowerCase().includes(cpName)
                            || cpName.includes((inv.supplier || '').toLowerCase());
                      }
                      return true;
                    }).sort((a, b) => {
                      const aPaid = a.status === 'paid' ? 1 : 0;
                      const bPaid = b.status === 'paid' ? 1 : 0;
                      if (aPaid !== bPaid) return aPaid - bPaid;
                      return (b.date || '').localeCompare(a.date || '');
                    });
                    if (list.length === 0) return <p className="text-sm text-slate-400 text-center py-8">Aucune facture trouvee</p>;
                    return list.map(inv => {
                      const isPaid = inv.status === 'paid';
                      const isSelected = selectedInvoiceIds.has(inv.id);
                      const borderClr = isPaid ? 'border-l-green-400 bg-green-50/40' : isSelected ? 'border-l-[#022D52] bg-blue-50/50' : 'border-l-red-400 bg-red-50/30';
                      return (
                        <div
                          key={inv.id}
                          className={`border border-slate-200 border-l-4 ${borderClr} rounded-md px-3 py-2.5 transition-shadow hover:shadow-sm`}
                          data-testid={`lettrage-invoice-row-${inv.id}`}
                        >
                          {/* Ligne 1 : checkbox + numero + fournisseur + badge statut + montant aligned right */}
                          <div className="flex items-start justify-between gap-3 mb-1">
                            <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                              {!isPaid && (
                                <input
                                  type="checkbox"
                                  checked={isSelected}
                                  onChange={() => toggleInvoiceSelected(inv.id)}
                                  className="shrink-0"
                                  data-testid={`lettrage-invoice-check-${inv.id}`}
                                  title="Cocher pour lettrer plusieurs factures avec cette transaction"
                                />
                              )}
                              <span className="font-mono text-xs font-semibold text-slate-700">{inv.number || '—'}</span>
                              <span className="text-sm font-medium text-slate-900 truncate">{inv.supplier || ''}</span>
                              {isPaid
                                ? <span className="text-[10px] bg-green-100 text-green-800 px-2 py-0.5 rounded-full font-semibold border border-green-300">PAYE</span>
                                : <span className="text-[10px] bg-red-100 text-red-800 px-2 py-0.5 rounded-full font-semibold border border-red-300">A PAYER</span>}
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-mono font-semibold text-slate-900 text-sm leading-tight">{Number(inv.total_amount || 0).toFixed(2)} <span className="text-[10px] text-slate-500">EUR</span></div>
                              <div className="text-[10px] text-slate-500 mt-0.5">{fmtDate(inv.date)}</div>
                            </div>
                          </div>
                          {/* Ligne 2 : description + bouton aligned right */}
                          <div className="flex items-end justify-between gap-3">
                            <p className="text-[11px] text-slate-600 line-clamp-2 leading-snug flex-1 min-w-0">
                              {inv.description || <span className="text-slate-400 italic">Aucune description</span>}
                            </p>
                            <div className="shrink-0">
                              {isPaid ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => unlettrageByInvoice(inv.id)}
                                  className="text-orange-600 border-orange-300 hover:bg-orange-50 h-7 text-xs"
                                  data-testid={`unlettrage-invoice-${inv.id}`}
                                ><Unlink size={11} className="mr-1" /> Delettrer</Button>
                              ) : (
                                <Button
                                  size="sm"
                                  onClick={() => doLettrage(inv.id, 'invoice')}
                                  className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-7 text-xs"
                                  data-testid={`lettrage-invoice-${inv.id}`}
                                ><Link2 size={11} className="mr-1" /> Lettrer</Button>
                              )}
                            </div>
                          </div>
                          {/* iter90hf : compte comptable de la depense + alerte
                              si la facture est d'un exercice anterieur au FY courant */}
                          {(() => {
                            const acctList = Array.isArray(inv.lines) && inv.lines.length > 0
                              ? [...new Set(inv.lines.map(l => l.account_number).filter(Boolean))]
                              : (inv.account_number ? [inv.account_number] : []);
                            const fyStart = selectedFiscalYear?.start_date;
                            const isPrevFY = fyStart && inv.date && inv.date < fyStart;
                            if (!acctList.length && !isPrevFY) return null;
                            return (
                              <div className="mt-1 flex items-center gap-1.5 flex-wrap text-[10px]">
                                {acctList.map(a => (
                                  <Badge key={a} variant="outline" className="bg-slate-50 text-slate-700 font-mono text-[10px] px-1.5 py-0 border-slate-300"
                                         title={`Compte de charges : ${a}`}>
                                    {a}
                                  </Badge>
                                ))}
                                {isPrevFY && (
                                  <Badge className="bg-orange-100 text-orange-800 border border-orange-300 text-[10px] px-1.5 py-0"
                                         title={`Facture datee du ${inv.date} - anterieure au debut d'exercice ${fyStart}. Verifier la balance A-Nouveau : le solde a peut-etre deja ete repris.`}>
                                    Exercice precedent
                                  </Badge>
                                )}
                              </div>
                            );
                          })()}
                        </div>
                      );
                    });
                  })()}
                </div>
              </TabsContent>

              <TabsContent value="suppliers" className="mt-0">
                <Input placeholder="Rechercher par nom ou TVA..." value={lookupQuery} onChange={e => setLookupQuery(e.target.value)} className="mb-3" />
                <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                  {suppliers.filter(s => !lookupQuery || (s.name || '').toLowerCase().includes(lookupQuery.toLowerCase()) || (s.vat_number || '').includes(lookupQuery)).map(s => {
                    // iter90hf : afficher le compte tier 44000XXX du fournisseur
                    // pour la copro courante (aide au rapprochement avec le journal AN).
                    const ta = s.tier_accounts && selectedCopro ? s.tier_accounts[selectedCopro] : null;
                    const tierAcc = ta ? (ta.main || ta) : null;
                    return (
                      <div key={s.id} className="flex items-center justify-between gap-3 border border-slate-200 rounded-md px-3 py-2.5 hover:border-[#022D52]/40 hover:bg-slate-50 transition-colors">
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-slate-900 truncate">{s.name}</div>
                          <div className="flex items-center gap-2 flex-wrap mt-0.5">
                            {s.vat_number && <span className="text-[11px] font-mono text-slate-500">{s.vat_number}</span>}
                            {tierAcc && (
                              <Badge variant="outline" className="bg-blue-50 border-blue-200 text-blue-900 font-mono text-[10px] px-1.5 py-0"
                                     title="Compte tier fournisseur 44000XXX pour cette ACP">
                                {typeof tierAcc === 'string' ? tierAcc : ''}
                              </Badge>
                            )}
                          </div>
                        </div>
                        <Button size="sm" onClick={() => doLettrage(s.id, 'supplier_payment')} className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-7 text-xs shrink-0">
                          <Link2 size={11} className="mr-1" /> Lettrer
                        </Button>
                      </div>
                    );
                  })}
                </div>
              </TabsContent>
            </Tabs>
          </div>
        </DialogContent>
      </Dialog>

      {/* ----- Dialog lettrage en lot (N transactions -> 1 facture) ----- */}
      <Dialog open={batchLettrageDialog} onOpenChange={setBatchLettrageDialog}>
        <DialogContent className="max-w-3xl p-0 overflow-hidden" data-testid="batch-lettrage-dialog">
          <div className="bg-gradient-to-r from-[#022D52] to-[#1D4ED8] text-white px-5 py-4">
            <DialogTitle className="text-base font-semibold m-0">Lettrer {selectedTxnIds.size} transaction(s) vers une facture</DialogTitle>
            <div className="mt-1 text-xs opacity-90">
              Total selectionne : <strong className="font-mono">{selectedTotal.toFixed(2)} EUR</strong>
              {' '} - choisissez UNE facture a solder (totalement ou partiellement).
            </div>
          </div>
          <div className="p-5 space-y-3">
            <Input
              placeholder="Rechercher par numero, fournisseur ou description..."
              value={batchInvoiceSearch}
              onChange={e => setBatchInvoiceSearch(e.target.value)}
              data-testid="batch-lettrage-search"
            />
            <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
              {(() => {
                const q = batchInvoiceSearch.trim().toLowerCase();
                const list = invoices.filter(inv => {
                  if (!q) return true;
                  return (inv.number || '').toLowerCase().includes(q)
                      || (inv.supplier || '').toLowerCase().includes(q)
                      || (inv.description || '').toLowerCase().includes(q);
                });
                if (list.length === 0) {
                  return <div className="text-center py-6 text-slate-400 text-sm">Aucune facture correspondante</div>;
                }
                return list.map(inv => {
                  const amount = Number(inv.total_amount || inv.amount_ttc || inv.amount || 0);
                  const diff = amount - selectedTotal;
                  const isExact = Math.abs(diff) < 0.01;
                  const isOver = diff < -0.01;
                  return (
                    <div key={inv.id} className={`border rounded-md px-3 py-2.5 ${isExact ? 'border-green-400 bg-green-50/40' : isOver ? 'border-amber-300 bg-amber-50/30' : 'border-slate-200'}`} data-testid={`batch-lettrage-invoice-${inv.id}`}>
                      <div className="flex items-start justify-between gap-3 mb-1">
                        <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                          <span className="font-mono text-xs font-semibold text-slate-700">{inv.number || '—'}</span>
                          <span className="text-sm font-medium text-slate-900 truncate">{inv.supplier || ''}</span>
                          {isExact && <span className="text-[10px] bg-green-600 text-white px-2 py-0.5 rounded-full font-semibold">SOLDE EXACT</span>}
                          {isOver && <span className="text-[10px] bg-amber-500 text-white px-2 py-0.5 rounded-full font-semibold" title="L'excedent sera porte au compte tiers du proprietaire lors de la comptabilisation">SUR-PAIEMENT (+{(selectedTotal - amount).toFixed(2)} EUR)</span>}
                          {!isExact && !isOver && diff > 0.01 && (
                            <span className="text-[10px] bg-amber-500 text-white px-2 py-0.5 rounded-full font-semibold">PARTIEL ({(amount - selectedTotal).toFixed(2)} EUR)</span>
                          )}
                        </div>
                        <div className="text-right shrink-0">
                          <div className="font-mono font-semibold text-slate-900 text-sm leading-tight">{amount.toFixed(2)} <span className="text-[10px] text-slate-500">EUR</span></div>
                          <div className="text-[10px] text-slate-500 mt-0.5">{fmtDate(inv.date)}</div>
                        </div>
                      </div>
                      <div className="flex items-end justify-between gap-3">
                        <p className="text-[11px] text-slate-600 line-clamp-2 leading-snug flex-1 min-w-0">
                          {inv.description || <span className="text-slate-400 italic">Aucune description</span>}
                        </p>
                        <Button
                          size="sm"
                          onClick={() => doBatchLettrage(inv.id)}
                          className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-7 text-xs"
                          data-testid={`batch-lettrage-confirm-${inv.id}`}
                        ><Link2 size={11} className="mr-1" /> Lettrer ici</Button>
                      </div>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* CODA Import Mapping Dialog */}
      <CodaImportDialog
        open={codaDialogOpen}
        onClose={() => setCodaDialogOpen(false)}
        preview={codaPreview}
        copropriete_id={selectedCopro || ''}
        owners={owners}
        suppliers={suppliers}
        invoices={invoices}
        onSuccess={() => { setCodaPreview(null); load(); }}
      />

      {/* iter90k : Categorize dialog (nature de depense / revenu) */}
      <Dialog open={categorizeDialog} onOpenChange={setCategorizeDialog}>
        <DialogContent className="max-w-4xl" data-testid="categorize-dialog">
          <DialogHeader>
            <DialogTitle className="text-base font-semibold m-0">Categoriser la transaction</DialogTitle>
          </DialogHeader>
          {categorizeTarget && (() => {
            const txnAmt = Math.abs(Number(categorizeTarget.amount) || 0);
            const isCredit = Number(categorizeTarget.amount) > 0;
            const sumSplits = categorizeSplits.reduce((a, s) => a + Number(s.amount || 0), 0);
            const diff = Math.round((sumSplits - txnAmt) * 100) / 100;
            const filteredCats = expenseCategories.filter(c => {
              // Si credit -> proposer produits (classe 7) en priorite; sinon charges (6).
              // On laisse tout visible pour flexibilite mais on tri.
              return true;
            }).sort((a, b) => {
              const isProdA = (a.kind === 'produit') || (a.account_number || '').startsWith('7');
              const isProdB = (b.kind === 'produit') || (b.account_number || '').startsWith('7');
              if (isCredit) return (isProdB ? 1 : 0) - (isProdA ? 1 : 0);
              return (isProdA ? 1 : 0) - (isProdB ? 1 : 0);
            });
            return (
              <div className="space-y-3">
                <div className="text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded px-3 py-2">
                  <div className="flex justify-between items-center">
                    <span><b>{fmtDate(categorizeTarget.date)}</b> — {categorizeTarget.counterparty_name || <em className="text-slate-400">Sans contrepartie</em>}</span>
                    <span className={`font-mono font-semibold ${isCredit ? 'text-green-700' : 'text-red-700'}`}>
                      {isCredit ? '+' : '−'}{txnAmt.toFixed(2)} EUR
                    </span>
                  </div>
                  {categorizeTarget.communication && (
                    <div className="mt-1 text-slate-500 truncate">{categorizeTarget.communication}</div>
                  )}
                </div>

                <div className="space-y-2 max-h-[50vh] overflow-y-auto pr-1">
                  {categorizeSplits.map((split, i) => {
                    const selectedCat = filteredCats.find(c => c.id === split.expense_category_id);
                    const directPcmn = !selectedCat && split.account_number ? pcmnAccounts.find(a => a.number === split.account_number) : null;
                    const displayAcc = selectedCat?.account_number || split.account_number || '';
                    const displayName = selectedCat?.account_name || selectedCat?.name || directPcmn?.name || '';
                    return (
                    <div key={split._key || i} className="border border-slate-200 rounded p-3 space-y-2" data-testid={`cat-split-${i}`}>
                      {/* Row 1: Nature de depense */}
                      <div className="grid grid-cols-12 gap-2 items-end">
                        <div className="col-span-11">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Nature de depense</label>
                          <Select value={split.expense_category_id} onValueChange={(v) => {
                            const cat = filteredCats.find(c => c.id === v);
                            setCategorizeSplits(prev => {
                              const s = [...prev];
                              s[i] = {
                                ...s[i],
                                expense_category_id: v,
                                account_number: '',
                                distribution_key_id: cat?.distribution_key_id || s[i].distribution_key_id || '',
                              };
                              return s;
                            });
                          }}>
                            <SelectTrigger className="h-8 text-xs" data-testid={`cat-split-nature-${i}`}><SelectValue placeholder="Choisir une nature..." /></SelectTrigger>
                            <SelectContent>
                              {filteredCats.length === 0 && <div className="px-3 py-2 text-xs text-slate-400">Aucune nature configuree</div>}
                              {filteredCats.map(c => {
                                const acc = c.account_number || '';
                                const isTransfer = c.kind === 'transfer' || acc.startsWith('58');
                                const isProd = !isTransfer && (c.kind === 'produit' || acc.startsWith('7'));
                                const badge = isTransfer ? 'virement' : (isProd ? 'produit' : 'charge');
                                const cls = isTransfer ? 'text-indigo-700' : (isProd ? 'text-emerald-700' : '');
                                return (
                                  <SelectItem key={c.id} value={c.id}>
                                    <span className={cls}>
                                      <span className="font-mono font-semibold">{acc}</span> — {c.name} <span className="text-slate-400 text-[10px]">({badge})</span>
                                    </span>
                                  </SelectItem>
                                );
                              })}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="col-span-1 flex justify-end">
                          {categorizeSplits.length > 1 && (
                            <Button variant="ghost" size="sm" onClick={() => removeCatSplit(i)}
                              className="h-8 w-8 p-0 text-red-400 hover:text-red-600"
                              data-testid={`cat-split-remove-${i}`}
                              title="Supprimer"><X size={13} /></Button>
                          )}
                        </div>
                      </div>
                      {/* Row 2: Compte + Nom (readonly from nature) + Cle + Montant */}
                      <div className="grid grid-cols-12 gap-2 items-end">
                        <div className="col-span-2">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Compte</label>
                          <div className="h-8 flex items-center px-2 bg-slate-50 border border-slate-200 rounded text-xs font-mono font-bold text-slate-800" data-testid={`cat-split-account-display-${i}`}>
                            {displayAcc || '—'}
                          </div>
                        </div>
                        <div className="col-span-4">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Libelle compte</label>
                          <div className="h-8 flex items-center px-2 bg-slate-50 border border-slate-200 rounded text-xs text-slate-700 truncate" data-testid={`cat-split-label-display-${i}`}>
                            {displayName || '—'}
                          </div>
                        </div>
                        <div className="col-span-3">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Cle repartition</label>
                          <Select value={split.distribution_key_id} onValueChange={(v) => updateCatSplit(i, 'distribution_key_id', v)}>
                            <SelectTrigger className="h-8 text-xs" data-testid={`cat-split-key-${i}`}><SelectValue placeholder="Cle..." /></SelectTrigger>
                            <SelectContent>
                              {distributionKeys.map(k => (
                                <SelectItem key={k.id} value={k.id}>{k.name}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="col-span-3">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Montant</label>
                          <Input type="number" step="0.01" className="h-8 text-xs font-mono" value={split.amount}
                            onChange={(e) => updateCatSplit(i, 'amount', e.target.value)}
                            data-testid={`cat-split-amount-${i}`} />
                        </div>
                      </div>
                      {/* Row 3: Parts proprio/occupant (from nature config) + Description */}
                      <div className="grid grid-cols-12 gap-2 items-end">
                        <div className="col-span-2">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Occupant %</label>
                          <div className="h-7 flex items-center px-2 bg-amber-50 border border-amber-200 rounded text-xs font-mono text-amber-800">
                            {selectedCat?.default_occupant_pct != null ? `${selectedCat.default_occupant_pct}%` : '—'}
                          </div>
                        </div>
                        <div className="col-span-2">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Proprio %</label>
                          <div className="h-7 flex items-center px-2 bg-blue-50 border border-blue-200 rounded text-xs font-mono text-blue-800">
                            {selectedCat?.default_proprietaire_pct != null ? `${selectedCat.default_proprietaire_pct}%` : '—'}
                          </div>
                        </div>
                        <div className="col-span-8">
                          <label className="text-[10px] text-slate-500 uppercase tracking-wide">Description</label>
                          <Input placeholder="Facultatif" className="h-7 text-xs"
                            value={split.description || ''}
                            onChange={(e) => updateCatSplit(i, 'description', e.target.value)}
                            data-testid={`cat-split-desc-${i}`} />
                        </div>
                      </div>
                      {/* Row 4: Compte PCMN direct (alternative to nature) */}
                      <div className="flex items-center gap-2 pt-1 border-t border-slate-100">
                        <span className="text-[10px] text-slate-400 uppercase tracking-wide shrink-0">ou compte direct</span>
                        <div className="flex-1">
                          <AccountSearchSelect
                            accounts={pcmnAccounts}
                            value={split.account_number || ''}
                            onChange={(num) => {
                              setCategorizeSplits(prev => {
                                const s = [...prev];
                                s[i] = {
                                  ...s[i],
                                  account_number: num || '',
                                  expense_category_id: num ? '' : s[i].expense_category_id,
                                };
                                return s;
                              });
                            }}
                            placeholder="Chercher un compte PCMN (numero ou nom)"
                            allowClear
                            testId={`cat-split-account-${i}`}
                          />
                        </div>
                      </div>
                    </div>
                  );})}
                </div>

                <Button variant="outline" size="sm" onClick={addCatSplit} className="h-7 text-xs" data-testid="cat-split-add">
                  <PlusCircle size={13} className="mr-1" /> Ajouter un split (multi-natures)
                </Button>

                <div className="flex justify-between items-center border-t border-slate-200 pt-2 text-xs">
                  <div className="text-slate-600">
                    Somme des splits : <span className="font-mono font-semibold">{sumSplits.toFixed(2)}</span> / <span className="font-mono">{txnAmt.toFixed(2)} EUR</span>
                  </div>
                  <div>
                    {Math.abs(diff) < 0.01 ? (
                      <Badge className="bg-green-50 text-green-700 border-green-200 text-[10px]" variant="outline"><CheckCircle2 size={11} className="mr-1" /> Equilibre</Badge>
                    ) : (
                      <Badge className="bg-orange-50 text-orange-700 border-orange-200 text-[10px]" variant="outline"><AlertTriangle size={11} className="mr-1" /> Ecart {diff > 0 ? '+' : ''}{diff.toFixed(2)}</Badge>
                    )}
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" size="sm" onClick={() => setCategorizeDialog(false)} className="h-8 text-xs">Annuler</Button>
                  <Button size="sm" onClick={doCategorize}
                    disabled={Math.abs(diff) >= 0.01 || categorizeSplits.some(s => {
                      const missingNat = !s.expense_category_id && !(s.account_number || '').trim();
                      if (missingNat) return true;
                      if (Number(s.amount) <= 0) return true;
                      // Cle facultative si compte 58 (virement interne) : ni account
                      // direct 58, ni nature dont le compte commence par 58.
                      const acc = (s.account_number || '').trim();
                      const cat = expenseCategories.find(c => c.id === s.expense_category_id);
                      const catAcc = (cat?.account_number || '');
                      const isTransfer = acc.startsWith('58') || catAcc.startsWith('58') || cat?.kind === 'transfer';
                      if (isTransfer) return false;
                      return !s.distribution_key_id;
                    })}
                    className="bg-purple-600 hover:bg-purple-700 text-white h-8 text-xs"
                    data-testid="cat-confirm-btn">
                    <Tag size={12} className="mr-1" /> Categoriser
                  </Button>
                </div>
              </div>
            );
          })()}
        </DialogContent>
      </Dialog>

      {/* ---- Modal confirmation suppression extrait ---- */}
      <Dialog open={!!deletePreview} onOpenChange={(o) => { if (!o) setDeletePreview(null); }}>
        <DialogContent className="max-w-md" data-testid="delete-stmt-confirm-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-600" style={{fontFamily:'Chivo,sans-serif'}}>
              <AlertTriangle className="w-5 h-5" /> Supprimer l&apos;extrait
            </DialogTitle>
          </DialogHeader>
          {deletePreview && (
            <div className="space-y-4 py-2">
              <p className="text-sm text-gray-700">
                Vous etes sur le point de supprimer l&apos;extrait <strong>{deletePreview.number || deletePreview.statement_id?.slice(0,8)}</strong>. Cette action est irreversible.
              </p>
              <div className="bg-gray-50 rounded-lg p-3 space-y-2 text-sm border">
                <div className="flex justify-between">
                  <span className="text-gray-600">Transactions</span>
                  <span className="font-medium">{deletePreview.total_transactions}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">Ecritures FI supprimees</span>
                  <span className="font-medium">{deletePreview.fi_entries_to_delete}</span>
                </div>
                {deletePreview.lettrages_to_cancel > 0 && (
                  <div className="flex justify-between text-amber-700 font-medium">
                    <span className="flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> Lettrages annules</span>
                    <span>{deletePreview.lettrages_to_cancel}</span>
                  </div>
                )}
                {deletePreview.invoices_impacted > 0 && (
                  <div className="flex justify-between text-amber-700">
                    <span className="text-gray-600 ml-5">Factures repassees en impayees</span>
                    <span className="font-medium">{deletePreview.invoices_impacted}</span>
                  </div>
                )}
              </div>
              {deletePreview.lettrages_to_cancel > 0 && (
                <p className="text-xs text-amber-600 flex items-start gap-1">
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  Les factures liees seront remises en statut &ldquo;impayee&rdquo;. Vous devrez les re-lettrer manuellement.
                </p>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" onClick={() => setDeletePreview(null)} data-testid="delete-stmt-cancel-btn">
                  Annuler
                </Button>
                <Button variant="destructive" onClick={confirmDeleteStmt} data-testid="delete-stmt-confirm-btn">
                  Supprimer definitivement
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
