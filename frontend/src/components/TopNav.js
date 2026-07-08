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
    // iter90bq : gradients doux + rounded-full sur les triggers pour un rendu
    // plus "sexy". Couleurs sectorielles alignees sur design_guidelines.json.
    const baseMap = {
      blue: 'text-blue-700',
      violet: 'text-violet-700',
      emerald: 'text-emerald-700',
      amber: 'text-amber-700',
      slate: 'text-slate-700',
    };
    const activeMap = {
      blue: 'text-white bg-gradient-to-r from-blue-500 to-blue-600 shadow-sm shadow-blue-500/30',
      violet: 'text-white bg-gradient-to-r from-violet-500 to-violet-600 shadow-sm shadow-violet-500/30',
      emerald: 'text-white bg-gradient-to-r from-emerald-500 to-emerald-600 shadow-sm shadow-emerald-500/30',
      amber: 'text-white bg-gradient-to-r from-amber-500 to-amber-600 shadow-sm shadow-amber-500/30',
      slate: 'text-white bg-gradient-to-r from-slate-600 to-slate-700 shadow-sm shadow-slate-500/30',
    };
    if (isActive) return activeMap[accent] || activeMap.blue;
    return `${baseMap[accent] || baseMap.blue} hover:bg-slate-100/70`;
  };
  const dotClass = (accent) => {
    // Dot color-coded avant le titre (visible en permanence)
    const map = {
      blue: 'bg-[#2563EB]',
      violet: 'bg-violet-500',
      emerald: 'bg-emerald-500',
      amber: 'bg-amber-500',
      slate: 'bg-slate-400',
    };
    return map[accent] || map.blue;
  };
  const itemAccentClass = (accent) => {
    const map = {
      blue: 'data-[highlighted]:bg-blue-50 data-[highlighted]:text-[#2563EB]',
      violet: 'data-[highlighted]:bg-violet-50 data-[highlighted]:text-violet-700',
      emerald: 'data-[highlighted]:bg-emerald-50 data-[highlighted]:text-emerald-700',
      amber: 'data-[highlighted]:bg-amber-50 data-[highlighted]:text-amber-700',
      slate: 'data-[highlighted]:bg-slate-100',
    };
    return map[accent] || map.blue;
  };
  const contentAccentClass = (accent) => {
    // Bordure gauche coloree du dropdown menu (rappel visuel de la section)
    const map = {
      blue: 'border-l-4 border-l-[#2563EB]',
      violet: 'border-l-4 border-l-violet-500',
      emerald: 'border-l-4 border-l-emerald-500',
      amber: 'border-l-4 border-l-amber-500',
      slate: 'border-l-4 border-l-slate-400',
    };
    return map[accent] || map.blue;
  };
  const labelAccentClass = (accent) => {
    const map = {
      blue: 'text-[#2563EB]',
      violet: 'text-violet-700',
      emerald: 'text-emerald-700',
      amber: 'text-amber-700',
      slate: 'text-slate-600',
    };
    return map[accent] || map.blue;
  };

  return (
    <nav
      className="bg-white/90 backdrop-blur border-b border-slate-200/60 sticky top-12 z-20 px-4 lg:px-6 flex items-center gap-1 overflow-x-auto shadow-glass"
      style={{ height: 44 }}
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
                className={`inline-flex items-center gap-2 h-9 px-3.5 rounded-full text-[13px] font-semibold transition-all duration-200 ${accentClass(sec.accent, containsActive)}`}
                data-testid={`top-nav-trigger-${sec.title.toLowerCase()}`}
              >
                <span className={`h-2 w-2 rounded-full ${containsActive ? 'bg-white/80' : dotClass(sec.accent)}`} />
                {sec.title}
                <ChevronDown size={13} className={containsActive ? 'text-white/80' : 'opacity-50'} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className={`min-w-[240px] rounded-2xl shadow-dropdown border-slate-200/60 ${contentAccentClass(sec.accent)}`}>
              <DropdownMenuLabel className={`text-[10px] uppercase tracking-widest font-bold py-1.5 ${labelAccentClass(sec.accent)}`}>
                {sec.title}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {sec.items.map(item => {
                const Icon = item.icon;
                return (
                  <DropdownMenuItem key={item.to} asChild className={`p-0 rounded-lg my-0.5 ${itemAccentClass(sec.accent)}`}>
                    <NavLink
                      to={item.to}
                      end={item.end}
                      className={({ isActive }) => `flex items-center gap-2.5 px-3 py-2 w-full text-[13px] ${isActive ? 'font-semibold' : ''}`}
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
