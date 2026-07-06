import { useState, useEffect, useMemo, useCallback } from 'react';
import { Star, ChevronDown, ChevronUp } from 'lucide-react';

/**
 * Adaptive quick-actions tile grid.
 * - Tracks per-user + per-ACP click frequency in localStorage.
 * - Sorts tiles by score = count * (1 + recency_boost).
 *   recency_boost = max(0, 14 - days_since_last) / 14 -> boost recent usage.
 * - Highlights #1 with a golden "Favori" star + subtle usage counter pill.
 * - Shows top 6 by default with a "Voir tout" expand toggle.
 *
 * @param {Array<{id, label, href, color, icon}>} actions - full catalog
 * @param {string} scopeKey - per-user + per-ACP storage key (e.g. `user123:acp456`)
 * @param {number} topN - default number of tiles shown (default 6)
 */
export default function AdaptiveQuickActions({ actions, scopeKey = 'default', topN = 6 }) {
  const storageKey = `qa-stats:${scopeKey}`;
  const [stats, setStats] = useState({});
  const [expanded, setExpanded] = useState(false);

  // Load stats on mount + when scope changes
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      setStats(raw ? JSON.parse(raw) : {});
    } catch {
      setStats({});
    }
  }, [storageKey]);

  const recordClick = useCallback((id) => {
    setStats((prev) => {
      const now = Date.now();
      const cur = prev[id] || { count: 0, last: 0 };
      const next = { ...prev, [id]: { count: cur.count + 1, last: now } };
      try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch {}
      return next;
    });
  }, [storageKey]);

  const sorted = useMemo(() => {
    const now = Date.now();
    const DAY = 86400000;
    return [...actions]
      .map((a, originalIdx) => {
        const s = stats[a.id] || { count: 0, last: 0 };
        const days = s.last > 0 ? (now - s.last) / DAY : 999;
        const recency = Math.max(0, 14 - days) / 14; // 0..1 boost, decays over 2 weeks
        const score = s.count * (1 + recency);
        return { ...a, count: s.count, score, originalIdx };
      })
      // Sort by score desc, then by original catalog order to keep predictable UX when tied
      .sort((a, b) => (b.score - a.score) || (a.originalIdx - b.originalIdx));
  }, [actions, stats]);

  const visible = expanded ? sorted : sorted.slice(0, topN);
  const topId = sorted[0]?.score > 0 ? sorted[0].id : null;

  return (
    <div className="space-y-3" data-testid="adaptive-quick-actions">
      <div
        className="grid grid-cols-2 sm:grid-cols-3 gap-2.5"
        style={{ transition: 'all 300ms ease' }}
      >
        {visible.map((a) => {
          const isTop = a.id === topId;
          const Icon = a.icon;
          return (
            <a
              key={a.id}
              href={a.href}
              onClick={() => recordClick(a.id)}
              data-testid={`qa-tile-${a.id}`}
              className={`
                relative ${a.color} rounded-lg border px-3 py-3
                text-sm font-medium text-center
                flex flex-col items-center justify-center gap-1.5
                transform-gpu will-change-transform
                transition-all duration-300 ease-out
                hover:scale-[1.03] hover:shadow-md hover:-translate-y-0.5
                ${isTop ? 'ring-2 ring-amber-300 ring-offset-1 shadow-sm' : ''}
              `}
              style={{
                animation: 'qa-fade-in 300ms ease-out both',
              }}
            >
              {isTop && (
                <span
                  className="absolute -top-1.5 -right-1.5 bg-amber-400 text-white rounded-full p-0.5 shadow-sm"
                  title="Action la plus utilisee"
                  data-testid="qa-favori-badge"
                >
                  <Star size={10} fill="currentColor" strokeWidth={0} />
                </span>
              )}
              {a.count > 0 && !isTop && (
                <span
                  className="absolute top-1 right-1.5 text-[9px] font-mono opacity-40 leading-none"
                  title={`Utilise ${a.count} fois`}
                >
                  {a.count > 99 ? '99+' : a.count}
                </span>
              )}
              <Icon size={16} />
              <span className="leading-tight">{a.label}</span>
            </a>
          );
        })}
      </div>

      {sorted.length > topN && (
        <button
          onClick={() => setExpanded((v) => !v)}
          data-testid="qa-toggle-expand"
          className="w-full text-xs text-slate-500 hover:text-slate-800 flex items-center justify-center gap-1 py-1.5 rounded-md hover:bg-slate-50 transition-colors"
        >
          {expanded ? (
            <><ChevronUp size={12} /> Reduire</>
          ) : (
            <><ChevronDown size={12} /> Voir toutes les actions ({sorted.length - topN} de plus)</>
          )}
        </button>
      )}

      <style>{`
        @keyframes qa-fade-in {
          from { opacity: 0; transform: translateY(4px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
