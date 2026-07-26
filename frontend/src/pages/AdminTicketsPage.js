import SupportTicketsList from '@/components/SupportTicketsList';

/**
 * Iter90fs - Page superadmin de gestion des tickets support.
 *
 * Route : /admin/tickets
 * Accessible uniquement en mode Administration plateforme (superadmin).
 */
export default function AdminTicketsPage() {
  return (
    <div data-testid="admin-tickets-page">
      <div className="page-header">
        <h1 className="page-title">Support &amp; Tickets</h1>
        <p className="page-subtitle">Suivi et traitement des remontees de bug des syndics</p>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200 shadow-card overflow-hidden" style={{ height: 'calc(100vh - 200px)' }}>
        <SupportTicketsList superadmin dense={false} />
      </div>
    </div>
  );
}
