import { NavLink, useLocation } from 'react-router-dom';
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { ChevronDown } from 'lucide-react';

/**
 * TopNav - Barre de navigation horizontale (iter90bk).
 *
 * Remplace la sidebar verticale sur desktop : plus de scroll pour
 * atteindre les items du bas, layout compact en tete de page avec des
 * menus deroulants groupes par section (Gestion, Comptabilite, Finance,
 * Rapports, +Plateforme si superadmin, +Compte).
 *
 * Chaque section retourne un dropdown declenche au clic ou survol du
 * trigger. Le trigger s'affiche en bleu s'il contient la route active.
 *
 * Props :
 *  - sections : [{title, accent, items:[{to, icon, label, end?}]}]
 *  - extraSections : [{title, accent, items}] (superadmin, compte, etc.)
 */
export default function TopNav({ sections, extraSections = [] }) {
  const location = useLocation();
  const allSections = [...sections, ...extraSections];

  const accentClass = (accent, isActive) => {
    if (!isActive) return 'text-slate-700 hover:text-slate-900 hover:bg-slate-100';
    const map = {
      blue: 'text-[#0055FF] bg-blue-50',
      violet: 'text-violet-700 bg-violet-50',
      emerald: 'text-emerald-700 bg-emerald-50',
      amber: 'text-amber-700 bg-amber-50',
      slate: 'text-slate-800 bg-slate-100',
    };
    return map[accent] || map.blue;
  };
  const itemAccentClass = (accent) => {
    const map = {
      blue: 'data-[highlighted]:bg-blue-50 data-[highlighted]:text-[#0055FF]',
      violet: 'data-[highlighted]:bg-violet-50 data-[highlighted]:text-violet-700',
      emerald: 'data-[highlighted]:bg-emerald-50 data-[highlighted]:text-emerald-700',
      amber: 'data-[highlighted]:bg-amber-50 data-[highlighted]:text-amber-700',
      slate: 'data-[highlighted]:bg-slate-100',
    };
    return map[accent] || map.blue;
  };

  return (
    <nav
      className="bg-white border-b border-slate-200 sticky top-12 z-20 px-4 lg:px-6 flex items-center gap-1 overflow-x-auto"
      style={{ height: 40 }}
      data-testid="top-nav"
    >
      {allSections.map(sec => {
        const containsActive = sec.items.some(i =>
          i.end ? location.pathname === i.to : location.pathname.startsWith(i.to)
        );
        return (
          <DropdownMenu key={sec.title}>
            <DropdownMenuTrigger asChild>
              <button
                className={`inline-flex items-center gap-1 h-8 px-3 rounded text-[13px] font-medium transition-colors ${accentClass(sec.accent, containsActive)}`}
                data-testid={`top-nav-trigger-${sec.title.toLowerCase()}`}
              >
                {sec.title}
                <ChevronDown size={14} className="opacity-60" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-[220px]">
              <DropdownMenuLabel className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold py-1">
                {sec.title}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {sec.items.map(item => {
                const Icon = item.icon;
                return (
                  <DropdownMenuItem key={item.to} asChild className={`p-0 ${itemAccentClass(sec.accent)}`}>
                    <NavLink
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) => `flex items-center gap-2 px-3 py-2 w-full text-[13px] ${isActive ? 'font-semibold' : ''}`}
                      data-testid={`top-nav-item-${item.to.replace(/\//g, '-')}`}
                    >
                      {Icon && <Icon size={15} strokeWidth={1.75} />}
                      <span>{item.label}</span>
                    </NavLink>
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuContent>
          </DropdownMenu>
        );
      })}
    </nav>
  );
}
