import { useEffect, useState } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Users, Plus, Pencil, Trash2, IdCard, Building2, Mail } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

export default function TeamMembersPage() {
  const { user } = useAuth();
  const [members, setMembers] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [copros, setCopros] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ email: '', name: '', password: '', role_template_id: '', copropriete_ids: [], permissions: [], must_change_password: true });

  const load = async () => {
    setLoading(true);
    try {
      const [m, t, c, p] = await Promise.all([
        api.get('/team/members'),
        api.get('/admin/role-templates').catch(() => ({ data: [] })),
        api.get('/admin/permissions-catalog').catch(() => ({ data: [] })),
        api.get('/coproprietes'),
      ]);
      setMembers(m.data);
      setTemplates(t.data);
      setCatalog(c.data);
      setCopros(p.data);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const openNew = () => {
    setEditing(null);
    setForm({ email: '', name: '', password: '', role_template_id: '', copropriete_ids: [], permissions: [], must_change_password: true });
    setDialog(true);
  };

  const openEdit = (m) => {
    setEditing(m);
    setForm({
      email: m.email,
      name: m.name || '',
      password: '',
      role_template_id: m.role_template_id || '',
      copropriete_ids: m.copropriete_ids || [],
      permissions: m.permissions || [],
      must_change_password: m.must_change_password || false,
    });
    setDialog(true);
  };

  // Quand on change le profil, charger automatiquement ses permissions (override possible)
  const onTemplateChange = (tplId) => {
    const tpl = templates.find(t => t.id === tplId);
    setForm({
      ...form,
      role_template_id: tplId || '',
      permissions: tpl ? [...(tpl.permissions || [])] : [],
    });
  };

  const togglePerm = (code) => {
    setForm(f => ({
      ...f,
      permissions: f.permissions.includes(code)
        ? f.permissions.filter(p => p !== code)
        : [...f.permissions, code],
    }));
  };

  const toggleAcp = (id) => {
    setForm(f => ({
      ...f,
      copropriete_ids: f.copropriete_ids.includes(id)
        ? f.copropriete_ids.filter(c => c !== id)
        : [...f.copropriete_ids, id],
    }));
  };

  const save = async () => {
    if (!form.name || !form.email) {
      toast.error('Nom et email requis');
      return;
    }
    try {
      if (editing) {
        const payload = { ...form };
        if (!payload.password) delete payload.password;
        delete payload.email;
        await api.put(`/team/members/${editing.id}`, payload);
        toast.success('Gestionnaire mis a jour');
      } else {
        if (!form.password || form.password.length < 6) {
          toast.error('Mot de passe initial requis (6 caracteres min)');
          return;
        }
        await api.post('/team/members', form);
        toast.success('Gestionnaire cree');
      }
      setDialog(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const remove = async (m) => {
    if (!window.confirm(`Supprimer ${m.name} (${m.email}) ?\n\nCette action est definitive.`)) return;
    try {
      await api.delete(`/team/members/${m.id}`);
      toast.success('Membre supprime');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const resendInvitation = async (m) => {
    if (!window.confirm(`Renvoyer un email d'invitation a ${m.email} ?`)) return;
    try {
      await api.post(`/team/members/${m.id}/resend-invitation`);
      toast.success(`Invitation renvoyee a ${m.email}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de l\'envoi');
    }
  };

  const tplById = templates.reduce((acc, t) => { acc[t.id] = t; return acc; }, {});
  const acpById = copros.reduce((acc, c) => { acc[c.id] = c; return acc; }, {});
  const groupedCatalog = catalog.reduce((acc, p) => {
    const g = p.code.split('.')[0];
    (acc[g] = acc[g] || []).push(p);
    return acc;
  }, {});

  return (
    <div className="space-y-4" data-testid="team-page">
      <div className="page-header flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-md bg-[#2563EB]/10 flex items-center justify-center text-[#2563EB]"><Users size={20} /></div>
          <div>
            <h1 className="page-title">Mon equipe</h1>
            <p className="page-subtitle">Gestionnaires rattaches a votre cabinet ({user?.name}). Vous pouvez attribuer un profil prefefini puis ajuster les permissions a la piece.</p>
          </div>
        </div>
        <Button onClick={openNew} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="new-member-btn">
          <Plus size={14} className="mr-1" /> Ajouter un gestionnaire
        </Button>
      </div>

      <Card>
        <CardContent className="pt-4">
          {loading ? (
            <div className="text-sm text-slate-500">Chargement...</div>
          ) : members.length === 0 ? (
            <div className="text-sm text-slate-500 italic text-center py-8">
              Aucun gestionnaire dans votre equipe pour le moment.
              <br />
              <Button onClick={openNew} variant="link" className="text-[#2563EB]">Ajouter votre premier gestionnaire</Button>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-100 text-slate-600 text-xs uppercase">
                  <tr>
                    <th className="px-2 py-2 text-left">Nom</th>
                    <th className="px-2 py-2 text-left">Email</th>
                    <th className="px-2 py-2 text-left">Profil</th>
                    <th className="px-2 py-2 text-center">ACPs assignees</th>
                    <th className="px-2 py-2 text-center">Permissions</th>
                    <th className="px-2 py-2 text-center">Cree le</th>
                    <th className="px-2 py-2 text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {members.map(m => (
                    <tr key={m.id} className="hover:bg-slate-50" data-testid={`member-row-${m.id}`}>
                      <td className="px-2 py-2 font-semibold">{m.name}</td>
                      <td className="px-2 py-2 text-slate-600 font-mono text-xs">
                        <span className="inline-flex items-center gap-1"><Mail size={12} className="text-slate-400" />{m.email}</span>
                        {m.must_change_password && <Badge variant="outline" className="ml-1 text-[10px] bg-amber-50 text-amber-700 border-amber-300">Mdp a changer</Badge>}
                      </td>
                      <td className="px-2 py-2">
                        {m.role_template_id ? (
                          <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-300"><IdCard size={10} className="mr-1" />{tplById[m.role_template_id]?.name || 'Profil ?'}</Badge>
                        ) : (
                          <Badge variant="outline" className="text-slate-500">Aucun</Badge>
                        )}
                      </td>
                      <td className="px-2 py-2 text-center">
                        <Badge variant="outline" className="text-xs">{(m.copropriete_ids || []).length} ACP{(m.copropriete_ids || []).length > 1 ? 's' : ''}</Badge>
                      </td>
                      <td className="px-2 py-2 text-center text-xs text-slate-500">{(m.permissions || []).length} perms</td>
                      <td className="px-2 py-2 text-center text-xs text-slate-500">{fmtDate(m.created_at)}</td>
                      <td className="px-2 py-2 text-center">
                        <div className="flex justify-center gap-1">
                          {m.must_change_password && (
                            <Button size="sm" variant="outline" onClick={() => resendInvitation(m)} className="text-blue-700 border-blue-300 h-7 px-2" title="Renvoyer l'invitation par email" data-testid={`resend-member-${m.id}`}>
                              <Mail size={12} />
                            </Button>
                          )}
                          <Button size="sm" variant="outline" onClick={() => openEdit(m)} data-testid={`edit-member-${m.id}`} className="h-7 px-2">
                            <Pencil size={12} />
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => remove(m)} className="text-red-700 border-red-300 h-7 px-2" data-testid={`del-member-${m.id}`}>
                            <Trash2 size={12} />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dialog new/edit */}
      <Dialog open={dialog} onOpenChange={(o) => !o && setDialog(false)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Users size={16} className="text-[#2563EB]" />
              {editing ? `Modifier ${editing.name}` : 'Nouveau gestionnaire'}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto space-y-3 px-1">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Nom complet *</label>
                <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="Marie Dupont" data-testid="member-name" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Email *</label>
                <Input value={form.email} disabled={!!editing} onChange={e => setForm({...form, email: e.target.value})} placeholder="marie@cabinet.be" data-testid="member-email" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">{editing ? 'Nouveau mot de passe (optionnel)' : 'Mot de passe initial *'}</label>
                <Input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} placeholder={editing ? 'Laissez vide pour ne pas changer' : '6+ caracteres'} data-testid="member-password" />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-xs text-slate-700">
                  <input type="checkbox" checked={form.must_change_password} onChange={e => setForm({...form, must_change_password: e.target.checked})} data-testid="member-must-change" />
                  Forcer le changement au prochain login
                </label>
              </div>
            </div>

            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Profil pre-defini (optionnel)</label>
              <Select value={form.role_template_id || '__none__'} onValueChange={v => onTemplateChange(v === '__none__' ? '' : v)}>
                <SelectTrigger data-testid="member-template"><SelectValue placeholder="Choisir un profil" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">— Aucun (permissions a la piece) —</SelectItem>
                  {templates.map(t => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name} ({(t.permissions || []).length} permissions)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.role_template_id && tplById[form.role_template_id]?.description && (
                <div className="text-xs text-slate-500 mt-1 italic">{tplById[form.role_template_id]?.description}</div>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
                  Coproprietes assignees ({form.copropriete_ids.length}/{copros.length})
                </label>
                <div className="flex gap-2">
                  <button type="button" className="text-xs text-[#2563EB] hover:underline" onClick={() => setForm({...form, copropriete_ids: copros.map(c => c.id)})}>Toutes</button>
                  <button type="button" className="text-xs text-[#2563EB] hover:underline" onClick={() => setForm({...form, copropriete_ids: []})}>Aucune</button>
                </div>
              </div>
              <div className="border border-slate-200 rounded p-2 max-h-32 overflow-auto bg-slate-50">
                {copros.length === 0 ? (
                  <div className="text-xs italic text-slate-500">Aucune ACP dans votre perimetre.</div>
                ) : copros.map(c => (
                  <label key={c.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-white rounded px-1" data-testid={`copro-check-${c.id}`}>
                    <input type="checkbox" checked={form.copropriete_ids.includes(c.id)} onChange={() => toggleAcp(c.id)} className="accent-[#2563EB]" />
                    <Building2 size={12} className="text-slate-400" />
                    <span>{c.name}</span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Permissions individuelles ({form.permissions.length})</label>
                <div className="flex gap-2">
                  <button type="button" className="text-xs text-[#2563EB] hover:underline" onClick={() => setForm({...form, permissions: catalog.map(p => p.code)})}>Toutes</button>
                  <button type="button" className="text-xs text-[#2563EB] hover:underline" onClick={() => setForm({...form, permissions: []})}>Aucune</button>
                </div>
              </div>
              <div className="border border-slate-200 rounded p-2 max-h-60 overflow-auto bg-slate-50 text-xs">
                {Object.entries(groupedCatalog).map(([group, perms]) => (
                  <div key={group} className="mb-2">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-600 mb-1">{group}</div>
                    {perms.map(p => (
                      <label key={p.code} className="flex items-start gap-2 py-1 cursor-pointer hover:bg-white rounded px-1">
                        <input type="checkbox" checked={form.permissions.includes(p.code)} onChange={() => togglePerm(p.code)} className="mt-0.5 accent-[#2563EB]" />
                        <div>
                          <code className="text-[10px] text-slate-500 font-mono">{p.code}</code>
                          <div className="text-slate-700">{p.label}</div>
                        </div>
                      </label>
                    ))}
                  </div>
                ))}
              </div>
              <p className="text-xs text-slate-500 mt-1 italic">Vous pouvez ajuster les permissions individuellement, meme si un profil a ete choisi.</p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(false)} data-testid="member-cancel">Annuler</Button>
            <Button onClick={save} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="member-save">
              {editing ? 'Mettre a jour' : 'Creer le gestionnaire'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
