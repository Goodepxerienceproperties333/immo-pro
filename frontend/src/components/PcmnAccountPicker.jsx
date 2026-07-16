/**
 * PcmnAccountPicker
 * Combobox custom pour selectionner un compte PCMN dans le wizard d'import.
 *
 * iter90gj : le `<datalist>` HTML natif etait peu fiable (clic sur option ne
 * declenchait pas toujours l'onChange React sur les longues listes de 380+
 * items, et l'UX force le syndic a scroller). Ce combobox custom :
 * - Input texte avec filtre live sur numero ET libelle
 * - Dropdown ouvert au focus, ferme au blur / clic exterieur
 * - Shortlist "Suggestions" en tete (via prop `shortlist`)
 * - Retourne { number, name } au parent via onChange
 */
import { useEffect, useRef, useState } from 'react';

export function PcmnAccountPicker({
  value,               // valeur courante : { number: string, name: string }
  onChange,            // callback ({number, name}) => void
  accounts,            // liste PCMN complete
  shortlist = [],      // suggestions prioritaires (mis en tete)
  placeholder = 'N\u00b0 ou libelle...',
  invalid = false,     // ajoute un highlight rouge si compte manquant
  testId = 'pcmn-picker',
  className = '',
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const wrapperRef = useRef(null);
  const listRef = useRef(null);

  // Sync l'input avec la value fournie par le parent
  useEffect(() => {
    if (!open) {
      setQuery(value?.number ? `${value.number}${value.name ? ' - ' + value.name : ''}` : '');
    }
  }, [value?.number, value?.name, open]);

  // Ferme au clic exterieur
  useEffect(() => {
    const handler = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Filtre : on cherche dans number ET name, case-insensitive
  const q = (query || '').toLowerCase().trim();
  const filtered = (() => {
    if (!q) {
      // Si input vide, montrer d'abord la shortlist puis les 20 premiers comptes
      const seen = new Set();
      const out = [];
      shortlist.forEach(s => {
        const found = accounts.find(a => (a.number || '').toString() === s.number);
        const item = found ? { number: found.number, name: found.name || s.name } : s;
        if (!seen.has(item.number)) { seen.add(item.number); out.push({ ...item, _priority: true }); }
      });
      accounts.slice(0, 30).forEach(a => {
        const num = (a.number || '').toString();
        if (!seen.has(num)) { seen.add(num); out.push({ number: num, name: a.name || '' }); }
      });
      return out;
    }
    return accounts
      .filter(a => {
        const num = (a.number || '').toString().toLowerCase();
        const name = (a.name || '').toLowerCase();
        return num.startsWith(q) || num.includes(q) || name.includes(q);
      })
      .slice(0, 50)
      .map(a => ({ number: (a.number || '').toString(), name: a.name || '' }));
  })();

  const select = (item) => {
    onChange({ number: item.number, name: item.name });
    setQuery(`${item.number} - ${item.name}`);
    setOpen(false);
  };

  const handleChange = (e) => {
    const raw = e.target.value || '';
    setQuery(raw);
    setOpen(true);
    // Si le raw commence par un numero seul (ex: "750"), remonte au parent
    // sans attendre le clic
    const numMatch = raw.match(/^(\d{3,7})/);
    if (numMatch) {
      const num = numMatch[1];
      const found = accounts.find(a => (a.number || '').toString() === num);
      onChange({ number: num, name: found?.name || raw.split(' - ').slice(1).join(' - ').trim() || '' });
    }
  };

  return (
    <div ref={wrapperRef} className={`relative ${className}`} data-testid={testId}>
      <input
        type="text"
        value={query}
        onChange={handleChange}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className={`w-full text-[10px] border ${invalid ? 'border-red-400 bg-red-50' : 'border-slate-200'} rounded px-1 py-0.5 font-mono`}
        data-testid={`${testId}-input`}
      />
      {open && filtered.length > 0 && (
        <div
          ref={listRef}
          className="absolute z-50 mt-1 max-h-[280px] w-72 overflow-y-auto rounded border border-slate-300 bg-white shadow-lg text-[11px]"
          data-testid={`${testId}-list`}
        >
          {filtered.map((item, i) => (
            <div
              key={`${item.number}-${i}`}
              onMouseDown={(ev) => { ev.preventDefault(); select(item); }}
              className={`px-2 py-1 cursor-pointer hover:bg-blue-50 border-b border-slate-100 ${item._priority ? 'bg-amber-50/60' : ''}`}
              data-testid={`${testId}-option-${item.number}`}
            >
              <span className="font-mono font-semibold text-[10px]">{item.number}</span>
              <span className="text-slate-600 ml-2">- {item.name || '\u2014'}</span>
              {item._priority && <span className="ml-1 text-[9px] bg-amber-200 text-amber-900 px-1 rounded">favori</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
