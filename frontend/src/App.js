import "@/App.css";
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
import TeamMembersPage from "@/pages/TeamMembersPage";
import ProfilePage from "@/pages/ProfilePage";
import CommunicationPage from "@/pages/CommunicationPage";
import EmailTemplatesPage from "@/pages/EmailTemplatesPage";
import AdminSyndicConfigPage from "@/pages/AdminSyndicConfigPage";
import MonBureauPage from "@/pages/MonBureauPage";
import AdminBackupsPage from "@/pages/AdminBackupsPage";
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

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#2563EB] animate-pulse w-1/2" /></div></div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#2563EB] animate-pulse w-1/2" /></div></div>;

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
        <Route path="admin/users" element={<AdminUsersPage />} />
        <Route path="admin" element={<AdminDashboardPage />} />
        <Route path="admin/unlock" element={<AdminUnlockEntryPage />} />
        <Route path="admin/audit" element={<AdminAuditLogPage />} />
        <Route path="admin/release-notes" element={<AdminReleaseNotesPage />} />
        <Route path="admin/legal" element={<AdminLegalDocsPage />} />
        <Route path="admin/rgpd-register" element={<AdminRgpdRegisterPage />} />
        <Route path="admin/login-history" element={<AdminLoginHistoryPage />} />
        <Route path="import-wizard" element={<ImportWizardPage />} />
        <Route path="admin/role-templates" element={<AdminRoleTemplatesPage />} />
        <Route path="admin/duplicates" element={<AdminDuplicatesPage />} />
        <Route path="team" element={<TeamMembersPage />} />
        <Route path="communication" element={<CommunicationPage />} />
        <Route path="email-templates" element={<EmailTemplatesPage />} />
        <Route path="admin/syndic-config" element={<AdminSyndicConfigPage />} />
        <Route path="mon-bureau" element={<MonBureauPage />} />
        <Route path="admin/backups" element={<AdminBackupsPage />} />
        <Route path="profile" element={<ProfilePage />} />
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
        <SyndicOnboardingWizard />
        <LegalAcceptanceModal />
        <ReleaseNotesModal />
        <CookieBanner />
        <Toaster position="top-right" />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
