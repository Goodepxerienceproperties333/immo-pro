import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Home, Search, Archive, RotateCcw, Landmark, PlusCircle, X, Eraser, Wand2, Upload, UserPlus, FileText, Download, Image as ImageIcon, CheckCircle2, ClipboardCheck, AlertTriangle, Users, Loader2 } from 'lucide-react';
import BulkCsvImportDialog from '@/components/BulkCsvImportDialog';
import PdfImportDialog from '@/components/PdfImportDialog';
import ImportSummary from '@/components/ImportSummary';
import { useDirtyGuard } from '@/hooks/useDirtyGuard';

// iter95l : combobox (Popover + Command) - dropdown avec liste + champ recherche.
// Remplace l'autocomplete precedent qui posait probleme (utilisateur clique sur
// suggestion mais assignation pas garantie). Ici : bouton "Selectionner..." ->
// popover avec search input + liste, click = affectation immediate + badge.
function LotOwnerCombobox({ lotIdx, options, onPick, ownersEmpty }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="ml-auto min-w-[240px] h-7 px-2 text-[11px] border border-slate-200 rounded-md bg-white text-left hover:bg-slate-50 flex items-center justify-between gap-2"
          data-testid={`lot-${lotIdx}-owner-search`}
        >
          <span className="text-slate-500 truncate">
            {options.length === 0 && ownersEmpty
              ? 'Aucun proprietaire dispo'
              : options.length === 0
                ? 'Tous deja affectes'
                : `Affecter un proprietaire (${options.length})`}
          </span>
          <svg width="10" height="10" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M6 8l4 4 4-4" stroke="#64748b" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[300px] p-0" data-testid={`lot-${lotIdx}-suggestions`}>
        <Command>
          <CommandInput placeholder="Rechercher..." className="h-8 text-[11px]" data-testid={`lot-${lotIdx}-combobox-input`} />
          <CommandList className="max-h-56 overflow-y-auto">
            <CommandEmpty className="py-3 text-center text-[11px] text-slate-500">
              {ownersEmpty ? "Retournez a l'etape 2 pour ajouter des proprietaires" : 'Aucun resultat'}
            </CommandEmpty>
            <CommandGroup>
              {options.map(o => {
                const label = `${o.name || `${o.last_name || ''} ${o.first_name || ''}`.trim()} ${o.email || ''} ${o.auxiliary_code || ''} ${o.vcs_code || ''}`;
                return (
                  <CommandItem
                    key={o.id}
                    value={label + ' ' + o.id}
                    onSelect={() => { onPick(o.id); setOpen(false); }}
                    className="text-[11px] cursor-pointer"
                    data-testid={`lot-${lotIdx}-suggestion-${o.id}`}
                  >
                    <div className="flex flex-col">
                      <span className="font-medium">
                        {o.name || `${o.last_name || ''} ${o.first_name || ''}`.trim()}
                        {o.auxiliary_code && <span className="ml-1 font-mono text-[9px] text-slate-500">({o.auxiliary_code})</span>}
                      </span>
                      {(o.email || o.phone) && (
                        <span className="text-[9px] text-slate-500">{[o.email, o.phone].filter(Boolean).join(' - ')}</span>
                      )}
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

// iter95k : dropdown en portail avec position fixed pour eviter le clipping
// par le parent scrollable (max-h-[380px] overflow-y-auto de la liste des lots).
function PortalDropdown({ anchorRef, open, children, testId }) {
  const [pos, setPos] = useState(null);
  useEffect(() => {
    if (!open || !anchorRef.current) { setPos(null); return; }
    const update = () => {
      const el = anchorRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPos({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 240) });
    };
    update();
    window.addEventListener('scroll', update, true);
    window.addEventListener('resize', update);
    return () => {
      window.removeEventListener('scroll', update, true);
      window.removeEventListener('resize', update);
    };
  }, [open, anchorRef]);
  if (!open || !pos) return null;
  return createPortal(
    <div
      className="bg-white border border-slate-200 rounded-md shadow-lg max-h-56 overflow-y-auto"
      style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
      onMouseDown={e => e.preventDefault()}
      data-testid={testId}
    >
      {children}
    </div>,
    document.body
  );
}

// iter95k : Input + dropdown en portail (evite le clipping par overflow parent)
function LotOwnerSearchInput({ lotIdx, value, onChange, onFocusLot, onBlurLot, focused, suggestions, ownersEmpty, onPick }) {
  const inputRef = useRef(null);
  return (
    <>
      <input
        ref={inputRef}
        value={value}
        onChange={e => onChange(e.target.value)}
        onFocus={onFocusLot}
        onBlur={onBlurLot}
        placeholder="Rechercher proprio..."
        className="pl-6 h-6 text-[11px] w-full rounded-md border border-slate-200 bg-white px-3 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-950 focus-visible:ring-offset-2"
        data-testid={`lot-${lotIdx}-owner-search`}
      />
      <PortalDropdown anchorRef={inputRef} open={focused} testId={`lot-${lotIdx}-suggestions`}>
        {suggestions.length > 0 ? (
          suggestions.map(o => (
            <button key={o.id} type="button"
              onMouseDown={e => { e.preventDefault(); onPick(o.id); }}
              className="w-full text-left px-2 py-1 hover:bg-[#022D52]/5 border-b last:border-b-0 border-slate-100 text-[11px] select-none cursor-pointer"
              data-testid={`lot-${lotIdx}-suggestion-${o.id}`}
            >
              <div className="font-medium pointer-events-none">{o.name} {o.auxiliary_code && <span className="font-mono text-[9px] text-slate-500">({o.auxiliary_code})</span>}</div>
              {(o.email || o.phone) && (
                <div className="text-[9px] text-slate-500 truncate pointer-events-none">{[o.email, o.phone].filter(Boolean).join(' - ')}</div>
              )}
            </button>
          ))
        ) : (
          <div className="px-2 py-2 text-[11px] text-slate-500 italic" data-testid={`lot-${lotIdx}-suggestions-empty`}>
            {(value || '').trim()
              ? `Aucun proprietaire ne correspond a "${value}"`
              : ownersEmpty
                ? "Aucun proprietaire disponible - retournez a l'etape 2 pour en ajouter"
                : "Tous les proprietaires sont deja affectes a ce lot"}
          </div>
        )}
      </PortalDropdown>
    </>
  );
}

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
  // iter95h : dialogs "ajouter manuellement" pour proprietaires et lots
  const emptyManualOwner = { last_name: '', first_name: '', email: '', phone: '', address: '', postal_code: '', city: '', country: 'Belgique' };
  const [manualOwnerOpen, setManualOwnerOpen] = useState(false);
  const [manualOwner, setManualOwner] = useState(emptyManualOwner);
  const [savingManualOwner, setSavingManualOwner] = useState(false);
  // iter95i : candidats de reutilisation quand un homonyme est detecte
  const [manualOwnerHomonyms, setManualOwnerHomonyms] = useState([]);
  const [manualLotOpen, setManualLotOpen] = useState(false);
  const [manualLot, setManualLot] = useState({ ...emptyLot });
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
  // iter93ag : dialogue de creation rapide d'un proprietaire manquant depuis
  // l'etape 5 (Affectation). Pre-rempli avec le libelle importe du lot orphelin.
  //   { lotIdx, aux, name, first, last, email, iban, saving }
  const [quickCreateOwner, setQuickCreateOwner] = useState(null);
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
    // iter93bo : FIX GDPR CRITIQUE. En mode CREATION ACP, on ne pre-fetch
    // PLUS les owners orphelins du syndic (fuite de PII : nom + email +
    // code affiches AVANT tout upload). On affiche uniquement les proprios
    // AJOUTES pendant la session d'import (sessionOwners). En mode EDITION
    // on garde le comportement historique (tous les proprios lies au syndic
    // + orphelins, pour permettre d'ajouter des orphelins existants a une
    // ACP existante). Le bouton "+ Ajouter un proprietaire existant" dans
    // l'onglet Lots utilise une API dediee qui reste, avec log d'audit.
    if (editing) {
      const params = { include_unassigned: true, copropriete_id: 'all' };
      api.get('/owners', { params }).then(r => {
        setOwners(r.data || []);
      }).catch(() => {});
    } else {
      // Creation : ne montrer QUE les proprios de la session en cours.
      setOwners(sessionOwners);
    }
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

  // iter95h : ouverture des dialogs "ajouter manuellement"
  const openManualOwnerDialog = () => { setManualOwner(emptyManualOwner); setManualOwnerHomonyms([]); setManualOwnerOpen(true); };
  const openManualLotDialog = () => { setManualLot({ ...emptyLot }); setManualLotOpen(true); };

  // iter95i : ajoute un proprio existant a la liste (reutilisation)
  const reuseExistingOwner = (existing) => {
    if (!existing?.id) return;
    setOwners(prev => prev.some(o => o.id === existing.id) ? prev : [...prev, existing]);
    setSessionOwners(prev => prev.some(o => o.id === existing.id) ? prev : [...prev, existing]);
    toast.success(`Proprietaire "${existing.name || existing.last_name}" reutilise`);
    setManualOwnerHomonyms([]);
    setManualOwnerOpen(false);
  };

  const submitManualOwner = async (opts = {}) => {
    const forceHomonym = !!opts.force;
    const acceptReuse = !!opts.acceptReuse;
    const last = (manualOwner.last_name || '').trim();
    if (!last) { toast.error('Le nom est obligatoire'); return; }
    if (savingManualOwner) return;
    setSavingManualOwner(true);
    try {
      const first = (manualOwner.first_name || '').trim();
      const name = (last + ' ' + first).trim();
      // iter95j : en ajout MANUEL on ne fusionne PAS silencieusement (bug UX
      // remonte : l'utilisateur croyait ajouter "Jean Sait rien" et voyait
      // apparaitre la fiche existante avec le meme email).
      // - Sans opts : creation STRICTE (409 si email/phone/BCE existe deja)
      // - opts.acceptReuse : autorise la reutilisation apres confirmation utilisateur
      // - opts.force : force la creation malgre un homonyme (nom identique)
      const params = new URLSearchParams();
      if (acceptReuse) params.set('reuse_on_duplicate', 'true');
      if (forceHomonym) params.set('force_create_despite_homonym', 'true');
      const url = params.toString() ? `/owners?${params.toString()}` : '/owners';
      const resp = await api.post(url, {
        first_name: first, last_name: last, name,
        address: manualOwner.address || '',
        postal_code: manualOwner.postal_code || '',
        city: manualOwner.city || '',
        country: manualOwner.country || 'Belgique',
        email: manualOwner.email || '',
        phone: manualOwner.phone || '',
      });
      const created = resp?.data;
      if (created?.id) {
        setOwners(prev => prev.some(o => o.id === created.id) ? prev : [...prev, created]);
        setSessionOwners(prev => prev.some(o => o.id === created.id) ? prev : [...prev, created]);
        if (created._reused) {
          toast.success(`Fiche existante reutilisee : ${created.name}`);
        } else {
          toast.success(`Proprietaire "${created.name}" ajoute`);
        }
        setManualOwnerHomonyms([]);
        setManualOwnerOpen(false);
      } else {
        toast.error('Reponse inattendue du serveur');
      }
    } catch (err) {
      const status = err.response?.status;
      const msg = err.response?.data?.detail || 'Echec de la creation du proprietaire';
      const msgLower = typeof msg === 'string' ? msg.toLowerCase() : '';
      const isStrict = status === 409 && msgLower.includes('doublon strict');
      const isHomonym = status === 409 && msgLower.includes('homonyme');
      if (isStrict || isHomonym) {
        // Cherche les candidats similaires pour proposer la reutilisation.
        // Priorite au match par email/phone (STRICT), fallback nom (HOMONYM).
        try {
          const em = (manualOwner.email || '').trim();
          const ph = (manualOwner.phone || '').trim();
          let candidates = [];
          if (em) {
            const r = await api.get('/owners', { params: { search: em, include_unassigned: true, copropriete_id: 'all', limit: 8 } });
            candidates = r.data || [];
          }
          if (!candidates.length && ph) {
            const r2 = await api.get('/owners', { params: { search: ph, include_unassigned: true, copropriete_id: 'all', limit: 8 } });
            candidates = r2.data || [];
          }
          if (!candidates.length) {
            const r3 = await api.get('/owners', { params: { search: last, include_unassigned: true, copropriete_id: 'all', limit: 8 } });
            candidates = (r3.data || []).filter(o => {
              const n = (o.name || (o.last_name || '') + ' ' + (o.first_name || '')).toLowerCase();
              return n.includes(last.toLowerCase());
            });
          }
          if (candidates.length > 0) {
            // Attache le contexte (strict vs homonym) pour adapter les CTA du bloc
            setManualOwnerHomonyms(candidates.map(c => ({ ...c, _dup_kind: isStrict ? 'strict' : 'homonym' })));
            toast.info(isStrict
              ? "Un proprietaire avec ces coordonnees (email/telephone) existe deja"
              : "Un proprietaire au nom similaire existe deja");
          } else {
            toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg));
          }
        } catch (_e) {
          toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
      } else {
        toast.error(typeof msg === 'string' ? msg : JSON.stringify(msg));
      }
    } finally {
      setSavingManualOwner(false);
    }
  };

  const submitManualLot = () => {
    const n = (manualLot.number || '').trim();
    if (!n) { toast.error('Le numero de lot est obligatoire'); return; }
    const existing = (form.lots || []).some(l => (l.number || '').trim().toLowerCase() === n.toLowerCase());
    if (existing) { toast.error(`Un lot avec le numero "${n}" existe deja`); return; }
    setForm(f => ({
      ...f,
      lots: [...(f.lots || []), {
        ...manualLot,
        number: n,
        floor: Number(manualLot.floor) || 0,
        area: Number(manualLot.area) || 0,
        quotity: Number(manualLot.quotity) || 0,
        owner_ids: [],
      }],
    }));
    toast.success(`Lot ${n} ajoute`);
    setManualLotOpen(false);
  };

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

  // iter93ag : ouvre le dialogue de creation rapide d'un proprietaire pour un
  // lot orphelin. Pre-remplit avec le libelle importe (heuristique last/first).
  const openQuickCreateOwner = (i) => {
    const lot = form.lots[i] || {};
    const rawName = (lot._imported_owner_name || '').trim();
    // Heuristique : "Mme LOUETTE Romain" -> last="LOUETTE" first="Romain"
    // "M. DUPONT Jean-Pierre" -> last="DUPONT" first="Jean-Pierre"
    // "LOUETTE & BARBIEAUX Romain" -> on garde tel quel en name, last=""
    let cleaned = rawName;
    const civilities = ['Mme', 'M.', 'Mlle', 'Mr', 'Mr.', 'Madame', 'Monsieur'];
    for (const c of civilities) {
      if (cleaned.toLowerCase().startsWith(c.toLowerCase() + ' ')) {
        cleaned = cleaned.substring(c.length + 1).trim();
        break;
      }
    }
    let first = '', last = '';
    if (cleaned && !cleaned.includes('&')) {
      const parts = cleaned.split(/\s+/);
      if (parts.length >= 2) {
        // Le prenom est generalement en dernier (format belge Optipro)
        first = parts.slice(-1)[0];
        last = parts.slice(0, -1).join(' ');
      } else {
        last = cleaned;
      }
    }
    setQuickCreateOwner({
      lotIdx: i,
      aux: lot._imported_owner_aux || '',
      name: rawName,
      first_name: first,
      last_name: last,
      email: '',
      iban: '',
      saving: false,
    });
  };

  const submitQuickCreateOwner = async () => {
    if (!quickCreateOwner) return;
    const q = quickCreateOwner;
    const last = (q.last_name || '').trim();
    const first = (q.first_name || '').trim();
    const name = (q.name || (last + ' ' + first).trim()).trim();
    if (!last && !name) {
      toast.error('Le nom est obligatoire');
      return;
    }
    setQuickCreateOwner({ ...q, saving: true });
    try {
      const { data } = await api.post('/owners?reuse_on_duplicate=true', {
        first_name: first,
        last_name: last || name,
        name,
        auxiliary_code: q.aux || '',
        email: q.email || '',
        iban: q.iban || '',
        country: 'Belgique',
      });
      if (data?._reused) {
        toast.success(`Fiche existante reutilisee : ${data.name}`);
      } else {
        toast.success(`Proprietaire cree : ${data.name}`);
      }
      // Merge dans sessionOwners pour visibility immediate
      setSessionOwners(prev => {
        const byId = new Map();
        for (const o of prev) if (o?.id) byId.set(o.id, o);
        if (data?.id) byId.set(data.id, data);
        return Array.from(byId.values());
      });
      // Auto-link au lot
      if (data?.id) {
        const ls = [...form.lots];
        const idx = q.lotIdx;
        const current = ls[idx].owner_ids || [];
        if (!current.includes(data.id)) {
          ls[idx] = {
            ...ls[idx],
            owner_ids: [...current, data.id],
            // Efface la mention orpheline maintenant que le lot est lie
            _imported_owner_name: '',
            _imported_owner_aux: '',
          };
          setForm({ ...form, lots: ls });
        }
      }
      setQuickCreateOwner(null);
    } catch (err) {
      const msg = err.response?.data?.detail || 'Erreur creation proprietaire';
      toast.error(msg);
      setQuickCreateOwner({ ...q, saving: false });
    }
  };
  const getOwnerSuggestions = (i) => {
    // iter95j : normalise accents/diacritiques pour matcher "Brouwers" quand on tape "brou"
    const _stripAccents = (s) => (s || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
    const q = _stripAccents((ownerSearchByLot[i] || '').trim());
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
      const key = _norm(o.name) || _norm((o.last_name || '') + ' ' + (o.first_name || ''));
      if (!key) continue;
      const cur = bestByName.get(key);
      if (!cur || _score(o) > _score(cur)) bestByName.set(key, o);
    }
    const deduped = Array.from(bestByName.values());
    if (!q) {
      if (ownerFocusLot === i) return deduped.slice(0, 50);
      return [];
    }
    return deduped.filter(o => {
      const fields = [
        o.name, o.last_name, o.first_name,
        o.email, o.auxiliary_code, o.vcs_code,
      ].map(_stripAccents);
      return fields.some(f => f.includes(q));
    }).slice(0, 25);
  };

  // Refetch owners on focus to ensure freshly-imported owners are visible.
  // iter93bo : GDPR - en creation, on n'affiche QUE les proprios de la
  // session en cours (sessionOwners). En edition, comportement historique.
  const refreshOwnersIfStale = async () => {
    try {
      if (editing) {
        const r = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
        setOwners(r.data || []);
      } else {
        setOwners(sessionOwners);
      }
    } catch { /* ignore */ }
  };

  // iter95l : rafraichit le pool des proprietaires quand on entre en substep
  // 'assign' pour que le combobox ait la liste a jour (avait ete cree par
  // refreshOwnersIfStale sur onFocus dans le vieil autocomplete).
  useEffect(() => {
    if (substep === 'assign') { refreshOwnersIfStale(); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [substep, sessionOwners]);

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
              + `[OK] : Lancer le wizard d'import Optipro (fournisseurs, categories, budget, factures, journaux...). Les mutations seront saisies A LA FIN.\n`
              + `[Annuler] : Passer directement a la saisie des mutations (les autres imports pourront etre faits plus tard).`
            : `L'ACP "${newCopro.name}" a ete creee.\n\n`
              + `S'agit-il d'une REPRISE depuis Optipro / Sogis ?\n\n`
              + `[OK] : Lancer le wizard d'import (fournisseurs, categories, budget, factures...).\n`
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
                + ` • Regularisations, categories de depense, documents\n\n`
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

      {/* iter93ag : dialogue de creation rapide d'un proprietaire manquant */}
      <Dialog open={!!quickCreateOwner} onOpenChange={(open) => !open && !quickCreateOwner?.saving && setQuickCreateOwner(null)}>
        <DialogContent className="max-w-md" data-testid="quick-create-owner-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>Creer le proprietaire manquant</DialogTitle>
            <p className="text-xs text-slate-500 mt-1">
              Ce proprietaire sera cree et affecte automatiquement au lot orphelin.
            </p>
          </DialogHeader>
          {quickCreateOwner && (
            <div className="space-y-3 mt-2">
              {quickCreateOwner.aux && (
                <div className="text-xs bg-amber-50 border border-amber-200 rounded px-2 py-1.5 font-mono">
                  Code auxiliaire importe : <b>{quickCreateOwner.aux}</b>
                </div>
              )}
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">Nom *</label>
                  <Input
                    value={quickCreateOwner.last_name}
                    onChange={e => setQuickCreateOwner({ ...quickCreateOwner, last_name: e.target.value })}
                    placeholder="LOUETTE"
                    className="h-8 text-sm mt-0.5"
                    data-testid="quick-owner-last"
                    autoFocus
                  />
                </div>
                <div>
                  <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">Prenom</label>
                  <Input
                    value={quickCreateOwner.first_name}
                    onChange={e => setQuickCreateOwner({ ...quickCreateOwner, first_name: e.target.value })}
                    placeholder="Romain"
                    className="h-8 text-sm mt-0.5"
                    data-testid="quick-owner-first"
                  />
                </div>
              </div>
              <div>
                <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">Nom complet (facultatif)</label>
                <Input
                  value={quickCreateOwner.name}
                  onChange={e => setQuickCreateOwner({ ...quickCreateOwner, name: e.target.value })}
                  placeholder="Ex : LOUETTE & BARBIEAUX Romain"
                  className="h-8 text-sm mt-0.5"
                  data-testid="quick-owner-name"
                />
                <p className="text-[10px] text-slate-500 mt-0.5">
                  Laisser vide pour usiner &laquo; Nom + Prenom &raquo;. Utile pour les cas type &laquo; X &amp; Y &raquo;.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">Email</label>
                  <Input
                    value={quickCreateOwner.email}
                    onChange={e => setQuickCreateOwner({ ...quickCreateOwner, email: e.target.value })}
                    placeholder="proprio@example.be"
                    type="email"
                    className="h-8 text-sm mt-0.5"
                    data-testid="quick-owner-email"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider">IBAN</label>
                  <Input
                    value={quickCreateOwner.iban}
                    onChange={e => setQuickCreateOwner({ ...quickCreateOwner, iban: e.target.value })}
                    placeholder="BE00 0000 0000 0000"
                    className="h-8 text-sm mt-0.5 font-mono"
                    data-testid="quick-owner-iban"
                  />
                </div>
              </div>
              <div className="flex justify-end gap-2 pt-2 border-t border-slate-200">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setQuickCreateOwner(null)}
                  disabled={quickCreateOwner.saving}
                  data-testid="quick-owner-cancel"
                >Annuler</Button>
                <Button
                  size="sm"
                  onClick={submitQuickCreateOwner}
                  disabled={quickCreateOwner.saving}
                  className="bg-[#022D52] hover:bg-[#022D52]/90 text-white"
                  data-testid="quick-owner-save"
                >
                  {quickCreateOwner.saving ? 'Creation...' : 'Creer et affecter'}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

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
                <div className="flex items-center gap-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Comptes bancaires</div>
                  {/* iter95r : mini-guide PCMN vs IBAN */}
                  <Popover>
                    <PopoverTrigger asChild>
                      <button type="button" className="text-[10px] text-slate-500 hover:text-[#022D52] underline decoration-dotted" data-testid="pcmn-vs-iban-guide-trigger">
                        Que faut-il saisir ?
                      </button>
                    </PopoverTrigger>
                    <PopoverContent align="start" className="w-[380px] text-xs" data-testid="pcmn-vs-iban-guide">
                      <div className="space-y-2">
                        <div className="font-semibold text-[#022D52] text-sm">IBAN vs code PCMN : quelle difference ?</div>
                        <div className="grid grid-cols-2 gap-2">
                          <div className="border border-emerald-200 bg-emerald-50 rounded p-2">
                            <div className="text-[10px] font-semibold text-emerald-800 uppercase">IBAN</div>
                            <div className="font-mono text-[11px] text-emerald-900">BE68 5390 0754 7034</div>
                            <div className="text-[10px] text-emerald-700 mt-1">Vrai numero de compte bancaire (chez BNP, KBC...). Utilise pour les virements.</div>
                          </div>
                          <div className="border border-blue-200 bg-blue-50 rounded p-2">
                            <div className="text-[10px] font-semibold text-blue-800 uppercase">PCMN 55xxxx</div>
                            <div className="font-mono text-[11px] text-blue-900">551034</div>
                            <div className="text-[10px] text-blue-700 mt-1">Code comptable belge (Classe 5 Financier). Sert au bilan.</div>
                          </div>
                        </div>
                        <div className="pt-1 border-t border-slate-200 text-[11px] text-slate-700">
                          <strong>Regle 1-1</strong> : chaque IBAN reel de l'ACP correspond a UN compte PCMN 55xxxx. Le champ IBAN ici accepte uniquement le vrai numero bancaire (commence par BE...). Le code PCMN est genere automatiquement en 55xxxx a partir des 4 derniers chiffres.
                        </div>
                        <div className="bg-amber-50 border border-amber-200 rounded p-2 text-[11px] text-amber-900">
                          Si vous voyez apparaitre &quot;IBAN&nbsp;551000&quot; a l'import CODA/PDF : c'est un code PCMN saisi a la place de l'IBAN. Corrigez ici pour debloquer la comptabilisation.
                        </div>
                      </div>
                    </PopoverContent>
                  </Popover>
                </div>
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
                      Importez la <em>Liste des coproprietaires</em> depuis un export PDF (Optipro/Sogis) ou CSV, ou <strong>ajoutez-les manuellement</strong>. Les proprietaires seront crees dans la base de donnees de votre syndic et lies a cette ACP.
                    </p>
                    <div className="flex flex-wrap gap-2 mb-3">
                      <Button variant="outline" size="sm" onClick={() => setPdfOwnersOpen(true)} disabled={importingOwners} className="border-emerald-400 text-emerald-800 hover:bg-emerald-100" data-testid="import-owners-pdf-btn">
                        <FileText size={14} className="mr-1" /> Import PDF (recommande)
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => setBulkOwnersOpen(true)} disabled={importingOwners} className="border-emerald-400 text-emerald-800 hover:bg-emerald-100" data-testid="import-owners-csv-btn">
                        <UserPlus size={14} className="mr-1" /> Import CSV
                      </Button>
                      <Button variant="outline" size="sm" onClick={openManualOwnerDialog} disabled={importingOwners} className="border-emerald-400 text-emerald-800 hover:bg-emerald-100" data-testid="add-owner-manual-btn">
                        <PlusCircle size={14} className="mr-1" /> Ajouter manuellement
                      </Button>
                    </div>
                    {/* iter93dm : overlay de chargement pendant l'import
                        (parsing PDF + creation des proprietaires en base) */}
                    <div className="bg-white rounded-md border border-emerald-200 p-3 relative" data-testid="owners-list-recap">
                      {importingOwners && (
                        <div
                          className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-white/85 backdrop-blur-sm rounded-md"
                          data-testid="owners-import-loading"
                        >
                          <div className="flex items-center gap-3">
                            <Loader2 className="h-6 w-6 text-emerald-600 animate-spin" />
                            <span className="text-sm font-semibold text-emerald-900">Chargement des proprietaires en cours...</span>
                          </div>
                          <div className="text-xs text-emerald-700">
                            Creation en base de donnees, verification des doublons, association a l&apos;ACP.
                          </div>
                        </div>
                      )}
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-semibold text-slate-700">{owners.length} proprietaire(s) charge(s)</span>
                        {owners.length === 0 && !importingOwners && (
                          <span className="text-[11px] text-amber-700 italic">Aucun - importez un PDF/CSV ou ajoutez manuellement</span>
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
                      <Button variant="outline" size="sm" onClick={openManualLotDialog} data-testid="add-lot-btn">
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
                                // iter93bo GDPR : fetch orphelins pour le
                                // matching aux/nom mais NE PAS les afficher
                                // dans le recap tant qu'ils ne sont pas
                                // assignes a un lot de cette session.
                                let nextOwners = owners;
                                try {
                                  const r = await api.get('/owners', { params: editing ? { include_unassigned: true, copropriete_id: 'all' } : { unassigned_only: true } });
                                  nextOwners = r.data || [];
                                  if (editing) {
                                    setOwners(nextOwners);
                                  }
                                  // En creation : on n'affiche PAS les orphelins
                                  // pre-emptivement (GDPR). Ils seront ajoutes
                                  // au fur et a mesure des matches lot-owner.
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
                                <>
                                  <Badge variant="outline" className="bg-amber-50 border-amber-300 text-amber-800 py-0 text-[10px]" data-testid={`lot-${i}-orphan`}>
                                    Orphelin : {lot._imported_owner_aux ? <span className="font-mono">{lot._imported_owner_aux} </span> : null}{lot._imported_owner_name}
                                  </Badge>
                                  {/* iter93ag : creation rapide d'un proprietaire manquant depuis l'etape 5 */}
                                  <button
                                    type="button"
                                    onClick={() => openQuickCreateOwner(i)}
                                    className="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 text-[10px] font-semibold transition"
                                    title="Creer ce proprietaire manquant et l'affecter au lot"
                                    data-testid={`lot-${i}-create-owner`}
                                  >
                                    <Plus size={9} /> Creer
                                  </button>
                                </>
                              ) : (
                                <span className="text-[10px] text-slate-400 italic">Aucun proprietaire</span>
                              )}
                              <LotOwnerCombobox
                                lotIdx={i}
                                options={(() => {
                                  const taken = form.lots[i].owner_ids || [];
                                  return owners.filter(o => !taken.includes(o.id));
                                })()}
                                ownersEmpty={owners.length === 0}
                                onPick={(oid) => addOwnerToLot(i, oid)}
                              />
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

      {/* iter95h : dialog "Ajouter proprietaire manuellement" */}
      <Dialog open={manualOwnerOpen} onOpenChange={(v) => { setManualOwnerOpen(v); if (!v) setManualOwnerHomonyms([]); }}>
        <DialogContent className="max-w-lg" data-testid="manual-owner-dialog">
          <DialogHeader>
            <DialogTitle>Ajouter un proprietaire manuellement</DialogTitle>
          </DialogHeader>
          {manualOwnerHomonyms.length > 0 && (
            <div className="bg-amber-50 border border-amber-300 rounded-md p-3 mb-2" data-testid="manual-owner-homonyms">
              <div className="flex items-start gap-2 mb-2">
                <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
                <div className="text-xs text-amber-900">
                  {manualOwnerHomonyms[0]?._dup_kind === 'strict' ? (
                    <>
                      <div className="font-semibold">Coordonnees deja utilisees (email/telephone)</div>
                      <div>La regle metier interdit deux fiches avec le meme email/telephone. Reutilisez la fiche ci-dessous, ou modifiez l&apos;email/telephone puis reessayez.</div>
                    </>
                  ) : (
                    <>
                      <div className="font-semibold">Un proprietaire au nom similaire existe deja</div>
                      <div>Cliquez sur une fiche pour la reutiliser, ou creez quand meme une nouvelle fiche (les emails/telephones doivent etre differents).</div>
                    </>
                  )}
                </div>
              </div>
              <div className="space-y-1.5 max-h-40 overflow-y-auto">
                {manualOwnerHomonyms.map(c => (
                  <button
                    type="button"
                    key={c.id}
                    onClick={() => reuseExistingOwner(c)}
                    className="w-full text-left bg-white hover:bg-amber-100 border border-amber-200 rounded px-2 py-1.5 text-xs transition-colors"
                    data-testid={`manual-owner-homonym-${c.id}`}
                  >
                    <div className="font-medium text-slate-800">{c.name || `${c.last_name || ''} ${c.first_name || ''}`.trim()}</div>
                    <div className="text-[10px] text-slate-500 flex gap-2 flex-wrap mt-0.5">
                      {c.email && <span>{c.email}</span>}
                      {c.phone && <span>{c.phone}</span>}
                      {(c.address || c.city) && <span>{[c.address, c.postal_code, c.city].filter(Boolean).join(' ')}</span>}
                    </div>
                  </button>
                ))}
              </div>
              <div className="mt-2 pt-2 border-t border-amber-200 flex gap-2">
                {manualOwnerHomonyms[0]?._dup_kind === 'strict' ? (
                  <Button size="sm" variant="outline" onClick={() => setManualOwnerHomonyms([])} className="text-[11px] h-7 border-amber-400 text-amber-800 hover:bg-amber-100" data-testid="manual-owner-edit-input">
                    Modifier mon saisie (email/telephone)
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" onClick={() => submitManualOwner({ force: true })} disabled={savingManualOwner} className="text-[11px] h-7 border-amber-400 text-amber-800 hover:bg-amber-100" data-testid="manual-owner-force-create">
                    Creer quand meme une nouvelle fiche
                  </Button>
                )}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3 pt-2">
            <div className="col-span-1">
              <label className="form-label">Nom *</label>
              <Input value={manualOwner.last_name} onChange={e => setManualOwner({ ...manualOwner, last_name: e.target.value })} data-testid="manual-owner-last-name" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Prenom</label>
              <Input value={manualOwner.first_name} onChange={e => setManualOwner({ ...manualOwner, first_name: e.target.value })} data-testid="manual-owner-first-name" />
            </div>
            <div className="col-span-2">
              <label className="form-label">Email</label>
              <Input type="email" value={manualOwner.email} onChange={e => setManualOwner({ ...manualOwner, email: e.target.value })} data-testid="manual-owner-email" />
            </div>
            <div className="col-span-2">
              <label className="form-label">GSM / Telephone</label>
              <Input value={manualOwner.phone} onChange={e => setManualOwner({ ...manualOwner, phone: e.target.value })} placeholder="+32 4 ..." data-testid="manual-owner-phone" />
            </div>
            <div className="col-span-2">
              <label className="form-label">Adresse</label>
              <Input value={manualOwner.address} onChange={e => setManualOwner({ ...manualOwner, address: e.target.value })} data-testid="manual-owner-address" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Code postal</label>
              <Input value={manualOwner.postal_code} onChange={e => setManualOwner({ ...manualOwner, postal_code: e.target.value })} data-testid="manual-owner-postal-code" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Ville</label>
              <Input value={manualOwner.city} onChange={e => setManualOwner({ ...manualOwner, city: e.target.value })} data-testid="manual-owner-city" />
            </div>
            <div className="col-span-2">
              <label className="form-label">Pays</label>
              <Input value={manualOwner.country} onChange={e => setManualOwner({ ...manualOwner, country: e.target.value })} data-testid="manual-owner-country" />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-3">
            <Button variant="outline" onClick={() => setManualOwnerOpen(false)} disabled={savingManualOwner} data-testid="manual-owner-cancel">Annuler</Button>
            <Button onClick={() => submitManualOwner()} disabled={savingManualOwner || !(manualOwner.last_name || '').trim()} className="bg-emerald-600 hover:bg-emerald-700 text-white" data-testid="manual-owner-submit">
              {savingManualOwner ? <><Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" /> Enregistrement...</> : 'Ajouter le proprietaire'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter95h : dialog "Ajouter lot manuellement" */}
      <Dialog open={manualLotOpen} onOpenChange={setManualLotOpen}>
        <DialogContent className="max-w-lg" data-testid="manual-lot-dialog">
          <DialogHeader>
            <DialogTitle>Ajouter un lot manuellement</DialogTitle>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3 pt-2">
            <div className="col-span-1">
              <label className="form-label">Numero du lot *</label>
              <Input value={manualLot.number} onChange={e => setManualLot({ ...manualLot, number: e.target.value })} placeholder="ex: A1.01" data-testid="manual-lot-number" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Type</label>
              <Select value={manualLot.lot_type} onValueChange={v => setManualLot({ ...manualLot, lot_type: v })}>
                <SelectTrigger data-testid="manual-lot-type"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="apartment">Appartement</SelectItem>
                  <SelectItem value="parking">Parking / Garage</SelectItem>
                  <SelectItem value="cave">Cave</SelectItem>
                  <SelectItem value="commercial">Commercial</SelectItem>
                  <SelectItem value="office">Bureau</SelectItem>
                  <SelectItem value="storage">Rangement</SelectItem>
                  <SelectItem value="other">Autre</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2">
              <label className="form-label">Description</label>
              <Input value={manualLot.description} onChange={e => setManualLot({ ...manualLot, description: e.target.value })} placeholder="ex: 2 chambres, 3e etage" data-testid="manual-lot-description" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Etage</label>
              <Input type="number" value={manualLot.floor} onChange={e => setManualLot({ ...manualLot, floor: e.target.value })} data-testid="manual-lot-floor" />
            </div>
            <div className="col-span-1">
              <label className="form-label">Surface (m2)</label>
              <Input type="number" step="0.01" value={manualLot.area} onChange={e => setManualLot({ ...manualLot, area: e.target.value })} data-testid="manual-lot-area" />
            </div>
            <div className="col-span-2">
              <label className="form-label">Quotite fondatrice (millemes)</label>
              <Input type="number" step="0.01" value={manualLot.quotity} onChange={e => setManualLot({ ...manualLot, quotity: e.target.value })} placeholder="ex: 125.5" data-testid="manual-lot-quotity" />
              <p className="text-[10px] text-slate-500 mt-1">
                Quotite du reglement de copro. Les parts par cle de repartition seront ajustables plus tard (etape Affectation).
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-3">
            <Button variant="outline" onClick={() => setManualLotOpen(false)} data-testid="manual-lot-cancel">Annuler</Button>
            <Button onClick={submitManualLot} disabled={!(manualLot.number || '').trim()} className="bg-[#022D52] hover:bg-[#1D4ED8] text-white" data-testid="manual-lot-submit">
              Ajouter le lot
            </Button>
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
              // iter93bo GDPR : fetch pour matching, mais DISPLAY = seulement
              // les proprios "created" (crees dans cette session).
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              nextOwners = Array.from(byId.values());
              setOwners(created);  // GDPR : n'affiche QUE les crees de cette session
            } else {
              nextOwners = fetched;
              setOwners(nextOwners);
            }
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
              // iter93bo GDPR : fetch pour matching mais display=created only
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of created) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              nextOwners = Array.from(byId.values());
              setOwners(created);
            } else {
              nextOwners = fetched;
              setOwners(nextOwners);
            }
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
          { key: 'nature', label: 'Categorie' },
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
              // iter93bo GDPR : fetch pour matching mais display=sessionOwners
              const byId = new Map();
              for (const o of fetched) if (o?.id) byId.set(o.id, o);
              for (const o of sessionOwners) if (o?.id && !byId.has(o.id)) byId.set(o.id, o);
              availableOwners = Array.from(byId.values());
              setOwners(sessionOwners);
            } else {
              availableOwners = fetched;
              setOwners(availableOwners);
            }
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
                  // Refetch owners (creation : uniquement sessionOwners pour GDPR)
                  try {
                    if (editing) {
                      const rr = await api.get('/owners', { params: { include_unassigned: true, copropriete_id: 'all' } });
                      setOwners(rr.data || []);
                    } else {
                      setOwners(sessionOwners);
                    }
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
