import { useState, useEffect, useCallback } from 'react';
import api, { extractApiError } from '@/lib/api';
import { sanitizeHtml } from '@/lib/sanitizeHtml';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  FileText, Plus, Trash2, Edit3, Eye, Copy, Save, X, Lock,
  Wand2, RefreshCw,
} from 'lucide-react';

const CATEGORIES = [
  { value: 'general', label: 'General' },
  { value: 'recouvrement', label: 'Recouvrement' },
  { value: 'information', label: 'Information' },
  { value: 'confirmation', label: 'Confirmation' },
  { value: 'convocation', label: 'Convocation AG' },
];

function CategoryBadge({ cat }) {
  const map = {
    recouvrement: 'bg-red-100 text-red-700',
    information: 'bg-blue-100 text-[#01213e]',
    confirmation: 'bg-emerald-100 text-emerald-700',
    convocation: 'bg-violet-100 text-violet-700',
    general: 'bg-slate-100 text-slate-700',
  };
  const label = CATEGORIES.find(c => c.value === cat)?.label || cat;
  return <Badge className={map[cat] || map.general}>{label}</Badge>;
}

// ============ Editor Modal ============
function TemplateEditor({ template, onSaved, onClose, variables }) {
  const [form, setForm] = useState({
    name: '', category: 'general', subject: '', body_html: '', order: 0,
  });
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState(null);
  // iter93cw : suivi du champ actif (sujet ou corps) pour permettre
  // l'insertion des variables dans les deux endroits.
  const [activeField, setActiveField] = useState('body'); // 'subject' | 'body'

  useEffect(() => {
    if (template) {
      setForm({
        name: template.name || '',
        category: template.category || 'general',
        subject: template.subject || '',
        body_html: template.body_html || '',
        order: template.order || 0,
      });
      setPreview(null);
      setActiveField('body');
    }
  }, [template]);

  const insertVar = (varName) => {
    const targetId = activeField === 'subject' ? 'tpl-subject' : 'tpl-body';
    const el = document.getElementById(targetId);
    if (!el) return;
    const fieldKey = activeField === 'subject' ? 'subject' : 'body_html';
    const currentValue = form[fieldKey] || '';
    const start = el.selectionStart ?? currentValue.length;
    const end = el.selectionEnd ?? currentValue.length;
    const before = currentValue.substring(0, start);
    const after = currentValue.substring(end);
    const inserted = `{${varName}}`;
    setForm({ ...form, [fieldKey]: `${before}${inserted}${after}` });
    setTimeout(() => {
      el.focus();
      const pos = start + inserted.length;
      el.selectionStart = el.selectionEnd = pos;
    }, 10);
  };

  const save = async () => {
    if (!form.name.trim() || !form.subject.trim() || !form.body_html.trim()) {
      toast.error('Nom, sujet et corps sont obligatoires');
      return;
    }
    setSaving(true);
    try {
      if (template?.id && !template.readonly) {
        // Update (custom ou override default)
        await api.put(`/email-templates/${template.id}`, form);
      } else if (template?.readonly) {
        // Override d'un default : POST-then-PUT semantically; API attend PUT sur meme id
        await api.put(`/email-templates/${template.id}`, form);
      } else {
        await api.post('/email-templates', form);
      }
      toast.success('Template enregistre');
      onSaved?.();
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  const doPreview = async () => {
    if (!template?.id) {
      // Preview locale avec vars fake
      const fake = {
        owner_name: 'Marie DEVOS', abs_balance: '247.50',
        copropriete_name: 'Les Peupliers', vcs_code: '+++123/4567/89012+++',
        iban: 'BE68 5390 0754 7034', today: new Date().toLocaleDateString('fr-BE'),
      };
      let s = form.subject; let b = form.body_html;
      Object.entries(fake).forEach(([k, v]) => {
        s = s.replaceAll(`{${k}}`, v);
        b = b.replaceAll(`{${k}}`, v);
      });
      setPreview({ subject: s, body_html: b });
      return;
    }
    try {
      const r = await api.post(`/email-templates/${template.id}/preview`);
      setPreview(r.data);
    } catch (e) { toast.error(extractApiError(e)); }
  };

  return (
    <Dialog open={!!template} onOpenChange={(o) => !o && onClose?.()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto" data-testid="template-editor-dialog">
        <DialogHeader>
          <DialogTitle>
            {template?.id ? (template.readonly ? 'Personnaliser (override default)' : 'Editer template') : 'Nouveau template'}
          </DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Nom *</Label>
                <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                       placeholder="Ex : Relance amiable J+15"
                       data-testid="tpl-input-name" />
              </div>
              <div>
                <Label className="text-xs">Categorie</Label>
                <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                  <SelectTrigger data-testid="tpl-select-category"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CATEGORIES.map(c => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="text-xs">Sujet *</Label>
              <Input id="tpl-subject" value={form.subject}
                     onChange={(e) => setForm({ ...form, subject: e.target.value })}
                     onFocus={() => setActiveField('subject')}
                     placeholder="Rappel amiable : votre solde du {today}"
                     data-testid="tpl-input-subject" />
            </div>
            <div>
              <Label className="text-xs">Corps HTML *</Label>
              <Textarea id="tpl-body" rows={12} value={form.body_html}
                        onChange={(e) => setForm({ ...form, body_html: e.target.value })}
                        onFocus={() => setActiveField('body')}
                        placeholder="Bonjour {owner_name}, il reste un solde de {abs_balance} EUR..."
                        className="font-mono text-xs"
                        data-testid="tpl-textarea-body" />
            </div>
            <Button onClick={doPreview} variant="outline" size="sm" data-testid="tpl-btn-preview">
              <Eye className="h-4 w-4 mr-1" /> Apercu (donnees fictives)
            </Button>
            {preview && (
              <Card className="mt-2">
                <CardHeader className="pb-2"><CardTitle className="text-sm">Preview</CardTitle></CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <div><b>Sujet :</b> {preview.subject}</div>
                  <div className="border-t pt-2" dangerouslySetInnerHTML={{ __html: sanitizeHtml(preview.body_html) }} />
                </CardContent>
              </Card>
            )}
          </div>
          <div>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm flex items-center gap-1">
                  <Wand2 className="h-4 w-4 text-violet-600" /> Variables
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-xs">
                <p className="text-slate-500 mb-1">Cliquez pour inserer dans :</p>
                <div className="mb-2 px-2 py-1 rounded bg-violet-50 border border-violet-200 text-[11px] text-violet-800 font-medium" data-testid="tpl-var-target">
                  {activeField === 'subject' ? 'le Sujet' : 'le Corps HTML'}
                </div>
                {Object.entries(variables || {}).map(([k, desc]) => (
                  <button key={k}
                          onClick={() => insertVar(k)}
                          className="w-full text-left p-1.5 rounded hover:bg-violet-50 border border-transparent hover:border-violet-200 transition"
                          data-testid={`tpl-var-${k}`}>
                    <code className="text-violet-700 font-mono">{`{${k}}`}</code>
                    <div className="text-[10px] text-slate-500 leading-tight">{desc}</div>
                  </button>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}><X className="h-4 w-4 mr-1" /> Annuler</Button>
          <Button onClick={save} disabled={saving} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="tpl-btn-save">
            <Save className="h-4 w-4 mr-1" /> Enregistrer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Main list page ============
export default function EmailTemplatesPage() {
  const [templates, setTemplates] = useState([]);
  const [variables, setVariables] = useState({});
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);
  const [filter, setFilter] = useState('all');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/email-templates');
      setTemplates(r.data.templates || []);
      setVariables(r.data.variables || {});
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const remove = async (t) => {
    if (!window.confirm(`Supprimer/reset "${t.name}" ?`)) return;
    try {
      await api.delete(`/email-templates/${t.id}`);
      toast.success('Template supprime/reset');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const duplicate = (t) => {
    setEditing({
      name: `${t.name} (copie)`,
      category: t.category,
      subject: t.subject,
      body_html: t.body_html,
    });
  };

  const filtered = filter === 'all' ? templates : templates.filter(t => t.category === filter);

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto space-y-4" data-testid="email-templates-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <FileText className="h-6 w-6 text-[#022D52]" />
            Modeles d&apos;emails
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Creez des modeles reutilisables avec variables dynamiques (nom proprietaire, solde, iban...).
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={load} data-testid="tpl-btn-refresh">
            <RefreshCw className="h-4 w-4" />
          </Button>
          <Button onClick={() => setEditing({})} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="tpl-btn-new">
            <Plus className="h-4 w-4 mr-1" /> Nouveau modele
          </Button>
        </div>
      </header>

      {/* Category filters */}
      <div className="flex flex-wrap items-center gap-2">
        {[{ value: 'all', label: 'Tous' }, ...CATEGORIES].map(c => (
          <Button key={c.value}
                  variant={filter === c.value ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setFilter(c.value)}
                  data-testid={`tpl-filter-${c.value}`}>
            {c.label}
          </Button>
        ))}
      </div>

      {loading ? (
        <div className="text-center p-10 text-slate-500">Chargement...</div>
      ) : filtered.length === 0 ? (
        <Card><CardContent className="p-10 text-center text-slate-500">
          Aucun modele dans cette categorie. Cliquez sur &quot;Nouveau modele&quot; pour en creer un.
        </CardContent></Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map(t => (
            <Card key={t.id} className="hover:shadow-md transition"
                  data-testid={`tpl-card-${t.id}`}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <CardTitle className="text-sm truncate">{t.name}</CardTitle>
                    <div className="flex items-center gap-1 mt-1">
                      <CategoryBadge cat={t.category} />
                      {t.readonly && (
                        <Badge variant="outline" className="text-[10px]">
                          <Lock className="h-3 w-3 mr-1" /> Default
                        </Badge>
                      )}
                    </div>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="text-xs text-slate-600 line-clamp-1"><b>{t.subject}</b></div>
                <div className="text-xs text-slate-500 line-clamp-3"
                     dangerouslySetInnerHTML={{ __html: sanitizeHtml(t.body_html) }} />
                <div className="flex gap-1 pt-2">
                  <Button size="sm" variant="outline" onClick={() => setEditing(t)}
                          data-testid={`tpl-btn-edit-${t.id}`}>
                    <Edit3 className="h-3 w-3 mr-1" />
                    {t.readonly ? 'Personnaliser' : 'Editer'}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => duplicate(t)}
                          data-testid={`tpl-btn-duplicate-${t.id}`}>
                    <Copy className="h-3 w-3" />
                  </Button>
                  {!t.readonly && (
                    <Button size="sm" variant="ghost" onClick={() => remove(t)} className="text-red-600"
                            data-testid={`tpl-btn-delete-${t.id}`}>
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {editing !== null && (
        <TemplateEditor
          template={editing}
          variables={variables}
          onSaved={() => { setEditing(null); load(); }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
