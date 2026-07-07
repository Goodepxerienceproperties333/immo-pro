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
    // iter90bl : chaque section a une couleur distinctive PERSISTANTE (texte
    // + petit dot color-coded), meme quand pas active. L'active ajoute un
    // background plus prononce et une bordure.
    const baseMap = {
      blue: 'text-[#0055FF]',
      violet: 'text-violet-700',
      emerald: 'text-emerald-700',
      amber: 'text-amber-700',
      slate: 'text-slate-700',
    };
    const activeMap = {
      blue: 'text-[#0055FF] bg-blue-50 shadow-inner ring-1 ring-inset ring-[#0055FF]/20',
      violet: 'text-violet-800 bg-violet-50 shadow-inner ring-1 ring-inset ring-violet-500/20',
      emerald: 'text-emerald-800 bg-emerald-50 shadow-inner ring-1 ring-inset ring-emerald-500/20',
      amber: 'text-amber-800 bg-amber-50 shadow-inner ring-1 ring-inset ring-amber-500/20',
      slate: 'text-slate-900 bg-slate-100 shadow-inner ring-1 ring-inset ring-slate-400/20',
    };
    if (isActive) return activeMap[accent] || activeMap.blue;
    return `${baseMap[accent] || baseMap.blue} hover:bg-slate-50`;
  };
  const dotClass = (accent) => {
    // Dot color-coded avant le titre (visible en permanence)
    const map = {
      blue: 'bg-[#0055FF]',
      violet: 'bg-violet-500',
      emerald: 'bg-emerald-500',
      amber: 'bg-amber-500',
      slate: 'bg-slate-400',
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
  const contentAccentClass = (accent) => {
    // Bordure gauche coloree du dropdown menu (rappel visuel de la section)
    const map = {
      blue: 'border-l-4 border-l-[#0055FF]',
      violet: 'border-l-4 border-l-violet-500',
      emerald: 'border-l-4 border-l-emerald-500',
      amber: 'border-l-4 border-l-amber-500',
      slate: 'border-l-4 border-l-slate-400',
    };
    return map[accent] || map.blue;
  };
  const labelAccentClass = (accent) => {
    const map = {
      blue: 'text-[#0055FF]',
      violet: 'text-violet-700',
      emerald: 'text-emerald-700',
      amber: 'text-amber-700',
      slate: 'text-slate-600',
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
                className={`inline-flex items-center gap-2 h-8 px-3 rounded text-[13px] font-medium transition-colors ${accentClass(sec.accent, containsActive)}`}
                data-testid={`top-nav-trigger-${sec.title.toLowerCase()}`}
              >
                <span className={`h-2 w-2 rounded-full ${dotClass(sec.accent)}`} />
                {sec.title}
                <ChevronDown size={13} className="opacity-60" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className={`min-w-[220px] ${contentAccentClass(sec.accent)}`}>
              <DropdownMenuLabel className={`text-[10px] uppercase tracking-wider font-semibold py-1 ${labelAccentClass(sec.accent)}`}>
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
