import { useState, useEffect, useRef, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { Search, X } from 'lucide-react';
import { Input } from '@/components/ui/input';

/**
 * CounterpartySearchSelect - selecteur de contrepartie (Owner ou Supplier).
 * Affiche la liste des proprietaires + fournisseurs au focus.
 * Filtre en local par nom (recherche instantanee, sans appel API).
 *
 * iter90o : le dropdown est rendu dans un Portal (document.body) avec
 * position fixed calculee via getBoundingClientRect. Ceci evite tout probleme
 * de clip par des ancetres avec overflow / stacking context (sticky headers,
 * dialogs, tables, etc.).
 *
 * Props:
 *  - owners: [{id, name, vcs_code, email, ...}]
 *  - suppliers: [{id, name, vat_number, ...}]
 *  - value: string (nom selectionne)
 *  - onSelect: ({type:'owner'|'supplier', item, name}) => void
 *  - onChange: (string) => void  (permet la saisie libre)
 *  - placeholder?: string
 *  - testId?: string
 *  - className?: string
 */
export default function CounterpartySearchSelect({
  owners = [],
  suppliers = [],
  value = '',
  onChange,
  onSelect,
  placeholder = 'Nom contrepartie',
  testId = 'counterparty',
  className = '',
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState(value || '');
  const [pos, setPos] = useState({ top: 0, left: 0, width: 0 });
  const anchorRef = useRef(null);
  const dropdownRef = useRef(null);

  useEffect(() => { setQuery(value || ''); }, [value]);

  // Recalculate dropdown position when opened, scrolled or window resized.
  useLayoutEffect(() => {
    if (!open || !anchorRef.current) return;
    const compute = () => {
      const r = anchorRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 320) });
    };
    compute();
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e) => {
      if (anchorRef.current?.contains(e.target)) return;
      if (dropdownRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const q = (query || '').toLowerCase().trim();
  const filteredOwners = owners
    .filter(o => !q
      || (o.name || '').toLowerCase().includes(q)
      || (o.vcs_code || '').toLowerCase().includes(q)
      || (o.email || '').toLowerCase().includes(q))
    .slice(0, 30);
  const filteredSuppliers = suppliers
    .filter(s => !q
      || (s.name || '').toLowerCase().includes(q)
      || (s.vat_number || '').toLowerCase().includes(q))
    .slice(0, 30);

  const pickOwner = (o) => {
    setQuery(o.name);
    onChange?.(o.name);
    onSelect?.({ type: 'owner', item: o, name: o.name });
    setOpen(false);
  };
  const pickSupplier = (s) => {
    setQuery(s.name);
    onChange?.(s.name);
    onSelect?.({ type: 'supplier', item: s, name: s.name });
    setOpen(false);
  };
  const handleChange = (v) => {
    setQuery(v);
    onChange?.(v);
    if (!open) setOpen(true);
  };
  const clear = () => {
    setQuery('');
    onChange?.('');
    setOpen(true);
  };

  const total = filteredOwners.length + filteredSuppliers.length;

  const dropdown = open ? (
    <div
      ref={dropdownRef}
      className="fixed bg-white border border-slate-200 rounded-md shadow-xl max-h-72 overflow-hidden flex flex-col"
      style={{ top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
    >
      {total === 0 ? (
        <div className="px-3 py-4 text-xs text-slate-400 text-center">
          {q ? 'Aucun resultat - utiliser le nom saisi' : 'Aucune contrepartie disponible'}
        </div>
      ) : (
        <div className="overflow-auto flex-1">
          {filteredOwners.length > 0 && (
            <>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-blue-700 font-semibold bg-blue-50 border-b border-blue-100">
                Proprietaires ({filteredOwners.length})
              </div>
              {filteredOwners.map(o => (
                <button
                  type="button"
                  key={`o-${o.id}`}
                  onClick={() => pickOwner(o)}
                  className="w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-blue-50 flex items-center justify-between gap-2"
                  data-testid={`${testId}-owner-${o.id}`}
                >
                  <span className="truncate">{o.name}</span>
                  {o.vcs_code && (
                    <span className="font-mono text-[10px] text-[#0055FF] shrink-0">{o.vcs_code}</span>
                  )}
                </button>
              ))}
            </>
          )}
          {filteredSuppliers.length > 0 && (
            <>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-orange-700 font-semibold bg-orange-50 border-b border-orange-100">
                Fournisseurs ({filteredSuppliers.length})
              </div>
              {filteredSuppliers.map(s => (
                <button
                  type="button"
                  key={`s-${s.id}`}
                  onClick={() => pickSupplier(s)}
                  className="w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-orange-50 flex items-center justify-between gap-2"
                  data-testid={`${testId}-supplier-${s.id}`}
                >
                  <span className="truncate">{s.name}</span>
                  {s.vat_number && (
                    <span className="font-mono text-[10px] text-orange-600 shrink-0">{s.vat_number}</span>
                  )}
                </button>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  ) : null;

  return (
    <div className={`relative ${className}`} ref={anchorRef}>
      <div className="relative">
        <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
        <Input
          value={query}
          onChange={e => handleChange(e.target.value)}
          onFocus={() => setOpen(true)}
          placeholder={placeholder}
          className="h-7 text-xs pl-7 pr-7"
          data-testid={testId}
        />
        {query && (
          <button
            type="button"
            onClick={clear}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-red-500"
            data-testid={`${testId}-clear`}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {dropdown && typeof document !== 'undefined' && createPortal(dropdown, document.body)}
    </div>
  );
}
