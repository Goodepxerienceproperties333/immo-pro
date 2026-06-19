import { useState, useEffect } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import {
  LayoutDashboard, Users, Building2, UserCheck, BookOpen, FileText,
  Receipt, Gauge, Landmark, FolderOpen, LogOut, ChevronLeft, ChevronRight,
  Menu, Shield, Home, Truck, Calendar, BookMarked, Megaphone, BarChart3, Bell, Wallet, Tag, UserCog, Pencil
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

const sections = [
  { title: 'Gestion', items: [
    { to: '/', icon: LayoutDashboard, label: 'Tableau de bord', end: true },
    { to: '/owners', icon: Users, label: 'Proprietaires' },
    { to: '/lots', icon: Building2, label: 'Lots' },
    { to: '/tenants', icon: UserCheck, label: 'Locataires' },
    { to: '/suppliers', icon: Truck, label: 'Fournisseurs' },
  ]},
  { title: 'Comptabilite', items: [
    { to: '/accounting', icon: BookOpen, label: 'Plan Comptable' },
    { to: '/fiscal', icon: Calendar, label: 'Exercices' },
    { to: '/journals', icon: FileText, label: 'Journaux' },
    { to: '/grand-livre', icon: BookMarked, label: 'Grand Livre' },
    { to: '/expense-categories', icon: Tag, label: 'Natures de depense' },
  ]},
  { title: 'Finance', items: [
    { to: '/invoices', icon: Receipt, label: 'Facturation' },
    { to: '/expenses', icon: Wallet, label: 'Depenses' },
    { to: '/fund-calls', icon: Megaphone, label: 'Appels de fonds' },
    { to: '/banking', icon: Landmark, label: 'Banque' },
    { to: '/meters', icon: Gauge, label: 'Compteurs' },
  ]},
  { title: 'Rapports', items: [
    { to: '/reports', icon: BarChart3, label: 'Bilan & Resultats' },
    { to: '/balance-tiers', icon: Users, label: 'Balance de Tiers' },
    { to: '/reminders', icon: Bell, label: 'Rappels paiement' },
    { to: '/documents', icon: FolderOpen, label: 'Documents' },
  ]},
];

export default function Layout() {
  const { user, logout, selectedCopro, setSelectedCopro, isAdmin, isManager, isSuperadmin } = useAuth();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [coproprietes, setCoproprietes] = useState([]);

  useEffect(() => { api.get('/coproprietes').then(r => setCoproprietes(r.data)).catch(() => {}); }, []);

  const handleLogout = async () => { await logout(); navigate('/login'); };
  const getRoleLabel = (role) => ({ superadmin: 'Syndic', admin: 'Syndic', syndic: 'Syndic', gestionnaire: 'Gestionnaire' }[role] || 'Proprietaire');
  const selectedCoproData = coproprietes.find(c => c.id === selectedCopro);
  const hasCopro = !!selectedCopro;

  const SidebarContent = () => (
    <div className="flex flex-col h-full">
      <div className="p-4 flex items-center gap-3">
        <div className="w-8 h-8 rounded bg-[#0055FF] flex items-center justify-center text-white font-bold text-sm flex-shrink-0">CP</div>
        {!collapsed && <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>CoproManager</span>}
      </div>
      <Separator className="bg-slate-800" />
      {/* ACP name */}
      {!collapsed && selectedCoproData && (
        <div className="px-4 py-2 bg-[#0055FF]/10 border-b border-slate-800 flex items-start gap-2">
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
            <div key={si} className="mb-2">
              {!collapsed && <div className="px-3 py-1 mt-1 text-[10px] uppercase tracking-[0.2em] text-slate-500 font-semibold">{section.title}</div>}
              <div className="space-y-0.5">
                {section.items.map(item => (
                  <NavLink key={item.to} to={item.to} end={item.end} onClick={() => setMobileOpen(false)}
                    className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                    data-testid={`nav-${item.to.replace(/\//g, '') || 'dashboard'}`}
                  >
                    <item.icon size={16} strokeWidth={1.5} />
                    {!collapsed && <span className="text-[13px]">{item.label}</span>}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
          {isSuperadmin && (
            <div className="mb-2">
              {!collapsed && <div className="px-3 py-1 mt-1 text-[10px] uppercase tracking-[0.2em] text-slate-500 font-semibold">Admin</div>}
              <NavLink to="/admin/users" onClick={() => setMobileOpen(false)}
                className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
                data-testid="nav-admin-users"
              ><Shield size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Utilisateurs</span>}</NavLink>
            </div>
          )}
          {/* "Mon profil" accessible a TOUS les utilisateurs authentifies */}
          <div className="mb-2">
            {!collapsed && <div className="px-3 py-1 mt-1 text-[10px] uppercase tracking-[0.2em] text-slate-500 font-semibold">Compte</div>}
            <NavLink to="/profile" onClick={() => setMobileOpen(false)}
              className={({ isActive }) => `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`}
              data-testid="nav-profile"
            ><UserCog size={16} strokeWidth={1.5} />{!collapsed && <span className="text-[13px]">Mon profil</span>}</NavLink>
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

  // ===== NO ACP SELECTED: Full-width layout without sidebar =====
  if (!hasCopro) {
    return (
      <div className="flex flex-col h-screen overflow-hidden bg-[#FAFAFA]">
        <header className="bg-slate-950 sticky top-0 z-30 h-14 flex items-center px-6 gap-4">
          <div className="w-8 h-8 rounded bg-[#0055FF] flex items-center justify-center text-white font-bold text-sm">CP</div>
          <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>CoproManager</span>
          <div className="flex-1" />
          {isSuperadmin && (
            <NavLink to="/admin/users" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-admin-users-top">
              <Shield size={14} /> Utilisateurs
            </NavLink>
          )}
          <NavLink to="/profile" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-profile-top">
            <UserCog size={14} /> Mon profil
          </NavLink>
          <NavLink to="/coproprietes" className="text-slate-400 hover:text-white text-xs flex items-center gap-1.5 transition-colors" data-testid="nav-coproprietes-top">
            <Home size={14} /> Gerer les ACP
          </NavLink>
          <Separator orientation="vertical" className="h-6 bg-slate-700" />
          <span className="text-[11px] text-slate-400">{getRoleLabel(user?.role)}</span>
          <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center text-xs font-semibold text-white">{(user?.name || 'U')[0].toUpperCase()}</div>
          <button onClick={handleLogout} className="text-slate-400 hover:text-red-400 transition-colors" data-testid="logout-btn-top"><LogOut size={16} /></button>
        </header>
        <main className="flex-1 overflow-auto p-6"><div className="max-w-[1400px] mx-auto"><Outlet /></div></main>
      </div>
    );
  }

  // ===== ACP SELECTED: Sidebar + content =====
  return (
    <div className="flex h-screen overflow-hidden bg-[#FAFAFA]">
      {mobileOpen && <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setMobileOpen(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-50 bg-slate-950 transition-transform duration-200 lg:hidden ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`} style={{width: 256}}><SidebarContent /></aside>
      <aside className="hidden lg:flex flex-col bg-slate-950 transition-all duration-200 flex-shrink-0" style={{width: collapsed ? 64 : 220}}>
        <SidebarContent />
        <button onClick={() => setCollapsed(!collapsed)} className="p-2 text-slate-400 hover:text-white transition-colors border-t border-slate-800 flex justify-center" data-testid="sidebar-toggle">
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-slate-200 sticky top-0 z-30 h-12 flex items-center px-4 lg:px-6 gap-4">
          <Button variant="ghost" size="sm" className="lg:hidden" onClick={() => setMobileOpen(true)} data-testid="mobile-menu-btn"><Menu size={20} /></Button>
          <Button variant="ghost" size="sm" onClick={() => { setSelectedCopro(''); navigate('/'); }} className="h-8 px-2 text-slate-400 hover:text-slate-700" data-testid="back-to-home" title="Retour aux coproprietes"><Home size={14} /></Button>
          <Select value={selectedCopro} onValueChange={(v) => setSelectedCopro(v)}>
            <SelectTrigger className="w-[220px] h-8 text-xs border-[#0055FF]/30" data-testid="copro-selector"><SelectValue /></SelectTrigger>
            <SelectContent>{coproprietes.filter(c => c.status !== 'archived').map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
          </Select>
          <div className="flex-1" />
          <span className="text-[11px] text-slate-500 hidden sm:block">{getRoleLabel(user?.role)}</span>
          <div className="w-7 h-7 rounded-full bg-slate-200 flex items-center justify-center text-xs font-semibold text-slate-600">{(user?.name || 'U')[0].toUpperCase()}</div>
        </header>
        <main className="flex-1 overflow-auto p-4 lg:p-6"><div className="max-w-[1600px] mx-auto"><Outlet /></div></main>
      </div>
    </div>
  );
}
