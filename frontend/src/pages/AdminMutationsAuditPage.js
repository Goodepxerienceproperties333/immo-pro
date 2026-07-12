import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AlertTriangle, RefreshCw, Wrench, ShieldCheck, FileText, Link2 } from 'lucide-react';

/**
 * iter90dx - Admin : detection et reparation des mutations avec from/to inverses.
 *
 * Cas d'usage : un promoteur (ex Matexi) est enregistre par erreur comme
 * ACHETEUR au lieu de VENDEUR dans certaines mutations, produisant des
 * ODs comptables dans le mauvais sens.
 */
export default function AdminMutationsAuditPage() {
  const [coproprietes, setCoproprietes] = useState([]);
  const [selectedCopro, setSelectedCopro] = useState('');
  const [owners, setOwners] = useState([]);
  const [founderId, setFounderId] = useState('');
  const [scanResult, setScanResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [fixingIds, setFixingIds] = useState(new Set());
  const [fixResults, setFixResults] = useState({});
  // iter90dz : reparation invoices phantom distribution_lines
  const [invoiceRepairLoading, setInvoiceRepairLoading] = useState(false);
  const [invoiceRepairReport, setInvoiceRepairReport] = useState(null);
  // iter90eh : reparation lettrages bancaires historiques
  const [lettrageRepairLoading, setLettrageRepairLoading] = useState(false);
  const [lettrageRepairReport, setLettrageRepairReport] = useState(null);

  const loadCopros = useCallback(async () => {
    try {
      const { data } = await api.get('/coproprietes');
      setCoproprietes(data || []);
    } catch (err) {
      toast.error('Erreur chargement ACPs');
    }
  }, []);

  const loadOwners = useCallback(async () => {
    if (!selectedCopro) { setOwners([]); return; }
    try {
      const { data } = await api.get('/owners');
      const filtered = (data || []).filter(o =>
        (o.copropriete_ids || []).includes(selectedCopro)
      );
      setOwners(filtered);
    } catch (err) {
      /* silent */
    }
  }, [selectedCopro]);

  useEffect(() => { loadCopros(); }, [loadCopros]);
  useEffect(() => { loadOwners(); }, [loadOwners]);

  const runScan = async () => {
    setLoading(true);
    setScanResult(null);
    setFixResults({});
    try {
      const params = {};
      if (selectedCopro) params.copropriete_id = selectedCopro;
      if (founderId) params.founder_owner_id = founderId;
      const { data } = await api.get('/mutations/detect-inversions', { params });
      setScanResult(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur scan');
    } finally {
      setLoading(false);
    }
  };

  const applyFix = async (mutationId) => {
    if (!window.confirm('Confirmer la reparation de cette mutation ? Les ODs comptables seront contre-passees.')) return;
    setFixingIds(prev => new Set(prev).add(mutationId));
    try {
      const { data } = await api.post(
        `/mutations/${mutationId}/fix-inversion`,
        {},
        { params: { dry_run: false } },
      );
      setFixResults(prev => ({ ...prev, [mutationId]: data }));
      toast.success(`Mutation reparee : ${data.reversed_ods_count} OD(s) contre-passee(s)`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur reparation');
    } finally {
      setFixingIds(prev => {
        const s = new Set(prev);
        s.delete(mutationId);
        return s;
      });
    }
  };

  const dryRunFix = async (mutationId) => {
    setFixingIds(prev => new Set(prev).add(mutationId));
    try {
      const { data } = await api.post(
        `/mutations/${mutationId}/fix-inversion`,
        {},
        { params: { dry_run: true } },
      );
      setFixResults(prev => ({ ...prev, [mutationId]: data }));
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur simulation');
    } finally {
      setFixingIds(prev => {
        const s = new Set(prev);
        s.delete(mutationId);
        return s;
      });
    }
  };

  // iter90dz : reparation retroactive des invoice.distribution_lines
  const runInvoiceRepair = async (dryRun) => {
    setInvoiceRepairLoading(true);
    if (dryRun) setInvoiceRepairReport(null);
    try {
      const payload = { dry_run: dryRun };
      if (selectedCopro) payload.copropriete_id = selectedCopro;
      const { data } = await api.post(
        '/invoices/repair-phantom-distribution-lines',
        payload,
      );
      setInvoiceRepairReport(data);
      if (!dryRun) {
        toast.success(
          `${data.invoices_repaired} facture(s) reparee(s) - ${data.lines_repaired_total} distribution_line(s) rebindee(s)`,
          { duration: 6000 }
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur reparation factures');
    } finally {
      setInvoiceRepairLoading(false);
    }
  };

  // iter90eh : reparation retroactive des lettrages historiques
  const runLettrageRepair = async () => {
    setLettrageRepairLoading(true);
    try {
      const payload = selectedCopro ? { copropriete_id: selectedCopro } : {};
      const { data } = await api.post('/banking/repair-legacy-lettrages', null, {
        params: payload,
      });
      setLettrageRepairReport(data);
      toast.success(
        `${data.transactions_repaired} transaction(s) reconciliee(s) (${data.transactions_already_ok} deja OK)`,
        { duration: 6000 }
      );
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur reparation lettrages');
    } finally {
      setLettrageRepairLoading(false);
    }
  };

  return (
    <div className="space-y-6 p-6 max-w-6xl">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
          <ShieldCheck size={22} className="text-[#02A9AA]" />
          Audit des mutations
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Detecte et repare les mutations dont le sens (vendeur / acheteur) est inverse en base.
          Utile apres imports Optipro ou saisies UI erronees.
        </p>
      </div>

      <Card className="p-5 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-500 mb-1 block">
              Copropriete
            </Label>
            <select
              value={selectedCopro}
              onChange={(e) => setSelectedCopro(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
              data-testid="audit-copro-select"
            >
              <option value="">Toutes les ACPs</option>
              {coproprietes.map(c => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-500 mb-1 block">
              Founder / promoteur (heuristique)
            </Label>
            <select
              value={founderId}
              onChange={(e) => setFounderId(e.target.value)}
              disabled={!selectedCopro}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm disabled:bg-slate-100"
              data-testid="audit-founder-select"
            >
              <option value="">Aucun (chaine ownership only)</option>
              {owners.map(o => (
                <option key={o.id} value={o.id}>{o.name}</option>
              ))}
            </select>
            <p className="text-[11px] text-slate-500 mt-1">
              Si defini, toute mutation ou ce proprietaire est ACHETEUR sera marquee suspect.
            </p>
          </div>
        </div>
        <Button
          onClick={runScan}
          disabled={loading}
          className="bg-[#022D52] hover:bg-[#02A9AA] text-white"
          data-testid="audit-scan-btn"
        >
          <RefreshCw size={16} className={`mr-2 ${loading ? 'animate-spin' : ''}`} />
          {loading ? 'Analyse...' : 'Analyser les mutations'}
        </Button>
      </Card>

      {scanResult && (
        <Card className="p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className={`p-2 rounded-full ${scanResult.suspects_count > 0 ? 'bg-amber-100' : 'bg-emerald-100'}`}>
              <AlertTriangle size={18} className={scanResult.suspects_count > 0 ? 'text-amber-700' : 'text-emerald-700'} />
            </div>
            <div>
              <div className="text-lg font-semibold text-slate-900">
                {scanResult.suspects_count} mutation(s) suspecte(s) sur {scanResult.total_mutations_scanned} scannee(s)
              </div>
              <div className="text-sm text-slate-500">
                {scanResult.auto_fixable_count} auto-reparable(s) (from/to inverse detecte)
              </div>
            </div>
          </div>

          {scanResult.suspects_count === 0 ? (
            <div className="bg-emerald-50 border border-emerald-200 rounded p-3 text-sm text-emerald-800">
              Aucune anomalie detectee. Les mutations sont coherentes.
            </div>
          ) : (
            <div className="space-y-3">
              {scanResult.suspects.map((s) => {
                const isFixing = fixingIds.has(s.mutation_id);
                const result = fixResults[s.mutation_id];
                const isFixed = result && result.applied;
                return (
                  <div
                    key={s.mutation_id}
                    className={`border rounded p-3 ${isFixed ? 'bg-emerald-50 border-emerald-300' : 'bg-white border-amber-300'}`}
                    data-testid={`audit-suspect-${s.mutation_id}`}
                  >
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
                      <div>
                        <div className="text-xs text-slate-500">Lot</div>
                        <div className="font-mono font-semibold">{s.lot_number || s.lot_id.slice(0, 8)}</div>
                        <div className="text-xs text-slate-500 mt-1">{s.sale_date}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-500">FROM (declare)</div>
                        <div className="font-medium">{s.declared_from_name}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-500">TO (declare)</div>
                        <div className="font-medium">{s.declared_to_name}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-500">Type</div>
                        <div className={`font-medium text-xs ${s.kind === 'from_to_swapped' ? 'text-amber-800' : 'text-red-800'}`}>
                          {s.kind}
                        </div>
                        {s.reason && (
                          <div className="text-[10px] text-slate-500 mt-1 italic">{s.reason}</div>
                        )}
                      </div>
                    </div>

                    {result && (
                      <div className="mt-3 bg-slate-50 rounded p-2 text-xs">
                        <div className="font-semibold text-slate-700 mb-1">
                          {result.applied ? 'Repare' : 'Simulation'} :
                        </div>
                        <div>Swap FROM {result.swap.from.before_id.slice(0, 8)} -&gt; {result.swap.from.after_id.slice(0, 8)}</div>
                        <div>OD(s) a contre-passer : {result.od_entries_to_reverse?.length || 0}</div>
                        {result.reversed_ods_count !== undefined && (
                          <div className="text-emerald-700 font-semibold mt-1">
                            {result.reversed_ods_count} OD(s) contre-passee(s)
                          </div>
                        )}
                      </div>
                    )}

                    <div className="mt-3 flex gap-2">
                      {!isFixed && s.auto_fixable && (
                        <>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => dryRunFix(s.mutation_id)}
                            disabled={isFixing}
                            data-testid={`audit-dryrun-${s.mutation_id}`}
                          >
                            Simuler
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => applyFix(s.mutation_id)}
                            disabled={isFixing}
                            className="bg-amber-600 hover:bg-amber-700 text-white"
                            data-testid={`audit-fix-${s.mutation_id}`}
                          >
                            <Wrench size={13} className="mr-1" />
                            {isFixing ? 'Reparation...' : 'Reparer (swap + reverse OD)'}
                          </Button>
                        </>
                      )}
                      {!s.auto_fixable && (
                        <div className="text-xs text-red-700 italic">
                          Anomalie non auto-reparable. Verifier manuellement la sequence de mutations.
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}

      {/* iter90dz : Reparation retroactive des invoice.distribution_lines phantoms */}
      <Card className="p-5 space-y-4 border-amber-200">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
              <FileText size={18} className="text-amber-600" />
              Reparation retroactive des factures (distribution_lines phantoms)
            </h2>
            <p className="text-xs text-slate-500 mt-1 max-w-2xl">
              Re-mappe les <code className="bg-slate-100 px-1 rounded">lot_id</code> perimes
              des factures vers les lot_ids actuels par matching sur
              <code className="bg-slate-100 px-1 rounded mx-1">lot_number</code>.
              Le fallback runtime (iter90dz) fonctionne deja pour l&apos;affichage,
              ceci nettoie la base definitivement.
              {selectedCopro ? '' : ' (Portee : toutes les ACPs si aucune n\'est selectionnee ci-dessus.)'}
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => runInvoiceRepair(true)}
              disabled={invoiceRepairLoading}
              data-testid="invoice-repair-dryrun-btn"
            >
              <RefreshCw size={14} className={`mr-2 ${invoiceRepairLoading ? 'animate-spin' : ''}`} />
              Simuler
            </Button>
            <Button
              onClick={() => runInvoiceRepair(false)}
              disabled={invoiceRepairLoading || !invoiceRepairReport || invoiceRepairReport.invoices_with_phantoms === 0}
              className="bg-amber-600 hover:bg-amber-700 text-white"
              data-testid="invoice-repair-apply-btn"
            >
              <Wrench size={14} className="mr-2" />
              Reparer
            </Button>
          </div>
        </div>
        {invoiceRepairReport && (
          <div className="border border-slate-200 rounded p-3 bg-slate-50">
            <div className="grid grid-cols-4 gap-2 text-xs mb-2">
              <div>
                <div className="text-slate-500">Scannees</div>
                <div className="text-lg font-bold text-slate-900">{invoiceRepairReport.invoices_scanned}</div>
              </div>
              <div>
                <div className="text-slate-500">Avec phantoms</div>
                <div className={`text-lg font-bold ${invoiceRepairReport.invoices_with_phantoms > 0 ? 'text-amber-700' : 'text-emerald-700'}`}>
                  {invoiceRepairReport.invoices_with_phantoms}
                </div>
              </div>
              <div>
                <div className="text-slate-500">Reparees</div>
                <div className="text-lg font-bold text-emerald-700">{invoiceRepairReport.invoices_repaired}</div>
              </div>
              <div>
                <div className="text-slate-500">Lines rebindees</div>
                <div className="text-lg font-bold text-emerald-700">{invoiceRepairReport.lines_repaired_total}</div>
              </div>
            </div>
            {invoiceRepairReport.details && invoiceRepairReport.details.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer font-medium text-slate-700">
                  Voir les {invoiceRepairReport.details.length} facture(s) impactee(s)
                </summary>
                <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
                  {invoiceRepairReport.details.slice(0, 100).map(d => (
                    <div key={d.invoice_id} className="flex items-center justify-between bg-white rounded px-2 py-1">
                      <span className="font-mono text-slate-700">{d.invoice_number || d.invoice_id.slice(0, 8)}</span>
                      <span className="text-slate-600">
                        {d.phantom_count} phantoms
                        {d.resolved_count > 0 && <span className="text-emerald-700 ml-2">+ {d.resolved_count} resolus</span>}
                        {d.unresolvable_count > 0 && <span className="text-red-600 ml-2">/ {d.unresolvable_count} irresolus</span>}
                        {d.applied && <span className="text-emerald-700 ml-2 font-semibold">OK</span>}
                      </span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </Card>

      {/* iter90eh : Reparation lettrages bancaires historiques */}
      <Card className="p-5 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
              <Link2 size={18} className="text-emerald-600" />
              Reparation lettrages bancaires historiques
            </h2>
            <p className="text-xs text-slate-500 mt-1 max-w-2xl">
              Reconcilie les <code className="bg-slate-100 px-1 rounded">bank_transactions</code>
              &nbsp;dont le champ <code className="bg-slate-100 px-1 rounded">matched</code> n&apos;est pas
              correctement rempli alors que la facture est marquee payee via
              <code className="bg-slate-100 px-1 rounded mx-1">paid_by_transaction_id</code>.
              Necessaire pour les lettrages effectues avant iter90eb ou importes depuis Optipro/CODA.
              {selectedCopro ? '' : ' (Portee : toutes les ACPs si aucune n\'est selectionnee ci-dessus.)'}
            </p>
          </div>
          <Button
            onClick={runLettrageRepair}
            disabled={lettrageRepairLoading}
            className="bg-emerald-600 hover:bg-emerald-700 text-white shrink-0"
            data-testid="lettrage-repair-btn"
          >
            <Wrench size={14} className={`mr-2 ${lettrageRepairLoading ? 'animate-spin' : ''}`} />
            {lettrageRepairLoading ? 'Reparation...' : 'Reparer les lettrages'}
          </Button>
        </div>
        {lettrageRepairReport && (
          <div className="border border-slate-200 rounded p-3 bg-slate-50" data-testid="lettrage-repair-report">
            <div className="grid grid-cols-4 gap-2 text-xs">
              <div>
                <div className="text-slate-500">Factures scannees</div>
                <div className="text-lg font-bold text-slate-900">{lettrageRepairReport.invoices_scanned}</div>
              </div>
              <div>
                <div className="text-slate-500">Transactions reparees</div>
                <div className="text-lg font-bold text-emerald-700">{lettrageRepairReport.transactions_repaired}</div>
              </div>
              <div>
                <div className="text-slate-500">Deja OK</div>
                <div className="text-lg font-bold text-slate-700">{lettrageRepairReport.transactions_already_ok}</div>
              </div>
              <div>
                <div className="text-slate-500">Manquantes</div>
                <div className={`text-lg font-bold ${lettrageRepairReport.transactions_missing > 0 ? 'text-amber-700' : 'text-slate-700'}`}>
                  {lettrageRepairReport.transactions_missing}
                </div>
              </div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
