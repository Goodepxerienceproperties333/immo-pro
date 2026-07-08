import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Filter, X } from 'lucide-react';

// ---- Period presets (re-usable) ----
export function getPreset(name) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const y = today.getFullYear();
  const m = today.getMonth();
  switch (name) {
    case 'today': return { start: iso(today), end: iso(today) };
    case 'month': return { start: iso(new Date(y, m, 1)), end: iso(today) };
    case 'prev_month': return { start: iso(new Date(y, m - 1, 1)), end: iso(new Date(y, m, 0)) };
    case 'quarter': {
      const qstart = Math.floor(m / 3) * 3;
      return { start: iso(new Date(y, qstart, 1)), end: iso(today) };
    }
    case 'year': return { start: `${y}-01-01`, end: iso(today) };
    case 'prev_year': return { start: `${y - 1}-01-01`, end: `${y - 1}-12-31` };
    case 'all': return { start: '', end: '' };
    default: return null;
  }
}

export default function FilterBar({ startDate, endDate, search, onChange, storageKey }) {
  const apply = (next) => {
    onChange(next);
    if (storageKey) localStorage.setItem(storageKey, JSON.stringify(next));
  };
  const reset = () => apply({ startDate: '', endDate: '', search: '' });
  const setPreset = (name) => {
    const p = getPreset(name);
    if (!p) return;
    apply({ startDate: p.start, endDate: p.end, search });
  };
  return (
    <div className="mb-4 p-3 bg-slate-50 border border-slate-200 rounded-md flex flex-wrap items-end gap-3" data-testid="filter-bar">
      <div className="flex items-center gap-1.5">
        <Filter size={14} className="text-slate-500" />
        <span className="text-xs font-semibold text-slate-600 uppercase tracking-wider">Filtres</span>
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Du</label>
        <Input type="date" value={startDate} onChange={e => apply({ startDate: e.target.value, endDate, search })} className="h-8 text-xs w-36" data-testid="filter-start-date" />
      </div>
      <div>
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Au</label>
        <Input type="date" value={endDate} onChange={e => apply({ startDate, endDate: e.target.value, search })} className="h-8 text-xs w-36" data-testid="filter-end-date" />
      </div>
      <div className="flex-1 min-w-[160px]">
        <label className="block text-[10px] uppercase tracking-wider text-slate-500 mb-1">Recherche</label>
        <Input value={search} onChange={e => apply({ startDate, endDate, search: e.target.value })} placeholder="Nom, VCS..." className="h-8 text-xs" data-testid="filter-search" />
      </div>
      <div className="flex flex-wrap gap-1">
        {[
          ['today', "Auj."],
          ['month', 'Ce mois'],
          ['prev_month', 'Mois -1'],
          ['quarter', 'Trim.'],
          ['year', 'Annee'],
          ['prev_year', 'N-1'],
          ['all', 'Tout'],
        ].map(([k, l]) => (
          <Button key={k} size="sm" variant="outline" className="h-8 text-[10px] px-2" onClick={() => setPreset(k)} data-testid={`filter-preset-${k}`}>
            {l}
          </Button>
        ))}
      </div>
      {(startDate || endDate || search) && (
        <Button size="sm" variant="ghost" className="h-8 text-red-600" onClick={reset} data-testid="filter-reset">
          <X size={12} className="mr-1" /> Reset
        </Button>
      )}
    </div>
  );
}
