/**
 * Import Wizard for Optipro/Sogis migration.
 *
 * 4 étapes Phase 1 :
 *  A) Propriétaires (CSV)
 *  C) Fournisseurs (CSV)
 *  D) Lots (CSV)
 *  K) Natures de dépense (PDF)
 *
 * Workflow par étape :
 *   1. Upload du fichier
 *   2. Preview (headers détectés + 20 premières lignes)
 *   3. Mapping des colonnes (drag-free, simples Select)
 *   4. Commit -> insertion en DB taggée avec import_session_id
 *
 * Rollback complet possible via le bouton "Annuler l'import" (DELETE session).
 */
import { useState, useEffect, Fragment } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import {
  Upload, Truck, Tag, CheckCircle2, X, AlertTriangle,
  ChevronRight, ChevronLeft, FileWarning, Loader2, RotateCcw,
  Calendar, Wallet, PieChart, Plus, Trash2, FileText, Landmark, Scale, ClipboardList
} from 'lucide-react';
import { toast } from 'sonner';

const STEPS = [
  // NOTE: 'owners' and 'lots' are now imported in the ACP Creation Assistant
  // (Step 2 - PDF/CSV from Optipro). They are NOT part of this post-creation
  // migration wizard to avoid redundancy.
  { key: 'suppliers', label: 'Fournisseurs',      icon: Truck,    optional: false, kind: 'csv_or_pdf' },
  { key: 'natures',   label: 'Natures depense',   icon: Tag,      optional: false, kind: 'pdf' },
  { key: 'fiscal_year', label: 'Exercice fiscal', icon: Calendar, optional: false, kind: 'form' },
  { key: 'budget',    label: 'Budget',            icon: Wallet,   optional: true,  kind: 'pdf' },
  { key: 'distribution_keys', label: 'Cles de repartition', icon: PieChart, optional: true, kind: 'pdf' },
  { key: 'invoices',  label: 'Factures',          icon: FileText, optional: true,  kind: 'csv_invoices' },
  { key: 'journals',  label: 'Journaux financiers', icon: Landmark, optional: true, kind: 'csv_journals' },
  { key: 'opening_balance', label: 'OD d\'ouverture', icon: Scale, optional: true, kind: 'pdf_balance' },
  { key: 'od_entries', label: 'OD year-end', icon: ClipboardList, optional: true, kind: 'pdf_od_entries' },
];

// Champs cibles attendus pour chaque étape (clé = nom du champ DB)
const TARGET_FIELDS = {
  suppliers: [
    { key: 'name',            label: 'Nom *',               required: true },
    { key: 'vat_number',      label: 'TVA',                  required: false },
    { key: 'bce_number',      label: 'BCE',                  required: false },
    { key: 'address',         label: 'Adresse',              required: false },
    { key: 'postal_code',     label: 'Code postal',          required: false },
    { key: 'city',            label: 'Ville',                required: false },
    { key: 'email',           label: 'Email',                required: false },
    { key: 'phone',           label: 'Telephone',            required: false },
    { key: 'iban',            label: 'IBAN',                 required: false },
    { key: 'default_account', label: 'Compte par defaut',   required: false },
  ],
};

export default function ImportWizardPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const coproId = params.get('copropriete_id');
  const { selectedCopro } = useAuth();
  const effectiveCopro = coproId || selectedCopro;

  const [session, setSession] = useState(null);
  const [stepIdx, setStepIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sniffing, setSniffing] = useState(false);
  const [sniffResult, setSniffResult] = useState(null);
  const [mapping, setMapping] = useState({});
  const [naturesParsed, setNaturesParsed] = useState([]);
  const [budgetSections, setBudgetSections] = useState([]);
  const [keysParsed, setKeysParsed] = useState([]);
  const [suppliersParsed, setSuppliersParsed] = useState([]);
  const [invoicesParsed, setInvoicesParsed] = useState([]);
  const [journalsParsed, setJournalsParsed] = useState([]);
  const [balanceParsed, setBalanceParsed] = useState({ actif: [], passif: [], total_actif: 0, total_passif: 0, balanced: false, period_end_date: '' });
  const [odEntriesParsed, setOdEntriesParsed] = useState({ format: '', entries: [], total_count: 0, total_amount: 0, period_start: '', period_end: '' });
  // For 'csv_or_pdf' steps : tracks which mode the user picked for THIS step
  // (resets on every step change / file reset).
  const [uploadMode, setUploadMode] = useState(null);  // null | 'csv' | 'pdf'
  const [fyForm, setFyForm] = useState({ name: '', start_date: '', end_date: '', status: 'open' });
  const [committing, setCommitting] = useState(false);

  const step = STEPS[stepIdx];

  // ----- session bootstrap -----
  useEffect(() => {
    if (!effectiveCopro) {
      setLoading(false);
      return;
    }
    (async () => {
      setLoading(true);
      try {
        const r = await api.get('/import-wizard/sessions/active', { params: { copropriete_id: effectiveCopro } });
        if (r.data) {
          setSession(r.data);
        } else {
          const c = await api.post('/import-wizard/sessions', { copropriete_id: effectiveCopro, source_system: 'Optipro' });
          setSession(c.data);
        }
      } catch (err) {
        toast.error(err.response?.data?.detail || 'Erreur creation session');
      } finally {
        setLoading(false);
      }
    })();
  }, [effectiveCopro]);

  // ----- file upload -----
  const handleFileChange = async (e, opts = {}) => {
    let { append = false } = opts;
    // Detection alternative via dataset (utilise par le bouton "Ajouter un autre PDF")
    if (!append && e?.target?.dataset?.appendMode === 'true') {
      append = true;
      e.target.dataset.appendMode = '';
    }
    const file = e.target.files?.[0];
    if (!file || !session) return;
    setSniffing(true);
    if (!append) {
      // Reinitialise les etats locaux uniquement lors d'un upload initial.
      // En mode append (multi-PDF cles), on conserve les donnees deja parsees.
      setSniffResult(null);
      setMapping({});
      setNaturesParsed([]);
      setBudgetSections([]);
      setKeysParsed([]);
      setSuppliersParsed([]);
      setInvoicesParsed([]);
      setJournalsParsed([]);
      setBalanceParsed({ actif: [], passif: [], total_actif: 0, total_passif: 0, balanced: false, period_end_date: '' });
      setOdEntriesParsed({ format: '', entries: [], total_count: 0, total_amount: 0, period_start: '', period_end: '' });
    }
    try {
      const fd = new FormData();
      fd.append('file', file);
      // Effective kind for the upload
      const effectiveKind = step.kind === 'csv_or_pdf'
        ? (uploadMode || (file.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'csv'))
        : step.kind;
      const isPdf = effectiveKind === 'pdf' || effectiveKind === 'pdf_balance' || effectiveKind === 'pdf_od_entries';
      const isStructuredCsv = effectiveKind === 'csv_invoices' || effectiveKind === 'csv_journals';
      let r;
      if (isPdf) {
        const kindMap = { natures: 'natures', budget: 'budget', distribution_keys: 'keys', suppliers: 'suppliers', opening_balance: 'balance', od_entries: 'od_entries' };
        fd.append('kind', kindMap[step.key] || 'generic');
        r = await api.post(`/import-wizard/sessions/${session.id}/sniff-pdf`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        if (step.key === 'natures') setNaturesParsed(r.data.natures || []);
        if (step.key === 'budget') setBudgetSections(r.data.sections || []);
        if (step.key === 'distribution_keys') {
          const incoming = r.data.keys || [];
          if (append && keysParsed.length > 0) {
            // Merge : dedupe par code (le dernier upload ecrase). Si pas de code,
            // dedupe par nom. Les nouvelles cles sans match sont ajoutees.
            const byCode = new Map();
            const noCodeByName = new Map();
            keysParsed.forEach(k => {
              if (k.code) byCode.set(k.code.trim(), k);
              else noCodeByName.set((k.name || '').trim(), k);
            });
            incoming.forEach(k => {
              const code = (k.code || '').trim();
              const nm = (k.name || '').trim();
              if (code) byCode.set(code, k);
              else if (nm) noCodeByName.set(nm, k);
            });
            const merged = [...byCode.values(), ...noCodeByName.values()];
            const newKeysCount = merged.length - keysParsed.length;
            setKeysParsed(merged);
            toast.success(`+${incoming.length} cle(s) parsees - ${newKeysCount > 0 ? newKeysCount + ' nouvelle(s)' : 'toutes deja presentes (mises a jour)'}. Total : ${merged.length}`);
          } else {
            setKeysParsed(incoming);
          }
        }
        if (step.key === 'suppliers') setSuppliersParsed(r.data.suppliers || []);
        if (step.key === 'opening_balance') {
          setBalanceParsed({
            actif: r.data.actif || [],
            passif: r.data.passif || [],
            total_actif: r.data.total_actif || 0,
            total_passif: r.data.total_passif || 0,
            balanced: r.data.balanced || false,
            period_end_date: r.data.period_end_date || '',
          });
        }
        if (step.key === 'od_entries') {
          // Branch by format. Journal OD : entries already have explicit lines
          // + included flag set by the parser. Liste des depenses : pre-fill
          // counterpart with high-confidence suggestions.
          const format = r.data.format || 'expense_list';
          let entries;
          if (format === 'od_journal') {
            entries = (r.data.entries || []).map(e => ({ ...e }));
          } else {
            entries = (r.data.entries || []).map(e => ({
              ...e,
              counterpart_account: e.suggested_counterpart?.confidence === 'high'
                ? (e.suggested_counterpart?.account || '')
                : '',
              counterpart_account_name: e.suggested_counterpart?.confidence === 'high'
                ? (e.suggested_counterpart?.account_name || '')
                : '',
            }));
          }
          setOdEntriesParsed({
            format,
            entries,
            total_count: r.data.total_count || 0,
            total_amount: r.data.total_amount || 0,
            period_start: r.data.period_start || '',
            period_end: r.data.period_end || '',
          });
        }
      } else if (isStructuredCsv) {
        const kindParam = effectiveKind === 'csv_invoices' ? 'invoices' : 'journals';
        fd.append('kind', kindParam);
        r = await api.post(`/import-wizard/sessions/${session.id}/sniff-csv?kind=${kindParam}`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        if (effectiveKind === 'csv_invoices') setInvoicesParsed(r.data.invoices || []);
        if (effectiveKind === 'csv_journals') setJournalsParsed(r.data.transactions || []);
      } else if (effectiveKind === 'csv') {
        r = await api.post(`/import-wizard/sessions/${session.id}/sniff-csv`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
      }
      setSniffResult(r?.data || {});
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec du parsing');
    } finally {
      setSniffing(false);
    }
  };

  // ----- commit -----
  const handleCommit = async () => {
    if (!session) return;
    setCommitting(true);
    try {
      let r;
      const effectiveKind = step.kind === 'csv_or_pdf'
        ? (uploadMode || (suppliersParsed.length > 0 ? 'pdf' : 'csv'))
        : step.kind;
      if (step.key === 'suppliers' && effectiveKind === 'pdf') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-suppliers-pdf`, {
          suppliers: suppliersParsed,
        });
        toast.success(`${r.data.inserted} fournisseur(s) importes`);
      } else if (effectiveKind === 'csv') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-${step.key}`, {
          mapping,
          rows: sniffResult?.rows || [],
        });
        toast.success(`${r.data.inserted} ${step.label.toLowerCase()} importes`);
      } else if (step.key === 'natures') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-natures`, { natures: naturesParsed });
        toast.success(`${r.data.inserted} natures importees`);
      } else if (step.key === 'budget') {
        const fyId = session?.steps?.fiscal_year?.fiscal_year_id;
        if (!fyId) {
          toast.error('Creez d\'abord l\'exercice fiscal a l\'etape precedente');
          setCommitting(false);
          return;
        }
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-budget`, {
          fiscal_year_id: fyId, sections: budgetSections
        });
        toast.success(`Budget cree : ${r.data.inserted} lignes (total ${r.data.total_amount?.toFixed(2)} EUR)`);
      } else if (step.key === 'distribution_keys') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-distribution-keys`, { keys: keysParsed });
        toast.success(`${r.data.inserted} cle(s) de repartition creees`);
      } else if (step.key === 'invoices') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-invoices`, { invoices: invoicesParsed });
        const m = r.data;
        toast.success(
          `${m.inserted} facture(s) validee(s) + ${m.journal_entries || 0} ecriture(s) AC creee(s)` +
          (m.pcmn_created ? ` - ${m.pcmn_created} compte(s) PCMN auto-ajoutes` : '') +
          ` - ${m.matched_supplier} avec fournisseur, ${m.matched_key} avec cle, ${m.matched_category} avec nature`
        );
      } else if (step.key === 'journals') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-journals`, { transactions: journalsParsed });
        const m = r.data;
        toast.success(
          `${m.inserted} transaction(s) bancaire(s) importee(s) + ${m.journal_entries || 0} ecriture(s) FI` +
          (m.pcmn_created ? ` - ${m.pcmn_created} compte(s) PCMN auto-ajoutes` : '')
        );
      } else if (step.key === 'opening_balance') {
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-opening-balance`, {
          actif: balanceParsed.actif,
          passif: balanceParsed.passif,
          period_end_date: balanceParsed.period_end_date,
          fiscal_year_id: session?.steps?.fiscal_year?.fiscal_year_id || '',
        });
        const m = r.data;
        toast.success(
          `OD d'ouverture creee : ${m.lines} ligne(s) au ${m.entry_date} ` +
          `(Debit/Credit ${m.total_debit.toFixed(2)} EUR)` +
          (m.pcmn_created ? ` - ${m.pcmn_created} compte(s) PCMN auto-ajoutes` : '') +
          ((m.owners_linked || m.suppliers_linked) ? ` - ${m.owners_linked} owner(s) + ${m.suppliers_linked} fournisseur(s) lies via auxiliary_code` : '')
        );
      } else if (step.key === 'od_entries') {
        const isJournalOd = odEntriesParsed.format === 'od_journal';
        if (isJournalOd) {
          // Format Journal OD : entries have explicit balanced lines, no counterpart needed.
          // Pre-flight : at least one entry must be included.
          const includedCount = (odEntriesParsed.entries || []).filter(e => e.included).length;
          if (includedCount === 0) {
            toast.error('Aucune ecriture cochee. Cochez au moins une ecriture a importer.');
            setCommitting(false);
            return;
          }
        } else {
          // Format Liste des depenses : every included entry MUST have a counterpart.
          const missing = (odEntriesParsed.entries || []).filter(e => !(e.counterpart_account || '').trim());
          if (missing.length > 0) {
            toast.error(`${missing.length} ecriture(s) sans contrepartie. Definissez le compte de contrepartie pour chaque ligne avant validation.`);
            setCommitting(false);
            return;
          }
        }
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-od-entries`, {
          entries: odEntriesParsed.entries,
        });
        const m = r.data;
        toast.success(
          `${m.inserted} ecriture(s) OD year-end creee(s)` +
          (m.skipped ? ` - ${m.skipped} ignoree(s) (${isJournalOd ? 'doublons ou exclues' : 'doublons'})` : '') +
          (m.pcmn_created ? ` - ${m.pcmn_created} compte(s) PCMN auto-ajoutes` : '')
        );
        if (m.errors?.length) {
          toast.error(`${m.errors.length} erreur(s) : ${m.errors[0].error}`);
        }
      } else if (step.key === 'fiscal_year') {
        if (!fyForm.name || !fyForm.start_date || !fyForm.end_date) {
          toast.error('Nom, date debut et date fin sont obligatoires');
          setCommitting(false);
          return;
        }
        r = await api.post(`/import-wizard/sessions/${session.id}/commit-fiscal-year`, fyForm);
        toast.success(`Exercice "${r.data.name}" cree`);
      }
      // Refresh session to update step counters
      const sRes = await api.get('/import-wizard/sessions/active', { params: { copropriete_id: effectiveCopro } });
      setSession(sRes.data);
      // advance
      if (stepIdx < STEPS.length - 1) {
        setStepIdx(stepIdx + 1);
        setSniffResult(null);
        setMapping({});
        setNaturesParsed([]);
        setBudgetSections([]);
        setKeysParsed([]);
        setSuppliersParsed([]);
        setInvoicesParsed([]);
        setJournalsParsed([]);
        setBalanceParsed({ actif: [], passif: [], total_actif: 0, total_passif: 0, balanced: false, period_end_date: '' });
        setOdEntriesParsed({ format: '', entries: [], total_count: 0, total_amount: 0, period_start: '', period_end: '' });
        setUploadMode(null);
      } else {
        // Final step : finish
        await api.post(`/import-wizard/sessions/${session.id}/finish`);
        toast.success('Import termine ! Toutes les donnees sont integrees.');
        navigate(`/?copropriete_id=${effectiveCopro}`);
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur de validation');
    } finally {
      setCommitting(false);
    }
  };

  const handleRollback = async () => {
    if (!session) return;
    if (!window.confirm('ATTENTION : Cela va supprimer TOUT ce qui a ete importe lors de cette session.\n\nLes donnees existantes (creees avant le wizard) ne sont pas touchees.\n\nContinuer ?')) return;
    try {
      const r = await api.delete(`/import-wizard/sessions/${session.id}`);
      toast.success(`Rollback effectue : ${Object.values(r.data.report || {}).reduce((a, b) => a + b, 0)} documents supprimes`);
      navigate(`/?copropriete_id=${effectiveCopro}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur rollback');
    }
  };

  if (!effectiveCopro) {
    return (
      <div className="max-w-2xl mx-auto mt-12 p-6 bg-amber-50 border border-amber-200 rounded-md text-center">
        <FileWarning size={36} className="mx-auto text-amber-600 mb-3" />
        <p className="text-sm text-slate-700">Selectionnez une copropriete avant de lancer le wizard d&apos;import.</p>
      </div>
    );
  }

  if (loading) {
    return <div className="p-8 text-center text-slate-400"><Loader2 size={20} className="inline animate-spin mr-2" /> Chargement...</div>;
  }

  return (
    <div data-testid="import-wizard-page" className="max-w-[1600px] mx-auto px-4">
      {/* Header */}
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">Wizard de reprise Optipro / Sogis</h1>
          <p className="page-subtitle">Importez vos donnees existantes en quelques etapes. Rollback complet possible a tout moment.</p>
        </div>
        <Button variant="outline" size="sm" onClick={handleRollback} className="text-red-700 border-red-300 hover:bg-red-50" data-testid="rollback-btn">
          <RotateCcw size={14} className="mr-1" /> Annuler l&apos;import
        </Button>
      </div>

      {/* Stepper */}
      <div className="flex items-center mb-6 gap-1 overflow-x-auto pb-2">
        {STEPS.map((s, i) => {
          const Icon = s.icon;
          const isDone = session?.steps?.[s.key]?.count > 0;
          const isActive = i === stepIdx;
          return (
            <div key={s.key} className="flex items-center" data-testid={`step-${s.key}`}>
              <div className={`flex items-center gap-2 px-3 py-2 rounded-md ${isActive ? 'bg-[#0055FF] text-white' : isDone ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>
                {isDone ? <CheckCircle2 size={16} /> : <Icon size={16} />}
                <div>
                  <div className="text-xs font-semibold">{s.label}</div>
                  {isDone && <div className="text-[10px] opacity-80">{session.steps[s.key].count} importes</div>}
                </div>
              </div>
              {i < STEPS.length - 1 && <ChevronRight size={14} className="text-slate-300 mx-1" />}
            </div>
          );
        })}
      </div>

      {/* Step content */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2">
            {(() => { const I = step.icon; return <I size={18} className="text-[#0055FF]" />; })()}
            Etape {stepIdx + 1} / {STEPS.length} : {step.label}
            {step.kind === 'csv' && <Badge variant="outline" className="text-[10px]">CSV</Badge>}
            {step.kind === 'pdf' && <Badge variant="outline" className="text-[10px]">PDF</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!sniffResult && step.key === 'od_entries' && (
            <div className="bg-blue-50 border border-blue-200 rounded p-3 text-[12px] text-blue-900 space-y-1.5">
              <div className="font-semibold flex items-center gap-1.5">
                <ClipboardList size={14} /> A quoi sert cette etape ?
              </div>
              <div>
                Les OD year-end sont des <strong>ecritures comptables de cloture d&apos;exercice</strong> qui
                n&apos;ont <strong>pas de facture associee</strong> (N&deg; piece = &quot;-&quot; dans Optipro) :
              </div>
              <ul className="list-disc pl-5 text-[11px] space-y-0.5">
                <li><strong>Charges a reporter / Annulation</strong> (compte 490) : depenses payees en N-1 imputables a N, ou inversement</li>
                <li><strong>Factures a recevoir (FAR)</strong> (compte 444) : services consommes en N mais facturees seulement en N+1</li>
                <li><strong>Nettoyage de bilan AGS</strong> (compte 417) : apurement de creances douteuses decide en assemblee</li>
                <li><strong>Ajustements sinistres</strong> (494, 499) : cloture de provisions et remboursements assurance</li>
                <li><strong>Imputations privatives</strong> (compte 643 vers 410) : transfert des frais privatifs vers les coproprietaires concernes</li>
              </ul>
              <div className="text-[11px] pt-1">
                Sans cette etape, la <strong>Liste des depenses</strong> de l&apos;app ne correspondra pas au total Optipro
                (par ex. -767 EUR d&apos;ecart sur Gaura 2025). Le wizard les detecte et propose une contrepartie automatique
                par mot-cle ; vous validez/corrigez ligne par ligne avant commit.
              </div>
            </div>
          )}
          {!sniffResult && step.kind !== 'form' && (
            <div className="border-2 border-dashed border-slate-300 rounded-md p-8 text-center">
              <Upload size={32} className="mx-auto text-slate-400 mb-2" />
              <p className="text-sm text-slate-600 mb-3">
                {step.kind === 'csv'
                  ? 'Chargez le fichier CSV exporte d\'Optipro/Sogis'
                  : step.kind === 'csv_or_pdf'
                    ? 'Chargez le fichier CSV ou PDF exporte d\'Optipro/Sogis'
                    : step.kind === 'csv_invoices'
                      ? 'Chargez le CSV "facture_xxx.csv" Optipro'
                      : step.kind === 'csv_journals'
                        ? 'Chargez le CSV "journaux_xxx.csv" Optipro (journaux financiers)'
                        : step.kind === 'pdf_balance'
                          ? 'Chargez le PDF "Bilan comptable au JJ/MM/AAAA" - utilise pour generer l\'OD d\'ouverture (A-Nouveau)'
                          : step.kind === 'pdf_od_entries'
                            ? <span>Chargez le <strong>meme PDF &laquo;Liste des depenses&raquo;</strong> que pour les factures.<br/>Le wizard extrait <strong>uniquement les lignes avec N&deg; piece = &quot;-&quot;</strong> (= ecritures OD year-end : charges a reporter, FAR, AGS, sinistres, imputations privatives) qui ne sont pas des factures.</span>
                            : `Chargez le PDF (${step.label})`}
              </p>
              <input
                type="file"
                id="file-input"
                className="hidden"
                accept={
                  step.kind === 'csv' || step.kind === 'csv_invoices' || step.kind === 'csv_journals'
                    ? '.csv,.txt'
                    : step.kind === 'csv_or_pdf'
                      ? '.csv,.txt,.pdf'
                      : '.pdf'
                }
                onChange={handleFileChange}
                data-testid="file-input"
              />
              {step.kind === 'csv_or_pdf' ? (
                <div className="flex justify-center gap-2">
                  <Button
                    onClick={() => { setUploadMode('csv'); setTimeout(() => document.getElementById('file-input').click(), 0); }}
                    disabled={sniffing}
                    variant="outline"
                    className="border-blue-300 text-blue-700 hover:bg-blue-50"
                    data-testid="upload-csv-btn"
                  >
                    <Upload size={14} className="mr-1" /> Choisir CSV
                  </Button>
                  <Button
                    onClick={() => { setUploadMode('pdf'); setTimeout(() => document.getElementById('file-input').click(), 0); }}
                    disabled={sniffing}
                    className="bg-emerald-600 hover:bg-emerald-700"
                    data-testid="upload-pdf-btn"
                  >
                    {sniffing ? <><Loader2 size={14} className="animate-spin mr-1" /> Analyse...</> : <><Upload size={14} className="mr-1" /> Choisir PDF</>}
                  </Button>
                </div>
              ) : (
                <Button onClick={() => document.getElementById('file-input').click()} disabled={sniffing} className="bg-[#0055FF] hover:bg-[#0040CC]">
                  {sniffing ? <><Loader2 size={14} className="animate-spin mr-1" /> Analyse en cours...</> : <><Upload size={14} className="mr-1" /> Choisir le fichier</>}
                </Button>
              )}
            </div>
          )}

          {step.kind === 'form' && step.key === 'fiscal_year' && (
            <FiscalYearForm fyForm={fyForm} setFyForm={setFyForm} />
          )}

          {sniffResult && (step.kind === 'csv' || (step.kind === 'csv_or_pdf' && uploadMode === 'csv')) && (
            <CsvMappingView
              sniff={sniffResult}
              targetFields={TARGET_FIELDS[step.key] || []}
              mapping={mapping}
              setMapping={setMapping}
            />
          )}

          {sniffResult && step.key === 'suppliers' && uploadMode === 'pdf' && (
            <SuppliersPdfPreview suppliers={suppliersParsed} setSuppliers={setSuppliersParsed} />
          )}

          {sniffResult && step.key === 'invoices' && (
            <InvoicesPreview invoices={invoicesParsed} setInvoices={setInvoicesParsed} />
          )}

          {sniffResult && step.key === 'journals' && (
            <JournalsPreview transactions={journalsParsed} setTransactions={setJournalsParsed} />
          )}

          {sniffResult && step.key === 'opening_balance' && (
            <OpeningBalancePreview balance={balanceParsed} setBalance={setBalanceParsed} />
          )}

          {sniffResult && step.key === 'od_entries' && (
            <OdEntriesPreview odData={odEntriesParsed} setOdData={setOdEntriesParsed} />
          )}

          {sniffResult && step.key === 'natures' && (
            <NaturesPreview natures={naturesParsed} setNatures={setNaturesParsed} />
          )}

          {sniffResult && step.key === 'budget' && (
            <BudgetPreview sections={budgetSections} setSections={setBudgetSections} />
          )}

          {sniffResult && step.key === 'distribution_keys' && (
            <KeysPreview
              keys={keysParsed}
              setKeys={setKeysParsed}
              onAddPdf={() => {
                const inp = document.getElementById('file-input');
                if (inp) {
                  // marqueur pour handleFileChange : prochain upload en mode append
                  inp.dataset.appendMode = 'true';
                  inp.value = '';
                  inp.click();
                }
              }}
            />
          )}
        </CardContent>
      </Card>

      {/* Footer */}
      <div className="flex items-center justify-between mt-4">
        <Button variant="outline" size="sm" disabled={stepIdx === 0} onClick={() => { setStepIdx(stepIdx - 1); setSniffResult(null); setMapping({}); setNaturesParsed([]); setSuppliersParsed([]); setInvoicesParsed([]); setJournalsParsed([]); setBalanceParsed({ actif: [], passif: [], total_actif: 0, total_passif: 0, balanced: false, period_end_date: '' }); setOdEntriesParsed({ format: '', entries: [], total_count: 0, total_amount: 0, period_start: '', period_end: '' }); setUploadMode(null); }} data-testid="prev-step">
          <ChevronLeft size={14} className="mr-1" /> Etape precedente
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { setSniffResult(null); setMapping({}); setNaturesParsed([]); setSuppliersParsed([]); setInvoicesParsed([]); setJournalsParsed([]); setBalanceParsed({ actif: [], passif: [], total_actif: 0, total_passif: 0, balanced: false, period_end_date: '' }); setOdEntriesParsed({ format: '', entries: [], total_count: 0, total_amount: 0, period_start: '', period_end: '' }); setUploadMode(null); }} data-testid="reset-step">
            <X size={14} className="mr-1" /> Annuler ce fichier
          </Button>
          {(() => {
            // Detect if the current step has already been validated by the
            // session. If yes (and the user has not loaded a new file / changed
            // the form), the click on the primary button should simply go to
            // the next step WITHOUT re-running the commit (which would error
            // out e.g. "L'exercice 2025 existe deja"). The user can still
            // "Annuler ce fichier" to reset and re-validate fresh.
            const stepData = session?.steps?.[step.key];
            const stepAlreadyDone = !!(stepData?.count > 0 || stepData?.inserted > 0 || stepData?.fiscal_year_id);
            const hasNewData = !!sniffResult;
            if (stepAlreadyDone && !hasNewData && stepIdx < STEPS.length - 1) {
              return (
                <Button
                  onClick={() => { setStepIdx(stepIdx + 1); setSniffResult(null); }}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white"
                  data-testid="next-step-validated"
                  title="Etape deja validee - on passe a la suivante sans rejouer l'import."
                >
                  Etape deja validee &mdash; Continuer <ChevronRight size={14} className="ml-1" />
                </Button>
              );
            }
            if (step.optional && stepIdx < STEPS.length - 1 && !sniffResult && step.kind !== 'form') {
              return (
                <Button variant="outline" size="sm" onClick={() => { setStepIdx(stepIdx + 1); setSniffResult(null); }} data-testid="skip-step">
                  Passer cette etape
                </Button>
              );
            }
            return (
              <Button
                disabled={(step.kind === 'form' ? !fyForm.name : !sniffResult) || committing}
                onClick={handleCommit}
                className="bg-[#0055FF] hover:bg-[#0040CC]"
                data-testid="commit-step"
              >
                {committing ? <><Loader2 size={14} className="animate-spin mr-1" /> Import...</> : (
                  stepIdx === STEPS.length - 1 ? <>Terminer le wizard <CheckCircle2 size={14} className="ml-1" /></> : <>Valider et continuer <ChevronRight size={14} className="ml-1" /></>
                )}
              </Button>
            );
          })()}
        </div>
      </div>
    </div>
  );
}

function CsvMappingView({ sniff, targetFields, mapping, setMapping }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3 text-xs">
        <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Encoding</div><div className="font-mono font-semibold">{sniff.encoding}</div></div>
        <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Separateur</div><div className="font-mono font-semibold">&laquo;{sniff.separator === ',' ? ',' : sniff.separator === ';' ? ';' : 'TAB'}&raquo;</div></div>
        <div className="bg-slate-50 rounded p-2"><div className="text-slate-500">Lignes detectees</div><div className="font-mono font-semibold">{sniff.total_rows}</div></div>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <strong>Mapping des colonnes :</strong> pour chaque champ cible (a gauche), choisissez la colonne du CSV (a droite) qui contient cette donnee. Laissez vide pour ignorer.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {targetFields.map(f => (
          <div key={f.key} className="flex items-center gap-2">
            <label className="text-xs font-medium text-slate-700 w-40 flex-shrink-0">{f.label}</label>
            <Select
              value={mapping[f.key]?.toString() || '__none__'}
              onValueChange={(v) => setMapping({ ...mapping, [f.key]: v === '__none__' ? '' : v })}
            >
              <SelectTrigger className="h-8 text-xs" data-testid={`map-${f.key}`}>
                <SelectValue placeholder="-- Ignorer --" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">-- Ignorer --</SelectItem>
                {sniff.headers.map((h, i) => (
                  <SelectItem key={i} value={i.toString()}>{h || `Colonne ${i+1}`}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ))}
      </div>

      <div className="border border-slate-200 rounded overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50">
            <tr>
              {sniff.headers.map((h, i) => (
                <th key={i} className="px-2 py-1 text-left font-medium text-slate-700 border-r border-slate-200 whitespace-nowrap">
                  {h || `Col ${i+1}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sniff.rows.slice(0, 8).map((row, ri) => (
              <tr key={ri} className="border-t border-slate-100">
                {sniff.headers.map((_, ci) => (
                  <td key={ci} className="px-2 py-1 text-slate-600 border-r border-slate-100 truncate max-w-[180px]" title={row[ci]}>
                    {row[ci]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {sniff.total_rows > 8 && <div className="text-[10px] text-slate-400 px-2 py-1 bg-slate-50">... et {sniff.total_rows - 8} autres lignes</div>}
      </div>
    </div>
  );
}

function NaturesPreview({ natures, setNatures }) {
  if (!natures?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucune nature extraite du PDF.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <strong>Verifiez puis modifiez si necessaire</strong> les natures extraites du PDF. Vous pouvez editer chaque ligne directement, ou supprimer celles qui ne sont pas pertinentes.
      </div>
      <div className="border border-slate-200 rounded overflow-x-auto max-h-96 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-2 py-1 text-left">Code</th>
              <th className="px-2 py-1 text-left">Libelle</th>
              <th className="px-2 py-1 text-left">Compte PCMN</th>
              <th className="px-2 py-1 text-left">TVA</th>
              <th className="px-2 py-1 text-center">% Occ</th>
              <th className="px-2 py-1 text-center">% Prop</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {natures.map((n, i) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="px-1 py-1"><input value={n.code} onChange={e => updateNature(natures, setNatures, i, 'code', e.target.value)} className="w-16 border-0 bg-transparent font-mono" data-testid={`nat-code-${i}`} /></td>
                <td className="px-1 py-1"><input value={n.libelle} onChange={e => updateNature(natures, setNatures, i, 'libelle', e.target.value)} className="w-full border-0 bg-transparent" data-testid={`nat-libelle-${i}`} /></td>
                <td className="px-1 py-1"><input value={n.account_number} onChange={e => updateNature(natures, setNatures, i, 'account_number', e.target.value)} className="w-20 border-0 bg-transparent font-mono" data-testid={`nat-account-${i}`} /></td>
                <td className="px-1 py-1"><input value={n.vat_code || ''} onChange={e => updateNature(natures, setNatures, i, 'vat_code', e.target.value)} className="w-12 border-0 bg-transparent font-mono" /></td>
                <td className="px-1 py-1"><input type="number" value={n.part_occupant} onChange={e => updateNature(natures, setNatures, i, 'part_occupant', parseFloat(e.target.value) || 0)} className="w-14 border-0 bg-transparent text-right" /></td>
                <td className="px-1 py-1"><input type="number" value={n.part_proprietaire} onChange={e => updateNature(natures, setNatures, i, 'part_proprietaire', parseFloat(e.target.value) || 0)} className="w-14 border-0 bg-transparent text-right" /></td>
                <td className="px-1 py-1"><button onClick={() => setNatures(natures.filter((_, idx) => idx !== i))} className="text-red-500 hover:text-red-700" data-testid={`nat-del-${i}`}><X size={12} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-slate-500">{natures.length} nature(s) detectee(s)</div>
    </div>
  );
}

function updateNature(arr, setArr, idx, field, value) {
  const next = arr.map((n, i) => i === idx ? { ...n, [field]: value } : n);
  setArr(next);
}

// ============== OPENING BALANCE PREVIEW (Step I - OD ouverture / Bilan) ==============
function OpeningBalancePreview({ balance, setBalance }) {
  if (!balance.actif?.length && !balance.passif?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucun compte detecte dans le bilan PDF.
      </div>
    );
  }
  // Leaf accounts = sub-accounts + main accounts that have NO children (same rule
  // as the backend commit_opening_balance endpoint, to avoid double-counting
  // parent + children sums).
  const leafOnly = (rows) => {
    const subCodes = rows.filter(r => r.is_subaccount).map(r => String(r.account || ''));
    return rows.filter(r => {
      if (r.is_subaccount) return true;
      const code = String(r.account || '');
      const hasChildren = subCodes.some(sc => sc.startsWith(code) && sc.length > code.length);
      return !hasChildren;
    });
  };
  const totalA = leafOnly(balance.actif).reduce((s, a) => s + (parseFloat(a.amount) || 0), 0);
  const totalP = leafOnly(balance.passif).reduce((s, p) => s + (parseFloat(p.amount) || 0), 0);
  const isBalanced = Math.abs(totalA - totalP) < 0.01;

  const updSide = (side, idx, field, value) => {
    const v = field === 'amount' ? (parseFloat(value) || 0) : value;
    const next = { ...balance, [side]: balance[side].map((it, i) => i === idx ? { ...it, [field]: v } : it) };
    setBalance(next);
  };
  const delSide = (side, idx) => {
    setBalance({ ...balance, [side]: balance[side].filter((_, i) => i !== idx) });
  };

  return (
    <div className="space-y-3">
      <div className={`border rounded p-3 text-xs flex justify-between items-center ${isBalanced ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
        <div>
          <div className="font-semibold">
            Bilan au <span className="font-mono">{balance.period_end_date || '—'}</span>
          </div>
          <div className="text-[11px] mt-0.5">
            {isBalanced
              ? `Equilibre OK. L'OD d'ouverture (type AN) sera generee avec ${balance.actif.length + balance.passif.length} ligne(s).`
              : 'ATTENTION : le bilan est desequilibre. Ajustez les montants avant validation.'}
          </div>
        </div>
        <div className="font-mono text-right">
          <div>Total Actif : <span className="font-semibold">{totalA.toFixed(2)}</span></div>
          <div>Total Passif : <span className="font-semibold">{totalP.toFixed(2)}</span></div>
          <div className={`text-[10px] ${isBalanced ? 'text-emerald-700' : 'text-red-700'}`}>
            Ecart : {(totalA - totalP).toFixed(2)} EUR
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {/* ACTIF (Debit) */}
        <div className="border border-slate-200 rounded">
          <div className="bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between">
            <span>ACTIF (Debit)</span>
            <span className="font-mono">{totalA.toFixed(2)} EUR</span>
          </div>
          <table className="w-full text-[11px]">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-1 py-1 text-left w-20">Compte</th>
                <th className="px-1 py-1 text-left">Libelle</th>
                <th className="px-1 py-1 text-right w-24">Montant</th>
                <th className="w-6"></th>
              </tr>
            </thead>
            <tbody>
              {balance.actif.map((a, i) => (
                <tr key={i} className={`border-t border-slate-100 ${a.is_subaccount ? 'pl-4 text-slate-600' : 'font-semibold'}`}>
                  <td className="px-1 py-0.5 font-mono text-[10px]">{a.is_subaccount ? '  ' : ''}<input value={a.account} onChange={e => updSide('actif', i, 'account', e.target.value)} className="w-16 border-0 bg-transparent font-mono text-[10px]" /></td>
                  <td className="px-1 py-0.5"><input value={a.label} onChange={e => updSide('actif', i, 'label', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                  <td className="px-1 py-0.5"><input type="number" step="0.01" value={a.amount} onChange={e => updSide('actif', i, 'amount', e.target.value)} className="w-24 border-0 bg-transparent text-right font-mono" /></td>
                  <td className="px-0 py-0.5"><button onClick={() => delSide('actif', i)} className="text-red-500 hover:text-red-700"><X size={11} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* PASSIF (Credit) */}
        <div className="border border-slate-200 rounded">
          <div className="bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between">
            <span>PASSIF (Credit)</span>
            <span className="font-mono">{totalP.toFixed(2)} EUR</span>
          </div>
          <table className="w-full text-[11px]">
            <thead className="bg-slate-50">
              <tr>
                <th className="px-1 py-1 text-left w-20">Compte</th>
                <th className="px-1 py-1 text-left">Libelle</th>
                <th className="px-1 py-1 text-right w-24">Montant</th>
                <th className="w-6"></th>
              </tr>
            </thead>
            <tbody>
              {balance.passif.map((p, i) => (
                <tr key={i} className={`border-t border-slate-100 ${p.is_subaccount ? 'pl-4 text-slate-600' : 'font-semibold'}`}>
                  <td className="px-1 py-0.5 font-mono text-[10px]"><input value={p.account} onChange={e => updSide('passif', i, 'account', e.target.value)} className="w-16 border-0 bg-transparent font-mono text-[10px]" /></td>
                  <td className="px-1 py-0.5"><input value={p.label} onChange={e => updSide('passif', i, 'label', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                  <td className="px-1 py-0.5"><input type="number" step="0.01" value={p.amount} onChange={e => updSide('passif', i, 'amount', e.target.value)} className="w-24 border-0 bg-transparent text-right font-mono" /></td>
                  <td className="px-0 py-0.5"><button onClick={() => delSide('passif', i)} className="text-red-500 hover:text-red-700"><X size={11} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="text-[11px] text-slate-500 italic">
        Une seule ecriture comptable de type <strong>AN</strong> (A-Nouveau) sera creee au 1er jour de l&apos;exercice fiscal selectionne,
        avec toutes les lignes Actif en DEBIT et toutes les lignes Passif en CREDIT.
        Les comptes principaux (ex. 410) qui regroupent des sous-comptes (ex. 4100960, 4100962) sont automatiquement
        exclus du commit pour eviter le double comptage : seuls les comptes detailles (feuilles) sont retenus.
      </div>
    </div>
  );
}

// ============== OD YEAR-END PREVIEW (Step J - Liste depenses OD) ==============
function OdEntriesPreview({ odData, setOdData }) {
  if (!odData?.entries?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucune ecriture OD year-end detectee dans le PDF.
      </div>
    );
  }
  // Dispatch on format : "od_journal" (preferred, explicit balanced lines)
  // vs "expense_list" (legacy, charge+counterpart dropdown).
  if (odData.format === 'od_journal') {
    return <OdJournalPreview odData={odData} setOdData={setOdData} />;
  }
  return <OdExpenseListPreview odData={odData} setOdData={setOdData} />;
}

// ============== Format A : Journal OD (full balanced lines) ==============
function OdJournalPreview({ odData, setOdData }) {
  const toggleIncluded = (idx) => {
    const next = odData.entries.map((e, i) => i === idx ? { ...e, included: !e.included } : e);
    setOdData({ ...odData, entries: next });
  };
  const includedCount = odData.entries.filter(e => e.included).length;
  const excludedCount = odData.entries.length - includedCount;
  const totalIncluded = odData.entries
    .filter(e => e.included)
    .reduce((s, e) => s + (Number(e.total_debit) || 0), 0);

  return (
    <div className="space-y-3" data-testid="od-journal-preview">
      <div className="border border-emerald-200 bg-emerald-50 rounded p-3 text-xs flex justify-between items-center">
        <div>
          <div className="font-semibold">
            Journal OD Optipro du <span className="font-mono">{odData.period_start || '—'}</span> au <span className="font-mono">{odData.period_end || '—'}</span>
          </div>
          <div className="text-[11px] mt-0.5">
            <strong>{odData.entries.length} ecritures detectees</strong> avec contreparties explicites.
            {' '}{includedCount} a importer, {excludedCount} exclues (ecritures de cloture - decoche pour reactiver).
          </div>
        </div>
        <div className="font-mono text-right text-[11px]">
          <div>Total a importer : <span className="font-semibold">{totalIncluded.toFixed(2)} EUR</span></div>
        </div>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded p-2 text-[11px] text-blue-900">
        <strong>Format detecte :</strong> Journal comptable OD Optipro - les contreparties sont deja explicites
        dans le PDF, aucune saisie manuelle requise. Les ecritures de <strong>cloture annuelle</strong> (transfert
        des charges vers 701 puis vers les coproprietaires) sont decochees par defaut pour eviter les doublons
        avec les factures AC + entrees AN deja importees.
      </div>

      <div className="border border-slate-200 rounded overflow-x-auto max-h-[70vh] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-center w-20">A importer</th>
              <th className="px-3 py-2 text-left w-28">Date</th>
              <th className="px-3 py-2 text-left w-20">Ref</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-right w-28">Total D/C</th>
              <th className="px-3 py-2 text-left w-28">Statut</th>
            </tr>
          </thead>
          <tbody>
            {odData.entries.map((e, i) => {
              const balanced = !!e.balanced;
              return (
                <Fragment key={i}>
                  <tr className={`border-t border-slate-100 ${!e.included ? 'bg-slate-50 text-slate-400' : ''}`} data-testid={`od-journal-row-${i}`}>
                    <td className="px-3 py-1.5 text-center">
                      <input
                        type="checkbox"
                        checked={!!e.included}
                        onChange={() => toggleIncluded(i)}
                        disabled={!balanced}
                        className="w-4 h-4"
                        data-testid={`od-include-${i}`}
                      />
                    </td>
                    <td className="px-3 py-1.5 font-mono">{e.date_display || e.date}</td>
                    <td className="px-3 py-1.5 font-mono text-[11px]">{e.reference}</td>
                    <td className="px-3 py-1.5">
                      {e.description}
                      {e.exclusion_reason && (
                        <div className="text-[10px] text-amber-700 italic mt-0.5">⚠ {e.exclusion_reason}</div>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold">
                      {(Number(e.total_debit) || 0).toFixed(2)}
                    </td>
                    <td className="px-3 py-1.5">
                      {balanced ? (
                        <span className="text-emerald-700 text-[11px]">✓ equilibree</span>
                      ) : (
                        <span className="text-red-700 text-[11px]">✗ desequilibree</span>
                      )}
                    </td>
                  </tr>
                  {/* Lines (indented, smaller) */}
                  {e.included && (e.lines || []).map((ln, li) => (
                    <tr key={`${i}-${li}`} className="text-[11px] text-slate-600 bg-slate-50/30">
                      <td colSpan={2}></td>
                      <td className="px-3 py-0.5 font-mono">{ln.account_number}</td>
                      <td className="px-3 py-0.5">
                        {ln.account_name}
                        {ln.auxiliary_info && <span className="text-slate-400 ml-2">| {ln.auxiliary_info}</span>}
                      </td>
                      <td className="px-3 py-0.5 text-right font-mono">
                        {ln.debit > 0 && <span className="text-slate-700">D {ln.debit.toFixed(2)}</span>}
                        {ln.credit > 0 && <span className="text-slate-700">C {ln.credit.toFixed(2)}</span>}
                      </td>
                      <td></td>
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="text-[11px] text-slate-500 italic">
        Chaque ecriture sera importee <strong>telle quelle</strong> (lignes deja equilibrees par Optipro) avec
        sa reference d&apos;origine conservee dans le champ <code>optipro_reference</code>. Les coproprietaires
        et fournisseurs sont rattaches automatiquement via leur <code>auxiliary_code</code> (C1996, F0145, etc.).
      </div>
    </div>
  );
}

// ============== Format B : Liste des depenses (legacy) ==============
function OdExpenseListPreview({ odData, setOdData }) {
  const upd = (idx, field, value) => {
    const next = odData.entries.map((e, i) => i === idx ? { ...e, [field]: value } : e);
    setOdData({ ...odData, entries: next });
  };
  const del = (idx) => {
    setOdData({ ...odData, entries: odData.entries.filter((_, i) => i !== idx) });
  };
  // Common Belgian PCMN counterpart accounts for OD
  const counterpartChoices = [
    { value: '', label: '-- Choisir --' },
    { value: '490|Charges a reporter', label: '490 - Charges a reporter' },
    { value: '491|Produits a reporter', label: '491 - Produits a reporter' },
    { value: '444|Factures a recevoir', label: '444 - Factures a recevoir (FAR)' },
    { value: '417|Creances douteuses', label: '417 - Creances douteuses (AGS)' },
    { value: '410|Coproprietaires', label: '410 - Coproprietaires (imputation)' },
    { value: '494|Provisions sinistres', label: '494 - Provisions sinistres' },
    { value: '494001|SIN 202200724 INONDATION', label: '494001 - SIN 202200724 INONDATION' },
    { value: '499|Provisions diverses', label: '499 - Provisions diverses' },
    { value: '499603|Sinistre garage - Pompe de relevage', label: '499603 - Sinistre garage - Pompe de relevage' },
    { value: '4990|Provisions sinistres diverses', label: '4990 - Provisions sinistres diverses' },
    { value: '4991|Arrondis crediteurs', label: '4991 - Arrondis crediteurs' },
  ];

  const missing = odData.entries.filter(e => !(e.counterpart_account || '').trim()).length;
  const sumPositive = odData.entries.filter(e => e.amount > 0).reduce((s, e) => s + e.amount, 0);
  const sumNegative = odData.entries.filter(e => e.amount < 0).reduce((s, e) => s + e.amount, 0);

  const confidenceBadge = (conf) => {
    if (conf === 'high') return <span className="text-emerald-700 text-[9px]">[OK]</span>;
    if (conf === 'medium') return <span className="text-amber-700 text-[9px]">[~]</span>;
    if (conf === 'low') return <span className="text-orange-700 text-[9px]">[?]</span>;
    return <span className="text-red-700 text-[9px]">[!]</span>;
  };

  return (
    <div className="space-y-3" data-testid="od-entries-preview">
      <div className={`border rounded p-3 text-xs flex justify-between items-center ${missing === 0 ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
        <div>
          <div className="font-semibold">
            OD year-end du <span className="font-mono">{odData.period_start || '—'}</span> au <span className="font-mono">{odData.period_end || '—'}</span>
          </div>
          <div className="text-[11px] mt-0.5">
            {missing === 0
              ? `Toutes les contreparties sont definies. ${odData.entries.length} ecriture(s) OD pretes a etre validees.`
              : `${missing} ecriture(s) sans contrepartie. Definissez le compte pour chaque ligne en rouge avant validation.`}
          </div>
        </div>
        <div className="font-mono text-right text-[11px]">
          <div>Positifs : <span className="font-semibold text-slate-700">+{sumPositive.toFixed(2)}</span></div>
          <div>Negatifs : <span className="font-semibold text-slate-700">{sumNegative.toFixed(2)}</span></div>
          <div>Total net : <span className="font-semibold">{odData.total_amount.toFixed(2)} EUR</span></div>
        </div>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded p-2 text-[11px] text-blue-900">
        <strong>Auto-detection :</strong> les contreparties marquees [OK] sont auto-detectees avec haute confiance.
        Les lignes [~] [?] [!] necessitent votre choix. Comptes proposes : 490 (charges a reporter), 444 (FAR),
        417 (AGS), 494/499 (sinistres), 410 (imputation copro).
      </div>

      <div className="border border-slate-200 rounded overflow-x-auto max-h-[480px] overflow-y-auto">
        <table className="w-full text-[11px]">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-2 py-1 text-left w-24">Date</th>
              <th className="px-2 py-1 text-left">Libelle</th>
              <th className="px-2 py-1 text-left w-20">Compte</th>
              <th className="px-2 py-1 text-right w-24">Montant</th>
              <th className="px-2 py-1 text-left w-72">Contrepartie</th>
              <th className="px-2 py-1 text-right w-12">Conf</th>
              <th className="w-6"></th>
            </tr>
          </thead>
          <tbody>
            {odData.entries.map((e, i) => {
              const hasCounter = !!(e.counterpart_account || '').trim();
              const cpVal = e.counterpart_account ? `${e.counterpart_account}|${e.counterpart_account_name || ''}` : '';
              const libelle = e.libelle || '';
              return (
                <tr key={i} className={`border-t border-slate-100 ${!hasCounter ? 'bg-red-50' : ''}`} data-testid={`od-row-${i}`}>
                  <td className="px-2 py-0.5 font-mono">{e.date}</td>
                  <td className="px-2 py-0.5 text-[10px]" title={libelle}>{libelle.length > 50 ? libelle.slice(0, 50) + '...' : libelle}</td>
                  <td className="px-2 py-0.5 font-mono">
                    <div>{e.account_number}</div>
                    <div className="text-[9px] text-slate-500">{(e.account_name || '').slice(0, 18)}</div>
                  </td>
                  <td className={`px-2 py-0.5 text-right font-mono ${e.amount < 0 ? 'text-red-700' : 'text-slate-800'}`}>{(Number(e.amount) || 0).toFixed(2)}</td>
                  <td className="px-2 py-0.5">
                    <select
                      value={cpVal}
                      onChange={ev => {
                        const [acc, name] = (ev.target.value || '|').split('|');
                        upd(i, 'counterpart_account', acc);
                        upd(i, 'counterpart_account_name', name || '');
                      }}
                      className={`w-full text-[10px] border ${hasCounter ? 'border-slate-200' : 'border-red-300 bg-red-50'} rounded px-1 py-0.5`}
                      data-testid={`od-counterpart-${i}`}
                    >
                      {counterpartChoices.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                      {/* If suggested isn't in standard list, keep it as custom */}
                      {e.counterpart_account && !counterpartChoices.find(c => c.value.startsWith(e.counterpart_account + '|')) && (
                        <option value={cpVal}>{e.counterpart_account} - {e.counterpart_account_name}</option>
                      )}
                    </select>
                  </td>
                  <td className="px-1 py-0.5 text-center">
                    {confidenceBadge(e.suggested_counterpart?.confidence)}
                  </td>
                  <td className="px-0 py-0.5">
                    <button onClick={() => del(i)} className="text-red-500 hover:text-red-700" data-testid={`od-del-${i}`}>
                      <X size={11} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="text-[11px] text-slate-500 italic">
        Chaque ecriture OD sera generee en double-entree equilibree :
        montant positif &rArr; <strong>DEBIT charge</strong> / <strong>CREDIT contrepartie</strong> ;
        montant negatif &rArr; <strong>DEBIT contrepartie</strong> / <strong>CREDIT charge</strong>.
        Les comptes de la liste ne sont que des suggestions standard PCMN belge ; vous pouvez en ajouter
        d&apos;autres via la page Comptabilite apres validation.
      </div>
    </div>
  );
}


// ============== INVOICES PREVIEW (Step G - Factures Optipro) ==============
function InvoicesPreview({ invoices, setInvoices }) {
  if (!invoices?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucune facture extraite du CSV.
      </div>
    );
  }
  const totalHT = invoices.reduce((a, i) => a + (parseFloat(i.montant_ht) || 0), 0);
  const totalTVAC = invoices.reduce((a, i) => a + (parseFloat(i.montant_tvac) || 0), 0);
  const uniqueSuppliers = [...new Set(invoices.map(i => i.supplier_aux_code).filter(Boolean))];
  const upd = (idx, field, value) => {
    const next = invoices.map((i, j) => j === idx ? { ...i, [field]: value } : i);
    setInvoices(next);
  };
  const del = (idx) => setInvoices(invoices.filter((_, j) => j !== idx));
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900 flex justify-between flex-wrap gap-2">
        <span><strong>{invoices.length} facture(s)</strong> detectee(s) - {uniqueSuppliers.length} fournisseur(s) distinct(s)</span>
        <span className="font-mono">HT : {totalHT.toFixed(2)} EUR | TVAC : <strong>{totalTVAC.toFixed(2)} EUR</strong></span>
      </div>
      <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded p-2">
        <AlertTriangle size={11} className="inline mr-1" /> Les factures seront auto-rattachees aux fournisseurs (via code <span className="font-mono">F0XXX</span>),
        aux cles de repartition et aux natures de depense importes precedemment.
      </div>
      <div className="border border-slate-200 rounded overflow-x-auto max-h-[420px] overflow-y-auto">
        <table className="w-full text-[11px]">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-1 py-1 text-left w-20">Date</th>
              <th className="px-1 py-1 text-left w-24">N° ext.</th>
              <th className="px-1 py-1 text-left">Fournisseur</th>
              <th className="px-1 py-1 text-left w-16">Cpte</th>
              <th className="px-1 py-1 text-left w-12">Cle</th>
              <th className="px-1 py-1 text-left w-12">Nat.</th>
              <th className="px-1 py-1 text-left">Libelle</th>
              <th className="px-1 py-1 text-right w-20">HT</th>
              <th className="px-1 py-1 text-right w-20 bg-blue-50">TVAC</th>
              <th className="px-1 py-1 text-center w-8">TVA</th>
              <th className="w-6"></th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((i, idx) => (
              <tr key={idx} className="border-t border-slate-100" data-testid={`inv-row-${idx}`}>
                <td className="px-1 py-0.5"><input value={i.date} onChange={e => upd(idx, 'date', e.target.value)} className="w-20 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5"><input value={i.external_ref} onChange={e => upd(idx, 'external_ref', e.target.value)} className="w-24 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5">
                  <div className="flex items-center gap-1">
                    {i.supplier_aux_code && <span className="font-mono text-[9px] bg-emerald-100 text-emerald-700 px-1 rounded">{i.supplier_aux_code}</span>}
                    <input value={i.supplier_name} onChange={e => upd(idx, 'supplier_name', e.target.value)} className="flex-1 border-0 bg-transparent text-[11px]" />
                  </div>
                </td>
                <td className="px-1 py-0.5"><input value={i.account_number} onChange={e => upd(idx, 'account_number', e.target.value)} className="w-16 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5"><input value={i.dist_key_code} onChange={e => upd(idx, 'dist_key_code', e.target.value)} className="w-12 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5"><input value={i.nature_code} onChange={e => upd(idx, 'nature_code', e.target.value)} className="w-12 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5"><input value={i.libelle} onChange={e => upd(idx, 'libelle', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-0.5"><input type="number" step="0.01" value={i.montant_ht} onChange={e => upd(idx, 'montant_ht', parseFloat(e.target.value) || 0)} className="w-20 border-0 bg-transparent text-right font-mono text-slate-500" /></td>
                <td className="px-1 py-0.5 bg-blue-50/40"><input type="number" step="0.01" value={i.montant_tvac} onChange={e => upd(idx, 'montant_tvac', parseFloat(e.target.value) || 0)} className="w-20 border-0 bg-transparent text-right font-mono font-semibold" /></td>
                <td className="px-1 py-0.5 text-center"><input value={i.vat_code} onChange={e => upd(idx, 'vat_code', e.target.value)} className="w-8 border-0 bg-transparent text-center font-mono text-[10px]" /></td>
                <td className="px-0 py-0.5"><button onClick={() => del(idx)} className="text-red-500 hover:text-red-700" data-testid={`inv-del-${idx}`}><X size={11} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ============== JOURNALS PREVIEW (Step H - Journaux financiers) ==============
function JournalsPreview({ transactions, setTransactions }) {
  if (!transactions?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucune transaction extraite du CSV.
      </div>
    );
  }
  const totalIn = transactions.filter(t => t.direction === 'in').reduce((a, t) => a + (parseFloat(t.amount) || 0), 0);
  const totalOut = transactions.filter(t => t.direction === 'out').reduce((a, t) => a + (parseFloat(t.amount) || 0), 0);
  const banks = [...new Set(transactions.map(t => t.bank_account).filter(Boolean))];
  const del = (idx) => setTransactions(transactions.filter((_, j) => j !== idx));
  const upd = (idx, field, value) => {
    const next = transactions.map((t, j) => j === idx ? { ...t, [field]: value } : t);
    setTransactions(next);
  };
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <div className="flex justify-between flex-wrap gap-2">
          <span><strong>{transactions.length} transaction(s)</strong> sur {banks.length} compte(s) bancaire(s) ({banks.join(', ') || 'aucun PCMN'})</span>
          <span className="font-mono">
            <span className="text-emerald-700">+{totalIn.toFixed(2)}</span> /{' '}
            <span className="text-red-700">-{totalOut.toFixed(2)}</span> EUR
          </span>
        </div>
        <div className="text-[10px] text-slate-600 mt-1 italic">
          Solde net : {(totalIn - totalOut).toFixed(2)} EUR. Les transactions seront importees comme lignes d&apos;extrait bancaire pour rapprochement ulterieur.
        </div>
      </div>
      <div className="border border-slate-200 rounded overflow-x-auto max-h-[420px] overflow-y-auto">
        <table className="w-full text-[11px]">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-1 py-1 text-left w-20">Date</th>
              <th className="px-1 py-1 text-left w-14">Doc</th>
              <th className="px-1 py-1 text-left w-12">J.</th>
              <th className="px-1 py-1 text-left w-20">Banque</th>
              <th className="px-1 py-1 text-left w-20">Cpte ctr.</th>
              <th className="px-1 py-1 text-left">Libelle</th>
              <th className="px-1 py-1 text-right w-20">Montant</th>
              <th className="px-1 py-1 text-center w-12">Sens</th>
              <th className="w-6"></th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((t, idx) => (
              <tr key={idx} className="border-t border-slate-100" data-testid={`tx-row-${idx}`}>
                <td className="px-1 py-0.5"><input value={t.date_value} onChange={e => upd(idx, 'date_value', e.target.value)} className="w-20 border-0 bg-transparent font-mono text-[10px]" /></td>
                <td className="px-1 py-0.5 font-mono text-[10px] text-slate-500">{t.num_doc}</td>
                <td className="px-1 py-0.5 font-mono text-[10px]">{t.code_journal}</td>
                <td className="px-1 py-0.5 font-mono text-[10px]">{t.bank_account || <span className="text-amber-600">!</span>}</td>
                <td className="px-1 py-0.5 font-mono text-[10px]">{t.counterparty_account}</td>
                <td className="px-1 py-0.5"><input value={t.libelle} onChange={e => upd(idx, 'libelle', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-0.5"><input type="number" step="0.01" value={t.amount} onChange={e => upd(idx, 'amount', parseFloat(e.target.value) || 0)} className="w-20 border-0 bg-transparent text-right font-mono font-semibold" /></td>
                <td className="px-1 py-0.5 text-center">
                  {t.direction === 'in' && <span className="text-emerald-700 font-bold">+</span>}
                  {t.direction === 'out' && <span className="text-red-700 font-bold">-</span>}
                  {t.direction === 'neutral' && <span className="text-slate-400">~</span>}
                </td>
                <td className="px-0 py-0.5"><button onClick={() => del(idx)} className="text-red-500 hover:text-red-700" data-testid={`tx-del-${idx}`}><X size={11} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ============== SUPPLIERS PDF PREVIEW (Step C variant for PDF) ==============
function SuppliersPdfPreview({ suppliers, setSuppliers }) {
  if (!suppliers?.length) {
    return (
      <div className="text-center py-6 text-amber-600 text-sm">
        <AlertTriangle size={24} className="inline mr-1" /> Aucun fournisseur extrait du PDF.
      </div>
    );
  }
  const update = (idx, field, value) => {
    const next = suppliers.map((s, i) => i === idx ? { ...s, [field]: value } : s);
    setSuppliers(next);
  };
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <strong>Verifiez puis modifiez si necessaire</strong> les fournisseurs extraits du PDF. Chaque ligne peut etre editee, ou supprimee.
      </div>
      <div className="border border-slate-200 rounded overflow-x-auto max-h-96 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 sticky top-0">
            <tr>
              <th className="px-2 py-1 text-left">Code aux.</th>
              <th className="px-2 py-1 text-left">Nom</th>
              <th className="px-2 py-1 text-left">Email</th>
              <th className="px-2 py-1 text-left">Telephone</th>
              <th className="px-2 py-1 text-left">Adresse</th>
              <th className="px-2 py-1 text-left">CP</th>
              <th className="px-2 py-1 text-left">Ville</th>
              <th className="px-2 py-1 text-center">Defaut</th>
              <th className="px-2 py-1"></th>
            </tr>
          </thead>
          <tbody>
            {suppliers.map((s, i) => (
              <tr key={i} className="border-t border-slate-100" data-testid={`sup-row-${i}`}>
                <td className="px-1 py-1 font-mono text-[10px] text-slate-500">{s.auxiliary_code || '-'}</td>
                <td className="px-1 py-1"><input value={s.name || ''} onChange={e => update(i, 'name', e.target.value)} className="w-full border-0 bg-transparent" data-testid={`sup-name-${i}`} /></td>
                <td className="px-1 py-1"><input value={s.email || ''} onChange={e => update(i, 'email', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-1"><input value={s.phone || ''} onChange={e => update(i, 'phone', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-1"><input value={s.address || ''} onChange={e => update(i, 'address', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-1"><input value={s.postal_code || ''} onChange={e => update(i, 'postal_code', e.target.value)} className="w-14 border-0 bg-transparent font-mono" /></td>
                <td className="px-1 py-1"><input value={s.city || ''} onChange={e => update(i, 'city', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                <td className="px-1 py-1 text-center">
                  <input type="checkbox" checked={!!s.is_default} onChange={e => update(i, 'is_default', e.target.checked)} />
                </td>
                <td className="px-1 py-1"><button onClick={() => setSuppliers(suppliers.filter((_, idx) => idx !== i))} className="text-red-500 hover:text-red-700" data-testid={`sup-del-${i}`}><X size={12} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-slate-500">{suppliers.length} fournisseur(s) detecte(s)</div>
    </div>
  );
}

// ============== FISCAL YEAR FORM (Step E) ==============
function FiscalYearForm({ fyForm, setFyForm }) {
  return (
    <div className="space-y-4">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        Definissez l&apos;exercice d&apos;ouverture (souvent l&apos;exercice courant ou le suivant). Le bilan d&apos;ouverture (Phase 3) sera repris a la date de debut.
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="form-label text-xs">Nom de l&apos;exercice *</label>
          <Input value={fyForm.name} onChange={e => setFyForm({...fyForm, name: e.target.value})} placeholder="Exercice 2026" data-testid="fy-name" />
        </div>
        <div>
          <label className="form-label text-xs">Statut</label>
          <Select value={fyForm.status} onValueChange={(v) => setFyForm({...fyForm, status: v})}>
            <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="open">Ouvert</SelectItem>
              <SelectItem value="closed">Cloture</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <label className="form-label text-xs">Date debut *</label>
          <Input type="date" value={fyForm.start_date} onChange={e => setFyForm({...fyForm, start_date: e.target.value})} data-testid="fy-start" />
        </div>
        <div>
          <label className="form-label text-xs">Date fin *</label>
          <Input type="date" value={fyForm.end_date} onChange={e => setFyForm({...fyForm, end_date: e.target.value})} data-testid="fy-end" />
        </div>
      </div>
    </div>
  );
}

// ============== BUDGET PREVIEW (Step F) ==============
function BudgetPreview({ sections, setSections }) {
  if (!sections?.length) {
    return <div className="text-center py-6 text-amber-600 text-sm"><AlertTriangle size={24} className="inline mr-1" /> Aucune section detectee dans le PDF.</div>;
  }
  // Total Budget N (this is what gets imported into the budget table)
  const totalBudgetN = sections.reduce(
    (acc, s) => acc + (s.lines || []).reduce((a, l) => a + (parseFloat(l.budget_n ?? l.amount) || 0), 0),
    0
  );
  const totalRealiseN1 = sections.reduce(
    (acc, s) => acc + (s.lines || []).reduce((a, l) => a + (parseFloat(l.realise_n1) || 0), 0),
    0
  );
  const totalEnCours = sections.reduce(
    (acc, s) => acc + (s.lines || []).reduce((a, l) => a + (parseFloat(l.en_cours) || 0), 0),
    0
  );
  const upd = (sIdx, lIdx, field, value) => {
    const next = sections.map((s, i) => {
      if (i !== sIdx) return s;
      const lines = s.lines.map((l, j) => {
        if (j !== lIdx) return l;
        let v = value;
        if (['amount', 'budget_n', 'realise_n1', 'en_cours'].includes(field)) {
          v = parseFloat(value) || 0;
        }
        const merged = { ...l, [field]: v };
        // Keep legacy 'amount' field in sync with 'budget_n'
        if (field === 'budget_n') merged.amount = v;
        if (field === 'amount') merged.budget_n = v;
        return merged;
      });
      return { ...s, lines };
    });
    setSections(next);
  };
  const addLine = (sIdx) => {
    const next = sections.map((s, i) => i === sIdx ? { ...s, lines: [...s.lines, { account: '', libelle: '', realise_n1: 0, budget_n: 0, en_cours: 0, amount: 0 }] } : s);
    setSections(next);
  };
  const delLine = (sIdx, lIdx) => {
    const next = sections.map((s, i) => i === sIdx ? { ...s, lines: s.lines.filter((_, j) => j !== lIdx) } : s);
    setSections(next);
  };
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <div className="flex justify-between mb-1">
          <span><strong>Verifiez puis ajustez le budget</strong> : {sections.length} section(s) detectees.</span>
          <span className="font-mono font-semibold">Budget N : {totalBudgetN.toFixed(2)} EUR</span>
        </div>
        <div className="text-[10px] text-slate-600 flex gap-4 mt-1">
          <span>Realise N-1 (reference) : <span className="font-mono">{totalRealiseN1.toFixed(2)}</span></span>
          <span>En cours (info) : <span className="font-mono">{totalEnCours.toFixed(2)}</span></span>
          <span className="ml-auto text-[10px] italic">Seule la colonne <strong>Budget N</strong> est importee comme budget previsionnel.</span>
        </div>
      </div>
      <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
        {sections.map((s, si) => (
          <div key={si} className={`border rounded ${s.is_special ? 'border-purple-300 bg-purple-50/40' : 'border-slate-200'}`}>
            <div className={`px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between items-center ${s.is_special ? 'bg-purple-100' : 'bg-slate-100'}`}>
              <span>
                [{s.key_code}] {s.key_label}
                {s.is_special && (
                  <span className="ml-2 px-1.5 py-0.5 rounded bg-purple-600 text-white text-[10px] uppercase tracking-wide" title="Cle speciale - ne s'applique qu'aux lots concernes (ex. ascenseur)">Speciale</span>
                )}
              </span>
              <div className="flex gap-3 text-[10px] font-normal text-slate-500">
                <span>N-1: <span className="font-mono">{(parseFloat(s.realise_n1) || 0).toFixed(2)}</span></span>
                <span>N: <span className="font-mono font-semibold text-slate-700">{(parseFloat(s.budget_n) || 0).toFixed(2)}</span></span>
                <span>En cours: <span className="font-mono">{(parseFloat(s.en_cours) || 0).toFixed(2)}</span></span>
                <span>({(s.lines || []).length} ligne(s))</span>
              </div>
            </div>
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-1 py-1 text-left w-20">Compte</th>
                  <th className="px-1 py-1 text-left">Libelle</th>
                  <th className="px-1 py-1 text-right w-20">Realise N-1</th>
                  <th className="px-1 py-1 text-right w-20 bg-blue-50">Budget N</th>
                  <th className="px-1 py-1 text-right w-20">En cours</th>
                  <th className="w-6"></th>
                </tr>
              </thead>
              <tbody>
                {(s.lines || []).map((l, li) => (
                  <tr key={li} className="border-t border-slate-100">
                    <td className="px-1 py-0.5"><input value={l.account} onChange={e => upd(si, li, 'account', e.target.value)} className="w-20 border-0 bg-transparent font-mono" /></td>
                    <td className="px-1 py-0.5"><input value={l.libelle} onChange={e => upd(si, li, 'libelle', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                    <td className="px-1 py-0.5"><input type="number" step="0.01" value={l.realise_n1 ?? 0} onChange={e => upd(si, li, 'realise_n1', e.target.value)} className="w-20 border-0 bg-transparent text-right font-mono text-slate-500" /></td>
                    <td className="px-1 py-0.5 bg-blue-50/50"><input type="number" step="0.01" value={l.budget_n ?? l.amount ?? 0} onChange={e => upd(si, li, 'budget_n', e.target.value)} className="w-20 border-0 bg-transparent text-right font-mono font-semibold" /></td>
                    <td className="px-1 py-0.5"><input type="number" step="0.01" value={l.en_cours ?? 0} onChange={e => upd(si, li, 'en_cours', e.target.value)} className="w-20 border-0 bg-transparent text-right font-mono text-slate-500" /></td>
                    <td className="px-0 py-0.5"><button onClick={() => delLine(si, li)} className="text-red-500 hover:text-red-700"><X size={11} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button onClick={() => addLine(si)} className="text-xs text-[#0055FF] hover:underline px-3 py-1 flex items-center gap-1">
              <Plus size={11} /> Ajouter une ligne
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ============== DISTRIBUTION KEYS PREVIEW (Step J) ==============
function KeysPreview({ keys, setKeys, onAddPdf }) {
  const addManualKey = () => {
    const next = [...keys, {
      code: '',
      name: 'Nouvelle cle',
      type: 'tantiemes',
      lines: [],
      total_quotities: 0,
      _manual: true,
    }];
    setKeys(next);
  };
  const addLine = (kIdx) => {
    const next = keys.map((k, i) => i === kIdx
      ? { ...k, lines: [...(k.lines || []), { lot_label: '', lot_code: '', owner_label: '', quotity: 0 }] }
      : k);
    setKeys(next);
  };
  const headerActions = (
    <div className="flex flex-wrap gap-2 mb-2">
      {onAddPdf && (
        <Button size="sm" variant="outline" onClick={onAddPdf} className="border-blue-300 text-blue-700 hover:bg-blue-50" data-testid="keys-add-pdf-btn">
          <Upload size={13} className="mr-1.5" /> Ajouter un autre PDF
        </Button>
      )}
      <Button size="sm" variant="outline" onClick={addManualKey} className="border-emerald-300 text-emerald-700 hover:bg-emerald-50" data-testid="keys-add-manual-btn">
        <Plus size={13} className="mr-1.5" /> Ajouter une cle manuelle
      </Button>
    </div>
  );
  if (!keys?.length) {
    return (
      <div>
        {headerActions}
        <div className="text-center py-6 text-amber-600 text-sm">
          <AlertTriangle size={24} className="inline mr-1" /> Aucune cle de repartition pour l&apos;instant. Ajoutez-en via un PDF ou manuellement.
        </div>
      </div>
    );
  }
  const updKey = (idx, field, value) => {
    setKeys(keys.map((k, i) => i === idx ? { ...k, [field]: value } : k));
  };
  const updLine = (kIdx, lIdx, field, value) => {
    const next = keys.map((k, i) => {
      if (i !== kIdx) return k;
      const lines = k.lines.map((l, j) => j === lIdx ? { ...l, [field]: field === 'quotity' ? (parseFloat(value) || 0) : value } : l);
      const total = lines.reduce((acc, l) => acc + (parseFloat(l.quotity) || 0), 0);
      return { ...k, lines, total_quotities: total };
    });
    setKeys(next);
  };
  const delLine = (kIdx, lIdx) => {
    const next = keys.map((k, i) => i === kIdx ? { ...k, lines: k.lines.filter((_, j) => j !== lIdx) } : k);
    setKeys(next);
  };
  const delKey = (idx) => setKeys(keys.filter((_, i) => i !== idx));
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
        <strong>Verifiez les cles de repartition</strong> : pour chaque cle, les quotites doivent etre proportionnelles. Les lots sont matches automatiquement par le numero. Vous pouvez uploader plusieurs PDFs successivement (les codes en double seront mis a jour).
      </div>
      {headerActions}
      <div className="space-y-2 max-h-[440px] overflow-y-auto pr-1">
        {keys.map((k, ki) => (
          <div key={ki} className="border border-slate-200 rounded">
            <div className="bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between items-center">
              <div className="flex gap-2 items-center">
                <input value={k.code || ''} onChange={e => updKey(ki, 'code', e.target.value)} placeholder="0001" className="w-14 bg-white border border-slate-300 rounded px-1 font-mono" />
                <input value={k.name || ''} onChange={e => updKey(ki, 'name', e.target.value)} className="bg-white border border-slate-300 rounded px-1 w-72" />
                {k._manual && <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 text-[10px]">manuel</span>}
              </div>
              <div className="flex gap-2 items-center">
                <span className="text-slate-500">Total quotites : <strong>{k.total_quotities?.toFixed(2) || '0.00'}</strong></span>
                <button onClick={() => addLine(ki)} className="text-emerald-600 hover:text-emerald-800" title="Ajouter une ligne"><Plus size={12} /></button>
                <button onClick={() => delKey(ki)} className="text-red-500"><Trash2 size={12} /></button>
              </div>
            </div>
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-1 py-1 text-left">Lot / Libelle</th>
                  <th className="px-1 py-1 text-left w-56">Coproprietaire</th>
                  <th className="px-1 py-1 text-right w-24">Quotite</th>
                  <th className="w-6"></th>
                </tr>
              </thead>
              <tbody>
                {(k.lines || []).map((l, li) => (
                  <tr key={li} className="border-t border-slate-100" data-testid={`key-${ki}-line-${li}`}>
                    <td className="px-1 py-0.5"><input value={l.lot_label || ''} onChange={e => updLine(ki, li, 'lot_label', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                    <td className="px-1 py-0.5"><input value={l.owner_label || ''} onChange={e => updLine(ki, li, 'owner_label', e.target.value)} className="w-full border-0 bg-transparent font-mono text-[11px]" /></td>
                    <td className="px-1 py-0.5"><input type="number" step="0.01" value={l.quotity} onChange={e => updLine(ki, li, 'quotity', e.target.value)} className="w-24 border-0 bg-transparent text-right font-mono" /></td>
                    <td className="px-0 py-0.5"><button onClick={() => delLine(ki, li)} className="text-red-500 hover:text-red-700"><X size={11} /></button></td>
                  </tr>
                ))}
                {(k.lines || []).length === 0 && (
                  <tr><td colSpan={4} className="px-2 py-3 text-center text-amber-700 text-[11px]">
                    <AlertTriangle size={11} className="inline mr-1" /> Aucune ligne de detail extraite pour cette cle. Verifiez le PDF source.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
