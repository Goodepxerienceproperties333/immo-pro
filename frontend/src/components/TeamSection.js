/**
 * Section "Mon equipe" integrable dans MonBureauPage.
 * Reutilise les endpoints /api/team/members existants.
 * Permet au syndic de creer, modifier, supprimer ses gestionnaires
 * et de leur affecter un role / profil de permissions + ACPs.
 */
import { useEffect, useState, useCallback } from 'react';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Users, Plus, Pencil, Trash2, IdCard, Building2, Mail, ChevronDown, ChevronUp, Shield } from 'lucide-react';

function fmtDate(d) {
  if (!d) return '';
  try { return new Date(d).toLocaleDateString('fr-BE'); } catch { return d; }
}

export function TeamSection() {
  const [members, setMembers] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [copros, setCopros] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState(false);
  const [editing, setEditing] = useState(null);
  const [expanded, setExpanded] = useState(true);
  const [form, setForm] = useState({
    email: '', name: '', password: '', role_template_id: '',
    copropriete_ids: [], permissions: [], must_change_password: true,
  });

  const load = useCallback(async () => {
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
  }, []);

  useEffect(() => { load(); }, [load]);

  const openNew = () => {
    setEditing(null);
    setForm({ email: '', name: '', password: '', role_template_id: '', copropriete_ids: [], permissions: [], must_change_password: true });
    setDialog(true);
  };

  const openEdit = (m) => {
    setEditing(m);
    setForm({
      email: m.email, name: m.name || '', password: '',
      role_template_id: m.role_template_id || '',
      copropriete_ids: m.copropriete_ids || [],
      permissions: m.permissions || [],
      must_change_password: m.must_change_password || false,
    });
    setDialog(true);
  };

  const onTemplateChange = (tplId) => {
    const tpl = templates.find(t => t.id === tplId);
    setForm({ ...form, role_template_id: tplId || '', permissions: tpl ? [...(tpl.permissions || [])] : [] });
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
    if (!form.name || !form.email) { toast.error('Nom et email requis'); return; }
    try {
      if (editing) {
        const payload = { ...form };
        if (!payload.password) delete payload.password;
        delete payload.email;
        await api.put(`/team/members/${editing.id}`, payload);
        toast.success('Collaborateur mis a jour');
      } else {
        if (!form.password || form.password.length < 6) {
          toast.error('Mot de passe initial requis (6 caracteres min)');
          return;
        }
        await api.post('/team/members', form);
        toast.success('Collaborateur cree');
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
      toast.success('Collaborateur supprime');
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
      toast.error(err.response?.data?.detail || "Erreur lors de l'envoi");
    }
  };

  const tplById = templates.reduce((acc, t) => { acc[t.id] = t; return acc; }, {});
  const groupedCatalog = catalog.reduce((acc, p) => {
    const g = p.code.split('.')[0];
    (acc[g] = acc[g] || []).push(p);
    return acc;
  }, {});

  return (
    <>
      <Card data-testid="mon-bureau-team-section">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle
              className="text-base flex items-center gap-2 cursor-pointer select-none"
              onClick={() => setExpanded(e => !e)}
            >
              <Users className="h-4 w-4 text-[#022D52]" />
              Mon equipe — Collaborateurs
              <Badge variant="outline" className="ml-2 text-xs">
                {members.length} membre{members.length !== 1 ? 's' : ''}
              </Badge>
              {expanded ? <ChevronUp className="h-4 w-4 text-slate-400" /> : <ChevronDown className="h-4 w-4 text-slate-400" />}
            </CardTitle>
            <Button
              onClick={openNew}
              size="sm"
              className="bg-[#022D52] hover:bg-[#1D4ED8]"
              data-testid="mon-bureau-add-member-btn"
            >
              <Plus size={14} className="mr-1" /> Ajouter
            </Button>
          </div>
        </CardHeader>
        {expanded && (
          <CardContent className="pt-0">
            {loading ? (
              <div className="text-sm text-slate-500 py-4 text-center">Chargement de l&apos;equipe...</div>
            ) : members.length === 0 ? (
              <div className="text-sm text-slate-500 italic text-center py-6 space-y-2" data-testid="mon-bureau-team-empty">
                <Shield className="h-8 w-8 mx-auto text-slate-300 mb-2" />
                <div>Aucun collaborateur pour le moment.</div>
                <div className="text-xs text-slate-400">
                  Ajoutez des gestionnaires pour deleguer la gestion de vos coproprietes.
                  Chaque collaborateur recevra un email d&apos;invitation.
                </div>
                <Button onClick={openNew} variant="link" className="text-[#022D52] text-xs" data-testid="mon-bureau-team-first-add">
                  Ajouter votre premier collaborateur
                </Button>
              </div>
            ) : (
              <div className="overflow-x-auto -mx-2">
                <table className="w-full text-sm" data-testid="mon-bureau-team-table">
                  <thead className="bg-slate-50 text-slate-600 text-[10px] uppercase tracking-wider">
                    <tr>
                      <th className="px-2 py-2 text-left font-semibold">Collaborateur</th>
                      <th className="px-2 py-2 text-left font-semibold">Profil / Role</th>
                      <th className="px-2 py-2 text-center font-semibold">ACPs</th>
                      <th className="px-2 py-2 text-center font-semibold">Perms</th>
                      <th className="px-2 py-2 text-center font-semibold">Statut</th>
                      <th className="px-2 py-2 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {members.map(m => (
                      <tr key={m.id} className="hover:bg-slate-50/50 transition-colors" data-testid={`mon-bureau-member-${m.id}`}>
                        <td className="px-2 py-2.5">
                          <div className="font-semibold text-slate-800 text-sm">{m.name}</div>
                          <div className="text-xs text-slate-500 font-mono flex items-center gap-1">
                            <Mail size={10} className="text-slate-400" />{m.email}
                          </div>
                        </td>
                        <td className="px-2 py-2.5">
                          {m.role_template_id && tplById[m.role_template_id] ? (
                            <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-300 text-xs">
                              <IdCard size={10} className="mr-1" />{tplById[m.role_template_id].name}
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-slate-400 text-xs">Gestionnaire</Badge>
                          )}
                        </td>
                        <td className="px-2 py-2.5 text-center">
                          <Badge variant="outline" className="text-xs">{(m.copropriete_ids || []).length}</Badge>
                        </td>
                        <td className="px-2 py-2.5 text-center text-xs text-slate-500">{(m.permissions || []).length}</td>
                        <td className="px-2 py-2.5 text-center">
                          {m.must_change_password ? (
                            <Badge className="bg-amber-50 text-amber-700 border border-amber-300 text-[10px]">En attente</Badge>
                          ) : (
                            <Badge className="bg-emerald-50 text-emerald-700 border border-emerald-300 text-[10px]">Actif</Badge>
                          )}
                        </td>
                        <td className="px-2 py-2.5 text-right">
                          <div className="flex justify-end gap-1">
                            {m.must_change_password && (
                              <Button
                                size="sm" variant="outline"
                                onClick={() => resendInvitation(m)}
                                className="text-[#01213e] border-blue-300 h-7 px-2"
                                title="Renvoyer l'invitation"
                                data-testid={`mon-bureau-resend-${m.id}`}
                              >
                                <Mail size={12} />
                              </Button>
                            )}
                            <Button
                              size="sm" variant="outline"
                              onClick={() => openEdit(m)}
                              className="h-7 px-2"
                              data-testid={`mon-bureau-edit-member-${m.id}`}
                            >
                              <Pencil size={12} />
                            </Button>
                            <Button
                              size="sm" variant="outline"
                              onClick={() => remove(m)}
                              className="text-red-700 border-red-300 h-7 px-2"
                              data-testid={`mon-bureau-del-member-${m.id}`}
                            >
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
        )}
      </Card>

      {/* Dialog creation/edition collaborateur */}
      <Dialog open={dialog} onOpenChange={(o) => !o && setDialog(false)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-hidden flex flex-col" data-testid="mon-bureau-member-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Users size={16} className="text-[#022D52]" />
              {editing ? `Modifier ${editing.name}` : 'Nouveau collaborateur'}
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-auto space-y-3 px-1">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Nom complet *</label>
                <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="Marie Dupont" data-testid="mon-bureau-member-name" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Email *</label>
                <Input value={form.email} disabled={!!editing} onChange={e => setForm({...form, email: e.target.value})} placeholder="marie@cabinet.be" data-testid="mon-bureau-member-email" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">{editing ? 'Nouveau mot de passe (optionnel)' : 'Mot de passe initial *'}</label>
                <Input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} placeholder={editing ? 'Laissez vide pour ne pas changer' : '6+ caracteres'} data-testid="mon-bureau-member-password" />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-xs text-slate-700">
                  <input type="checkbox" checked={form.must_change_password} onChange={e => setForm({...form, must_change_password: e.target.checked})} data-testid="mon-bureau-member-must-change" />
                  Forcer le changement au prochain login
                </label>
              </div>
            </div>

            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Profil pre-defini (optionnel)</label>
              <Select value={form.role_template_id || '__none__'} onValueChange={v => onTemplateChange(v === '__none__' ? '' : v)}>
                <SelectTrigger data-testid="mon-bureau-member-template"><SelectValue placeholder="Choisir un profil" /></SelectTrigger>
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
                  <button type="button" className="text-xs text-[#022D52] hover:underline" onClick={() => setForm({...form, copropriete_ids: copros.map(c => c.id)})}>Toutes</button>
                  <button type="button" className="text-xs text-[#022D52] hover:underline" onClick={() => setForm({...form, copropriete_ids: []})}>Aucune</button>
                </div>
              </div>
              <div className="border border-slate-200 rounded p-2 max-h-32 overflow-auto bg-slate-50">
                {copros.length === 0 ? (
                  <div className="text-xs italic text-slate-500">Aucune ACP dans votre perimetre.</div>
                ) : copros.map(c => (
                  <label key={c.id} className="flex items-center gap-2 text-sm py-1 cursor-pointer hover:bg-white rounded px-1" data-testid={`mon-bureau-copro-check-${c.id}`}>
                    <input type="checkbox" checked={form.copropriete_ids.includes(c.id)} onChange={() => toggleAcp(c.id)} className="accent-[#022D52]" />
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
                  <button type="button" className="text-xs text-[#022D52] hover:underline" onClick={() => setForm({...form, permissions: catalog.map(p => p.code)})}>Toutes</button>
                  <button type="button" className="text-xs text-[#022D52] hover:underline" onClick={() => setForm({...form, permissions: []})}>Aucune</button>
                </div>
              </div>
              <div className="border border-slate-200 rounded p-2 max-h-48 overflow-auto bg-slate-50 text-xs">
                {Object.keys(groupedCatalog).length === 0 ? (
                  <div className="text-xs italic text-slate-500 py-2">Aucun catalogue de permissions configure.</div>
                ) : Object.entries(groupedCatalog).map(([group, perms]) => (
                  <div key={group} className="mb-2">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-600 mb-1">{group}</div>
                    {perms.map(p => (
                      <label key={p.code} className="flex items-start gap-2 py-1 cursor-pointer hover:bg-white rounded px-1">
                        <input type="checkbox" checked={form.permissions.includes(p.code)} onChange={() => togglePerm(p.code)} className="mt-0.5 accent-[#022D52]" />
                        <div>
                          <code className="text-[10px] text-slate-500 font-mono">{p.code}</code>
                          <div className="text-slate-700">{p.label}</div>
                        </div>
                      </label>
                    ))}
                  </div>
                ))}
              </div>
              <p className="text-xs text-slate-500 mt-1 italic">
                Vous pouvez ajuster les permissions individuellement, meme si un profil a ete choisi.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialog(false)} data-testid="mon-bureau-member-cancel">Annuler</Button>
            <Button onClick={save} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="mon-bureau-member-save">
              {editing ? 'Mettre a jour' : 'Creer le collaborateur'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
