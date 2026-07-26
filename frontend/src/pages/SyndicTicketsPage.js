import SupportTicketsList from '@/components/SupportTicketsList';

/**
 * iter90g4 - Page syndic de suivi de ses propres tickets support.
 *
 * Route : /support/tickets
 * Accessible a tout utilisateur syndic (non-superadmin). Le composant
 * `SupportTicketsList` (dense=false, superadmin=false) filtre cote backend
 * pour ne montrer que les tickets ouverts par ce syndic (ou son ACP).
 */
export default function SyndicTicketsPage() {
  return (
    <div data-testid="syndic-tickets-page">
      <div className="page-header">
        <h1 className="page-title">Mes tickets support</h1>
        <p className="page-subtitle">Suivi de vos remontees de bug et demandes d&apos;assistance</p>
      </div>
      <div className="bg-white rounded-2xl border border-slate-200 shadow-card overflow-hidden" style={{ height: 'calc(100vh - 200px)' }}>
        <SupportTicketsList superadmin={false} dense={false} />
      </div>
    </div>
  );
}
