import { useState } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import {
  LayoutDashboard, Users, Building2, UserCheck, BookOpen, FileText,
  Receipt, Gauge, Landmark, FolderOpen, LogOut, ChevronLeft, ChevronRight, Menu
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Tableau de bord', end: true },
  { to: '/owners', icon: Users, label: 'Proprietaires' },
  { to: '/lots', icon: Building2, label: 'Lots' },
  { to: '/tenants', icon: UserCheck, label: 'Locataires' },
  { to: '/accounting', icon: BookOpen, label: 'Plan Comptable' },
  { to: '/journals', icon: FileText, label: 'Journaux' },
  { to: '/invoices', icon: Receipt, label: 'Facturation' },
  { to: '/meters', icon: Gauge, label: 'Compteurs' },
  { to: '/banking', icon: Landmark, label: 'Banque' },
  { to: '/documents', icon: FolderOpen, label: 'Documents' },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const SidebarContent = () => (
    <div className="flex flex-col h-full">
      <div className="p-4 flex items-center gap-3">
        <div className="w-8 h-8 rounded bg-[#0055FF] flex items-center justify-center text-white font-bold text-sm flex-shrink-0">
          CP
        </div>
        {!collapsed && <span className="text-white font-bold text-lg tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>CoproManager</span>}
      </div>
      <Separator className="bg-slate-800" />
      <ScrollArea className="flex-1 px-2 py-3">
        <nav className="space-y-1">
          {navItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `sidebar-link ${isActive ? 'active' : ''} ${collapsed ? 'justify-center px-2' : ''}`
              }
              data-testid={`nav-${item.to.replace('/', '') || 'dashboard'}`}
            >
              <item.icon size={18} strokeWidth={1.5} />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>
      </ScrollArea>
      <Separator className="bg-slate-800" />
      <div className="p-3">
        {!collapsed && (
          <div className="text-xs text-slate-400 mb-2 truncate">{user?.name || user?.email}</div>
        )}
        <button
          onClick={handleLogout}
          data-testid="logout-btn"
          className="sidebar-link w-full text-slate-400 hover:text-red-400"
        >
          <LogOut size={18} strokeWidth={1.5} />
          {!collapsed && <span>Deconnexion</span>}
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[#FAFAFA]">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 bg-black/50 z-40 lg:hidden" onClick={() => setMobileOpen(false)} />
      )}

      {/* Sidebar - mobile */}
      <aside className={`fixed inset-y-0 left-0 z-50 bg-slate-950 transition-transform duration-200 lg:hidden ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}`} style={{width: 256}}>
        <SidebarContent />
      </aside>

      {/* Sidebar - desktop */}
      <aside
        className={`hidden lg:flex flex-col bg-slate-950 transition-all duration-200 flex-shrink-0`}
        style={{width: collapsed ? 64 : 240}}
      >
        <SidebarContent />
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-2 text-slate-400 hover:text-white transition-colors border-t border-slate-800 flex justify-center"
          data-testid="sidebar-toggle"
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <header className="bg-white border-b border-slate-200 sticky top-0 z-30 h-14 flex items-center px-4 lg:px-6 gap-4">
          <Button
            variant="ghost"
            size="sm"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            data-testid="mobile-menu-btn"
          >
            <Menu size={20} />
          </Button>
          <div className="flex-1" />
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-500 hidden sm:block">{user?.role === 'admin' ? 'Syndic' : 'Proprietaire'}</span>
            <div className="w-8 h-8 rounded-full bg-slate-200 flex items-center justify-center text-sm font-semibold text-slate-600">
              {(user?.name || 'U')[0].toUpperCase()}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-auto p-4 lg:p-6">
          <div className="max-w-[1600px] mx-auto">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
