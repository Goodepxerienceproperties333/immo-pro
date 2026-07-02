import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { FileArchive, Download, Save, Plus, Trash2, Loader2, Building2, Users, Shield, ShieldCheck } from 'lucide-react';

/**
 * Ecran admin d'edition du Registre RGPD (article 30).
 * - Section responsable de traitement (societe, BCE, TVA, DPO, ...)
 * - Liste editable des activites de traitement (7 champs par entree)
 * - Liste editable des sous-traitants (4 champs par entree)
 * - Liste editable des mesures de securite (texte libre)
 * - Bouton Sauvegarder + Bouton "Telecharger le PDF"
 */
export default function AdminRgpdRegisterPage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [data, setData] = useState({
    controller: {},
    processings: [],
    subprocessors: [],
    security_measures: [],
    updated_at: null,
  });

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/legal/admin/rgpd-register');
      setData({
        controller: r.data.controller || {},
        processings: r.data.processings || [],
        subprocessors: r.data.subprocessors || [],
        security_measures: r.data.security_measures || [],
        updated_at: r.data.updated_at || null,
      });
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Impossible de charger le registre');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const r = await api.put('/legal/admin/rgpd-register', {
        controller: data.controller,
        processings: data.processings,
        subprocessors: data.subprocessors,
        security_measures: data.security_measures,
      });
      toast.success(r.data?.message || 'Registre sauvegarde');
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur sauvegarde');
    } finally {
      setSaving(false);
    }
  };

  const downloadPdf = async () => {
    setDownloading(true);
    try {
      const r = await api.get('/legal/admin/rgpd-register/pdf', { responseType: 'blob' });
      const blob = new Blob([r.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const now = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      a.href = url;
      a.download = `registre-rgpd-copromanager-${now}.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success('PDF genere');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur PDF');
    } finally {
      setDownloading(false);
    }
  };

  const updateController = (k, v) => setData(d => ({ ...d, controller: { ...d.controller, [k]: v } }));

  const updateProcessing = (idx, k, v) => setData(d => {
    const arr = [...d.processings];
    arr[idx] = { ...arr[idx], [k]: v };
    return { ...d, processings: arr };
  });
  const addProcessing = () => setData(d => ({
    ...d,
    processings: [...d.processings, {
      name: '', purpose: '', legal_basis: '', data_categories: '',
      subjects: '', recipients: '', transfers: '', retention: '',
    }],
  }));
  const removeProcessing = (idx) => setData(d => ({
    ...d, processings: d.processings.filter((_, i) => i !== idx),
  }));

  const updateSubproc = (idx, k, v) => setData(d => {
    const arr = [...d.subprocessors];
    arr[idx] = { ...arr[idx], [k]: v };
    return { ...d, subprocessors: arr };
  });
  const addSubproc = () => setData(d => ({
    ...d,
    subprocessors: [...d.subprocessors, { name: '', service: '', location: '', guarantees: '' }],
  }));
  const removeSubproc = (idx) => setData(d => ({
    ...d, subprocessors: d.subprocessors.filter((_, i) => i !== idx),
  }));

  const updateMeasure = (idx, v) => setData(d => {
    const arr = [...d.security_measures];
    arr[idx] = v;
    return { ...d, security_measures: arr };
  });
  const addMeasure = () => setData(d => ({ ...d, security_measures: [...d.security_measures, ''] }));
  const removeMeasure = (idx) => setData(d => ({
    ...d, security_measures: d.security_measures.filter((_, i) => i !== idx),
  }));

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-sm p-8 justify-center">
        <Loader2 size={16} className="animate-spin" /> Chargement du registre...
      </div>
    );
  }

  const ctrl = data.controller || {};

  return (
    <div className="space-y-4" data-testid="admin-rgpd-register-page">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
            <FileArchive size={22} className="text-[#0055FF]" />
            Registre des traitements RGPD (art. 30)
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Document unique liste des activites de traitement, sous-traitants et mesures
            de securite. Genere en PDF pret pour l&apos;APD en cas de controle.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-[10px] border-amber-300 bg-amber-50 text-amber-700">
            Super admin uniquement
          </Badge>
          {data.updated_at && (
            <Badge variant="outline" className="text-[10px] border-slate-200 text-slate-600">
              MAJ : {new Date(data.updated_at).toLocaleDateString('fr-BE')}
            </Badge>
          )}
        </div>
      </div>

      {/* Actions bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button onClick={save} disabled={saving} className="bg-slate-800 hover:bg-slate-900 text-white" data-testid="rgpd-reg-save-btn">
          {saving ? <><Loader2 size={13} className="animate-spin mr-1.5" /> Sauvegarde...</> : <><Save size={13} className="mr-1.5" /> Sauvegarder</>}
        </Button>
        <Button onClick={downloadPdf} disabled={downloading} className="bg-[#0055FF] hover:bg-[#0040CC] text-white" data-testid="rgpd-reg-download-pdf-btn">
          {downloading ? <><Loader2 size={13} className="animate-spin mr-1.5" /> Generation...</> : <><Download size={13} className="mr-1.5" /> Telecharger le PDF</>}
        </Button>
      </div>

      {/* 1. Responsable de traitement */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
            <Building2 size={14} className="text-slate-500" />
            1. Responsable de traitement
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
            {[
              ['societe', 'Denomination sociale', 'ex: Good Experience Properties SRL'],
              ['forme_juridique', 'Forme juridique', 'ex: SRL / SPRL / SA'],
              ['adresse', 'Siege social (adresse complete)', 'ex: Rue X 1, 1000 Bruxelles'],
              ['bce', 'N° BCE (Banque-Carrefour)', 'ex: 0700.000.000'],
              ['tva', 'N° TVA', 'ex: BE 0700.000.000'],
              ['representant', 'Representant legal', 'ex: Jean Dupont, Gerant'],
              ['email', 'Email contact', 'welcome@goodexperienceproperties.be'],
              ['telephone', 'Telephone', 'ex: +32 2 000 00 00'],
              ['dpo_email', 'DPO / Delegue a la protection', 'DPO email si designe'],
            ].map(([key, label, placeholder]) => (
              <div key={key}>
                <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">{label}</label>
                <Input
                  value={ctrl[key] || ''}
                  onChange={(e) => updateController(key, e.target.value)}
                  placeholder={placeholder}
                  className="mt-1 text-sm"
                  data-testid={`rgpd-reg-ctrl-${key}`}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* 2. Activités de traitement */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3 flex-row items-start justify-between">
          <div>
            <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
              <Users size={14} className="text-slate-500" />
              2. Activites de traitement ({data.processings.length})
            </CardTitle>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Liste des traitements de donnees. Ajoutez, editez ou supprimez selon vos activites reelles.
            </p>
          </div>
          <Button onClick={addProcessing} size="sm" variant="outline" className="text-xs" data-testid="rgpd-reg-add-processing">
            <Plus size={12} className="mr-1" /> Ajouter
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {data.processings.map((p, idx) => (
            <div key={idx} className="border border-slate-200 rounded-lg p-3 space-y-2 bg-slate-50/50" data-testid={`rgpd-reg-processing-${idx}`}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-slate-500 uppercase">Traitement #{idx + 1}</span>
                <Button
                  onClick={() => removeProcessing(idx)}
                  variant="ghost"
                  size="sm"
                  className="text-red-600 hover:bg-red-50 h-7 text-xs"
                  data-testid={`rgpd-reg-remove-processing-${idx}`}
                >
                  <Trash2 size={11} className="mr-1" /> Supprimer
                </Button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {[
                  ['name', 'Nom du traitement', 'ex: Gestion des utilisateurs'],
                  ['legal_basis', 'Base legale RGPD', 'ex: Execution du contrat (art. 6.1.b)'],
                ].map(([k, label, ph]) => (
                  <div key={k}>
                    <label className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">{label}</label>
                    <Input value={p[k] || ''} onChange={(e) => updateProcessing(idx, k, e.target.value)} placeholder={ph} className="text-sm mt-1" />
                  </div>
                ))}
                {[
                  ['purpose', 'Finalite (a quoi ca sert)'],
                  ['data_categories', 'Categories de donnees (quels champs)'],
                  ['subjects', 'Personnes concernees'],
                  ['recipients', 'Destinataires'],
                  ['retention', 'Duree de conservation'],
                ].map(([k, label]) => (
                  <div key={k} className="md:col-span-2">
                    <label className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">{label}</label>
                    <Textarea
                      value={p[k] || ''}
                      onChange={(e) => updateProcessing(idx, k, e.target.value)}
                      className="text-xs mt-1 min-h-[45px]"
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* 3. Sous-traitants */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3 flex-row items-start justify-between">
          <div>
            <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
              <Shield size={14} className="text-slate-500" />
              3. Sous-traitants ({data.subprocessors.length})
            </CardTitle>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Liste des tiers auxquels des donnees sont transmises pour traitement.
            </p>
          </div>
          <Button onClick={addSubproc} size="sm" variant="outline" className="text-xs" data-testid="rgpd-reg-add-subproc">
            <Plus size={12} className="mr-1" /> Ajouter
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {data.subprocessors.map((s, idx) => (
            <div key={idx} className="border border-slate-200 rounded-lg p-3 space-y-2 bg-slate-50/50" data-testid={`rgpd-reg-subproc-${idx}`}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold text-slate-500 uppercase">Sous-traitant #{idx + 1}</span>
                <Button
                  onClick={() => removeSubproc(idx)}
                  variant="ghost"
                  size="sm"
                  className="text-red-600 hover:bg-red-50 h-7 text-xs"
                  data-testid={`rgpd-reg-remove-subproc-${idx}`}
                >
                  <Trash2 size={11} className="mr-1" /> Supprimer
                </Button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {[
                  ['name', 'Nom du sous-traitant'],
                  ['location', 'Localisation (siege / donnees)'],
                ].map(([k, label]) => (
                  <div key={k}>
                    <label className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">{label}</label>
                    <Input value={s[k] || ''} onChange={(e) => updateSubproc(idx, k, e.target.value)} className="text-sm mt-1" />
                  </div>
                ))}
                <div className="md:col-span-2">
                  <label className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">Prestation</label>
                  <Textarea value={s['service'] || ''} onChange={(e) => updateSubproc(idx, 'service', e.target.value)} className="text-xs mt-1 min-h-[40px]" />
                </div>
                <div className="md:col-span-2">
                  <label className="text-[10px] font-semibold text-slate-600 uppercase tracking-wide">Garanties (CCT, DPA, ISO...)</label>
                  <Textarea value={s['guarantees'] || ''} onChange={(e) => updateSubproc(idx, 'guarantees', e.target.value)} className="text-xs mt-1 min-h-[40px]" />
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* 4. Mesures de sécurité */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3 flex-row items-start justify-between">
          <div>
            <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
              <ShieldCheck size={14} className="text-slate-500" />
              4. Mesures techniques et organisationnelles ({data.security_measures.length})
            </CardTitle>
            <p className="text-[11px] text-slate-500 mt-0.5">
              Une ligne = une mesure. Ex: &laquo; Mots de passe hashes bcrypt &raquo;.
            </p>
          </div>
          <Button onClick={addMeasure} size="sm" variant="outline" className="text-xs" data-testid="rgpd-reg-add-measure">
            <Plus size={12} className="mr-1" /> Ajouter
          </Button>
        </CardHeader>
        <CardContent className="space-y-2">
          {data.security_measures.map((m, idx) => (
            <div key={idx} className="flex items-start gap-2" data-testid={`rgpd-reg-measure-${idx}`}>
              <span className="text-[11px] text-slate-400 pt-2 tabular-nums w-6 text-right shrink-0">{idx + 1}.</span>
              <Input value={m} onChange={(e) => updateMeasure(idx, e.target.value)} className="text-sm flex-1" />
              <Button
                onClick={() => removeMeasure(idx)}
                variant="ghost"
                size="sm"
                className="text-red-600 hover:bg-red-50 h-9 shrink-0"
                data-testid={`rgpd-reg-remove-measure-${idx}`}
              >
                <Trash2 size={11} />
              </Button>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Sticky bottom bar */}
      <div className="sticky bottom-4 bg-white border border-slate-300 shadow-lg rounded-lg p-3 flex items-center gap-2 justify-end">
        <span className="text-[11px] text-slate-500 mr-auto">
          {data.updated_at ? `Derniere sauvegarde : ${new Date(data.updated_at).toLocaleString('fr-BE')}` : 'Non encore sauvegarde'}
        </span>
        <Button onClick={save} disabled={saving} className="bg-slate-800 hover:bg-slate-900 text-white" data-testid="rgpd-reg-save-btn-bottom">
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
          <span className="ml-1.5">Sauvegarder</span>
        </Button>
        <Button onClick={downloadPdf} disabled={downloading} className="bg-[#0055FF] hover:bg-[#0040CC] text-white" data-testid="rgpd-reg-download-pdf-btn-bottom">
          {downloading ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
          <span className="ml-1.5">Telecharger PDF</span>
        </Button>
      </div>
    </div>
  );
}
