import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { Toaster } from "@/components/ui/sonner";
import Layout from "@/components/Layout";
import LoginPage from "@/pages/LoginPage";
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
import DocumentsPage from "@/pages/DocumentsPage";
import AdminUsersPage from "@/pages/AdminUsersPage";
import CoproprietesPage from "@/pages/CoproprietesPage";

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div></div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function AppRoutes() {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex h-screen items-center justify-center"><div className="h-1 w-48 bg-slate-200 rounded overflow-hidden"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div></div>;

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
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
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="admin/users" element={<AdminUsersPage />} />
      </Route>
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
        <Toaster position="top-right" />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
