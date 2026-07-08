import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import { ScrollText, Shield, FileText, Cookie, AlertTriangle, Save, Zap, Eye, History, Loader2, Lock } from 'lucide-react';

const SLUG_ORDER = ['cgu', 'privacy', 'mentions', 'cookies', 'disclaimer'];
const SLUG_META = {
  cgu: { label: 'CGU', icon: ScrollText, colorClass: 'text-blue-600', bgClass: 'bg-blue-50 border-blue-200' },
  privacy: { label: 'Confidentialite (RGPD)', icon: Shield, colorClass: 'text-emerald-600', bgClass: 'bg-emerald-50 border-emerald-200' },
  mentions: { label: 'Mentions Legales', icon: FileText, colorClass: 'text-slate-600', bgClass: 'bg-slate-50 border-slate-200' },
  cookies: { label: 'Cookies', icon: Cookie, colorClass: 'text-amber-600', bgClass: 'bg-amber-50 border-amber-200' },
  disclaimer: { label: 'Disclaimer', icon: AlertTriangle, colorClass: 'text-red-600', bgClass: 'bg-red-50 border-red-200' },
};

/**
 * Ecran admin pour editer les 5 documents legaux (CGU, Privacy, Mentions,
 * Cookies, Disclaimer). Split-view editeur/preview. Bump version force les
 * utilisateurs a re-accepter.
 * Access : superadmin only (backend 403 sinon).
 */
export default function AdminLegalDocsPage() {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeSlug, setActiveSlug] = useState('cgu');
  const [editContent, setEditContent] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [bumpDialogOpen, setBumpDialogOpen] = useState(false);

  useEffect(() => { void load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/legal/admin/documents');
      setDocs(r.data || []);
      const current = (r.data || []).find(d => d.slug === activeSlug) || r.data?.[0];
      if (current) {
        setActiveSlug(current.slug);
        setEditContent(current.content || '');
        setEditTitle(current.title || '');
        setDirty(false);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Impossible de charger les documents');
    } finally {
      setLoading(false);
    }
  };

  const activeDoc = docs.find(d => d.slug === activeSlug);

  const selectSlug = (slug) => {
    if (dirty && !window.confirm('Modifications non enregistrees. Changer de document quand meme ?')) return;
    const d = docs.find(x => x.slug === slug);
    if (!d) return;
    setActiveSlug(slug);
    setEditContent(d.content || '');
    setEditTitle(d.title || '');
    setDirty(false);
    setShowHistory(false);
    setHistory([]);
  };

  const save = async (bump) => {
    if (!editContent.trim()) {
      toast.error('Le contenu ne peut pas etre vide');
      return;
    }
    setSaving(true);
    try {
      const r = await api.put(`/legal/admin/documents/${activeSlug}`, {
        title: editTitle,
        content: editContent,
        bump_version: !!bump,
      });
      toast.success(r.data?.message || 'Sauvegarde');
      setBumpDialogOpen(false);
      await load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur lors de la sauvegarde');
    } finally {
      setSaving(false);
    }
  };

  const loadHistory = async () => {
    try {
      const r = await api.get(`/legal/admin/documents/${activeSlug}/history`);
      setHistory(r.data || []);
      setShowHistory(true);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur historique');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-sm p-8 justify-center">
        <Loader2 size={16} className="animate-spin" /> Chargement des documents...
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="admin-legal-docs-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
            <ScrollText size={22} className="text-[#2563EB]" />
            Documents legaux
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Edition des CGU, politique de confidentialite, mentions et cookies. Publier une nouvelle
            version force les utilisateurs a re-accepter.
          </p>
        </div>
        <Badge variant="outline" className="text-[10px] border-amber-300 bg-amber-50 text-amber-700">
          <Lock size={10} className="mr-1" /> Super admin uniquement
        </Badge>
      </div>

      {/* Tabs des documents */}
      <div className="flex flex-wrap gap-1.5" data-testid="admin-legal-tabs">
        {SLUG_ORDER.map((slug) => {
          const meta = SLUG_META[slug];
          const d = docs.find(x => x.slug === slug);
          const isActive = slug === activeSlug;
          const Icon = meta.icon;
          return (
            <button
              key={slug}
              onClick={() => selectSlug(slug)}
              data-testid={`admin-legal-tab-${slug}`}
              className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded border transition-colors ${
                isActive
                  ? 'bg-[#2563EB] text-white border-[#2563EB]'
                  : `bg-white text-slate-700 border-slate-200 hover:border-[#2563EB] hover:text-[#2563EB]`
              }`}
            >
              <Icon size={12} />
              <span>{meta.label}</span>
              {d && (
                <span className={`text-[10px] rounded px-1 py-0 ${
                  isActive ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-500'
                }`}>v{d.version}</span>
              )}
            </button>
          );
        })}
      </div>

      {activeDoc && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {/* Editeur */}
          <Card className="border-slate-200">
            <CardHeader className="pb-3 flex-row items-start justify-between">
              <div>
                <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
                  <ScrollText size={14} className="text-slate-500" />
                  Editeur
                </CardTitle>
                <p className="text-[11px] text-slate-500 mt-1">
                  Version courante : <strong>v{activeDoc.version}</strong> — MAJ le {activeDoc.updated_at ? new Date(activeDoc.updated_at).toLocaleString('fr-BE') : '—'}
                </p>
              </div>
              <Button
                onClick={loadHistory}
                variant="outline"
                size="sm"
                className="text-xs"
                data-testid="admin-legal-history-btn"
              >
                <History size={12} className="mr-1" /> Historique
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">Titre</label>
                <Input
                  value={editTitle}
                  onChange={(e) => { setEditTitle(e.target.value); setDirty(true); }}
                  data-testid="admin-legal-title-input"
                  className="mt-1 text-sm"
                />
              </div>
              <div>
                <label className="text-[11px] font-semibold text-slate-600 uppercase tracking-wide">
                  Contenu (Markdown)
                </label>
                <Textarea
                  value={editContent}
                  onChange={(e) => { setEditContent(e.target.value); setDirty(true); }}
                  data-testid="admin-legal-content-input"
                  className="mt-1 min-h-[500px] font-mono text-xs leading-relaxed"
                  spellCheck={false}
                />
                <div className="mt-1 flex items-center justify-between text-[10px] text-slate-400">
                  <span>{editContent.length} caracteres</span>
                  {dirty && <span className="text-amber-600 font-medium">Modifie - non sauvegarde</span>}
                </div>
              </div>

              <div className="flex flex-col sm:flex-row gap-2 pt-2 border-t border-slate-100">
                <Button
                  onClick={() => save(false)}
                  disabled={saving || !dirty}
                  className="bg-slate-800 hover:bg-slate-900 text-white flex-1"
                  data-testid="admin-legal-save-btn"
                >
                  {saving ? <><Loader2 size={13} className="animate-spin mr-1.5" /> Sauvegarde...</> : <><Save size={13} className="mr-1.5" /> Sauvegarder (sans bump)</>}
                </Button>
                <AlertDialog open={bumpDialogOpen} onOpenChange={setBumpDialogOpen}>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="default"
                      className="bg-[#2563EB] hover:bg-[#1D4ED8] text-white flex-1"
                      disabled={saving}
                      data-testid="admin-legal-bump-btn"
                    >
                      <Zap size={13} className="mr-1.5" /> Publier nouvelle version (v{activeDoc.version + 1})
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent data-testid="admin-legal-bump-dialog">
                    <AlertDialogHeader>
                      <AlertDialogTitle>Publier la version v{activeDoc.version + 1} ?</AlertDialogTitle>
                      <AlertDialogDescription>
                        <div className="space-y-2 text-sm">
                          <div>
                            Vous vous appretez a publier une <strong>nouvelle version</strong> du document
                            &laquo; {activeDoc.title} &raquo;.
                          </div>
                          {(activeSlug === 'cgu' || activeSlug === 'privacy') ? (
                            <div className="bg-amber-50 border border-amber-200 rounded p-2 text-amber-800 text-xs">
                              <strong>Impact important :</strong> Tous les utilisateurs devront a nouveau accepter
                              les {activeSlug === 'cgu' ? 'CGU' : 'la Politique de Confidentialite'} lors de leur
                              prochaine connexion (modal bloquant).
                            </div>
                          ) : (
                            <div className="bg-slate-50 border border-slate-200 rounded p-2 text-slate-600 text-xs">
                              Ce document ne declenche pas de re-acceptation obligatoire, mais la version
                              incrementee sera visible pour tracabilite.
                            </div>
                          )}
                        </div>
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel data-testid="admin-legal-bump-cancel">Annuler</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={(e) => { e.preventDefault(); save(true); }}
                        className="bg-[#2563EB] hover:bg-[#1D4ED8] text-white"
                        data-testid="admin-legal-bump-confirm"
                      >
                        <Zap size={13} className="mr-1.5" /> Publier v{activeDoc.version + 1}
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>

              {showHistory && (
                <div className="mt-3 border-t pt-3 border-slate-100" data-testid="admin-legal-history-panel">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-slate-700">Historique ({history.length})</h4>
                    <button onClick={() => setShowHistory(false)} className="text-[11px] text-slate-500 hover:underline">
                      Masquer
                    </button>
                  </div>
                  {history.length === 0 ? (
                    <p className="text-[11px] text-slate-500 italic mt-1">Aucune modification enregistree.</p>
                  ) : (
                    <ul className="mt-2 space-y-1 max-h-40 overflow-auto">
                      {history.map((h) => (
                        <li key={h.id} className="text-[11px] px-2 py-1 rounded bg-slate-50 border border-slate-100 flex items-center gap-2">
                          <span className={h.bumped ? 'font-semibold text-blue-700' : 'text-slate-700'}>
                            v{h.version_before} → v{h.version_after}
                          </span>
                          {h.bumped && <Badge className="bg-blue-100 text-blue-700 border-0 text-[9px]">bump</Badge>}
                          <span className="text-slate-500 ml-auto">
                            {h.edited_by_email} — {new Date(h.edited_at).toLocaleString('fr-BE')}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Preview */}
          <Card className="border-slate-200">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2" style={{ fontFamily: 'Chivo, sans-serif' }}>
                <Eye size={14} className="text-slate-500" />
                Preview (rendu Markdown)
              </CardTitle>
              <p className="text-[11px] text-slate-500">
                Apercu du rendu tel que les utilisateurs le verront sur <code>/legal/{activeSlug}</code>.
              </p>
            </CardHeader>
            <CardContent>
              <div className={`p-4 rounded border ${SLUG_META[activeSlug].bgClass} min-h-[550px] max-h-[700px] overflow-auto`}>
                <h2 className="text-lg font-semibold text-slate-900 mb-3 pb-2 border-b border-slate-200" style={{ fontFamily: 'Chivo, sans-serif' }}>
                  {editTitle || activeDoc.title}
                </h2>
                <article
                  className="prose prose-slate prose-sm max-w-none prose-headings:font-semibold prose-headings:text-slate-800 prose-a:text-[#2563EB] prose-table:text-xs prose-code:text-[13px] prose-strong:text-slate-900"
                  data-testid="admin-legal-preview"
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{editContent || '*(vide)*'}</ReactMarkdown>
                </article>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
