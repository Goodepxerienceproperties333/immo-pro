import { useState, useEffect, useRef, useMemo } from 'react';
import { Search, ChevronDown, X } from 'lucide-react';
import { Input } from '@/components/ui/input';

/**
 * NaturePickerSelect - selecteur unifie pour la colonne "Nature" du journal OD.
 * Groupes proposes :
 *   - Charges & produits (expense_categories)
 *   - Proprietaires (comptes PCMN 400x)
 *   - Fournisseurs (comptes PCMN 440x)
 * Recherche par nom OU par numero de compte.
 *
 * Props :
 *  - categories: [{id, name, account_number, account_name}]
 *  - accounts: [{number, name, class_num}]
 *  - value: string (composite : "cat:{id}" | "acct:{number}" | "")
 *  - onChange: (value: string) => void
 *  - testId?: string
 */
export default function NaturePickerSelect({
  categories = [],
  accounts = [],
  value = '',
  onChange,
  testId = 'nature-picker',
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const items = useMemo(() => {
    const list = [];
    for (const c of (categories || [])) {
      list.push({
        key: `cat:${c.id}`,
        group: 'charges',
        label: c.name || '(sans nom)',
        subtext: c.account_number ? `[${c.account_number}]` : '',
      });
    }
    // iter93k : Proprietaires - dedupliqué par nom.
    // Les comptes 41000xxx (fonds reserve) et 41010xxx (fonds roulement) ont
    // le format "Acompte de fonds de X appele - {owner_name}". On extrait le
    // nom du proprietaire, on regroupe et on choisit un compte defaut par
    // owner : priorite au fonds de roulement (4101), sinon reserve (4100).
    const ownerBuckets = new Map();  // ownerName -> { rollAcct, reserveAcct, otherAccts }
    for (const a of (accounts || [])) {
      if (!a?.number) continue;
      const m = /^41(00|01)\d{2,}/.exec(a.number);
      if (!m) continue;
      const isMaster = a.number === '400000' || a.number === '400100' || a.number === '410' || a.number === '4100' || a.number === '4101';
      if (isMaster) continue;
      // Extraction du owner name : "Acompte de fonds de X appele - {name}"
      let ownerName = (a.name || '').trim();
      const nameMatch = /appel(?:e|é)\s*[-–]\s*(.+)$/i.exec(ownerName);
      if (nameMatch) ownerName = nameMatch[1].trim();
      if (!ownerName) continue;
      if (!ownerBuckets.has(ownerName)) ownerBuckets.set(ownerName, {});
      const b = ownerBuckets.get(ownerName);
      if (m[1] === '01') b.roll = a.number;   // 4101 fonds de roulement
      else if (m[1] === '00') b.reserve = a.number;  // 4100 fonds de reserve
    }
    for (const [ownerName, b] of ownerBuckets) {
      // Priorite : fonds de roulement -> reserve
      const defaultAcct = b.roll || b.reserve;
      if (!defaultAcct) continue;
      list.push({
        key: `acct:${defaultAcct}`,
        group: 'owners',
        label: ownerName,
        subtext: b.roll && b.reserve ? '(roulement + reserve)' : (b.roll ? '(roulement)' : '(reserve)'),
      });
    }
    // iter93k : Fournisseurs (440xxxxx, exclu 440000 Master)
    for (const a of (accounts || [])) {
      if (!a?.number) continue;
      if (!a.number.startsWith('440')) continue;
      if (a.number === '440' || a.number === '4400' || a.number === '440000') continue;
      const supplierName = (a.name || '').trim();
      if (!supplierName || supplierName.toLowerCase().startsWith('fournisseurs - master')) continue;
      list.push({
        key: `acct:${a.number}`,
        group: 'suppliers',
        label: supplierName,
        subtext: `[${a.number}]`,
      });
    }
    return list;
  }, [categories, accounts]);

  const selected = items.find(i => i.key === value);
  const displayValue = selected ? `${selected.label} ${selected.subtext}` : '';

  const filtered = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return items;
    return items.filter(i =>
      (i.label || '').toLowerCase().includes(q) ||
      (i.subtext || '').toLowerCase().includes(q)
    );
  }, [items, query]);

  const groupOrder = ['charges', 'owners', 'suppliers'];
  const groupLabels = {
    charges: 'Charges & produits',
    owners: 'Proprietaires',
    suppliers: 'Fournisseurs',
  };
  const groupColors = {
    charges: 'text-slate-500',
    owners: 'text-emerald-600',
    suppliers: 'text-blue-700',
  };
  const grouped = groupOrder.reduce((acc, g) => {
    const arr = filtered.filter(i => i.group === g);
    if (arr.length) acc.push([g, arr]);
    return acc;
  }, []);

  const handleSelect = (item) => {
    onChange?.(item.key);
    setQuery('');
    setOpen(false);
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange?.('none');
    setQuery('');
  };

  return (
    <div className="relative min-w-0 w-full" ref={ref}>
      <div
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 border border-slate-200 rounded-md px-2 py-1.5 bg-white cursor-pointer hover:border-slate-300 transition h-8"
        data-testid={testId}
      >
        <Search size={12} className="text-slate-400 shrink-0" />
        <span className={`flex-1 min-w-0 text-xs truncate ${selected ? '' : 'text-slate-400'}`}>
          {selected ? displayValue : '(aucune)'}
        </span>
        {selected && (
          <button onClick={handleClear} className="text-slate-400 hover:text-red-500 shrink-0" data-testid={`${testId}-clear`}>
            <X size={12} />
          </button>
        )}
        <ChevronDown size={12} className={`text-slate-400 shrink-0 transition ${open ? 'rotate-180' : ''}`} />
      </div>

      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-[420px] w-[min(420px,90vw)] overflow-hidden flex flex-col">
          <div className="p-2 border-b border-slate-100 sticky top-0 bg-white">
            <Input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Rechercher par nom, numero..."
              className="h-8 text-sm"
              data-testid={`${testId}-input`}
            />
          </div>
          <div className="overflow-auto flex-1">
            {grouped.length === 0 ? (
              <div className="px-3 py-4 text-xs text-slate-400 text-center">Aucun resultat</div>
            ) : grouped.map(([g, arr]) => (
              <div key={g}>
                <div className={`px-3 py-1 text-[10px] font-bold uppercase tracking-wide bg-slate-50 border-b border-slate-100 ${groupColors[g]}`}>
                  {groupLabels[g]} <span className="text-slate-400 font-normal">({arr.length})</span>
                </div>
                {arr.slice(0, 60).map((it, i) => {
                  const isSelected = it.key === value;
                  return (
                    <button
                      type="button"
                      key={`${it.key}-${i}`}
                      onClick={() => handleSelect(it)}
                      className={`w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-blue-50 ${isSelected ? 'bg-blue-50 font-medium' : ''}`}
                      data-testid={`${testId}-option-${it.key}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate">{it.label}</span>
                        <span className="text-slate-400 font-mono text-[10px] shrink-0">{it.subtext}</span>
                      </div>
                    </button>
                  );
                })}
                {arr.length > 60 && (
                  <div className="px-3 py-1 text-[10px] text-slate-400 text-center bg-slate-50">
                    60+ resultats dans ce groupe - affinez la recherche
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
