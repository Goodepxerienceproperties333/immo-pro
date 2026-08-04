import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import {
  Activity, Play, Trash2, CheckCircle2, XCircle, AlertTriangle, Loader2,
  Building2, RefreshCw, ChevronDown, ChevronRight,
} from 'lucide-react';

/**
 * Page Superadmin - Agent de tests E2E.
 * Permet de lancer un test complet de la plateforme :
 * seeding d'une ACP fictive + scenarios comptables + assertions
 * transverses. Ideal apres chaque deploiement/refactoring pour smoke test.
 */
export default function AdminE2ETestPage() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [currentRun, setCurrentRun] = useState(null);
  const [expandedRun, setExpandedRun] = useState(null);

  const loadHistory = async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/admin/e2e-test/history');
      setRuns(data.runs || []);
    } catch (e) {
      toast.error('Impossible de charger l\'historique');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadHistory(); }, []);

  const runTest = async (mode) => {
    if (running) return;
    setRunning(true);
    setCurrentRun(null);
    const modeLabel = mode === 'with_optipro' ? 'avec Optipro' : 'sans Optipro';
    toast.info(`Lancement du test E2E ${modeLabel}...`);
    try {
      const { data } = await api.post('/admin/e2e-test/run', { mode });
      setCurrentRun(data);
      const s = data.summary || {};
      if (s.success) {
        toast.success(`${s.passed}/${s.total} verifications OK (${data.duration_ms}ms)`,
          { duration: 6000 });
      } else {
        toast.error(`${s.failed} echec(s), ${s.errors} erreur(s) sur ${s.total}`,
          { duration: 10000 });
      }
      await loadHistory();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur pendant l\'execution');
    } finally {
      setRunning(false);
    }
  };

  const purge = async (acpId) => {
    if (!window.confirm('Purger definitivement cette ACP de test et toutes ses donnees ?')) return;
    try {
      const { data } = await api.post(`/admin/e2e-test/purge/${acpId}`);
      const deletedCount = Object.values(data.deleted || {}).reduce((s, n) => s + n, 0);
      toast.success(`${deletedCount} enregistrement(s) purge(s)`);
      await loadHistory();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur purge');
    }
  };

  const StatusIcon = ({ status }) => {
    if (status === 'PASS') return <CheckCircle2 size={14} className="text-emerald-600" />;
    if (status === 'FAIL') return <XCircle size={14} className="text-red-600" />;
    return <AlertTriangle size={14} className="text-amber-600" />;
  };

  const renderRunSteps = (run) => (
    <div className="mt-3 space-y-1">
      {(run.steps || []).map((s, i) => (
        <div key={i}
          className={`flex items-start gap-2 px-3 py-1.5 rounded text-xs border ${
            s.status === 'PASS' ? 'bg-emerald-50/60 border-emerald-100'
              : s.status === 'FAIL' ? 'bg-red-50 border-red-200'
              : 'bg-amber-50 border-amber-200'
          }`}
          data-testid={`e2e-step-${s.step}`}
        >
          <StatusIcon status={s.status} />
          <div className="flex-1">
            <div className="font-mono text-[10px] text-slate-500">{s.step}</div>
            <div className="text-slate-700">{s.message}</div>
            {s.trace && (
              <pre className="text-[9px] text-red-800 mt-1 whitespace-pre-wrap">{s.trace}</pre>
            )}
          </div>
        </div>
      ))}
    </div>
  );

  return (
    <div className="p-4 sm:p-6 space-y-6">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Activity size={22} className="text-amber-600" />
            Agent de tests E2E
          </h1>
          <p className="page-subtitle max-w-3xl">
            Cree une ACP fictive complete (proprietaires, lots, fournisseurs, factures, extraits bancaires)
            puis execute des scenarios comptables reels pour verifier le bon fonctionnement de bout en bout.
            Utilisez apres chaque deploiement pour un smoke test complet.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={loadHistory} disabled={loading} data-testid="e2e-refresh">
          <RefreshCw size={14} className={`mr-1 ${loading ? 'animate-spin' : ''}`} /> Actualiser
        </Button>
      </div>

      {/* Panneau de lancement */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card className="border-emerald-200 bg-emerald-50/40">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Building2 size={18} className="text-emerald-700" />
              Test sans Optipro
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-slate-600">
              Flux manuel : creation directe d&apos;une ACP + proprietaires + lots + factures
              + extraits bancaires + assertions comptables (AC, FI, bilan equilibre, verrous).
            </p>
            <Button
              className="bg-emerald-600 hover:bg-emerald-700 text-white w-full"
              onClick={() => runTest('without_optipro')}
              disabled={running}
              data-testid="e2e-run-without-optipro"
            >
              {running ? <Loader2 size={16} className="mr-2 animate-spin" /> : <Play size={16} className="mr-2" />}
              Lancer sans Optipro
            </Button>
          </CardContent>
        </Card>

        <Card className="border-[#01213e]/20 bg-blue-50/40">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Building2 size={18} className="text-[#01213e]" />
              Test avec import Optipro
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-slate-600">
              Meme flux + import simule Optipro (categories, budget, factures) et verification
              de l&apos;idempotence (aucun doublon de categorie apres import).
            </p>
            <Button
              className="bg-[#022D52] hover:bg-[#1D4ED8] text-white w-full"
              onClick={() => runTest('with_optipro')}
              disabled={running}
              data-testid="e2e-run-with-optipro"
            >
              {running ? <Loader2 size={16} className="mr-2 animate-spin" /> : <Play size={16} className="mr-2" />}
              Lancer avec Optipro
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Resultat du dernier run */}
      {currentRun && (
        <Card className="border-2 border-amber-300 shadow-md">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Activity size={18} />
                Dernier run : {currentRun.acp_name}
                <Badge variant="outline" className="text-[10px]">{currentRun.mode}</Badge>
              </CardTitle>
              <div className="flex items-center gap-2">
                {currentRun.summary?.success ? (
                  <Badge className="bg-emerald-600">
                    <CheckCircle2 size={11} className="mr-1" /> Succes {currentRun.summary.passed}/{currentRun.summary.total}
                  </Badge>
                ) : (
                  <Badge className="bg-red-600">
                    <XCircle size={11} className="mr-1" /> Echec {currentRun.summary?.failed || 0}/{currentRun.summary?.total || 0}
                  </Badge>
                )}
                <span className="text-xs text-slate-500">{currentRun.duration_ms} ms</span>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {renderRunSteps(currentRun)}
            <div className="mt-3 flex justify-end">
              <Button
                variant="outline" size="sm"
                className="text-red-600 hover:bg-red-50"
                onClick={() => purge(currentRun.acp_id)}
                data-testid="e2e-purge-current"
              >
                <Trash2 size={13} className="mr-1" /> Purger cette ACP de test
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Historique */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Historique des runs ({runs.length})</CardTitle>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <div className="text-sm text-slate-500 italic text-center py-6">
              Aucun run pour l&apos;instant. Lancez un test ci-dessus.
            </div>
          ) : (
            <div className="space-y-2">
              {runs.map(run => {
                const isExpanded = expandedRun === run.id;
                const s = run.summary || {};
                return (
                  <div key={run.id} className="border border-slate-200 rounded-lg overflow-hidden">
                    <button
                      className="w-full flex items-center gap-3 px-3 py-2 hover:bg-slate-50 text-left"
                      onClick={() => setExpandedRun(isExpanded ? null : run.id)}
                      data-testid={`e2e-history-row-${run.id}`}
                    >
                      {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {s.success ? (
                        <CheckCircle2 size={16} className="text-emerald-600" />
                      ) : (
                        <XCircle size={16} className="text-red-600" />
                      )}
                      <div className="flex-1">
                        <div className="text-xs font-semibold">{run.acp_name}</div>
                        <div className="text-[10px] text-slate-500">
                          {run.mode} · {new Date(run.started_at).toLocaleString('fr-BE')}
                          {' · '}{run.duration_ms} ms · {s.passed}/{s.total} OK
                          {s.failed > 0 && ` · ${s.failed} FAIL`}
                        </div>
                      </div>
                      <Button
                        variant="ghost" size="sm"
                        className="text-red-600 hover:bg-red-50 h-7 px-2"
                        onClick={(e) => { e.stopPropagation(); purge(run.acp_id); }}
                        data-testid={`e2e-purge-${run.id}`}
                      >
                        <Trash2 size={12} />
                      </Button>
                    </button>
                    {isExpanded && (
                      <div className="px-3 pb-3">
                        {renderRunSteps(run)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
