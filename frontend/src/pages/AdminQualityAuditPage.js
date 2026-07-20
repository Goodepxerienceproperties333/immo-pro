/*
 * iter90gk : Page admin "Quality Audit" - visualise le rapport
 * /api/admin/duplicates-audit avec actions rapides par ligne.
 *
 * Sections :
 *  - Fournisseurs : BCE dupliques, sans BCE (top 10), noms dupliques
 *  - Proprietaires : email/telephone dupliques
 *  - PCMN : tier accounts orphelins, comptes bancaires dupliques
 *  - Notes de Credit sans ecriture AC
 * Export CSV disponible.
 */
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { AlertTriangle, CheckCircle2, RefreshCw, Download, Users, Truck, FileWarning, Landmark, FileText, HardDrive, Play } from 'lucide-react';
import api from '@/lib/api';

const SEV_COLOR = {
  ok: 'bg-emerald-50 border-emerald-200 text-emerald-900',
  warn: 'bg-yellow-50 border-yellow-200 text-yellow-900',
  err: 'bg-red-50 border-red-200 text-red-900',
};

function Section({ icon: Icon, title, count, children, severity = 'ok' }) {
  return (
    <Card className={`${SEV_COLOR[severity]} border`} data-testid={`section-${title.toLowerCase().replace(/\s+/g,'-')}`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Icon size={16} />
            {title}
          </span>
          <Badge className={severity === 'ok' ? 'bg-emerald-600' : (severity === 'warn' ? 'bg-yellow-600' : 'bg-red-600')}>
            {count} {count === 1 ? 'entree' : 'entrees'}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default function AdminQualityAuditPage() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [copros, setCopros] = useState([]);
  const [coproFilter, setCoproFilter] = useState('all');

  const loadCopros = async () => {
    try {
      const { data } = await api.get('/coproprietes');
      setCopros(data || []);
    } catch { /* silent */ }
  };
  const load = async (opts = {}) => {
    setLoading(true);
    try {
      const params = coproFilter !== 'all' ? { copro_id: coproFilter } : {};
      if (opts.force) params.force_refresh = true;
      const { data } = await api.get('/admin/duplicates-audit', { params });
      setReport(data);
      if (opts.force) toast.success('Rapport regenere depuis la DB');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement rapport');
    } finally {
      setLoading(false);
    }
  };
  const downloadCsv = () => {
    const params = coproFilter !== 'all' ? `&copro_id=${coproFilter}` : '';
    window.open(`${process.env.REACT_APP_BACKEND_URL}/api/admin/duplicates-audit?format=csv${params}`, '_blank');
  };
  const goToSupplier = (id) => window.open(`/suppliers?highlight=${id}`, '_blank');
  const goToOwner = (id) => window.open(`/owners?highlight=${id}`, '_blank');

  useEffect(() => { loadCopros(); load(); /* eslint-disable-next-line */ }, []);

  const s = report?.summary || {};
  const healthy = report?.healthy === true;

  // iter90i1 : Migration GridFS des uploads (superadmin only)
  const [gridfsBusy, setGridfsBusy] = useState(false);
  const [gridfsResult, setGridfsResult] = useState(null);
  // iter90i6 : Backfill notes de credit sans ecriture AC
  const [ncBusy, setNcBusy] = useState(false);
  const [ncResult, setNcResult] = useState(null);
  // iter90i7 : Nettoyage doublons AP/VE (fonds de reserve/roulement)
  const [dupBusy, setDupBusy] = useState(false);
  const [dupResult, setDupResult] = useState(null);
  // iter90ig : Guerison comptes Optipro pollues (owners avec 4100XXX au lieu de 4101XXXX)
  const [optiproBusy, setOptiproBusy] = useState(false);
  const [optiproResult, setOptiproResult] = useState(null);
  const [optiproCopro, setOptiproCopro] = useState('');
  // iter90ih : Deduplication natures + PCMN
  const [dedupNatBusy, setDedupNatBusy] = useState(false);
  const [dedupNatResult, setDedupNatResult] = useState(null);
  const [dedupPcmnBusy, setDedupPcmnBusy] = useState(false);
  const [dedupPcmnResult, setDedupPcmnResult] = useState(null);
  const [dedupCopro, setDedupCopro] = useState('all');
  // iter90ii : Deduplication owners + suppliers
  const [dedupOwnBusy, setDedupOwnBusy] = useState(false);
  const [dedupOwnResult, setDedupOwnResult] = useState(null);
  const [dedupSupBusy, setDedupSupBusy] = useState(false);
  const [dedupSupResult, setDedupSupResult] = useState(null);
  const runDedupOwners = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "Fusionner les proprietaires dupliques ?\n\n" +
      "* Regroupe par code auxiliaire GLOBAL (cross-ACP).\n" +
      "* Le PLUS ANCIEN est garde et herite de TOUTES les ACPs des doublons.\n" +
      "* Lots + tenants + journal_entries repointes vers le survivant.\n" +
      "* Nettoie aussi les tier_accounts orphelins (ACP non presente dans copropriete_ids).\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setDedupOwnBusy(true);
    setDedupOwnResult(null);
    try {
      // iter90ij : mode cross_acp par defaut - fusionne un meme aux_code sur toutes les ACPs
      const q = dedupCopro !== 'all' ? `copropriete_id=${dedupCopro}&` : '';
      const { data } = await api.post(`/admin/heal-duplicate-owners?cross_acp=true&${q}dry_run=${dryRun}`);
      setDedupOwnResult(data);
      if (dryRun) toast.info(`Dry-run : ${data.duplicate_groups} groupe(s) a fusionner`, { duration: 6000 });
      else toast.success(`${data.deleted_owners} owner(s) supprimes, ${data.lots_repointed} lots repointes, ${data.orphan_tier_accounts_cleaned} tier_accounts orphelins nettoyes`, { duration: 8000 });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur dedup owners');
    } finally { setDedupOwnBusy(false); }
  };
  const runDedupSuppliers = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "Fusionner les fournisseurs dupliques ?\n\n" +
      "* Regroupe par (ACP, nom normalise).\n" +
      "* Le PLUS ANCIEN est garde, les autres sont supprimes.\n" +
      "* Factures + journal_entries repointees vers le survivant.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setDedupSupBusy(true);
    setDedupSupResult(null);
    try {
      const q = dedupCopro !== 'all' ? `copropriete_id=${dedupCopro}&` : '';
      const { data } = await api.post(`/admin/heal-duplicate-suppliers?${q}dry_run=${dryRun}`);
      setDedupSupResult(data);
      if (dryRun) toast.info(`Dry-run : ${data.duplicate_groups} groupe(s) a fusionner`, { duration: 6000 });
      else toast.success(`${data.deleted_suppliers} fournisseur(s) supprimes, ${data.invoices_repointed} factures repointees`, { duration: 8000 });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur dedup suppliers');
    } finally { setDedupSupBusy(false); }
  };
  // iter90im : Suppression definitive des orphelins sans transactions
  const [purgeBusy, setPurgeBusy] = useState(false);
  const [purgeResult, setPurgeResult] = useState(null);
  const runPurgeOrphans = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "SUPPRESSION DEFINITIVE des fiches orphelines ?\n\n" +
      "* Uniquement les fiches SANS transactions liees (JE + lots + factures + tenants).\n" +
      "* Ces fiches sont des artefacts d'import defectueux sans impact business.\n" +
      "* Idempotent - re-executable sans risque.\n\n" +
      "Recommande : lancer d'abord un DRY-RUN pour verifier.\n\n" +
      "Confirmez pour lancer la SUPPRESSION.",
    )) return;
    setPurgeBusy(true);
    setPurgeResult(null);
    try {
      const q = dedupCopro !== 'all' ? `copropriete_id=${dedupCopro}&` : '';
      const { data } = await api.post(`/admin/heal-remove-orphan-tiers-without-transactions?${q}dry_run=${dryRun}`);
      setPurgeResult(data);
      if (dryRun) toast.info(`Dry-run : ${data.orphan_owners_found} owners + ${data.orphan_suppliers_found} suppliers a supprimer`, { duration: 6000 });
      else toast.success(`${data.deleted_owners} owners + ${data.deleted_suppliers} suppliers supprimes`, { duration: 8000 });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur purge');
    } finally { setPurgeBusy(false); }
  };
  const runDedupNatures = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "Fusionner les natures de depenses dupliquees ?\n\n" +
      "* Idempotent - regroupe par (ACP, compte comptable).\n" +
      "* Le PLUS ANCIEN est garde, les autres sont supprimes.\n" +
      "* Les factures pointant vers les doublons sont repointees vers le survivant.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setDedupNatBusy(true);
    setDedupNatResult(null);
    try {
      const q = dedupCopro !== 'all' ? `copropriete_id=${dedupCopro}&` : '';
      const { data } = await api.post(`/admin/heal-duplicate-natures?${q}dry_run=${dryRun}`);
      setDedupNatResult(data);
      if (dryRun) toast.info(`Dry-run : ${data.duplicate_groups} groupe(s) a nettoyer`, { duration: 6000 });
      else toast.success(`${data.deleted_natures} nature(s) supprimee(s), ${data.invoices_repointed} facture(s) repointee(s)`, { duration: 8000 });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur dedup natures');
    } finally { setDedupNatBusy(false); }
  };
  const runDedupPcmn = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "Supprimer les comptes PCMN dupliques ?\n\n" +
      "* Idempotent - regroupe par (ACP, numero de compte).\n" +
      "* Un seul compte est garde par groupe.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setDedupPcmnBusy(true);
    setDedupPcmnResult(null);
    try {
      const q = dedupCopro !== 'all' ? `copropriete_id=${dedupCopro}&` : '';
      const { data } = await api.post(`/admin/heal-duplicate-pcmn?${q}dry_run=${dryRun}`);
      setDedupPcmnResult(data);
      if (dryRun) toast.info(`Dry-run : ${data.duplicate_groups} groupe(s) a nettoyer`, { duration: 6000 });
      else toast.success(`${data.deleted_pcmn} compte(s) PCMN supprime(s)`, { duration: 8000 });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur dedup PCMN');
    } finally { setDedupPcmnBusy(false); }
  };
  const runHealOptipro = async (dryRun) => {
    if (!optiproCopro) {
      toast.error('Selectionnez une ACP a nettoyer.');
      return;
    }
    if (!dryRun && !window.confirm(
      "ATTENTION : fusionner les comptes Optipro 7-char des proprietaires vers les comptes canoniques 4101XXXX ?\n\n" +
      "* Idempotent : re-executable sans risque.\n" +
      "* Reecrit les lignes journal_entries : 4100XXX -> 4101XXXX (ou 4001XXX -> 4100XXXX pour reserve).\n" +
      "* Met a jour tier_accounts des owners pour pointer vers le canonique.\n" +
      "* Supprime les comptes pcmn Optipro devenus orphelins.\n\n" +
      "Recommande : lancer d'abord un DRY-RUN pour verifier les changements.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setOptiproBusy(true);
    setOptiproResult(null);
    try {
      // iter90ij : LANCE AUSSI heal-orphan-tier-accounts dans la foulee car
      // les 2 healings sont complementaires (7-char + hybrides 8-char).
      const { data } = await api.post(
        `/admin/heal-optipro-owner-accounts?copropriete_id=${optiproCopro}&dry_run=${dryRun}`,
      );
      let orphan = null;
      try {
        const { data: orphanData } = await api.post(
          `/admin/heal-orphan-tier-accounts?copropriete_id=${optiproCopro}&dry_run=${dryRun}`,
        );
        orphan = orphanData;
      } catch (e) { /* ignore */ }
      setOptiproResult({ ...data, orphan });
      if (dryRun) {
        toast.info(
          `Dry-run : ${data.owners_healed} owner(s) + ${orphan?.orphan_accounts_found || 0} orphelin(s) a nettoyer`,
          { duration: 6000 },
        );
      } else {
        toast.success(
          `${data.owners_healed} owner(s) - ${data.lines_remapped + (orphan?.lines_remapped || 0)} lignes reecrites - ${data.pcmn_accounts_deleted + (orphan?.pcmn_accounts_deleted || 0)} comptes Optipro supprimes`,
          { duration: 8000 },
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur heal-optipro');
    } finally {
      setOptiproBusy(false);
    }
  };
  const runHealDuplicateFundCalls = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "ATTENTION : supprimer les ecritures AP en doublon avec les VE deja auto-generees ?\n\n" +
      "* Idempotent : uniquement les paires AP+VE detectees sur le meme fund_call_id.\n" +
      "* Le VE est conserve, l'AP legacy est supprime.\n" +
      "* Corrige le double-comptage du fonds de reserve/roulement sur le bilan.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setDupBusy(true);
    setDupResult(null);
    try {
      const { data } = await api.post(
        `/admin/heal-duplicate-fund-call-entries?dry_run=${dryRun}`,
      );
      setDupResult(data);
      if (dryRun) {
        toast.info(
          `Dry-run : ${data.duplicates_found} doublon(s) AP/VE detectes`,
          { duration: 6000 },
        );
      } else {
        toast.success(
          `${data.removed} ecriture(s) AP supprimee(s) - bilan reserve corrige`,
          { duration: 8000 },
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur heal-duplicate');
    } finally {
      setDupBusy(false);
    }
  };
  const runHealCreditNotes = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "ATTENTION : creer les ecritures AC MANQUANTES pour toutes les notes de credit ?\n\n" +
      "* Idempotent : les NC deja liees a une ecriture sont ignorees.\n" +
      "* Sens comptable : Dr fournisseur (44xxx) / Cr charge (6xxx).\n" +
      "* Confirmez pour lancer.",
    )) return;
    setNcBusy(true);
    setNcResult(null);
    try {
      const { data } = await api.post(
        `/admin/heal-credit-notes?dry_run=${dryRun}`,
      );
      setNcResult(data);
      if (dryRun) {
        toast.info(
          `Dry-run : ${data.healed_total} NC a corriger, ${data.skipped_total} deja OK`,
          { duration: 6000 },
        );
      } else {
        toast.success(
          `${data.healed_total} ecriture(s) AC creees pour les notes de credit`,
          { duration: 8000 },
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur heal-credit-notes');
    } finally {
      setNcBusy(false);
    }
  };
  const runGridfsMigration = async (dryRun) => {
    if (!dryRun && !window.confirm(
      "ATTENTION : lancer la migration REELLE des uploads vers MongoDB GridFS ?\n\n" +
      "* Idempotent : les fichiers deja migres seront ignores.\n" +
      "* Les fichiers presents sur le filesystem seront copies dans MongoDB.\n" +
      "* Cette operation est SAFE et peut etre rejouee. Elle protege les fichiers\n" +
      "  contre la perte lors des redeploiements du container.\n\n" +
      "Confirmez pour lancer.",
    )) return;
    setGridfsBusy(true);
    setGridfsResult(null);
    try {
      const { data } = await api.post(
        `/admin/migrate-uploads-to-gridfs?dry_run=${dryRun}`,
      );
      setGridfsResult(data);
      const t = data.totals || {};
      if (dryRun) {
        toast.info(
          `Dry-run : ${t.migrated} a migrer, ${t.skipped_already_in_gridfs} deja OK, ${t.missing_file_on_disk} fichier(s) absent(s) - ${t.total_bytes_human}`,
          { duration: 8000 },
        );
      } else {
        toast.success(
          `Migration LIVE terminee : ${t.migrated} fichier(s) copies vers GridFS (${t.total_bytes_human})`,
          { duration: 10000 },
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur migration GridFS');
    } finally {
      setGridfsBusy(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="admin-quality-audit-page">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold" style={{ fontFamily: 'Chivo,sans-serif' }}>Quality Audit</h1>
          <p className="text-sm text-slate-500 mt-1">
            Rapport global anti-doublons - regle stricte comptable
          </p>
          {report && (
            <p className="text-xs text-slate-400 mt-1">
              Genere le {new Date(report.generated_at).toLocaleString('fr-BE')} - Scope : {report.scope}
              {report._cache_hit && <span className="ml-2 text-emerald-600" data-testid="cache-hit-badge">(cache 5min)</span>}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={coproFilter}
            onChange={(e) => setCoproFilter(e.target.value)}
            className="text-xs border border-slate-300 rounded px-2 py-1"
            data-testid="quality-copro-filter"
          >
            <option value="all">Toutes les ACPs</option>
            {copros.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
          <Button size="sm" onClick={() => load()} disabled={loading} data-testid="quality-refresh">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Analyse...' : 'Actualiser'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => load({ force: true })} disabled={loading} data-testid="quality-force-refresh" title="Force regeneration depuis la DB (bypass cache 5min)">
            <RefreshCw size={14} className="mr-1" /> Force
          </Button>
          <Button size="sm" variant="outline" onClick={downloadCsv} data-testid="quality-download-csv">
            <Download size={14} className="mr-1" /> CSV
          </Button>
        </div>
      </div>

      {report && (
        <div className={`p-4 rounded border-2 ${healthy ? 'bg-emerald-50 border-emerald-400' : 'bg-yellow-50 border-yellow-400'}`}>
          <div className="flex items-center gap-3">
            {healthy ? (
              <CheckCircle2 size={24} className="text-emerald-600" />
            ) : (
              <AlertTriangle size={24} className="text-yellow-600" />
            )}
            <div>
              <div className="font-bold text-lg">
                {healthy ? 'Base saine - aucun doublon detecte' : 'Anomalies detectees - action requise'}
              </div>
              <div className="text-xs mt-1 flex flex-wrap gap-3">
                <span>BCE dup : <strong>{s.supplier_bce_duplicates || 0}</strong></span>
                <span>Suppliers sans BCE : <strong>{s.supplier_missing_bce || 0}</strong></span>
                <span>Owner email dup : <strong>{s.owner_email_dup_groups || 0}</strong></span>
                <span>Owner tel dup : <strong>{s.owner_phone_dup_groups || 0}</strong></span>
                <span>PCMN orphan tier : <strong>{s.pcmn_orphan_tier || 0}</strong></span>
                <span>Bank dup : <strong>{s.pcmn_dup_bank || 0}</strong></span>
                <span>NC sans ecriture : <strong>{s.credit_notes_missing_entry || 0}</strong></span>
                <span>Docs sans GridFS : <strong>{s.documents_without_gridfs || 0}</strong></span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* iter90i1 : Panneau migration GridFS - resistance au redeploiement */}
      <Card className="border-blue-200" data-testid="gridfs-migration-panel">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <HardDrive size={16} className="text-[#022D52]" />
            Migration des uploads vers MongoDB GridFS
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-slate-600 mb-3 leading-relaxed">
            Copie les fichiers du filesystem <code className="bg-slate-100 px-1 rounded">/app/uploads/</code>
            vers MongoDB GridFS. Idempotent (skip les fichiers deja migres). A executer une fois apres chaque
            redeploiement pour proteger les nouveaux fichiers uploades. Recommande : d&apos;abord un dry-run,
            puis un run reel.
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              onClick={() => runGridfsMigration(true)}
              disabled={gridfsBusy}
              data-testid="gridfs-dry-run-btn"
              className="text-[#022D52] border-[#022D52]/30 hover:bg-blue-50"
            >
              <RefreshCw size={13} className={`mr-1 ${gridfsBusy ? 'animate-spin' : ''}`} />
              Dry-run (compte les fichiers)
            </Button>
            <Button
              size="sm"
              onClick={() => runGridfsMigration(false)}
              disabled={gridfsBusy}
              data-testid="gridfs-live-run-btn"
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
            >
              <Play size={13} className="mr-1" />
              Executer la migration
            </Button>
            {gridfsBusy && <span className="text-xs text-slate-500 italic">En cours...</span>}
          </div>
          {gridfsResult && (
            <div className="mt-4 border-t border-slate-200 pt-3" data-testid="gridfs-result-panel">
              <div className="text-xs font-semibold mb-2">
                Resultat ({gridfsResult.mode === 'dry_run' ? 'Dry-run' : 'Migration live'}) - {gridfsResult.duration_seconds}s
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {[
                  { key: 'invoices_attachments', label: 'Factures - PJ' },
                  { key: 'journal_attachments', label: 'Ecritures - PJ' },
                  { key: 'documents', label: 'Documents' },
                ].map(({ key, label }) => {
                  const b = gridfsResult[key] || {};
                  return (
                    <div key={key} className="p-2 border border-slate-200 rounded bg-slate-50" data-testid={`gridfs-block-${key}`}>
                      <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">{label}</div>
                      <div className="text-xs">
                        <span className="font-mono font-bold text-emerald-700">{b.migrated || 0}</span> migres,{' '}
                        <span className="font-mono text-slate-500">{b.skipped || 0}</span> deja OK,{' '}
                        <span className={`font-mono ${b.missing ? 'text-amber-600 font-bold' : 'text-slate-500'}`}>{b.missing || 0}</span> absents
                      </div>
                      <div className="text-[10px] text-slate-500 mt-0.5">{b.bytes_human || '0 B'}</div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center gap-2 flex-wrap text-xs">
                <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">
                  Total migres : {gridfsResult.totals?.migrated || 0}
                </Badge>
                <Badge variant="outline" className="bg-slate-100 text-slate-600 border-slate-200">
                  Deja OK : {gridfsResult.totals?.skipped_already_in_gridfs || 0}
                </Badge>
                {(gridfsResult.totals?.missing_file_on_disk || 0) > 0 && (
                  <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200">
                    Fichiers absents du disque : {gridfsResult.totals.missing_file_on_disk}
                  </Badge>
                )}
                <span className="text-slate-500">
                  ({gridfsResult.totals?.total_bytes_human || '0 B'})
                </span>
                {gridfsResult.ttl_index_invoice_bundle_sessions && (
                  <span className="text-slate-400 italic">
                    TTL index : {gridfsResult.ttl_index_invoice_bundle_sessions}
                  </span>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* iter90i6 : Panneau backfill notes de credit sans ecriture AC */}
      <Card className="border-amber-200" data-testid="heal-nc-panel">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <FileWarning size={16} className="text-amber-700" />
            Notes de credit sans ecriture comptable
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-slate-600 mb-3 leading-relaxed">
            Corrige les notes de credit (factures avec montant negatif) importees ou creees
            <strong> avant </strong>le fix iter90i6, qui n&apos;avaient pas d&apos;ecriture dans le
            journal des achats (bug : le generateur skipait <code className="bg-slate-100 px-1 rounded">amount &lt;= 0</code>).
            Idempotent (les NC deja liees a une AC sont ignorees). Comptabilise en respectant
            le principe belge PCMN : <strong>Dr fournisseur</strong> / <strong>Cr charge</strong>.
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              onClick={() => runHealCreditNotes(true)}
              disabled={ncBusy}
              data-testid="heal-nc-dry-run-btn"
              className="text-amber-700 border-amber-300 hover:bg-amber-50"
            >
              <RefreshCw size={13} className={`mr-1 ${ncBusy ? 'animate-spin' : ''}`} />
              Dry-run (compte les NC)
            </Button>
            <Button
              size="sm"
              onClick={() => runHealCreditNotes(false)}
              disabled={ncBusy}
              data-testid="heal-nc-live-btn"
              className="bg-amber-600 hover:bg-amber-700 text-white"
            >
              <Play size={13} className="mr-1" />
              Creer les ecritures manquantes
            </Button>
            {ncBusy && <span className="text-xs text-slate-500 italic">En cours...</span>}
          </div>
          {ncResult && (
            <div className="mt-4 border-t border-slate-200 pt-3" data-testid="heal-nc-result-panel">
              <div className="text-xs font-semibold mb-2">
                Resultat ({ncResult.mode === 'dry_run' ? 'Dry-run' : 'Live'}) :
                <span className="ml-2 font-mono text-emerald-700">{ncResult.healed_total} a corriger / crees</span>
                {ncResult.skipped_total > 0 && (
                  <span className="ml-2 font-mono text-slate-500">{ncResult.skipped_total} deja OK</span>
                )}
              </div>
              {(ncResult.healed || []).length > 0 && (
                <div className="overflow-x-auto border border-slate-200 rounded">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">Fournisseur</th>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">Numero</th>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">Date</th>
                        <th className="text-right px-2 py-1 text-[10px] uppercase tracking-wider">Montant</th>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">Ref AC</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ncResult.healed.map((h, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="px-2 py-1 truncate max-w-[180px]" title={h.supplier}>{h.supplier}</td>
                          <td className="px-2 py-1 font-mono text-slate-600">{h.number}</td>
                          <td className="px-2 py-1 font-mono text-slate-500">{h.date || '-'}</td>
                          <td className="px-2 py-1 text-right font-mono text-red-600">
                            {h.amount?.toFixed(2)} EUR
                          </td>
                          <td className="px-2 py-1 font-mono text-emerald-700 truncate max-w-[160px]" title={h.reference || h.je_id}>
                            {h.reference || (h.would_create_je ? '(a creer)' : '-')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* iter90i7 : Panneau nettoyage doublons AP/VE fund calls */}
      <Card className="border-red-200" data-testid="heal-dup-panel">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <AlertTriangle size={16} className="text-red-600" />
            Doublons d&apos;appels de fonds (AP + VE)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-xs text-slate-600 mb-3 leading-relaxed">
            Detecte et supprime les ecritures <code className="bg-slate-100 px-1 rounded">AP</code> (Appel legacy)
            en doublon avec les <code className="bg-slate-100 px-1 rounded">VE</code> (Ventes auto-generees).
            Bug historique : l&apos;endpoint <code className="bg-slate-100 px-1 rounded">/fund-calls/&#123;id&#125;/generate-entries</code>
            creait un AP alors que le POST /fund-calls creait deja un VE via <code>generate_sale_entry</code>.
            <strong> Consequence : fonds de reserve/roulement surestime de 2x l&apos;appel</strong> (ex : bilan 9000 au lieu de 7000).
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              size="sm"
              variant="outline"
              onClick={() => runHealDuplicateFundCalls(true)}
              disabled={dupBusy}
              data-testid="heal-dup-dry-run-btn"
              className="text-red-600 border-red-300 hover:bg-red-50"
            >
              <RefreshCw size={13} className={`mr-1 ${dupBusy ? 'animate-spin' : ''}`} />
              Dry-run (detecte)
            </Button>
            <Button
              size="sm"
              onClick={() => runHealDuplicateFundCalls(false)}
              disabled={dupBusy}
              data-testid="heal-dup-live-btn"
              className="bg-red-600 hover:bg-red-700 text-white"
            >
              <Play size={13} className="mr-1" />
              Supprimer les AP en doublon
            </Button>
            {dupBusy && <span className="text-xs text-slate-500 italic">En cours...</span>}
          </div>
          {dupResult && (
            <div className="mt-4 border-t border-slate-200 pt-3" data-testid="heal-dup-result">
              <div className="text-xs font-semibold mb-2">
                <span className="font-mono text-red-700">{dupResult.duplicates_found}</span> doublon(s) detecte(s)
                {dupResult.mode === 'live' && (
                  <span className="ml-2 font-mono text-emerald-700">
                    - {dupResult.removed} supprime(s)
                  </span>
                )}
              </div>
              {(dupResult.duplicates || []).length > 0 && (
                <div className="overflow-x-auto border border-slate-200 rounded">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">Date</th>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">AP (a supprimer)</th>
                        <th className="text-left px-2 py-1 text-[10px] uppercase tracking-wider">VE (conserve)</th>
                        <th className="text-right px-2 py-1 text-[10px] uppercase tracking-wider">Montant</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dupResult.duplicates.map((d, i) => (
                        <tr key={i} className="border-t border-slate-100">
                          <td className="px-2 py-1 font-mono text-slate-600">{d.date}</td>
                          <td className="px-2 py-1 truncate max-w-[200px] text-red-600" title={d.ap_reference}>
                            {d.ap_reference}
                          </td>
                          <td className="px-2 py-1 truncate max-w-[200px] text-emerald-600" title={d.ve_reference}>
                            {d.ve_reference}
                          </td>
                          <td className="px-2 py-1 text-right font-mono text-slate-700">
                            {d.ap_amount?.toFixed(2)} EUR
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* iter90ig : Guerison comptes Optipro (7-char) -> canoniques (4101XXXX) */}
      <Card className="border-fuchsia-200 bg-fuchsia-50/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-fuchsia-900 text-base">
            Nettoyer les comptes Optipro pollues (owners &quot;Ex-prop.&quot;)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-fuchsia-900 leading-relaxed">
            Lors d&apos;un import Optipro, l&apos;OD d&apos;ouverture ecrivait par erreur le compte 7-char (ex. <code className="bg-white px-1">4100959</code>) comme compte du proprietaire au lieu du canonique <code className="bg-white px-1">4101XXXX</code>. La balance des tiers affiche alors 2 lignes par proprietaire (compte propre + &quot;Ex-prop.&quot;) et le lettrage bancaire ne sait plus lequel utiliser. Ce healing fusionne les 2 comptes.
          </p>
          <div className="flex flex-col sm:flex-row gap-2 items-stretch">
            <Select value={optiproCopro} onValueChange={setOptiproCopro}>
              <SelectTrigger className="flex-1" data-testid="optipro-copro-select">
                <SelectValue placeholder="Selectionner une ACP a nettoyer..." />
              </SelectTrigger>
              <SelectContent>
                {copros.filter(c => c.status !== 'archived').map(c => (
                  <SelectItem key={c.id} value={c.id}>{c.reference ? `${c.reference} - ` : ''}{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => runHealOptipro(true)}
                disabled={optiproBusy || !optiproCopro}
                data-testid="optipro-dry-run-btn"
              >
                {optiproBusy ? '...' : 'Dry-run'}
              </Button>
              <Button
                onClick={() => runHealOptipro(false)}
                disabled={optiproBusy || !optiproCopro}
                className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white"
                data-testid="optipro-live-btn"
              >
                {optiproBusy ? '...' : 'Nettoyer'}
              </Button>
            </div>
          </div>
          {optiproResult && (
            <div className="bg-white border border-fuchsia-200 rounded-md p-3 space-y-2 text-xs">
              <div>
                <b className="text-fuchsia-800">Mode :</b> {optiproResult.mode} - <b>{optiproResult.owners_healed}</b> owner(s) a corriger
                {optiproResult.mode === 'live' && (
                  <span className="ml-2 text-emerald-700 font-semibold">
                    - {optiproResult.lines_remapped} lignes reecrites - {optiproResult.pcmn_accounts_deleted} compte(s) supprimes
                  </span>
                )}
              </div>
              {Object.keys(optiproResult.account_remaps || {}).length > 0 && (
                <div>
                  <b>Fusion de comptes :</b>
                  <ul className="list-disc list-inside ml-2 font-mono text-[11px]">
                    {Object.entries(optiproResult.account_remaps).map(([src, dst]) => (
                      <li key={src}>{src} <b>&rarr;</b> {dst}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(optiproResult.details || []).length > 0 && (
                <details>
                  <summary className="cursor-pointer text-fuchsia-800">Details par owner ({optiproResult.details.length})</summary>
                  <ul className="mt-1 ml-2 space-y-1">
                    {optiproResult.details.slice(0, 50).map((d, i) => (
                      <li key={i} className="text-slate-700">
                        <b>{d.owner_name}</b> :
                        {d.bad_provisions && <span className="ml-1 text-red-600">provisions {d.before?.provisions || '-'}</span>}
                        {d.bad_reserve && <span className="ml-1 text-red-600">reserve {d.before?.reserve || '-'}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* iter90ih : Deduplication natures + PCMN */}
      <Card className="border-cyan-200 bg-cyan-50/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-cyan-900 text-base">
            Doublons natures de depenses / comptes PCMN
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-cyan-900 leading-relaxed">
            Chaque relance du wizard d&apos;import creait auparavant de nouveaux doublons de natures (jusqu&apos;a 4x observees). Le fix preventif est actif (idempotence sur ACP+compte). Ce healing nettoie les ACP deja polluees. Les factures pointant vers un doublon sont repointees vers la nature la plus ancienne.
          </p>
          <div className="flex gap-2 items-center">
            <Select value={dedupCopro} onValueChange={setDedupCopro}>
              <SelectTrigger className="flex-1 max-w-md" data-testid="dedup-copro-select">
                <SelectValue placeholder="Selectionner une ACP..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Toutes les ACP</SelectItem>
                {copros.filter(c => c.status !== 'archived').map(c => (
                  <SelectItem key={c.id} value={c.id}>{c.reference ? `${c.reference} - ` : ''}{c.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="bg-white border border-cyan-200 rounded p-3 space-y-2">
              <div className="font-semibold text-sm text-cyan-900">Natures de depenses</div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => runDedupNatures(true)} disabled={dedupNatBusy} data-testid="dedup-nat-dry-btn">
                  {dedupNatBusy ? '...' : 'Dry-run'}
                </Button>
                <Button size="sm" onClick={() => runDedupNatures(false)} disabled={dedupNatBusy} className="bg-cyan-600 hover:bg-cyan-700 text-white" data-testid="dedup-nat-live-btn">
                  {dedupNatBusy ? '...' : 'Fusionner'}
                </Button>
              </div>
              {dedupNatResult && (
                <div className="text-xs text-slate-700">
                  <b>{dedupNatResult.duplicate_groups}</b> groupe(s) - {dedupNatResult.mode === 'live' && <>{dedupNatResult.deleted_natures} suppr. / {dedupNatResult.invoices_repointed} factures repointees</>}
                </div>
              )}
            </div>
            <div className="bg-white border border-cyan-200 rounded p-3 space-y-2">
              <div className="font-semibold text-sm text-cyan-900">Comptes PCMN</div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => runDedupPcmn(true)} disabled={dedupPcmnBusy} data-testid="dedup-pcmn-dry-btn">
                  {dedupPcmnBusy ? '...' : 'Dry-run'}
                </Button>
                <Button size="sm" onClick={() => runDedupPcmn(false)} disabled={dedupPcmnBusy} className="bg-cyan-600 hover:bg-cyan-700 text-white" data-testid="dedup-pcmn-live-btn">
                  {dedupPcmnBusy ? '...' : 'Fusionner'}
                </Button>
              </div>
              {dedupPcmnResult && (
                <div className="text-xs text-slate-700">
                  <b>{dedupPcmnResult.duplicate_groups}</b> groupe(s) - {dedupPcmnResult.mode === 'live' && <>{dedupPcmnResult.deleted_pcmn} suppr.</>}
                </div>
              )}
            </div>
            {/* iter90ii : Owners + Suppliers */}
            <div className="bg-white border border-cyan-200 rounded p-3 space-y-2">
              <div className="font-semibold text-sm text-cyan-900">Proprietaires</div>
              <p className="text-[10px] text-slate-500">Regroupement par (ACP, code auxiliaire).</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => runDedupOwners(true)} disabled={dedupOwnBusy} data-testid="dedup-own-dry-btn">
                  {dedupOwnBusy ? '...' : 'Dry-run'}
                </Button>
                <Button size="sm" onClick={() => runDedupOwners(false)} disabled={dedupOwnBusy} className="bg-cyan-600 hover:bg-cyan-700 text-white" data-testid="dedup-own-live-btn">
                  {dedupOwnBusy ? '...' : 'Fusionner'}
                </Button>
              </div>
              {dedupOwnResult && (
                <div className="text-xs text-slate-700">
                  <b>{dedupOwnResult.duplicate_groups}</b> groupe(s) - {dedupOwnResult.mode === 'live' && <>{dedupOwnResult.deleted_owners} suppr. / {dedupOwnResult.lots_repointed} lots repointes</>}
                </div>
              )}
            </div>
            <div className="bg-white border border-cyan-200 rounded p-3 space-y-2">
              <div className="font-semibold text-sm text-cyan-900">Fournisseurs</div>
              <p className="text-[10px] text-slate-500">Regroupement par (ACP, nom normalise).</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => runDedupSuppliers(true)} disabled={dedupSupBusy} data-testid="dedup-sup-dry-btn">
                  {dedupSupBusy ? '...' : 'Dry-run'}
                </Button>
                <Button size="sm" onClick={() => runDedupSuppliers(false)} disabled={dedupSupBusy} className="bg-cyan-600 hover:bg-cyan-700 text-white" data-testid="dedup-sup-live-btn">
                  {dedupSupBusy ? '...' : 'Fusionner'}
                </Button>
              </div>
              {dedupSupResult && (
                <div className="text-xs text-slate-700">
                  <b>{dedupSupResult.duplicate_groups}</b> groupe(s) - {dedupSupResult.mode === 'live' && <>{dedupSupResult.deleted_suppliers} suppr. / {dedupSupResult.invoices_repointed} factures repointees</>}
                </div>
              )}
            </div>
          </div>

          {/* iter90im : Purge definitive orphelins sans transactions */}
          <div className="bg-red-50/50 border-2 border-red-300 rounded p-3 space-y-2 mt-3">
            <div className="font-semibold text-sm text-red-900 flex items-center gap-2">
              Purge des orphelins SANS transactions (owners + suppliers)
            </div>
            <p className="text-[11px] text-red-800 leading-relaxed">
              Supprime DEFINITIVEMENT les fiches proprietaires + fournisseurs qui n&apos;ont AUCUNE transaction associee (aucune ligne de journal, aucun lot, aucune facture, aucun tenant). Ces fiches sont typiquement des artefacts d&apos;imports defectueux avant iter90im. Le nettoyage est sans risque business.
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => runPurgeOrphans(true)} disabled={purgeBusy} data-testid="purge-orphan-dry-btn">
                {purgeBusy ? '...' : 'Dry-run'}
              </Button>
              <Button size="sm" onClick={() => runPurgeOrphans(false)} disabled={purgeBusy} className="bg-red-600 hover:bg-red-700 text-white" data-testid="purge-orphan-live-btn">
                {purgeBusy ? '...' : 'Purger'}
              </Button>
            </div>
            {purgeResult && (
              <div className="text-xs text-slate-800 bg-white border border-red-200 rounded p-2 space-y-1">
                <div>Mode : <b>{purgeResult.mode}</b> | Scope : <b>{purgeResult.copropriete_id}</b></div>
                <div>Owners : <b>{purgeResult.orphan_owners_found}</b> orphelins detectes{purgeResult.mode === 'live' && ` / ${purgeResult.deleted_owners} supprimes`}</div>
                <div>Suppliers : <b>{purgeResult.orphan_suppliers_found}</b> orphelins detectes{purgeResult.mode === 'live' && ` / ${purgeResult.deleted_suppliers} supprimes`}</div>
                {(purgeResult.orphan_owners_sample || []).length > 0 && (
                  <details>
                    <summary className="cursor-pointer text-red-700">Voir les owners a supprimer</summary>
                    <ul className="mt-1 text-[10px] max-h-32 overflow-auto">
                      {purgeResult.orphan_owners_sample.map((o, i) => (
                        <li key={i}>{o.name} {o.aux ? `(${o.aux})` : ''}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {report && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* BCE duplicates */}
          <Section
            icon={Truck}
            title="Fournisseurs - BCE duplique"
            count={report.suppliers.bce_duplicates.length}
            severity={report.suppliers.bce_duplicates.length ? 'err' : 'ok'}
          >
            {report.suppliers.bce_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun BCE duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">BCE</TableHead>
                    <TableHead className="text-xs">Fiches</TableHead>
                    <TableHead className="w-16"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.suppliers.bce_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.bce}</TableCell>
                      <TableCell className="text-xs">
                        {g.suppliers.map(s => s.name).join(', ')}
                      </TableCell>
                      <TableCell>
                        <Button size="sm" variant="outline" onClick={() => goToSupplier(g.suppliers[0].id)} className="text-xs py-0 h-6">
                          Ouvrir
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          {/* Missing BCE (top 10 with usage) */}
          <Section
            icon={FileWarning}
            title="Fournisseurs sans BCE (utilises)"
            count={s.supplier_missing_bce || 0}
            severity={(s.supplier_missing_bce || 0) > 0 ? 'warn' : 'ok'}
          >
            {report.suppliers.missing_bce_examples.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun fournisseur utilise sans BCE</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">Top 10 les plus utilises :</p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Nom</TableHead>
                      <TableHead className="text-xs w-16">Factures</TableHead>
                      <TableHead className="w-16"></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.suppliers.missing_bce_examples.map((ex, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs">{ex.name}</TableCell>
                        <TableCell className="text-xs">{ex.invoices_using}</TableCell>
                        <TableCell>
                          <Button size="sm" variant="outline" onClick={() => goToSupplier(ex.id)} className="text-xs py-0 h-6">
                            Corriger
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Owner email/phone duplicates */}
          <Section
            icon={Users}
            title="Owners - Email duplique"
            count={report.owners.email_duplicates.length}
            severity={report.owners.email_duplicates.length ? 'err' : 'ok'}
          >
            {report.owners.email_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun email duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Email</TableHead>
                    <TableHead className="text-xs">Owners</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.owners.email_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.email}</TableCell>
                      <TableCell className="text-xs">
                        {g.owners.map((o, j) => (
                          <button
                            key={j}
                            className="underline text-blue-600 hover:text-blue-800 mr-2"
                            onClick={() => goToOwner(o.id)}
                          >
                            {o.name || o.id.slice(0,8)}
                          </button>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          <Section
            icon={Users}
            title="Owners - Telephone duplique"
            count={report.owners.phone_duplicates.length}
            severity={report.owners.phone_duplicates.length ? 'err' : 'ok'}
          >
            {report.owners.phone_duplicates.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun telephone duplique</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs">Telephone</TableHead>
                    <TableHead className="text-xs">Owners</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {report.owners.phone_duplicates.map((g, i) => (
                    <TableRow key={i}>
                      <TableCell className="font-mono text-xs">{g.phone}</TableCell>
                      <TableCell className="text-xs">
                        {g.owners.map((o, j) => (
                          <button key={j} className="underline text-blue-600 mr-2" onClick={() => goToOwner(o.id)}>
                            {o.name || o.id.slice(0,8)}
                          </button>
                        ))}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Section>

          {/* Orphan tier accounts */}
          <Section
            icon={Landmark}
            title="PCMN - Tier orphelins"
            count={report.pcmn_accounts.orphan_tier_accounts.length}
            severity={report.pcmn_accounts.orphan_tier_accounts.length ? 'warn' : 'ok'}
          >
            {report.pcmn_accounts.orphan_tier_accounts.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun compte tier orphelin</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gk_cleanup_orphan_tiers --copro-id XXX --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Compte</TableHead>
                      <TableHead className="text-xs">Nom</TableHead>
                      <TableHead className="text-xs w-16">ACP</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.pcmn_accounts.orphan_tier_accounts.slice(0, 10).map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">{r.account}</TableCell>
                        <TableCell className="text-xs">{r.name || '(sans nom)'}</TableCell>
                        <TableCell className="text-xs text-slate-400">{r.copro_id.slice(0,8)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Duplicated bank accounts */}
          <Section
            icon={Landmark}
            title="PCMN - Bank duplique"
            count={report.pcmn_accounts.duplicated_bank_accounts.length}
            severity={report.pcmn_accounts.duplicated_bank_accounts.length ? 'warn' : 'ok'}
          >
            {report.pcmn_accounts.duplicated_bank_accounts.length === 0 ? (
              <p className="text-xs text-slate-500">Aucun compte bancaire duplique</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gm_merge_bank_accounts --copro-id XXX --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Comptes</TableHead>
                      <TableHead className="text-xs w-16">ACP</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.pcmn_accounts.duplicated_bank_accounts.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">
                          {r.accounts.map(a => `${a.number} (${a.name})`).join(' + ')}
                        </TableCell>
                        <TableCell className="text-xs text-slate-400">{r.copro_id.slice(0,8)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* Credit notes without entry */}
          <Section
            icon={FileText}
            title="NC sans ecriture AC"
            count={report.credit_notes_without_entry.length}
            severity={report.credit_notes_without_entry.length ? 'err' : 'ok'}
          >
            {report.credit_notes_without_entry.length === 0 ? (
              <p className="text-xs text-slate-500">Toutes les notes de credit ont leur ecriture</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  Executez : <code className="bg-slate-100 px-1">python -m migrations.iter90gl_heal_credit_notes --apply</code>
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Ref</TableHead>
                      <TableHead className="text-xs">Fournisseur</TableHead>
                      <TableHead className="text-xs w-24">Montant</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.credit_notes_without_entry.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="font-mono text-xs">{r.internal_reference}</TableCell>
                        <TableCell className="text-xs">{r.supplier}</TableCell>
                        <TableCell className="text-xs font-mono text-red-600">{r.amount}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>

          {/* iter90gn : Documents without GridFS - eviter la perte de fichiers au redeploy */}
          <Section
            icon={FileWarning}
            title="Documents sans GridFS"
            count={report.documents_without_gridfs?.length || 0}
            severity={(report.documents_without_gridfs?.length || 0) > 0 ? 'err' : 'ok'}
          >
            {(report.documents_without_gridfs?.length || 0) === 0 ? (
              <p className="text-xs text-slate-500">Tous les documents sont persistes en GridFS</p>
            ) : (
              <>
                <p className="text-xs text-slate-600 mb-2">
                  <strong>Attention</strong> : ces documents ont leur fichier sur filesystem ephemere (perte au redeploiement K8s).
                  Re-uploadez-les ou supprimez-les via l&apos;UI documents.
                </p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Titre</TableHead>
                      <TableHead className="text-xs w-32">Date</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {report.documents_without_gridfs.map((d, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs">{d.title || d.document_id.slice(0,12)}</TableCell>
                        <TableCell className="text-xs text-slate-400">{d.created_at?.slice(0,10)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </Section>
        </div>
      )}
    </div>
  );
}
