// iter91e : Page admin pour dedupliquer les Natures de depense
import { useState, useCallback } from 'react';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Search, Play, Merge, AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';

export default function AdminExpenseCategoriesDedupePage() {
  const [copropriete, setCopropriete] = useState(
    localStorage.getItem('selectedCopro') || localStorage.getItem('copropriete_id') || ''
  );
  const [strategy, setStrategy] = useState('name_normalized');
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  // Selection des sources a fusionner par target: { target_id: Set<source_id> }
  const [selection, setSelection] = useState({});

  const runDryRun = useCallback(async () => {
    if (!copropriete || copropriete === 'all') {
      toast.error("Selectionnez une ACP dans l'en-tete de la plateforme");
      return;
    }
    setLoading(true);
    setReport(null);
    try {
      const { data } = await api.post('/admin/expense-categories/dedupe', {
        copropriete_id: copropriete,
        dry_run: true,
        strategy,
        cleanup_malformed: true,
      });
      setReport(data);
      // Selection par defaut : toutes les sources cochees
      const initSel = {};
      for (const g of data.duplicate_groups || []) {
        initSel[g.target.id] = new Set(g.sources.map((s) => s.id));
      }
      setSelection(initSel);
      toast.success(
        `Analyse OK : ${(data.duplicate_groups || []).length} groupe(s) de doublons, ${(data.malformed || []).length} libelle(s) malforme(s)`
      );
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erreur lors de l'analyse");
    } finally {
      setLoading(false);
    }
  }, [copropriete, strategy]);

  const toggleSource = (targetId, sourceId) => {
    setSelection((prev) => {
      const s = new Set(prev[targetId] || []);
      if (s.has(sourceId)) s.delete(sourceId); else s.add(sourceId);
      return { ...prev, [targetId]: s };
    });
  };

  const runMerge = useCallback(async () => {
    if (!report) return;
    const merges = [];
    for (const g of report.duplicate_groups || []) {
      const sel = Array.from(selection[g.target.id] || []);
      if (sel.length === 0) continue;
      merges.push({ target_id: g.target.id, source_ids: sel });
    }
    if (merges.length === 0) {
      toast.error("Aucune fusion selectionnee");
      return;
    }
    if (!window.confirm(`Executer ${merges.length} fusion(s) ? Cette action est irreversible.`)) return;
    setLoading(true);
    try {
      const { data } = await api.post('/admin/expense-categories/dedupe', {
        copropriete_id: copropriete,
        dry_run: false,
        strategy,
        cleanup_malformed: true,
        merges,
      });
      toast.success(
        `Fusion OK : ${data.merges_done} groupe(s), ${data.categories_deleted} categorie(s) supprimee(s), ${data.entries_updated} ecriture(s) mise(s) a jour`
      );
      // Refresh report
      await runDryRun();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Erreur lors de la fusion");
    } finally {
      setLoading(false);
    }
  }, [report, selection, copropriete, strategy, runDryRun]);

  const totalSourcesSelected = report
    ? Object.values(selection).reduce((n, s) => n + (s?.size || 0), 0)
    : 0;

  return (
    <div className="p-6 space-y-4" data-testid="admin-dedupe-page">
      <div>
        <h1 className="text-2xl font-bold" style={{ fontFamily: 'Chivo, sans-serif' }}>
          Nettoyage des Natures de depense
        </h1>
        <p className="text-sm text-slate-500">
          Detecte et fusionne les Natures dupliquees (accents, casse) et les libelles mal formes sur l&apos;ACP selectionnee.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Etape 1 : Analyse (dry-run)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-[200px]">
              <label className="text-xs text-slate-500">ACP (copropriete_id)</label>
              <input
                className="w-full border rounded px-2 py-1 text-sm font-mono"
                value={copropriete}
                onChange={(e) => setCopropriete(e.target.value)}
                data-testid="dedupe-copro-input"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Strategie</label>
              <select
                className="border rounded px-2 py-1 text-sm"
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                data-testid="dedupe-strategy"
              >
                <option value="name_normalized">Nom normalise (accents ignores)</option>
                <option value="name_account">Nom + Numero de compte</option>
                <option value="name">Nom exact (lowercase)</option>
              </select>
            </div>
            <Button
              onClick={runDryRun}
              disabled={loading || !copropriete}
              className="bg-[#022D52] hover:bg-[#1D4ED8]"
              data-testid="dedupe-run-dryrun-btn"
            >
              {loading ? <RefreshCw size={14} className="mr-2 animate-spin" /> : <Search size={14} className="mr-2" />}
              Analyser
            </Button>
          </div>
        </CardContent>
      </Card>

      {report && (
        <>
          <Card>
            <CardHeader className="pb-3 flex flex-row items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                Doublons detectes
                <Badge variant="outline">{(report.duplicate_groups || []).length} groupe(s)</Badge>
              </CardTitle>
              <div className="text-xs text-slate-500">
                Total categories : <span className="font-semibold">{report.total_categories}</span>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {(report.duplicate_groups || []).length === 0 && (
                <p className="text-sm text-emerald-600 flex items-center gap-2">
                  <CheckCircle2 size={14} /> Aucun doublon detecte sur cette ACP.
                </p>
              )}
              {(report.duplicate_groups || []).map((g) => (
                <div key={g.target.id} className="border rounded-lg p-3 space-y-2 bg-slate-50/50">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1">
                      <div className="text-[10px] uppercase text-slate-400 tracking-wide">Cle : {g.key}</div>
                      <div className="text-sm">
                        <span className="text-emerald-700 font-semibold">Conserver :</span>{' '}
                        <span className="font-mono">{g.target.name}</span>{' '}
                        <Badge variant="outline" className="text-[10px] ml-1">
                          [{g.target.account_number}]
                        </Badge>
                        <Badge variant="outline" className="text-[10px] ml-1 bg-blue-50">
                          {g.target.usages} usage(s)
                        </Badge>
                      </div>
                    </div>
                  </div>
                  <div className="pl-4 space-y-1">
                    {g.sources.map((s) => (
                      <label
                        key={s.id}
                        className="flex items-center gap-2 text-sm hover:bg-white/50 rounded px-2 py-1 cursor-pointer"
                      >
                        <Checkbox
                          checked={(selection[g.target.id] || new Set()).has(s.id)}
                          onCheckedChange={() => toggleSource(g.target.id, s.id)}
                          data-testid={`dedupe-src-${s.id}`}
                        />
                        <span className="text-red-700 line-through font-mono">{s.name}</span>
                        <Badge variant="outline" className="text-[10px]">
                          [{s.account_number}]
                        </Badge>
                        <Badge variant="outline" className="text-[10px] bg-amber-50">
                          {s.usages} usage(s)
                        </Badge>
                        {s.malformed && (
                          <Badge variant="outline" className="text-[10px] bg-red-100 text-red-700 border-red-200">
                            malforme
                          </Badge>
                        )}
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          {(report.malformed || []).length > 0 && (
            <Card className="border-amber-200 bg-amber-50/40">
              <CardHeader className="pb-3">
                <CardTitle className="text-base flex items-center gap-2 text-amber-800">
                  <AlertTriangle size={16} />
                  Libelles malformes
                  <Badge variant="outline">{report.malformed.length}</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                {report.malformed.map((m) => (
                  <div key={m.id} className="font-mono text-xs">
                    <span className="text-amber-800 font-semibold">&quot;{m.name}&quot;</span>{' '}
                    <span className="text-slate-500">[{m.account_number}]</span>{' '}
                    <span className="text-slate-400">{m.usages} usage(s)</span>
                  </div>
                ))}
                <p className="text-[11px] text-slate-500 mt-2">
                  Les libelles malformes peuvent etre renommes ou fusionnes via l&apos;interface Comptabilite &gt; Natures de depense.
                </p>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Etape 2 : Fusion</CardTitle>
            </CardHeader>
            <CardContent className="flex items-center gap-3 flex-wrap">
              <div className="text-sm">
                <span className="font-semibold">{totalSourcesSelected}</span> source(s) selectionnee(s) a fusionner
              </div>
              <Button
                onClick={runMerge}
                disabled={loading || totalSourcesSelected === 0}
                className="bg-emerald-700 hover:bg-emerald-800"
                data-testid="dedupe-run-merge-btn"
              >
                {loading ? <RefreshCw size={14} className="mr-2 animate-spin" /> : <Merge size={14} className="mr-2" />}
                Executer la fusion
              </Button>
              <p className="text-[11px] text-slate-500 flex-1 min-w-[300px]">
                Les Natures selectionnees seront supprimees, et toutes les ecritures les referencant seront reaffectees a la Nature conservee. Action <b>irreversible</b>.
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
