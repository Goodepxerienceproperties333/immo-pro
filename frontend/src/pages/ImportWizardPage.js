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
import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import {
  Upload, Users, Truck, Home, Tag, CheckCircle2, X, AlertTriangle,
  ChevronRight, ChevronLeft, FileWarning, Loader2, RotateCcw,
  Calendar, Wallet, PieChart, Plus, Trash2
} from 'lucide-react';
import { toast } from 'sonner';

const STEPS = [
  { key: 'owners',    label: 'Proprietaires',     icon: Users,    optional: false, kind: 'csv' },
  { key: 'suppliers', label: 'Fournisseurs',      icon: Truck,    optional: false, kind: 'csv' },
  { key: 'lots',      label: 'Lots',              icon: Home,     optional: false, kind: 'csv' },
  { key: 'natures',   label: 'Natures depense',   icon: Tag,      optional: false, kind: 'pdf' },
  { key: 'fiscal_year', label: 'Exercice fiscal', icon: Calendar, optional: false, kind: 'form' },
  { key: 'budget',    label: 'Budget',            icon: Wallet,   optional: true,  kind: 'pdf' },
  { key: 'distribution_keys', label: 'Cles de repartition', icon: PieChart, optional: true, kind: 'pdf' },
];

// Champs cibles attendus pour chaque étape (clé = nom du champ DB)
const TARGET_FIELDS = {
  owners: [
    { key: 'last_name',   label: 'Nom *',                required: true },
    { key: 'first_name',  label: 'Prenom',               required: false },
    { key: 'address',     label: 'Adresse',              required: false },
    { key: 'postal_code', label: 'Code postal',          required: false },
    { key: 'city',        label: 'Ville',                required: false },
    { key: 'country',     label: 'Pays',                 required: false },
    { key: 'email',       label: 'Email',                required: false },
    { key: 'phone',       label: 'Telephone',            required: false },
    { key: 'iban',        label: 'IBAN',                 required: false },
  ],
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
  lots: [
    { key: 'number',      label: 'Numero *',         required: true },
    { key: 'description', label: 'Description',      required: false },
    { key: 'lot_type',    label: 'Type',              required: false },
    { key: 'floor',       label: 'Etage',             required: false },
    { key: 'area',        label: 'Surface (m2)',     required: false },
    { key: 'quotity',     label: 'Quotite (1000es)', required: false },
    { key: 'owner_name',  label: 'Proprietaire (nom)', required: false },
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
  const handleFileChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !session) return;
    setSniffing(true);
    setSniffResult(null);
    setMapping({});
    setNaturesParsed([]);
    setBudgetSections([]);
    setKeysParsed([]);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const isPdf = step.kind === 'pdf';
      let r;
      if (isPdf) {
        // Map step.key -> backend kind
        const kindMap = { natures: 'natures', budget: 'budget', distribution_keys: 'keys' };
        fd.append('kind', kindMap[step.key] || 'generic');
        r = await api.post(`/import-wizard/sessions/${session.id}/sniff-pdf`, fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        if (step.key === 'natures') setNaturesParsed(r.data.natures || []);
        if (step.key === 'budget') setBudgetSections(r.data.sections || []);
        if (step.key === 'distribution_keys') setKeysParsed(r.data.keys || []);
      } else if (step.kind === 'csv') {
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
      if (step.kind === 'csv') {
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
    <div data-testid="import-wizard-page" className="max-w-6xl mx-auto">
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
          {!sniffResult && step.kind !== 'form' && (
            <div className="border-2 border-dashed border-slate-300 rounded-md p-8 text-center">
              <Upload size={32} className="mx-auto text-slate-400 mb-2" />
              <p className="text-sm text-slate-600 mb-3">
                {step.kind === 'csv'
                  ? 'Chargez le fichier CSV exporte d\'Optipro/Sogis'
                  : `Chargez le PDF (${step.label})`}
              </p>
              <input
                type="file"
                id="file-input"
                className="hidden"
                accept={step.kind === 'csv' ? '.csv,.txt' : '.pdf'}
                onChange={handleFileChange}
                data-testid="file-input"
              />
              <Button onClick={() => document.getElementById('file-input').click()} disabled={sniffing} className="bg-[#0055FF] hover:bg-[#0040CC]">
                {sniffing ? <><Loader2 size={14} className="animate-spin mr-1" /> Analyse en cours...</> : <><Upload size={14} className="mr-1" /> Choisir le fichier</>}
              </Button>
            </div>
          )}

          {step.kind === 'form' && step.key === 'fiscal_year' && (
            <FiscalYearForm fyForm={fyForm} setFyForm={setFyForm} />
          )}

          {sniffResult && step.kind === 'csv' && (
            <CsvMappingView
              sniff={sniffResult}
              targetFields={TARGET_FIELDS[step.key] || []}
              mapping={mapping}
              setMapping={setMapping}
            />
          )}

          {sniffResult && step.key === 'natures' && (
            <NaturesPreview natures={naturesParsed} setNatures={setNaturesParsed} />
          )}

          {sniffResult && step.key === 'budget' && (
            <BudgetPreview sections={budgetSections} setSections={setBudgetSections} />
          )}

          {sniffResult && step.key === 'distribution_keys' && (
            <KeysPreview keys={keysParsed} setKeys={setKeysParsed} />
          )}
        </CardContent>
      </Card>

      {/* Footer */}
      <div className="flex items-center justify-between mt-4">
        <Button variant="outline" size="sm" disabled={stepIdx === 0} onClick={() => { setStepIdx(stepIdx - 1); setSniffResult(null); setMapping({}); setNaturesParsed([]); }} data-testid="prev-step">
          <ChevronLeft size={14} className="mr-1" /> Etape precedente
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { setSniffResult(null); setMapping({}); setNaturesParsed([]); }} data-testid="reset-step">
            <X size={14} className="mr-1" /> Annuler ce fichier
          </Button>
          {step.optional && stepIdx < STEPS.length - 1 && (
            <Button variant="outline" size="sm" onClick={() => { setStepIdx(stepIdx + 1); setSniffResult(null); }} data-testid="skip-step">
              Passer cette etape
            </Button>
          )}
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
  const total = sections.reduce((acc, s) => acc + (s.lines || []).reduce((a, l) => a + (parseFloat(l.amount) || 0), 0), 0);
  const upd = (sIdx, lIdx, field, value) => {
    const next = sections.map((s, i) => {
      if (i !== sIdx) return s;
      const lines = s.lines.map((l, j) => j === lIdx ? { ...l, [field]: field === 'amount' ? (parseFloat(value) || 0) : value } : l);
      return { ...s, lines };
    });
    setSections(next);
  };
  const addLine = (sIdx) => {
    const next = sections.map((s, i) => i === sIdx ? { ...s, lines: [...s.lines, { account: '', libelle: '', amount: 0 }] } : s);
    setSections(next);
  };
  const delLine = (sIdx, lIdx) => {
    const next = sections.map((s, i) => i === sIdx ? { ...s, lines: s.lines.filter((_, j) => j !== lIdx) } : s);
    setSections(next);
  };
  return (
    <div className="space-y-3">
      <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900 flex justify-between">
        <span><strong>Verifiez puis ajustez le budget</strong> : {sections.length} section(s) detectees.</span>
        <span className="font-mono font-semibold">Total : {total.toFixed(2)} EUR</span>
      </div>
      <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
        {sections.map((s, si) => (
          <div key={si} className="border border-slate-200 rounded">
            <div className="bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between">
              <span>[{s.key_code}] {s.key_label}</span>
              <span className="text-slate-500">{(s.lines || []).length} ligne(s)</span>
            </div>
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-1 py-1 text-left w-20">Compte</th>
                  <th className="px-1 py-1 text-left">Libelle</th>
                  <th className="px-1 py-1 text-right w-24">Montant (EUR)</th>
                  <th className="w-6"></th>
                </tr>
              </thead>
              <tbody>
                {(s.lines || []).map((l, li) => (
                  <tr key={li} className="border-t border-slate-100">
                    <td className="px-1 py-0.5"><input value={l.account} onChange={e => upd(si, li, 'account', e.target.value)} className="w-20 border-0 bg-transparent font-mono" /></td>
                    <td className="px-1 py-0.5"><input value={l.libelle} onChange={e => upd(si, li, 'libelle', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                    <td className="px-1 py-0.5"><input type="number" step="0.01" value={l.amount} onChange={e => upd(si, li, 'amount', e.target.value)} className="w-24 border-0 bg-transparent text-right font-mono" /></td>
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
function KeysPreview({ keys, setKeys }) {
  if (!keys?.length) {
    return <div className="text-center py-6 text-amber-600 text-sm"><AlertTriangle size={24} className="inline mr-1" /> Aucune cle de repartition detectee dans le PDF.</div>;
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
        <strong>Verifiez les cles de repartition</strong> : pour chaque cle, les quotites doivent etre proportionnelles. Les lots sont matches automatiquement par le numero. Si le match echoue, il sera demande au syndic.
      </div>
      <div className="space-y-2 max-h-[440px] overflow-y-auto pr-1">
        {keys.map((k, ki) => (
          <div key={ki} className="border border-slate-200 rounded">
            <div className="bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 flex justify-between items-center">
              <div className="flex gap-2 items-center">
                <input value={k.code} onChange={e => updKey(ki, 'code', e.target.value)} className="w-14 bg-white border border-slate-300 rounded px-1 font-mono" />
                <input value={k.name} onChange={e => updKey(ki, 'name', e.target.value)} className="bg-white border border-slate-300 rounded px-1 w-72" />
              </div>
              <div className="flex gap-2 items-center">
                <span className="text-slate-500">Total quotites : <strong>{k.total_quotities?.toFixed(2) || '0.00'}</strong></span>
                <button onClick={() => delKey(ki)} className="text-red-500"><Trash2 size={12} /></button>
              </div>
            </div>
            <table className="w-full text-xs">
              <thead className="bg-slate-50">
                <tr>
                  <th className="px-1 py-1 text-left">Lot / Libelle</th>
                  <th className="px-1 py-1 text-left w-24">Code</th>
                  <th className="px-1 py-1 text-right w-24">Quotite</th>
                  <th className="w-6"></th>
                </tr>
              </thead>
              <tbody>
                {(k.lines || []).map((l, li) => (
                  <tr key={li} className="border-t border-slate-100">
                    <td className="px-1 py-0.5"><input value={l.lot_label} onChange={e => updLine(ki, li, 'lot_label', e.target.value)} className="w-full border-0 bg-transparent" /></td>
                    <td className="px-1 py-0.5"><input value={l.lot_code} onChange={e => updLine(ki, li, 'lot_code', e.target.value)} className="w-24 border-0 bg-transparent font-mono" /></td>
                    <td className="px-1 py-0.5"><input type="number" step="0.01" value={l.quotity} onChange={e => updLine(ki, li, 'quotity', e.target.value)} className="w-24 border-0 bg-transparent text-right font-mono" /></td>
                    <td className="px-0 py-0.5"><button onClick={() => delLine(ki, li)} className="text-red-500 hover:text-red-700"><X size={11} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
