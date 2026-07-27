import "@/App.css";
import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import Layout from "@/components/Layout";
import LoginPage from "@/pages/LoginPage";
import ForgotPasswordPage from "@/pages/ForgotPasswordPage";
import ResetPasswordPage from "@/pages/ResetPasswordPage";
import DashboardPage from "@/pages/DashboardPage";
import OwnersPage from "@/pages/OwnersPage";
import LotsPage from "@/pages/LotsPage";
import TenantsPage from "@/pages/TenantsPage";
import SuppliersPage from "@/pages/SuppliersPage";
import AccountingPage from "@/pages/AccountingPage";
import FiscalYearPage from "@/pages/FiscalYearPage";
import JournalsPage from "@/pages/JournalsPage";
import GrandLivrePage from "@/pages/GrandLivrePage";
import InvoicesPage from "@/pages/InvoicesPage";
import FundCallsPage from "@/pages/FundCallsPage";
import MetersPage from "@/pages/MetersPage";
import BankingPage from "@/pages/BankingPage";
import ReportsPage from "@/pages/ReportsPage";
import BalanceTiersPage from "@/pages/BalanceTiersPage";
import DocumentsPage from "@/pages/DocumentsPage";
import AdminUsersPage from "@/pages/AdminUsersPage";
import AdminDashboardPage from "@/pages/AdminDashboardPage";
import AdminUnlockEntryPage from "@/pages/AdminUnlockEntryPage";
import AdminAuditLogPage from "@/pages/AdminAuditLogPage";
import AdminReleaseNotesPage from "@/pages/AdminReleaseNotesPage";
import AdminLoginHistoryPage from "@/pages/AdminLoginHistoryPage";
import ImportWizardPage from "@/pages/ImportWizardPage";
import AdminRoleTemplatesPage from "@/pages/AdminRoleTemplatesPage";
import AdminDuplicatesPage from "@/pages/AdminDuplicatesPage";
import AdminExpenseCategoriesDedupePage from "@/pages/AdminExpenseCategoriesDedupePage";
import AdminQualityAuditPage from "@/pages/AdminQualityAuditPage";
import AdminMutationsAuditPage from "@/pages/AdminMutationsAuditPage";
import TeamMembersPage from "@/pages/TeamMembersPage";
import ProfilePage from "@/pages/ProfilePage";
import CommunicationPage from "@/pages/CommunicationPage";
import CommunicationHistoryPage from "@/pages/CommunicationHistoryPage";
import EmailTemplatesPage from "@/pages/EmailTemplatesPage";
import AdminSyndicConfigPage from "@/pages/AdminSyndicConfigPage";
import MonBureauPage from "@/pages/MonBureauPage";
import AdminBackupsPage from "@/pages/AdminBackupsPage";
import AdminTicketsPage from "@/pages/AdminTicketsPage";
import SyndicTicketsPage from "@/pages/SyndicTicketsPage";
import SyndicOnboardingWizard from "@/pages/SyndicOnboardingWizard";
import CoproprietesPage from "@/pages/CoproprietesPage";
import OwnerPortalPage from "@/pages/OwnerPortalPage";
import RemindersPage from "@/pages/RemindersPage";
import ExpensesPage from "@/pages/ExpensesPage";
import ExpenseCategoriesPage from "@/pages/ExpenseCategoriesPage";
import DistributionKeysPage from "@/pages/DistributionKeysPage";
import LegalDocPage from "@/pages/LegalDocPage";
import AdminLegalDocsPage from "@/pages/AdminLegalDocsPage";
import AdminRgpdRegisterPage from "@/pages/AdminRgpdRegisterPage";
import CookieBanner from "@/components/CookieBanner";
import LegalAcceptanceModal from "@/components/LegalAcceptanceModal";
import ReleaseNotesModal from "@/components/ReleaseNotesModal";
import ErrorBoundary from "@/components/ErrorBoundary";

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#022D52] animate-pulse w-1/2" /></div></div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

// iter90i3 : GUARD RBAC - protection stricte des routes reservees aux
// super-administrateurs. Un syndic, un gestionnaire ou un proprietaire ne
// doit JAMAIS pouvoir acceder aux pages `/admin/*` (faille de securite).
// Redirige vers `/` (dashboard syndic) ou `/portal` (proprio) selon le role.
function RequireSuperadmin({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  const isSuper = user.role === 'superadmin' || user.role === 'admin';
  if (!isSuper) {
    const fallback = user.role === 'owner' ? '/portal' : '/';
    return <Navigate to={fallback} replace />;
  }
  return children;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#022D52] animate-pulse w-1/2" /></div></div>;

  // Owners go directly to their dedicated portal
  const isOwnerRole = user && user.role === 'owner';
  // Superadmins land on the admin dashboard at /admin by default
  const isSuperadminRole = user && (user.role === 'superadmin' || user.role === 'admin');
  const defaultPath = isOwnerRole ? '/portal' : (isSuperadminRole ? '/admin' : '/');

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to={defaultPath} replace /> : <LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/legal/:slug" element={<LegalDocPage />} />
      <Route path="/legal" element={<Navigate to="/legal/cgu" replace />} />
      <Route path="/portal" element={<ProtectedRoute><OwnerPortalPage /></ProtectedRoute>} />
      <Route path="/" element={<ProtectedRoute>{isOwnerRole ? <Navigate to="/portal" replace /> : <Layout />}</ProtectedRoute>}>
        <Route index element={<DashboardPage />} />
        <Route path="coproprietes" element={<CoproprietesPage />} />
        <Route path="owners" element={<OwnersPage />} />
        <Route path="lots" element={<LotsPage />} />
        <Route path="tenants" element={<TenantsPage />} />
        <Route path="suppliers" element={<SuppliersPage />} />
        <Route path="accounting" element={<AccountingPage />} />
        <Route path="fiscal" element={<FiscalYearPage />} />
        <Route path="journals" element={<JournalsPage />} />
        <Route path="grand-livre" element={<GrandLivrePage />} />
        <Route path="invoices" element={<InvoicesPage />} />
        <Route path="fund-calls" element={<FundCallsPage />} />
        <Route path="meters" element={<MetersPage />} />
        <Route path="banking" element={<BankingPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="balance-tiers" element={<BalanceTiersPage />} />
        <Route path="reminders" element={<RemindersPage />} />
        <Route path="expenses" element={<ExpensesPage />} />
        <Route path="expense-categories" element={<ExpenseCategoriesPage />} />
        <Route path="distribution-keys" element={<DistributionKeysPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="admin/users" element={<RequireSuperadmin><AdminUsersPage /></RequireSuperadmin>} />
        <Route path="admin" element={<RequireSuperadmin><AdminDashboardPage /></RequireSuperadmin>} />
        <Route path="admin/unlock" element={<RequireSuperadmin><AdminUnlockEntryPage /></RequireSuperadmin>} />
        <Route path="admin/audit" element={<RequireSuperadmin><AdminAuditLogPage /></RequireSuperadmin>} />
        <Route path="admin/release-notes" element={<RequireSuperadmin><AdminReleaseNotesPage /></RequireSuperadmin>} />
        <Route path="admin/legal" element={<RequireSuperadmin><AdminLegalDocsPage /></RequireSuperadmin>} />
        <Route path="admin/rgpd-register" element={<RequireSuperadmin><AdminRgpdRegisterPage /></RequireSuperadmin>} />
        <Route path="admin/login-history" element={<RequireSuperadmin><AdminLoginHistoryPage /></RequireSuperadmin>} />
        <Route path="import-wizard" element={<ImportWizardPage />} />
        <Route path="admin/role-templates" element={<RequireSuperadmin><AdminRoleTemplatesPage /></RequireSuperadmin>} />
        <Route path="admin/duplicates" element={<RequireSuperadmin><AdminDuplicatesPage /></RequireSuperadmin>} />
        <Route path="admin/expense-categories-dedupe" element={<RequireSuperadmin><AdminExpenseCategoriesDedupePage /></RequireSuperadmin>} />
        <Route path="admin/quality-audit" element={<RequireSuperadmin><AdminQualityAuditPage /></RequireSuperadmin>} />
        <Route path="admin/mutations-audit" element={<RequireSuperadmin><AdminMutationsAuditPage /></RequireSuperadmin>} />
        <Route path="team" element={<TeamMembersPage />} />
        <Route path="communication" element={<CommunicationPage />} />
        <Route path="communication/history" element={<CommunicationHistoryPage />} />
        <Route path="email-templates" element={<EmailTemplatesPage />} />
        <Route path="admin/syndic-config" element={<RequireSuperadmin><AdminSyndicConfigPage /></RequireSuperadmin>} />
        <Route path="mon-bureau" element={<MonBureauPage />} />
        <Route path="admin/backups" element={<RequireSuperadmin><AdminBackupsPage /></RequireSuperadmin>} />
        <Route path="admin/tickets" element={<RequireSuperadmin><AdminTicketsPage /></RequireSuperadmin>} />
        <Route path="support/tickets" element={<SyndicTicketsPage />} />
        <Route path="profile" element={<ProfilePage />} />
      </Route>
    </Routes>
  );
}

function App() {
  // iter90er : bloque la modification des inputs number a la roulette
  // (couvre les <input type="number"> bruts non wrappes par le composant
  // Input partage - ex: ImportWizardPage).
  useEffect(() => {
    const handler = (e) => {
      const t = e.target;
      if (
        t &&
        t.tagName === "INPUT" &&
        t.type === "number" &&
        document.activeElement === t
      ) {
        t.blur();
      }
    };
    document.addEventListener("wheel", handler, { passive: true });
    return () => document.removeEventListener("wheel", handler);
  }, []);

  // iter90gz : catch-all pour les Promise Rejections non-gerees.
  // Empeche que des erreurs async (Axios sans .catch) ne declenchent
  // l'overlay dev de react-scripts. En prod build ces erreurs restent
  // silencieuses mais on les loggue en console pour observabilite.
  useEffect(() => {
    const onUnhandled = (event) => {
      const reason = event?.reason;
      // Axios errors : deja logue par l'intercepteur, on suppress juste l'overlay
      const isAxios = reason && (reason.isAxiosError || reason?.response);
      console.error("[iter90gz unhandledrejection]", reason);
      if (isAxios) {
        // Empeche react-scripts d'afficher son overlay rouge
        event.preventDefault();
      }
    };
    window.addEventListener("unhandledrejection", onUnhandled);
    return () => window.removeEventListener("unhandledrejection", onUnhandled);
  }, []);

  return (
    <BrowserRouter>
      <ErrorBoundary>
        <AuthProvider>
          <AppRoutes />
          <SyndicOnboardingWizard />
          <LegalAcceptanceModal />
          <ReleaseNotesModal />
          <CookieBanner />
          <Toaster position="top-right" />
        </AuthProvider>
      </ErrorBoundary>
    </BrowserRouter>
  );
}

export default App;
