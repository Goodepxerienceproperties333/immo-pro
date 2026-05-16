import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Users, Building2, UserCheck, Receipt, AlertCircle, TrendingUp } from 'lucide-react';

export default function DashboardPage() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/dashboard/stats').then(r => { setStats(r.data); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <div className="h-1 w-48 bg-slate-200 rounded overflow-hidden mx-auto mt-20"><div className="h-full bg-[#0055FF] animate-pulse w-1/2" /></div>;

  const kpis = [
    { label: 'Proprietaires', value: stats?.owners_count || 0, icon: Users, color: '#0055FF' },
    { label: 'Lots', value: stats?.lots_count || 0, icon: Building2, color: '#0284C7' },
    { label: 'Locataires', value: stats?.tenants_count || 0, icon: UserCheck, color: '#00A650' },
    { label: 'Factures', value: stats?.invoices_count || 0, icon: Receipt, color: '#FF6B00' },
    { label: 'Impayees', value: stats?.unpaid_invoices || 0, icon: AlertCircle, color: '#DC2626' },
    { label: 'Total charges', value: `${(stats?.total_charges || 0).toLocaleString('fr-BE')} EUR`, icon: TrendingUp, color: '#0055FF' },
  ];

  return (
    <div data-testid="dashboard-page">
      <div className="page-header">
        <h1 className="page-title">Tableau de bord</h1>
        <p className="page-subtitle">Vue d'ensemble de votre copropriete</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 mb-8">
        {kpis.map((kpi, i) => (
          <Card key={i} className="stat-card border-slate-200" data-testid={`kpi-${kpi.label.toLowerCase().replace(/\s/g, '-')}`}>
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] uppercase tracking-[0.2em] font-semibold text-slate-500">{kpi.label}</span>
                <kpi.icon size={16} style={{color: kpi.color}} strokeWidth={1.5} />
              </div>
              <div className="text-2xl font-bold text-slate-900 tracking-tight" style={{fontFamily:'Chivo,sans-serif'}}>
                {kpi.value}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="border-slate-200">
          <CardContent className="p-6">
            <h3 className="text-lg font-semibold text-slate-900 mb-4" style={{fontFamily:'Chivo,sans-serif'}}>
              Dernieres ecritures
            </h3>
            {stats?.recent_entries?.length > 0 ? (
              <div className="space-y-3">
                {stats.recent_entries.map((entry, i) => (
                  <div key={i} className="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
                    <div>
                      <span className="text-sm font-medium text-slate-700">{entry.description}</span>
                      <div className="text-xs text-slate-400">{entry.date} - {entry.journal_type}</div>
                    </div>
                    <span className="text-sm font-semibold text-slate-900">{entry.total_debit?.toFixed(2)} EUR</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-400">Aucune ecriture recente</p>
            )}
          </CardContent>
        </Card>

        <Card className="border-slate-200">
          <CardContent className="p-6">
            <h3 className="text-lg font-semibold text-slate-900 mb-4" style={{fontFamily:'Chivo,sans-serif'}}>
              Actions rapides
            </h3>
            <div className="grid grid-cols-2 gap-3">
              {[
                { label: 'Nouveau proprietaire', href: '/owners', color: 'bg-blue-50 text-blue-700 border-blue-200' },
                { label: 'Nouvelle facture', href: '/invoices', color: 'bg-orange-50 text-orange-700 border-orange-200' },
                { label: 'Ecriture comptable', href: '/journals', color: 'bg-green-50 text-green-700 border-green-200' },
                { label: 'Import CODA', href: '/banking', color: 'bg-purple-50 text-purple-700 border-purple-200' },
              ].map((action, i) => (
                <a
                  key={i}
                  href={action.href}
                  className={`${action.color} rounded-md border px-3 py-3 text-sm font-medium text-center hover:opacity-80 transition-opacity`}
                  data-testid={`quick-action-${i}`}
                >
                  {action.label}
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
