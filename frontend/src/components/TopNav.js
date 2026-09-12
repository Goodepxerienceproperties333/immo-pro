import { NavLink, useLocation } from 'react-router-dom';
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { ChevronDown, ExternalLink } from 'lucide-react';

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
 *  - externalLinks : [{label, href, icon, accent}] - liens externes
 *    autonomes (redirect vers site tiers, sans dropdown).
 */
export default function TopNav({ sections, extraSections = [], externalLinks = [] }) {
  const location = useLocation();
  const allSections = [...sections, ...extraSections];

  const accentClass = (accent, isActive) => {
    // Onglets plus grands + contraste marque + COULEURS DISTINCTES par section
    // (2026-02 : chaque section garde sa couleur propre pour aider a la
    // memorisation et l'orientation visuelle rapide).
    const baseMap = {
      blue: 'text-[#022D52] bg-blue-50 hover:bg-blue-100 border-2 border-blue-200 hover:border-blue-400',
      violet: 'text-violet-800 bg-violet-50 hover:bg-violet-100 border-2 border-violet-200 hover:border-violet-400',
      emerald: 'text-emerald-800 bg-emerald-50 hover:bg-emerald-100 border-2 border-emerald-200 hover:border-emerald-400',
      amber: 'text-amber-800 bg-amber-50 hover:bg-amber-100 border-2 border-amber-200 hover:border-amber-400',
      sky: 'text-sky-800 bg-sky-50 hover:bg-sky-100 border-2 border-sky-200 hover:border-sky-400',
      rose: 'text-rose-800 bg-rose-50 hover:bg-rose-100 border-2 border-rose-200 hover:border-rose-400',
      indigo: 'text-indigo-800 bg-indigo-50 hover:bg-indigo-100 border-2 border-indigo-200 hover:border-indigo-400',
      slate: 'text-slate-800 bg-slate-100 hover:bg-slate-200 border-2 border-slate-300 hover:border-slate-400',
    };
    const activeMap = {
      blue: 'text-white bg-gradient-to-r from-[#022D52] to-[#1D4ED8] shadow-lg shadow-blue-500/50 border-2 border-blue-800',
      violet: 'text-white bg-gradient-to-r from-violet-600 to-violet-700 shadow-lg shadow-violet-500/50 border-2 border-violet-800',
      emerald: 'text-white bg-gradient-to-r from-emerald-600 to-emerald-700 shadow-lg shadow-emerald-500/50 border-2 border-emerald-800',
      amber: 'text-white bg-gradient-to-r from-amber-600 to-amber-700 shadow-lg shadow-amber-500/50 border-2 border-amber-800',
      sky: 'text-white bg-gradient-to-r from-sky-600 to-sky-700 shadow-lg shadow-sky-500/50 border-2 border-sky-800',
      rose: 'text-white bg-gradient-to-r from-rose-600 to-rose-700 shadow-lg shadow-rose-500/50 border-2 border-rose-800',
      indigo: 'text-white bg-gradient-to-r from-indigo-600 to-indigo-700 shadow-lg shadow-indigo-500/50 border-2 border-indigo-800',
      slate: 'text-white bg-gradient-to-r from-slate-700 to-slate-800 shadow-lg shadow-slate-500/50 border-2 border-slate-900',
    };
    if (isActive) return activeMap[accent] || activeMap.blue;
    return baseMap[accent] || baseMap.blue;
  };
  const dotClass = (accent) => {
    const map = {
      blue: 'bg-[#022D52]',
      violet: 'bg-violet-500',
      emerald: 'bg-emerald-500',
      amber: 'bg-amber-500',
      sky: 'bg-sky-500',
      rose: 'bg-rose-500',
      indigo: 'bg-indigo-500',
      slate: 'bg-slate-400',
    };
    return map[accent] || map.blue;
  };
  const itemAccentClass = (accent) => {
    const map = {
      blue: 'data-[highlighted]:bg-blue-50 data-[highlighted]:text-[#022D52]',
      violet: 'data-[highlighted]:bg-violet-50 data-[highlighted]:text-violet-700',
      emerald: 'data-[highlighted]:bg-emerald-50 data-[highlighted]:text-emerald-700',
      amber: 'data-[highlighted]:bg-amber-50 data-[highlighted]:text-amber-700',
      sky: 'data-[highlighted]:bg-sky-50 data-[highlighted]:text-sky-700',
      rose: 'data-[highlighted]:bg-rose-50 data-[highlighted]:text-rose-700',
      indigo: 'data-[highlighted]:bg-indigo-50 data-[highlighted]:text-indigo-700',
      slate: 'data-[highlighted]:bg-slate-100',
    };
    return map[accent] || map.blue;
  };
  const contentAccentClass = (accent) => {
    const map = {
      blue: 'border-l-4 border-l-[#022D52]',
      violet: 'border-l-4 border-l-violet-500',
      emerald: 'border-l-4 border-l-emerald-500',
      amber: 'border-l-4 border-l-amber-500',
      sky: 'border-l-4 border-l-sky-500',
      rose: 'border-l-4 border-l-rose-500',
      indigo: 'border-l-4 border-l-indigo-500',
      slate: 'border-l-4 border-l-slate-400',
    };
    return map[accent] || map.blue;
  };
  const labelAccentClass = (accent) => {
    const map = {
      blue: 'text-[#022D52]',
      violet: 'text-violet-700',
      emerald: 'text-emerald-700',
      amber: 'text-amber-700',
      sky: 'text-sky-700',
      rose: 'text-rose-700',
      indigo: 'text-indigo-700',
      slate: 'text-slate-600',
    };
    return map[accent] || map.blue;
  };

  return (
    <nav
      className="bg-white/95 backdrop-blur border-b-2 border-slate-200 sticky top-12 z-20 px-4 lg:px-6 flex items-center gap-2 overflow-x-auto shadow-md"
      style={{ height: 56 }}
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
                className={`inline-flex items-center gap-2 h-11 px-5 rounded-full text-[15px] font-bold transition-all duration-200 ${accentClass(sec.accent, containsActive)}`}
                data-testid={`top-nav-trigger-${sec.title.toLowerCase()}`}
              >
                <span className={`h-2.5 w-2.5 rounded-full ${containsActive ? 'bg-white' : dotClass(sec.accent)}`} />
                {sec.title}
                <ChevronDown size={15} className={containsActive ? 'text-white/90' : 'opacity-60'} />
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
      {externalLinks.length > 0 && (
        <div className="flex items-center gap-2 ml-1 pl-3 border-l-2 border-slate-200">
          {externalLinks.map(link => {
            const Icon = link.icon;
            return (
              <a
                key={link.href}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className={`inline-flex items-center gap-2 h-11 px-5 rounded-full text-[15px] font-bold transition-all duration-200 ${accentClass(link.accent || 'indigo', false)}`}
                data-testid={`top-nav-external-${(link.label || '').toLowerCase().replace(/\s+/g, '-')}`}
                title={`Ouvrir ${link.label} dans un nouvel onglet`}
              >
                <span className={`h-2.5 w-2.5 rounded-full ${dotClass(link.accent || 'indigo')}`} />
                {Icon && <Icon size={16} strokeWidth={2} />}
                {link.label}
                <ExternalLink size={13} className="opacity-60" />
              </a>
            );
          })}
        </div>
      )}
    </nav>
  );
}
