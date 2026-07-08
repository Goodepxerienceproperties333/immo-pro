/**
 * AdminReleaseNotesPage (iter90br)
 *
 * CRUD des notes de version + telechargement du PDF commercial.
 * Accessible aux superadmins uniquement.
 */
import { useState, useEffect } from 'react';
import api from '@/lib/api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import { Sparkles, Bug, Shield, AlertTriangle, TrendingUp,
         FileText, Plus, Pencil, Trash2, Download } from 'lucide-react';

const CATEGORIES = [
  { key: 'feature', label: 'Nouveaute', icon: Sparkles, color: 'text-blue-600' },
  { key: 'improvement', label: 'Amelioration', icon: TrendingUp, color: 'text-violet-600' },
  { key: 'fix', label: 'Correction', icon: Bug, color: 'text-emerald-600' },
  { key: 'security', label: 'Securite', icon: Shield, color: 'text-amber-600' },
  { key: 'breaking', label: 'Rupture', icon: AlertTriangle, color: 'text-red-600' },
];

const emptyForm = () => ({
  version: '',
  title: '',
  category: 'improvement',
  description: '',
  roles_target: ['all'],
  published: true,
});

export default function AdminReleaseNotesPage() {
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState(emptyForm());
  const [editingId, setEditingId] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/release-notes/all');
      setNotes(r.data || []);
    } catch (err) {
      toast.error('Erreur chargement notes');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm());
    setDialogOpen(true);
  };
  const openEdit = (n) => {
    setEditingId(n.id);
    setForm({
      version: n.version || '',
      title: n.title || '',
      category: n.category || 'improvement',
      description: n.description || '',
      roles_target: n.roles_target || ['all'],
      published: n.published !== false,
    });
    setDialogOpen(true);
  };
  const save = async () => {
    if (!form.title.trim() || !form.version.trim()) {
      toast.error('Titre et version requis');
      return;
    }
    try {
      if (editingId) {
        await api.put(`/release-notes/${editingId}`, form);
        toast.success('Note mise a jour');
      } else {
        await api.post('/release-notes', form);
        toast.success('Note creee');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const del = async (id) => {
    if (!window.confirm('Supprimer cette note ?')) return;
    try {
      await api.delete(`/release-notes/${id}`);
      toast.success('Note supprimee');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const downloadPdf = async () => {
    try {
      const r = await api.get('/documentation/features-pdf', { responseType: 'blob' });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `CoproManager-Fonctionnalites-${new Date().toISOString().slice(0, 10)}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success('PDF telecharge');
    } catch (err) {
      toast.error('Erreur telechargement PDF');
    }
  };

  return (
    <div className="space-y-6" data-testid="admin-release-notes-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-display font-bold text-slate-900">
            Notes de version
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Les utilisateurs verront un popup au login pour les notes non-lues.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={downloadPdf}
            data-testid="download-features-pdf"
          >
            <Download size={16} className="mr-1.5" />
            PDF commercial
          </Button>
          <Button onClick={openCreate} data-testid="create-note-btn">
            <Plus size={16} className="mr-1.5" />
            Nouvelle note
          </Button>
        </div>
      </div>

      {loading ? (
        <p className="text-slate-500 text-sm">Chargement...</p>
      ) : notes.length === 0 ? (
        <Card>
          <CardContent className="p-10 text-center text-slate-500">
            <FileText size={32} className="mx-auto mb-3 opacity-40" />
            <p>Aucune note de version.</p>
            <p className="text-xs mt-1">Creez la premiere pour informer vos utilisateurs.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {notes.map((n) => {
            const cat = CATEGORIES.find((c) => c.key === n.category) || CATEGORIES[1];
            const Icon = cat.icon;
            return (
              <Card key={n.id} data-testid={`admin-note-${n.id}`}>
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap mb-1.5">
                        <Icon size={14} className={cat.color} />
                        <span className={`text-[10px] uppercase font-bold ${cat.color}`}>
                          {cat.label}
                        </span>
                        <span className="text-[11px] text-slate-500 font-mono">v{n.version}</span>
                        <span className="text-[11px] text-slate-400">•</span>
                        <span className="text-[11px] text-slate-500">{n.date}</span>
                        {!n.published && (
                          <Badge variant="outline" className="text-[9px]">Brouillon</Badge>
                        )}
                        {n.roles_target && !n.roles_target.includes('all') && (
                          <Badge variant="outline" className="text-[9px]">
                            {n.roles_target.join(', ')}
                          </Badge>
                        )}
                      </div>
                      <h3 className="text-sm font-bold text-slate-900">{n.title}</h3>
                      {n.description && (
                        <p className="text-xs text-slate-600 mt-1 whitespace-pre-line line-clamp-3">
                          {n.description}
                        </p>
                      )}
                    </div>
                    <div className="flex gap-1 shrink-0">
                      <Button variant="ghost" size="icon" onClick={() => openEdit(n)}
                              data-testid={`edit-note-${n.id}`}>
                        <Pencil size={14} />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => del(n.id)}
                              className="text-red-500 hover:text-red-700 hover:bg-red-50"
                              data-testid={`delete-note-${n.id}`}>
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Dialog create/edit */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editingId ? 'Modifier la note' : 'Nouvelle note de version'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider font-semibold text-slate-500 block mb-1">
                Version *
              </label>
              <Input
                value={form.version}
                onChange={(e) => setForm({ ...form, version: e.target.value })}
                placeholder="ex : 1.2.3 ou iter90bq"
                data-testid="note-version-input"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-semibold text-slate-500 block mb-1">
                Titre *
              </label>
              <Input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="ex : Refonte visuelle moderne"
                data-testid="note-title-input"
              />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-semibold text-slate-500 block mb-1">
                Categorie
              </label>
              <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                <SelectTrigger data-testid="note-category-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.key} value={c.key}>{c.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider font-semibold text-slate-500 block mb-1">
                Description
              </label>
              <Textarea
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                rows={5}
                placeholder="Description detaillee. Sautez des lignes pour lister les changements."
                data-testid="note-description-input"
              />
            </div>
            <div className="flex items-center gap-2 pt-1">
              <input
                type="checkbox"
                id="note-published"
                checked={form.published}
                onChange={(e) => setForm({ ...form, published: e.target.checked })}
                data-testid="note-published-checkbox"
              />
              <label htmlFor="note-published" className="text-xs text-slate-700">
                Publiee (visible par les utilisateurs)
              </label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
            <Button onClick={save} data-testid="note-save-btn">
              {editingId ? 'Enregistrer' : 'Creer'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
