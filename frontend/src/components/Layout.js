import { useState, useEffect, useMemo } from 'react';
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import {
  LayoutDashboard, Users, Building2, UserCheck, BookOpen, FileText,
  Receipt, Gauge, Landmark, FolderOpen, LogOut, ChevronLeft, ChevronRight,
  Menu, Shield, Home, Truck, Calendar, BookMarked, Megaphone, BarChart3, Bell, Wallet, Tag, UserCog, Pencil,
  ShieldAlert, Unlock, ScrollText, IdCard, Activity, FileCheck, FileArchive, Mail, HardDrive, Key, Send, Ticket
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import TopNav from '@/components/TopNav';
import OnboardingDialog from '@/components/OnboardingDialog';
import SupportChatBubble from '@/components/SupportChatBubble';

const sections = [
  { title: 'Gestion', accent: 'blue', items: [
    { to: '/', icon: LayoutDashboard, label: 'Tableau de bord', end: true },
    { to: '/owners', icon: Users, label: 'Proprietaires' },
    { to: '/lots', icon: Building2, label: 'Lots' },
    { to: '/tenants', icon: UserCheck, label: 'Locataires' },
    { to: '/suppliers', icon: Truck, label: 'Fournisseurs' },
  ]},
  { title: 'Comptabilite', accent: 'violet', items: [
    { to: '/accounting', icon: BookOpen, label: 'Plan Comptable' },
    { to: '/fiscal', icon: Calendar, label: 'Exercices' },
    { to: '/journals', icon: FileText, label: 'Journaux' },
    { to: '/grand-livre', icon: BookMarked, label: 'Grand Livre' },
    { to: '/distribution-keys', icon: Key, label: 'Cles de repartition' },
    { to: '/expense-categories', icon: Tag, label: 'Categories de depense' },
  ]},
  { title: 'Finance', accent: 'emerald', items: [
    { to: '/invoices', icon: Receipt, label: 'Facturation' },
    { to: '/expenses', icon: Wallet, label: 'Depenses' },
    { to: '/fund-calls', icon: Megaphone, label: 'Appels de fonds' },
    { to: '/banking', icon: Landmark, label: 'Banque' },
    { to: '/meters', icon: Gauge, label: 'Compteurs' },
  ]},
  { title: 'Rapports', accent: 'amber', items: [
    { to: '/reports', icon: BarChart3, label: 'Bilan & Resultats' },
    { to: '/balance-tiers', icon: Users, label: 'Balance de Tiers' },
    { to: '/documents', icon: FolderOpen, label: 'Documents' },
  ]},
  // iter93dg : onglet dedie "Communication" regroupant tous les outils
  // d'echange avec les proprietaires (rappels, envois, historique, modeles).
  { title: 'Communication', accent: 'sky', items: [
    { to: '/reminders', icon: Bell, label: 'Rappels paiement' },
    { to: '/communication', icon: Mail, label: 'Envoi email' },
    { to: '/communication/history', icon: Send, label: 'Historique envois' },
    { to: '/email-templates', icon: FileText, label: 'Modeles emails' },
  ]},
  // iter90g4 : onglet "Support" pour permettre au syndic de suivre ses tickets bug.
  { title: 'Support', accent: 'blue', items: [
    { to: '/support/tickets', icon: Ticket, label: 'Mes tickets' },
  ]},
];

export default function Layout() {
  const { user, logout, selectedCopro, setSelectedCopro, fiscalYears, selectedFiscalYearId, setSelectedFiscalYearId, isAdmin, isManager, isSuperadmin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [coproprietes, setCoproprietes] = useState([]);

  useEffect(() => { api.get('/coproprietes').then(r => setCoproprietes(r.data)).catch(() => {}); }, []);

  const handleLogout = async () => { await logout(); navigate('/login'); };
  const getRoleLabel = (role) => ({ superadmin: 'Super Admin', admin: 'Admin', syndic: 'Syndic', gestionnaire: 'Gestionnaire' }[role] || 'Proprietaire');
  const selectedCoproData = coproprietes.find(c => c.id === selectedCopro);
  const hasCopro = !!selectedCopro;

  // iter90cv : memoize expensive filter/sort dans les Select pour eviter
  // le recalcul a chaque render de Layout (impacte toutes les pages).
  const activeCoproprietes = useMemo(
    () => coproprietes.filter(c => c.status !== 'archived'),
    [coproprietes],
  );
  const sortedFiscalYears = useMemo(
    () => [...(fiscalYears || [])].sort(
      (a, b) => (b.start_date || '').localeCompare(a.start_date || ''),
    ),
    [fiscalYears],
  );

  // MODE ADMIN PLATEFORME : superadmin sur une route /admin/* voit une interface
  // dediee a l'administration de la plateforme (pas de selecteur ACP, pas de
  // menu de gestion des coproprietes).
  const isAdminRoute = location.pathname.startsWith('/admin');
  const adminPlatformMode = isSuperadmin && isAdminRoute;

  // Sidebar admin pure (mode plateforme)
  const AdminSidebarContent = () => (
    <div className="flex flex-col h-full">
      <div className="p-4 flex items-center gap-3">
        <div className="w-8 h-8 rounded bg-gradient-to-br from-amber-500 to-red-600 flex items-center justify-center text-white flex-shrink-0">
          <ShieldAlert size={16} />
        </div>
        {!collapsed && <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>Plateforme</span>}
      </div>
      <Separator className="bg-slate-800" />
      <ScrollArea className="flex-1">
        <div className="py-3 px-2">
          {!collapsed && <div className="px-3 py-1 mt-1 text-[10px] uppercase tracking-[0.2em] text-amber-400 font-semibold">Administration</div>}
          <NavLink to="/admin" end onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-dashboard"
          ><LayoutDashboard size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Tableau de bord</span>}</NavLink>
          <NavLink to="/admin/users" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-users"
          ><Users size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Utilisateurs</span>}</NavLink>
          <NavLink to="/admin/role-templates" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-templates"
          ><IdCard size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Profils utilisateurs</span>}</NavLink>
          <NavLink to="/admin/unlock" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-unlock"
          ><Unlock size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Outils deblocage</span>}</NavLink>
          <NavLink to="/admin/audit" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-audit"
          ><ScrollText size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Journal d&apos;audit</span>}</NavLink>
          <NavLink to="/admin/login-history" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-login-history"
          ><Activity size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Historique connexions</span>}</NavLink>
          <NavLink to="/admin/legal" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-legal"
          ><FileCheck size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Documents legaux</span>}</NavLink>
          <NavLink to="/admin/rgpd-register" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-rgpd-register"
          ><FileArchive size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Registre RGPD</span>}</NavLink>
          <NavLink to="/admin/syndic-config" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-syndic-config"
          ><Building2 size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Config comptes plateforme</span>}</NavLink>
          <NavLink to="/admin/backups" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-backups"
          ><HardDrive size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Sauvegardes ACP</span>}</NavLink>
          <NavLink to="/admin/billing" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-billing"
          ><Wallet size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Facturation</span>}</NavLink>
          <NavLink to="/admin/e2e-test" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-e2e-test"
          ><Activity size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Tests E2E</span>}</NavLink>
          <NavLink to="/admin/tickets" onClick={() => setMobileOpen(false)}
            className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
            data-testid="adm-nav-tickets"
          ><Ticket size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Support &amp; Tickets</span>}</NavLink>
        </div>
      </ScrollArea>
      {/* Bottom: switch to syndic mode */}
      <div className="p-2 border-t border-slate-800">
        <button
          onClick={() => navigate('/')}
          className="w-full sidebar-link justify-center text-blue-300 hover:text-blue-200 hover:bg-blue-900/30"
          data-testid="switch-to-syndic-mode"
          title="Basculer vers l'interface de gestion syndic (intervention sur une ACP)"
        >
          <Building2 size={16} strokeWidth={1.5} />
          {!collapsed && <span className="text-[12px]">Mode syndic</span>}
        </button>
      </div>
      <div className="p-2 border-t border-slate-800">
        <button onClick={handleLogout} className="w-full sidebar-link justify-center text-red-400 hover:text-red-300" data-testid="logout-btn">
          <LogOut size={16} strokeWidth={1.5} />
          {!collapsed && <span className="text-[12px]">Deconnexion</span>}
        </button>
      </div>
    </div>
  );

  const SidebarContent = () => (
    <div className="flex flex-col h-full">
      <div className="p-4 flex items-center gap-3">
        <img src="/logo-nextge.png" alt="NextGe Copro" className="h-8 w-auto flex-shrink-0 bg-white rounded p-0.5" />
        {!collapsed && <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>NextGe Copro</span>}
      </div>
      <Separator className="bg-slate-800" />
      {/* ACP name */}
      {!collapsed && selectedCoproData && (
        <div className="px-4 py-2 bg-[#022D52]/10 border-b border-slate-800 flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Copropriete</div>
            <div className="text-sm text-white font-semibold truncate">{selectedCoproData.name}</div>
            {selectedCoproData.reference && <div className="text-[10px] text-slate-400 font-mono">{selectedCoproData.reference}</div>}
          </div>
          <button
            onClick={() => navigate(`/coproprietes?edit=${selectedCoproData.id}`)}
            className="flex-shrink-0 p-1.5 rounded-md text-slate-300 hover:text-white hover:bg-white/10 transition-colors"
            title="Editer cette copropriete (nom, adresse, banques, parametres)"
            data-testid="sidebar-edit-acp"
          >
            <Pencil size={13} />
          </button>
        </div>
      )}
      <ScrollArea className="flex-1 px-2 py-2">
        <nav>
          {sections.map((section, si) => (
            <div key={si} className="mb-3" data-accent={section.accent}>
              {!collapsed && (
                <div className="px-3 pt-2 pb-1 flex items-center gap-2">
                  <span className={`h-1.5 w-1.5 rounded-full sidebar-accent-dot-${section.accent}`} />
                  <span className="text-[10px] uppercase tracking-[0.2em] text-slate-400 font-semibold">{section.title}</span>
                </div>
              )}
              <div className="space-y-0.5">
                {section.items.map(item => (
                  <NavLink key={item.to} to={item.to} end={item.end} onClick={() => setMobileOpen(false)}
                    className={({ isActive }) => `sidebar-link sidebar-link-${section.accent} ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                    data-testid={`nav-${item.to.replace(/\//g, '') || 'dashboard'}`}
                  >
                    <span className="sidebar-icon-wrap">
                      <item.icon size={18} strokeWidth={2} />
                    </span>
                    {!collapsed && <span className="text-[13.5px] font-medium">{item.label}</span>}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
          {isSuperadmin && (
            <div className="mb-3" data-accent="amber">
              {!collapsed && (
                <div className="px-3 pt-2 pb-1 flex items-center gap-2">
                  <span className="h-1.5 w-1.5 rounded-full sidebar-accent-dot-amber" />
                  <span className="text-[10px] uppercase tracking-[0.2em] text-amber-400 font-semibold">Plateforme</span>
                </div>
              )}
              <NavLink to="/admin" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-amber ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-admin-dashboard"
              ><span className="sidebar-icon-wrap"><Shield size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Tableau admin</span>}</NavLink>
              <NavLink to="/admin/users" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-amber ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-admin-users"
              ><span className="sidebar-icon-wrap"><Shield size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Utilisateurs</span>}</NavLink>
              <NavLink to="/admin/unlock" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-amber ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-admin-unlock"
              ><span className="sidebar-icon-wrap"><Shield size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Outils deblocage</span>}</NavLink>
              <NavLink to="/admin/audit" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-amber ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-admin-audit"
              ><span className="sidebar-icon-wrap"><Shield size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Journal d'audit</span>}</NavLink>
            </div>
          )}
          {/* "Mon profil" accessible a TOUS les utilisateurs authentifies */}
          <div className="mb-2" data-accent="slate">
            {!collapsed && (
              <div className="px-3 pt-2 pb-1 flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full sidebar-accent-dot-slate" />
                <span className="text-[10px] uppercase tracking-[0.2em] text-slate-400 font-semibold">Compte</span>
              </div>
            )}
            {(user?.role === 'syndic' || user?.role === 'admin' || user?.role === 'superadmin') && (
              <NavLink to="/mon-bureau" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-slate ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-mon-bureau"
              ><span className="sidebar-icon-wrap"><Building2 size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Mon bureau</span>}</NavLink>
            )}
            {(user?.role === 'syndic' || user?.role === 'admin' || user?.role === 'superadmin') && (
              <NavLink to="/team" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link sidebar-link-slate ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-team"
              ><span className="sidebar-icon-wrap"><Users size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Mon equipe</span>}</NavLink>
            )}
            <NavLink to="/profile" onClick={() => setMobileOpen(false)}
              className={({ isActive }) => `sidebar-link sidebar-link-slate ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
              data-testid="nav-profile"
            ><span className="sidebar-icon-wrap"><UserCog size={18} strokeWidth={2} /></span>{!collapsed && <span className="text-[13.5px] font-medium">Mon profil</span>}</NavLink>
          </div>
        </nav>
      </ScrollArea>
      <Separator className="bg-slate-800" />
      <div className="p-3">
        {!collapsed && <div className="mb-2"><div className="text-xs text-slate-400 truncate">{user?.name}</div><div className="text-[10px] text-slate-500">{getRoleLabel(user?.role)}</div></div>}
        <button onClick={handleLogout} data-testid="logout-btn" className="sidebar-link w-full text-slate-400 hover:text-red-400">
          <LogOut size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Deconnexion</span>}
        </button>
        {!collapsed && (
          <div className="mt-3 px-1 py-2 rounded-md bg-slate-900 border border-slate-800 flex items-center gap-2">
            <svg width="20" height="14" viewBox="0 0 20 14" className="flex-shrink-0"><rect width="6.67" height="14" fill="#000" /><rect x="6.67" width="6.67" height="14" fill="#FFD700" /><rect x="13.33" width="6.67" height="14" fill="#FF0000" /></svg>
            <svg width="20" height="14" viewBox="0 0 20 14" className="flex-shrink-0"><rect width="6.67" height="14" fill="#002395" /><rect x="6.67" width="6.67" height="14" fill="#FFF" /><rect x="13.33" width="6.67" height="14" fill="#ED2939" /></svg>
            <div className="text-[9px] text-slate-500 leading-tight">Donnees hebergees<br/>en <span className="text-slate-300 font-medium">Belgique/France</span><br/>Conforme RGPD</div>
          </div>
        )}
      </div>
    </div>
  );

  // ===== ADMIN PLATFORM MODE: dedicated layout (no ACP) =====
  if (adminPlatformMode) {
    return (
      <div className="flex h-screen overflow-hidden bg-[#FAFAFA]">
        {/* Mobile burger */}
        <button onClick={() => setMobileOpen(true)} className="md:hidden fixed top-3 left-3 z-40 bg-slate-900 text-white p-2 rounded shadow"><Menu size={18} /></button>
        {/* Sidebar admin */}
        <aside className={`bg-slate-950 text-slate-100 hidden md:flex flex-col transition-all duration-200 ${collapsed ? 'w-16' : 'w-60'}`}>
          <AdminSidebarContent />
        </aside>
        {/* Mobile drawer */}
        {mobileOpen && (
          <>
            <div className="fixed inset-0 bg-black/40 z-40 md:hidden" onClick={() => setMobileOpen(false)} />
            <aside className="fixed top-0 left-0 h-screen w-60 bg-slate-950 text-slate-100 z-50 md:hidden flex flex-col">
              <AdminSidebarContent />
            </aside>
          </>
        )}
        {/* Main */}
        <main className="flex-1 flex flex-col overflow-hidden">
          <header className="bg-white border-b border-slate-200 px-5 h-12 flex items-center gap-3">
            <button onClick={() => setCollapsed(c => !c)} className="hidden md:inline-flex items-center justify-center w-7 h-7 rounded hover:bg-slate-100 text-slate-500"><Menu size={16} /></button>
            <div className="flex-1" />
            <span className="text-[11px] text-amber-700 font-semibold uppercase tracking-wider">Administration plateforme</span>
            <Separator orientation="vertical" className="h-5 bg-slate-200" />
            <SupportChatBubble />
            <NavLink
              to="/profile"
              className="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-100 transition-colors group"
              title="Mon profil"
              data-testid="user-info-top-admin"
            >
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-amber-500 to-red-600 text-white flex items-center justify-center text-xs font-semibold">{(user?.name || 'U')[0].toUpperCase()}</div>
              <div className="hidden md:flex flex-col items-start leading-tight">
                <span className="text-xs font-semibold text-slate-800 group-hover:text-amber-700" data-testid="user-name-display-admin">{user?.name || 'Utilisateur'}</span>
                <span className="text-[10px] text-slate-500">{getRoleLabel(user?.role)}</span>
              </div>
            </NavLink>
          </header>
          <div className="flex-1 overflow-auto p-5 md:p-6"><Outlet /></div>
          <OnboardingDialog />
        </main>
      </div>
    );
  }

  // ===== NO ACP SELECTED: Full-width layout without sidebar =====
  if (!hasCopro) {
    return (
      <div className="flex flex-col h-screen overflow-hidden bg-[#FAFAFA]">
        <header className="bg-slate-950 sticky top-0 z-30 h-14 flex items-center px-6 gap-4">
          <img src="/logo-nextge.png" alt="NextGe Copro" className="h-9 w-auto bg-white rounded p-0.5" />
          <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>NextGe Copro</span>
          <div className="flex-1" />
          <NavLink to="/" end className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-dashboard-top">
            <LayoutDashboard size={14} /> Tableau de bord
          </NavLink>
          {isSuperadmin && (
            <NavLink to="/admin" className="text-amber-400 hover:text-amber-300 text-xs flex items-center gap-1.5 transition-colors font-semibold" data-testid="nav-admin-top">
              <Shield size={14} /> Admin
            </NavLink>
          )}
          {isSuperadmin && (
            <NavLink to="/admin/users" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-admin-users-top">
              <Shield size={14} /> Utilisateurs
            </NavLink>
          )}
          <NavLink to="/profile" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-profile-top">
            <UserCog size={14} /> Mon profil
          </NavLink>
          {(user?.role === 'syndic' || user?.role === 'admin' || user?.role === 'superadmin') && (
            <NavLink to="/mon-bureau" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-mon-bureau-top">
              <Building2 size={14} /> Mon bureau
            </NavLink>
          )}
          <NavLink to="/coproprietes" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-coproprietes-top">
            <Home size={14} /> Gerer les ACP
          </NavLink>
          <Separator orientation="vertical" className="h-6 bg-slate-700" />
          <SupportChatBubble />
          <NavLink
            to="/profile"
            className="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-800 transition-colors group"
            title="Mon profil"
            data-testid="user-info-top-noacp"
          >
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-xs font-bold text-white shadow-md shadow-blue-500/25 ring-2 ring-white">{(user?.name || 'U')[0].toUpperCase()}</div>
            <div className="hidden md:flex flex-col items-start leading-tight">
              <span className="text-xs font-semibold text-white group-hover:text-blue-200" data-testid="user-name-display-noacp">{user?.name || 'Utilisateur'}</span>
              <span className="text-[10px] text-slate-400">{getRoleLabel(user?.role)}</span>
            </div>
          </NavLink>
          <button onClick={handleLogout} className="text-slate-400 hover:text-red-400 transition-colors" data-testid="logout-btn-top"><LogOut size={16} /></button>
        </header>
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-6"><div className="min-w-0"><Outlet /></div>
          <footer className="pt-6 pb-2 border-t border-slate-200 mt-6">
            <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-[10px] text-slate-400" data-testid="layout-legal-footer">
              <a href="/legal/cgu" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">CGU</a>
              <span>·</span>
              <a href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Confidentialite</a>
              <span>·</span>
              <a href="/legal/mentions" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Mentions Legales</a>
              <span>·</span>
              <a href="/legal/cookies" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Cookies</a>
              <span>·</span>
              <a href="/legal/disclaimer" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Disclaimer</a>
              <span>·</span>
              <a href="/legal/data-act" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="footer-data-act-link">Registre Data Act</a>
            </div>
          </footer>
        </main>
        <OnboardingDialog />
      </div>
    );
  }

  // ===== ACP SELECTED: TopNav horizontal + content (iter90bk) =====
  // Preparation des sections dynamiques (Plateforme si superadmin, Compte
  // pour tous). L'ancien sidebar est conserve uniquement pour mobile (< lg)
  // sous forme de drawer, pour ne pas casser l'UX mobile.
  const extraSections = [];
  if (isSuperadmin) {
    extraSections.push({
      title: 'Plateforme', accent: 'amber', items: [
        { to: '/admin', icon: Shield, label: 'Tableau admin' },
        { to: '/admin/users', icon: Shield, label: 'Utilisateurs' },
        { to: '/admin/billing', icon: Wallet, label: 'Facturation' },
        { to: '/admin/e2e-test', icon: Activity, label: 'Tests E2E' },
        { to: '/admin/unlock', icon: Unlock, label: 'Outils deblocage' },
        { to: '/admin/audit', icon: Activity, label: "Journal d'audit" },
        { to: '/admin/release-notes', icon: FileText, label: 'Notes de version' },
      ],
    });
  }
  const accountItems = [];
  if (user?.role === 'syndic' || user?.role === 'admin' || user?.role === 'superadmin') {
    accountItems.push({ to: '/mon-bureau', icon: Building2, label: 'Mon bureau' });
    accountItems.push({ to: '/team', icon: Users, label: 'Mon equipe' });
  }
  accountItems.push({ to: '/profile', icon: UserCog, label: 'Mon profil' });
  extraSections.push({ title: 'Compte', accent: 'slate', items: accountItems });

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#FAFAFA]">
      {/* Mobile drawer (< lg) : conserve l'ancien sidebar vertical */}
      {mobileOpen && <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setMobileOpen(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-50 bg-slate-950 transition-transform duration-200 lg:hidden ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`} style={{width: 256}}>
        <SidebarContent />
      </aside>

      {/* Header (ACP selector + fiscal year + user) */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-30 h-12 flex items-center px-4 lg:px-6 gap-3 flex-shrink-0">
        <Button variant="ghost" size="sm" className="lg:hidden" onClick={() => setMobileOpen(true)} data-testid="mobile-menu-btn"><Menu size={20} /></Button>
        <div className="hidden lg:flex items-center gap-2">
          <img src="/logo-nextge.png" alt="NextGe Copro" className="h-8 w-auto flex-shrink-0" />
          <span className="text-slate-900 font-bold text-sm tracking-tight font-display">NextGe Copro</span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setSelectedCopro(''); navigate('/'); }}
          className="h-8 px-2.5 text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/5 gap-1.5"
          data-testid="back-to-home"
          title="Retour a l'apercu de toutes les ACPs"
        >
          <Home size={14} />
          <span className="hidden sm:inline text-xs font-medium">Retour ACPs</span>
        </Button>
        <Select value={selectedCopro} onValueChange={(v) => setSelectedCopro(v)}>
          <SelectTrigger className="w-[220px] h-8 text-xs border-[#022D52]/30" data-testid="copro-selector"><SelectValue /></SelectTrigger>
          <SelectContent>{activeCoproprietes.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
        </Select>
        {selectedCopro && fiscalYears && fiscalYears.length > 0 && (
          <Select value={selectedFiscalYearId || '__all__'} onValueChange={(v) => setSelectedFiscalYearId(v === '__all__' ? '' : v)}>
            <SelectTrigger className="w-[200px] h-8 text-xs border-emerald-300 bg-emerald-50/50 text-emerald-900" data-testid="fiscal-year-selector" title="Filtre par exercice comptable (applique a tous les ecrans)">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__" data-testid="fy-option-all">— Tous les exercices —</SelectItem>
              {sortedFiscalYears.map(y => (
                <SelectItem key={y.id} value={y.id} data-testid={`fy-option-${y.id}`}>
                  {y.name} {y.status === 'closed' ? '(cloture)' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {selectedCoproData && (
          <button
            onClick={() => navigate(`/coproprietes?edit=${selectedCoproData.id}`)}
            className="hidden md:inline-flex items-center gap-1 h-8 px-2 rounded text-slate-500 hover:text-[#022D52] hover:bg-slate-100 text-xs"
            title="Editer cette copropriete"
            data-testid="header-edit-acp"
          >
            <Pencil size={13} />
          </button>
        )}
        <div className="flex-1" />
        <SupportChatBubble />
        <button
          onClick={handleLogout}
          className="hidden md:inline-flex items-center gap-1 h-8 px-2 rounded text-slate-500 hover:text-red-500 hover:bg-red-50 text-xs"
          data-testid="logout-btn"
          title="Deconnexion"
        >
          <LogOut size={14} />
        </button>
        <NavLink
          to="/profile"
          className="flex items-center gap-2 px-2 py-1 rounded hover:bg-slate-100 transition-colors group"
          title="Mon profil"
          data-testid="user-info-top"
        >
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-xs font-bold text-white shadow-md shadow-blue-500/25 ring-2 ring-white">{(user?.name || 'U')[0].toUpperCase()}</div>
          <div className="hidden md:flex flex-col items-start leading-tight">
            <span className="text-xs font-semibold text-slate-800 group-hover:text-[#022D52]" data-testid="user-name-display">{user?.name || 'Utilisateur'}</span>
            <span className="text-[10px] text-slate-500">{getRoleLabel(user?.role)}</span>
          </div>
        </NavLink>
      </header>

      {/* iter90bk : TopNav horizontal (desktop uniquement) */}
      <div className="hidden lg:block">
        <TopNav sections={sections} extraSections={extraSections} />
      </div>

      <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 lg:p-6">
        <div className="min-w-0"><Outlet /></div>
        <footer className="pt-6 pb-2 border-t border-slate-200 mt-6">
          <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-[10px] text-slate-400" data-testid="layout-legal-footer">
            <a href="/legal/cgu" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">CGU</a>
            <span>·</span>
            <a href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Confidentialite</a>
            <span>·</span>
            <a href="/legal/mentions" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Mentions Legales</a>
            <span>·</span>
            <a href="/legal/cookies" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Cookies</a>
            <span>·</span>
            <a href="/legal/disclaimer" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline">Disclaimer</a>
            <span>·</span>
            <a href="/legal/data-act" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="footer-data-act-link-2">Registre Data Act</a>
          </div>
        </footer>
      </main>
      <OnboardingDialog />
    </div>
  );
}
