import { useState, useEffect, useMemo, useRef } from 'react';
import { Search, ChevronDown, X, Plus, Building2, Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { normSupplierName, normSupplierNameCandidates } from '@/lib/supplierName';

/**
 * SupplierSearchSelect - selecteur avec autocomplete des fournisseurs
 * UTILISES dans au moins une facture (dedoublonne par nom).
 *
 * - Propose une option "Utiliser libre" pour saisir un nom sans creer de fiche.
 * - Propose un formulaire INLINE "Creer une fiche" (nom + BCE + TVA + IBAN) :
 *      appelle `onCreateSupplier(data)` (Promise -> supplier).
 *      Le parent gere la verification anti-doublon (409) cote backend.
 *
 * Props:
 *  - suppliers: [{id, name, bce_number, vat_number, iban, ...}]  // fiches existantes
 *  - usedNames: [string]   // noms presents dans les factures (deja dedoublonnes/non normalises)
 *  - value: string         // nom courant du fournisseur (string)
 *  - onChange: (name, supplier?) => void
 *  - onCreateSupplier: (data) => Promise<supplier>   // appel API POST /suppliers
 *  - placeholder?, testId?, disabled?
 */
export default function SupplierSearchSelect({
  suppliers = [],
  usedNames = [],
  value = '',
  onChange,
  onCreateSupplier,
  placeholder = 'Rechercher un fournisseur ou saisir un nouveau nom...',
  testId = 'supplier-search',
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState({
    name: '', bce_number: '', vat_number: '', iban: '',
  });
  const [createError, setCreateError] = useState('');
  const ref = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
        setQuery('');
        setShowCreate(false);
        setCreateError('');
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  // Construit la liste DEDOUBLONNEE des fournisseurs utilises :
  // iter90fa : cle = nom normalise (particules juridiques filtrees + mots
  // tries + ponctuation retiree). Ceci fait matcher "Finlead", "Finlead
  // SRL" et "SRL Finlead" comme UNE seule entree.
  // - On enrichit avec la fiche correspondante (si elle existe) pour montrer BCE/IBAN/Ville
  // - On inclut les noms libres (utilises mais sans fiche)
  const dedupedList = useMemo(() => {
    const map = new Map();
    const byNormName = new Map();
    suppliers.forEach((s) => {
      const k = normSupplierName(s.name);
      if (k && !byNormName.has(k)) byNormName.set(k, s);
    });
    usedNames.forEach((rawName) => {
      const name = (rawName || '').trim();
      if (!name) return;
      const k = normSupplierName(name);
      if (!k) return;
      if (map.has(k)) return; // dedup
      const card = byNormName.get(k) || null;
      map.set(k, {
        id: card?.id || `freetext::${k}`,
        // Si une fiche existe, on affiche SON nom canonique (pas la
        // variante libre). Sinon on garde le nom libre.
        name: card?.name || name,
        bce_number: card?.bce_number || '',
        vat_number: card?.vat_number || '',
        iban: card?.iban || '',
        city: card?.city || '',
        hasCard: !!card,
      });
    });
    // Aussi : inclure les fiches JAMAIS utilisees dans une facture
    // (au cas ou une fiche existe mais aucun usedNames match). Utile
    // pour la premiere selection d'un nouveau fournisseur.
    suppliers.forEach((s) => {
      const k = normSupplierName(s.name);
      if (!k || map.has(k)) return;
      map.set(k, {
        id: s.id,
        name: s.name,
        bce_number: s.bce_number || '',
        vat_number: s.vat_number || '',
        iban: s.iban || '',
        city: s.city || '',
        hasCard: true,
      });
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [suppliers, usedNames]);

  // Recherche actuelle
  const q = query.trim().toLowerCase();
  const filtered = dedupedList
    .filter((s) => {
      if (!q) return true;
      return (
        s.name.toLowerCase().includes(q) ||
        (s.bce_number || '').toLowerCase().includes(q) ||
        (s.vat_number || '').toLowerCase().includes(q) ||
        (s.iban || '').toLowerCase().includes(q)
      );
    })
    .slice(0, 50);

  // Trouve une fiche dans `suppliers` qui matche le nom courant.
  // iter90fa : match par nom normalise (particules juridiques filtrees).
  const matchedCard = suppliers.find(
    (s) => normSupplierName(s.name) === normSupplierName(value)
  );

  const queryIsExisting = q && filtered.some(
    (s) => normSupplierName(s.name) === normSupplierName(q)
  );

  const handleSelect = (s) => {
    onChange?.(s.name, s.hasCard ? s : null);
    setQuery('');
    setOpen(false);
    setShowCreate(false);
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange?.('', null);
    setQuery('');
  };

  const handleUseAsNew = () => {
    const name = query.trim();
    if (!name) return;
    onChange?.(name, null);
    setQuery('');
    setOpen(false);
    setShowCreate(false);
  };

  const handleOpenCreate = () => {
    setCreateForm({
      name: query.trim() || value || '',
      bce_number: '', vat_number: '', iban: '',
    });
    setCreateError('');
    setShowCreate(true);
  };

  const handleSubmitCreate = async () => {
    setCreateError('');
    if (!createForm.name.trim()) {
      setCreateError('Le nom est obligatoire.');
      return;
    }
    // iter90ik : BCE OBLIGATOIRE. Regle utilisateur : "empecher la creation
    // de fournisseurs sans BCE bloquant donc la validation sur BCE". Le
    // BCE (ou TVA) est le seul identifiant unique fiable pour eviter les
    // doublons entre imports Optipro / creation manuelle / lettrage
    // bancaire. Format attendu : BE0123456789 (10 chiffres apres le
    // prefixe pays), extension au VAT europeen (FR, NL, LU, DE...).
    const bce = createForm.bce_number.trim().replace(/[.\s]/g, '');
    const vat = createForm.vat_number.trim().replace(/[.\s]/g, '');
    if (!bce && !vat) {
      setCreateError(
        'Le numero BCE (ou TVA equivalent) est obligatoire. '
        + 'Format attendu : BE0123456789 - identifiant unique de l\'entreprise. '
        + 'Verifiez sur https://kbopub.economie.fgov.be/'
      );
      return;
    }
    // Validation format leger : au moins 8 chars alphanumeriques
    if (bce && !/^[A-Z]{2}[0-9]{8,12}$/i.test(bce)) {
      setCreateError(
        `Format BCE invalide : "${createForm.bce_number}". Format attendu : BE0123456789 (2 lettres pays + 8-12 chiffres).`
      );
      return;
    }
    if (!onCreateSupplier) {
      setCreateError('Action non disponible.');
      return;
    }
    setCreating(true);
    try {
      const created = await onCreateSupplier({
        name: createForm.name.trim(),
        bce_number: bce,
        vat_number: vat,
        iban: createForm.iban.trim().replace(/\s+/g, ''),
      });
      onChange?.(created.name, created);
      setQuery('');
      setOpen(false);
      setShowCreate(false);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Erreur lors de la creation.';
      setCreateError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setCreating(false);
    }
  };

  const handleKeyDown = (e) => {
    if (showCreate) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      const exact = filtered.find((s) => s.name.trim().toLowerCase() === q);
      if (exact) handleSelect(exact);
      else if (query.trim()) handleUseAsNew();
    } else if (e.key === 'Escape') {
      setOpen(false);
      setQuery('');
    }
  };

  return (
    <div className="relative min-w-0 w-full" ref={ref}>
      <div
        onClick={() => {
          if (disabled) return;
          setOpen(true);
          setTimeout(() => inputRef.current?.focus(), 0);
        }}
        className={`flex items-center gap-2 border border-slate-200 rounded-md px-3 py-2 bg-white cursor-text hover:border-slate-300 transition w-full min-w-0 ${
          disabled ? 'opacity-50 pointer-events-none' : ''
        }`}
        data-testid={testId}
      >
        <Search size={14} className="text-slate-400 shrink-0" />
        <span
          className={`flex-1 min-w-0 text-sm truncate ${
            value ? 'text-slate-800' : 'text-slate-400'
          }`}
        >
          {value || placeholder}
        </span>
        {matchedCard && (
          <span
            className="text-[10px] font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded shrink-0"
            title="Fournisseur lie a une fiche"
          >
            <Building2 size={10} className="inline mr-0.5" />
            fiche
          </span>
        )}
        {value && !disabled && (
          <button
            type="button"
            onClick={handleClear}
            className="text-slate-400 hover:text-red-500 shrink-0"
            data-testid={`${testId}-clear`}
            title="Effacer"
          >
            <X size={14} />
          </button>
        )}
        <ChevronDown
          size={14}
          className={`text-slate-400 shrink-0 transition ${open ? 'rotate-180' : ''}`}
        />
      </div>

      {open && !disabled && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-[420px] overflow-hidden flex flex-col">
          {!showCreate && (
            <>
              <div className="p-2 border-b border-slate-100">
                <Input
                  ref={inputRef}
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Tapez un nom, BCE, TVA, IBAN..."
                  className="h-8 text-sm"
                  data-testid={`${testId}-input`}
                />
                <p className="text-[10px] text-slate-400 mt-1 px-1">
                  Affiche uniquement les fournisseurs deja utilises dans une facture
                  ({dedupedList.length} au total).
                </p>
              </div>

              {/* Option "Utiliser libre" + "Creer fiche" si saisie ne matche pas */}
              {q && !queryIsExisting && (
                <div className="border-b border-slate-100 bg-blue-50/30">
                  <button
                    type="button"
                    onClick={handleUseAsNew}
                    className="w-full text-left px-3 py-2 text-xs hover:bg-blue-100 transition"
                    data-testid={`${testId}-use-as-new`}
                  >
                    <div className="flex items-center gap-2">
                      <Plus size={12} className="text-[#022D52] shrink-0" />
                      <span className="text-blue-800">
                        Utiliser <span className="font-semibold">{`"${query.trim()}"`}</span> sans creer de fiche
                      </span>
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={handleOpenCreate}
                    className="w-full text-left px-3 py-2 text-xs hover:bg-emerald-100 transition border-t border-blue-100"
                    data-testid={`${testId}-open-create`}
                  >
                    <div className="flex items-center gap-2">
                      <Building2 size={12} className="text-emerald-700 shrink-0" />
                      <span className="text-emerald-800">
                        Creer une <b>fiche fournisseur</b> pour{' '}
                        <span className="font-semibold">{`"${query.trim()}"`}</span>
                      </span>
                    </div>
                    <div className="text-[10px] text-emerald-700/80 ml-5 mt-0.5">
                      Permet d&apos;ajouter BCE, TVA, IBAN. Anti-doublon BCE applique.
                    </div>
                  </button>
                </div>
              )}

              <div className="overflow-auto flex-1">
                {filtered.length === 0 ? (
                  <div className="px-3 py-4 text-xs text-slate-400 text-center">
                    {q
                      ? 'Aucun fournisseur deja utilise ne correspond.'
                      : 'Aucun fournisseur enregistre dans une facture pour le moment.'}
                  </div>
                ) : (
                  filtered.map((s) => {
                    const isSelected =
                      s.name.trim().toLowerCase() === (value || '').trim().toLowerCase();
                    return (
                      <button
                        type="button"
                        key={s.id}
                        onClick={() => handleSelect(s)}
                        className={`w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-blue-50 ${
                          isSelected ? 'bg-blue-50 font-medium' : ''
                        }`}
                        data-testid={`${testId}-option-${s.id}`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-slate-800 truncate">{s.name}</span>
                          <div className="flex items-center gap-1 shrink-0">
                            {!s.hasCard && (
                              <span
                                className="text-[9px] uppercase tracking-wide text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded"
                                title="Utilise dans une facture mais aucune fiche enregistree"
                              >
                                sans fiche
                              </span>
                            )}
                            {(s.bce_number || s.vat_number) && (
                              <span className="text-slate-400 text-[10px] font-mono">
                                {s.bce_number || s.vat_number}
                              </span>
                            )}
                          </div>
                        </div>
                        {(s.iban || s.city) && (
                          <div className="text-[10px] text-slate-500 truncate">
                            {[s.iban, s.city].filter(Boolean).join(' - ')}
                          </div>
                        )}
                      </button>
                    );
                  })
                )}
                {dedupedList.length > filtered.length && filtered.length === 50 && (
                  <div className="px-3 py-2 text-[10px] text-slate-400 text-center bg-slate-50">
                    50+ resultats - affinez votre recherche
                  </div>
                )}
              </div>
            </>
          )}

          {showCreate && (
            <div className="p-3 space-y-2" data-testid={`${testId}-create-form`}>
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-emerald-800 flex items-center gap-1.5">
                  <Building2 size={14} /> Nouvelle fiche fournisseur
                </h4>
                <button
                  type="button"
                  onClick={() => { setShowCreate(false); setCreateError(''); }}
                  className="text-slate-400 hover:text-slate-600"
                  data-testid={`${testId}-create-cancel`}
                >
                  <X size={16} />
                </button>
              </div>
              <div className="space-y-2">
                <div>
                  <label className="text-[11px] text-slate-600">Nom *</label>
                  <Input
                    value={createForm.name}
                    onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                    className="h-8 text-sm"
                    data-testid={`${testId}-create-name`}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[11px] text-slate-600 flex items-center gap-1">
                      BCE <span className="text-red-600 font-bold">*</span>
                    </label>
                    <Input
                      value={createForm.bce_number}
                      onChange={(e) => setCreateForm({ ...createForm, bce_number: e.target.value })}
                      placeholder="BE0123456789"
                      className={`h-8 text-sm font-mono ${!createForm.bce_number.trim() && !createForm.vat_number.trim() ? 'border-red-300 bg-red-50/30' : ''}`}
                      data-testid={`${testId}-create-bce`}
                      required
                    />
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-600">TVA (si different)</label>
                    <Input
                      value={createForm.vat_number}
                      onChange={(e) => setCreateForm({ ...createForm, vat_number: e.target.value })}
                      placeholder="BE0123456789"
                      className="h-8 text-sm font-mono"
                      data-testid={`${testId}-create-vat`}
                    />
                  </div>
                </div>
                {!createForm.bce_number.trim() && !createForm.vat_number.trim() && (
                  <div className="text-[10px] text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1" data-testid={`${testId}-bce-hint`}>
                    <b>Le BCE (ou TVA equivalent) est obligatoire.</b> Verifiez le numero sur
                    {' '}<a href="https://kbopub.economie.fgov.be/" target="_blank" rel="noreferrer" className="underline text-blue-700">kbopub.economie.fgov.be</a>.
                  </div>
                )}
                <div>
                  <label className="text-[11px] text-slate-600">IBAN</label>
                  <Input
                    value={createForm.iban}
                    onChange={(e) => setCreateForm({ ...createForm, iban: e.target.value })}
                    placeholder="BE68 5390 0754 7034"
                    className="h-8 text-sm font-mono"
                    data-testid={`${testId}-create-iban`}
                  />
                </div>
                {createError && (
                  <div
                    className="text-[11px] text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1"
                    data-testid={`${testId}-create-error`}
                  >
                    {createError}
                  </div>
                )}
                <div className="flex justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => { setShowCreate(false); setCreateError(''); }}
                    disabled={creating}
                  >
                    Annuler
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    className="bg-emerald-600 hover:bg-emerald-700 text-white"
                    onClick={handleSubmitCreate}
                    disabled={creating || !createForm.name.trim() || (!createForm.bce_number.trim() && !createForm.vat_number.trim())}
                    data-testid={`${testId}-create-submit`}
                    title={(!createForm.bce_number.trim() && !createForm.vat_number.trim()) ? "BCE ou TVA obligatoire pour creer une fiche fournisseur" : ""}
                  >
                    {creating ? (
                      <><Loader2 size={12} className="mr-1 animate-spin" /> Creation...</>
                    ) : (
                      'Creer la fiche'
                    )}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
