import { useState, useEffect, useRef } from 'react';
import { Search, ChevronDown, X } from 'lucide-react';
import { Input } from '@/components/ui/input';

/**
 * AccountSearchSelect - selecteur de compte PCMN avec recherche par numero OU nom.
 *
 * Props:
 *  - accounts: [{number, name, class_num, ...}]
 *  - value: string (account number)
 *  - onChange: (number) => void
 *  - placeholder?: string
 *  - classFilter?: number | number[]   - filtre optionnel par classe(s) PCMN
 *  - allowClear?: boolean              - bouton X pour effacer
 *  - testId?: string
 *  - disabled?: boolean
 */
export default function AccountSearchSelect({
  accounts = [],
  value = '',
  onChange,
  placeholder = 'Rechercher un compte (numero ou nom)...',
  classFilter,
  allowClear = false,
  testId = 'account-search',
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef(null);

  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const classes = Array.isArray(classFilter) ? classFilter : (classFilter ? [classFilter] : null);

  // Filtered accounts
  const filtered = accounts
    .filter(a => !classes || classes.includes(a.class_num))
    .filter(a => {
      const q = query.toLowerCase().trim();
      if (!q) return true;
      return (a.number || '').toLowerCase().includes(q) ||
             (a.name || '').toLowerCase().includes(q);
    })
    .slice(0, 50);

  const selected = accounts.find(a => a.number === value);
  const displayValue = selected ? `${selected.number} - ${selected.name}` : '';

  const handleSelect = (acc) => {
    onChange?.(acc.number);
    setQuery('');
    setOpen(false);
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange?.('');
    setQuery('');
  };

  return (
    <div className="relative min-w-0 w-full" ref={ref}>
      <div
        onClick={() => !disabled && setOpen(o => !o)}
        className={`flex items-center gap-2 border border-slate-200 rounded-md px-3 py-2 bg-white cursor-pointer hover:border-slate-300 transition w-full min-w-0 ${disabled ? 'opacity-50 pointer-events-none' : ''}`}
        data-testid={testId}
      >
        <Search size={14} className="text-slate-400 shrink-0" />
        <span className={`flex-1 min-w-0 text-sm truncate ${selected ? 'font-mono' : 'text-slate-400'}`}>
          {selected ? displayValue : placeholder}
        </span>
        {allowClear && selected && (
          <button onClick={handleClear} className="text-slate-400 hover:text-red-500 shrink-0" data-testid={`${testId}-clear`}>
            <X size={14} />
          </button>
        )}
        <ChevronDown size={14} className={`text-slate-400 shrink-0 transition ${open ? 'rotate-180' : ''}`} />
      </div>

      {open && !disabled && (
        <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-80 overflow-hidden flex flex-col">
          <div className="p-2 border-b border-slate-100">
            <Input
              autoFocus
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Rechercher par numero ou nom..."
              className="h-8 text-sm"
              data-testid={`${testId}-input`}
            />
          </div>
          <div className="overflow-auto flex-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-4 text-xs text-slate-400 text-center">Aucun compte trouve</div>
            ) : filtered.map(a => {
              const isSelected = a.number === value;
              return (
                <button
                  type="button"
                  key={a.number}
                  onClick={() => handleSelect(a)}
                  className={`w-full text-left px-3 py-1.5 text-xs border-b last:border-b-0 border-slate-50 hover:bg-blue-50 ${isSelected ? 'bg-blue-50 font-medium' : ''}`}
                  data-testid={`${testId}-option-${a.number}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-slate-700">{a.number}</span>
                    <span className="text-slate-400 text-[10px]">Cl.{a.class_num}</span>
                  </div>
                  <div className="text-slate-600 truncate">{a.name}</div>
                </button>
              );
            })}
            {accounts.length > filtered.length && filtered.length === 50 && (
              <div className="px-3 py-2 text-[10px] text-slate-400 text-center bg-slate-50">
                50+ resultats - affinez votre recherche
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
