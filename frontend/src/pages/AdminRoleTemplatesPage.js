import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { toast } from 'sonner';
import { ArrowLeft, IdCard, Plus, Pencil, Trash2, Check, Lock } from 'lucide-react';

export default function AdminRoleTemplatesPage() {
  const [catalog, setCatalog] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', description: '', permissions: [] });

  const load = async () => {
    setLoading(true);
    try {
      const [c, t] = await Promise.all([
        api.get('/admin/permissions-catalog'),
        api.get('/admin/role-templates'),
      ]);
      setCatalog(c.data);
      setTemplates(t.data);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const groupedCatalog = catalog.reduce((acc, p) => {
    const group = p.code.split('.')[0];
    (acc[group] = acc[group] || []).push(p);
    return acc;
  }, {});

  const openNew = () => {
    setEditing(null);
    setForm({ name: '', description: '', permissions: [] });
    setDialog(true);
  };

  const openEdit = (tpl) => {
    setEditing(tpl);
    setForm({ name: tpl.name || '', description: tpl.description || '', permissions: [...(tpl.permissions || [])] });
    setDialog(true);
  };

  const togglePerm = (code) => {
    setForm(f => ({
      ...f,
      permissions: f.permissions.includes(code)
        ? f.permissions.filter(p => p !== code)
        : [...f.permissions, code],
    }));
  };

  const save = async () => {
    if (!form.name || form.name.length < 2) {
      toast.error('Nom requis (2 caracteres min)');
      return;
    }
    try {
      if (editing) {
        await api.put(`/admin/role-templates/${editing.id}`, form);
        toast.success('Profil mis a jour');
      } else {
        await api.post('/admin/role-templates', form);
        toast.success('Profil cree');
      }
      setDialog(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const remove = async (tpl) => {
    if (tpl.is_system) {
      toast.error('Les profils systeme ne peuvent pas etre supprimes');
      return;
    }
    if (!window.confirm(`Supprimer le profil "${tpl.name}" ?`)) return;
    try {
      await api.delete(`/admin/role-templates/${tpl.id}`);
      toast.success('Profil supprime');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  return (
    <div className="space-y-4" data-testid="admin-templates-page">
      <div className="flex items-center gap-3">
        <Link to="/admin" className="text-[#0055FF] hover:underline text-sm flex items-center gap-1" data-testid="back-to-admin"><ArrowLeft size={14} /> Retour Admin</Link>
      </div>
      <div className="page-header flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-emerald-100 flex items-center justify-center text-emerald-700"><IdCard size={20} /></div>
          <div>
            <h1 className="page-title">Profils utilisateurs</h1>
            <p className="page-subtitle">Templates de permissions reutilisables par les syndics lors de la creation de gestionnaires.</p>
          </div>
        </div>
        <Button onClick={openNew} className="bg-emerald-600 hover:bg-emerald-700" data-testid="new-template-btn">
          <Plus size={14} className="mr-1" /> Nouveau profil
        </Button>
      </div>

      {loading ? (
        <div className="text-sm text-slate-500">Chargement...</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map(tpl => (
            <Card key={tpl.id} className={tpl.is_system ? 'border-blue-200' : ''} data-testid={`template-card-${tpl.id}`}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <CardTitle className="text-base flex items-center gap-2">
                    <IdCard size={16} className="text-emerald-600" />
                    {tpl.name}
                  </CardTitle>
                  {tpl.is_system && (
                    <Badge variant="outline" className="text-[10px] bg-blue-50 text-blue-700 border-blue-300">
                      <Lock size={9} className="mr-1" /> Systeme
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent className="text-sm">
                <p className="text-slate-600 mb-2">{tpl.description}</p>
                <div className="text-xs text-slate-500 mb-2">
                  <Badge variant="outline" className="mr-1">{(tpl.permissions || []).length} permissions</Badge>
                </div>
                <div className="flex flex-wrap gap-1 mb-3">
                  {(tpl.permissions || []).slice(0, 6).map(p => (
                    <Badge key={p} variant="outline" className="text-[10px] font-mono">{p}</Badge>
                  ))}
                  {(tpl.permissions || []).length > 6 && (
                    <Badge variant="outline" className="text-[10px]">+{tpl.permissions.length - 6} autres</Badge>
                  )}
                </div>
                <div className="flex gap-1 mt-3">
                  <Button size="sm" variant="outline" onClick={() => openEdit(tpl)} data-testid={`edit-tpl-${tpl.id}`}>
                    <Pencil size={12} className="mr-1" /> {tpl.is_system ? 'Voir / Modifier' : 'Modifier'}
                  </Button>
                  {!tpl.is_system && (
                    <Button size="sm" variant="outline" className="text-red-700 border-red-300" onClick={() => remove(tpl)} data-testid={`del-tpl-${tpl.id}`}>
                      <Trash2 size={12} />
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Dialog new/edit */}
      <Dialog open={dialog} onOpenChange={(o) => !o && setDialog(false)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <IdCard size={16} className="text-emerald-600" />
              {editing ? `Modifier le profil "${editing.name}"` : 'Nouveau profil utilisateur'}
              {editing?.is_system && (
                <Badge variant="outline" className="text-xs bg-blue-50 text-blue-700 border-blue-300 ml-2">
                  <Lock size={10} className="mr-1" /> Systeme
                </Badge>
              )}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto px-1 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Nom du profil *</label>
                <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="ex: Comptable senior" data-testid="tpl-name" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">{form.permissions.length} permission(s) cochee(s)</label>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" onClick={() => setForm({...form, permissions: catalog.map(p => p.code)})}>Tout cocher</Button>
                  <Button size="sm" variant="outline" onClick={() => setForm({...form, permissions: []})}>Tout decocher</Button>
                </div>
              </div>
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Description</label>
              <Textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})} placeholder="A quoi sert ce profil ?" rows={2} data-testid="tpl-desc" />
            </div>
            <div className="border border-slate-200 rounded-md p-3 max-h-[400px] overflow-auto bg-slate-50">
              {Object.entries(groupedCatalog).map(([group, perms]) => (
                <div key={group} className="mb-3">
                  <div className="text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">{group}</div>
                  <div className="space-y-1">
                    {perms.map(p => (
                      <label key={p.code} className="flex items-start gap-2 text-sm cursor-pointer hover:bg-white p-1.5 rounded" data-testid={`perm-${p.code}`}>
                        <input
                          type="checkbox"
                          checked={form.permissions.includes(p.code)}
                          onChange={() => togglePerm(p.code)}
                          className="mt-0.5 accent-emerald-600"
                        />
                        <div className="flex-1">
                          <code className="text-xs text-slate-500 font-mono">{p.code}</code>
                          <div className="text-xs text-slate-700">{p.label}</div>
                        </div>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(false)} data-testid="tpl-cancel">Annuler</Button>
            <Button onClick={save} className="bg-emerald-600 hover:bg-emerald-700" data-testid="tpl-save">
              <Check size={14} className="mr-1" /> {editing ? 'Mettre a jour' : 'Creer le profil'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
