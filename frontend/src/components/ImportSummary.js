import { CheckCircle2, XCircle, AlertTriangle, Building2, Wallet, Users, Truck, PieChart, Tag, FileText, ScrollText, Scale } from 'lucide-react';

/**
 * iter90if - Recapitulatif de l'import d'une ACP.
 *
 * Affiche de maniere compacte et rassurante ce qui a ete cree pour l'ACP :
 * exercice, budget, lots, cles, natures, fournisseurs, factures, OD ouverture.
 * Utilise :
 *   - en fin de wizard (avant redirection) via <ImportSummary summary={...} />
 *   - depuis un banner "Import incomplet" sur la liste des ACP
 *
 * Props :
 *   summary : reponse JSON de GET /api/import-wizard/coproprietes/{id}/import-summary
 *   compact : bool (default false) - version reduite pour banner
 */
export default function ImportSummary({ summary, compact = false }) {
  if (!summary) return null;
  const { counts = {}, budget, active_fiscal_year: fy, opening_od_entries: openingOD, missing = [] } = summary;

  const rows = [
    { icon: Building2, label: 'Exercice fiscal', value: fy ? `${fy.name} (${fy.status})` : null, ok: !!fy, critical: true },
    { icon: Users, label: 'Lots', value: counts.lots, ok: counts.lots > 0, critical: true },
    { icon: PieChart, label: 'Cles de repartition', value: counts.distribution_keys, ok: counts.distribution_keys > 0, critical: true },
    { icon: Tag, label: 'Natures de depense', value: counts.natures, ok: counts.natures > 0, critical: false },
    { icon: Wallet, label: 'Budget previsionnel', value: budget ? `${budget.lines_count} lignes - ${Number(budget.total_amount).toFixed(2)} EUR` : null, ok: !!budget, critical: false },
    { icon: Truck, label: 'Fournisseurs', value: counts.suppliers, ok: counts.suppliers > 0, critical: false },
    { icon: FileText, label: 'Factures importees', value: counts.invoices, ok: counts.invoices > 0, critical: false },
    { icon: Scale, label: "OD d'ouverture", value: openingOD, ok: openingOD > 0, critical: false },
    { icon: Users, label: 'Coproprietaires', value: counts.owners, ok: counts.owners > 0, critical: false },
  ];

  if (compact) {
    return (
      <div className="flex flex-wrap gap-2 text-xs" data-testid="import-summary-compact">
        {rows.filter(r => r.ok || r.critical).map((r, i) => {
          const Icon = r.icon;
          const color = r.ok ? 'bg-emerald-50 text-emerald-800 border-emerald-200' :
                                (r.critical ? 'bg-red-50 text-red-800 border-red-200' :
                                              'bg-amber-50 text-amber-800 border-amber-200');
          return (
            <span key={i} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border ${color}`}>
              <Icon size={11} />
              {r.label}: <b>{r.value ?? (r.ok ? 'OK' : 'Manquant')}</b>
            </span>
          );
        })}
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="import-summary">
      <div className="text-sm text-slate-600 leading-relaxed">
        Voici ce qui a ete cree pour cette ACP. Une case verte indique que la donnee est presente.
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {rows.map((r, i) => {
          const Icon = r.icon;
          const StatusIcon = r.ok ? CheckCircle2 : (r.critical ? XCircle : AlertTriangle);
          const border = r.ok ? 'border-emerald-200 bg-emerald-50/50' :
                                (r.critical ? 'border-red-200 bg-red-50/50' : 'border-amber-200 bg-amber-50/50');
          const statusColor = r.ok ? 'text-emerald-600' : (r.critical ? 'text-red-500' : 'text-amber-500');
          return (
            <div key={i} className={`flex items-center gap-2 p-2.5 rounded-md border ${border}`} data-testid={`summary-row-${r.label.toLowerCase().replace(/[^a-z]/g,'-')}`}>
              <Icon size={16} className="text-slate-600 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-slate-700">{r.label}</div>
                <div className="text-sm font-bold text-slate-900 truncate">
                  {r.value === null || r.value === 0 ? (r.critical ? 'Manquant' : '-') : r.value}
                </div>
              </div>
              <StatusIcon size={18} className={`${statusColor} flex-shrink-0`} />
            </div>
          );
        })}
      </div>
      {missing.length > 0 && (
        <div className="mt-3 p-3 rounded-md border border-amber-300 bg-amber-50 text-xs text-amber-900" data-testid="import-summary-missing">
          <div className="flex items-center gap-2 font-semibold mb-1">
            <ScrollText size={14} /> Elements manquants ou incomplets :
          </div>
          <ul className="list-disc list-inside ml-1 space-y-0.5">
            {missing.map((m, i) => (
              <li key={i}>
                <b>{m.label}</b>
                {m.critical && <span className="ml-1 inline-block bg-red-100 text-red-800 text-[10px] font-bold rounded px-1">critique</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
