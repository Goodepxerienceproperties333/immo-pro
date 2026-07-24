/**
 * Section "Equipe / Collaborateurs" pour la page Mon Bureau.
 *
 * Le syndic peut :
 *  - Voir ses collaborateurs (gestionnaires)
 *  - Ajouter directement (nom + email + mdp temp) OU par invitation (email seul)
 *  - Choisir un profil (role_template) et les ACPs accessibles
 *  - Modifier / supprimer un collaborateur
 */
import { useEffect, useState, useCallback } from 'react';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Users, UserPlus, Pencil, Trash2, Loader2, Send, Shield, Building2,
} from 'lucide-react';

export default function TeamSection() {
  const [members, setMembers] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [copros, setCopros] = useState([]);
  const [loading, setLoading] = useState(true);

  // Dialog state
  const [dialog, setDialog] = useState(null); // null | { mode: 'add'|'edit', data: {...} }
  const [saving, setSaving] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [mRes, tRes, cRes] = await Promise.all([
        api.get('/team/members'),
        api.get('/admin/role-templates'),
        api.get('/coproprietes'),
      ]);
      setMembers(mRes.data || []);
      setTemplates(tRes.data || []);
      setCopros(cRes.data || []);
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  // Open add dialog
  const openAdd = () => setDialog({
    mode: 'add',
    data: {
      name: '', email: '', password: '',
      role_template_id: '', copropriete_ids: [],
      creation_mode: 'direct', // 'direct' | 'invitation'
    },
  });

  // Open edit dialog
  const openEdit = (m) => setDialog({
    mode: 'edit',
    data: {
      id: m.id,
      name: m.name,
      email: m.email,
      role_template_id: m.role_template_id || '',
      copropriete_ids: m.copropriete_ids || [],
      password: '',
    },
  });

  // Save (create or update)
  const handleSave = async () => {
    if (!dialog) return;
    const d = dialog.data;
    if (!d.name?.trim()) { toast.error('Le nom est requis'); return; }
    if (!d.email?.trim()) { toast.error("L'email est requis"); return; }

    setSaving(true);
    try {
      if (dialog.mode === 'add') {
        const payload = {
          name: d.name.trim(),
          email: d.email.trim().toLowerCase(),
          copropriete_ids: d.copropriete_ids,
          role_template_id: d.role_template_id || null,
          must_change_password: d.creation_mode === 'invitation',
        };
        if (d.creation_mode === 'direct' && d.password) {
          payload.password = d.password;
        }
        await api.post('/team/members', payload);
        toast.success(
          d.creation_mode === 'invitation'
            ? `Invitation envoyee a ${d.email}`
            : `Collaborateur ${d.name} cree`
        );
      } else {
        const payload = {
          name: d.name.trim(),
          copropriete_ids: d.copropriete_ids,
          role_template_id: d.role_template_id || null,
        };
        if (d.password) payload.password = d.password;
        await api.put(`/team/members/${d.id}`, payload);
        toast.success('Collaborateur mis a jour');
      }
      setDialog(null);
      await loadAll();
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setSaving(false);
    }
  };

  // Delete
  const handleDelete = async (m) => {
    if (!window.confirm(`Supprimer ${m.name} (${m.email}) de votre equipe ?`)) return;
    try {
      await api.delete(`/team/members/${m.id}`);
      toast.success(`${m.name} supprime`);
      await loadAll();
    } catch (e) {
      toast.error(extractApiError(e));
    }
  };

  // Resend invitation
  const handleResend = async (m) => {
    try {
      await api.post(`/team/members/${m.id}/resend-invitation`);
      toast.success(`Invitation renvoyee a ${m.email}`);
    } catch (e) {
      toast.error(extractApiError(e));
    }
  };

  // Toggle ACP in dialog
  const toggleCopro = (cid) => {
    if (!dialog) return;
    const ids = dialog.data.copropriete_ids || [];
    const next = ids.includes(cid) ? ids.filter(i => i !== cid) : [...ids, cid];
    setDialog({ ...dialog, data: { ...dialog.data, copropriete_ids: next } });
  };

  const tplName = (tid) => templates.find(t => t.id === tid)?.name || '';

  if (loading) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-slate-500">
          <Loader2 className="h-5 w-5 animate-spin mx-auto mb-2" /> Chargement de l'equipe...
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card data-testid="team-section">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Users className="h-4 w-4 text-[#022D52]" /> Collaborateurs
              {members.length > 0 && (
                <Badge variant="secondary" className="ml-1">{members.length}</Badge>
              )}
            </CardTitle>
            <Button
              size="sm"
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
              onClick={openAdd}
              data-testid="team-btn-add"
            >
              <UserPlus className="h-4 w-4 mr-1" /> Ajouter
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {members.length === 0 ? (
            <div className="text-center py-6 text-sm text-slate-500" data-testid="team-empty">
              Aucun collaborateur. Cliquez sur "Ajouter" pour inviter votre premier gestionnaire.
            </div>
          ) : (
            <div className="space-y-2" data-testid="team-list">
              {members.map(m => (
                <div
                  key={m.id}
                  className="flex items-center justify-between border rounded-lg p-3 hover:bg-slate-50 transition-colors"
                  data-testid={`team-member-${m.id}`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm truncate">{m.name}</span>
                      {m.role_template_id && (
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          <Shield className="h-3 w-3 mr-0.5" />
                          {tplName(m.role_template_id)}
                        </Badge>
                      )}
                      {m.must_change_password && (
                        <Badge className="bg-amber-100 text-amber-800 text-[10px] shrink-0">
                          En attente
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-slate-500 truncate">{m.email}</div>
                    {(m.copropriete_ids || []).length > 0 && (
                      <div className="flex gap-1 mt-1 flex-wrap">
                        {m.copropriete_ids.map(cid => {
                          const c = copros.find(cp => cp.id === cid);
                          return (
                            <Badge key={cid} variant="secondary" className="text-[9px]">
                              <Building2 className="h-2.5 w-2.5 mr-0.5" />
                              {c?.name || c?.reference || cid.slice(0, 8)}
                            </Badge>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1 shrink-0 ml-2">
                    {m.must_change_password && (
                      <Button
                        size="icon" variant="ghost"
                        className="h-7 w-7 text-blue-600"
                        onClick={() => handleResend(m)}
                        title="Renvoyer l'invitation"
                        data-testid={`team-resend-${m.id}`}
                      >
                        <Send className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    <Button
                      size="icon" variant="ghost"
                      className="h-7 w-7"
                      onClick={() => openEdit(m)}
                      title="Modifier"
                      data-testid={`team-edit-${m.id}`}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="icon" variant="ghost"
                      className="h-7 w-7 text-red-600 hover:text-red-700"
                      onClick={() => handleDelete(m)}
                      title="Supprimer"
                      data-testid={`team-delete-${m.id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dialog Ajouter / Modifier */}
      <Dialog open={!!dialog} onOpenChange={(open) => !open && !saving && setDialog(null)}>
        <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" data-testid="team-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {dialog?.mode === 'add' ? (
                <><UserPlus className="h-5 w-5 text-[#022D52]" /> Ajouter un collaborateur</>
              ) : (
                <><Pencil className="h-5 w-5 text-[#022D52]" /> Modifier {dialog?.data?.name}</>
              )}
            </DialogTitle>
          </DialogHeader>
          {dialog && (
            <div className="space-y-3 mt-2">
              {/* Mode de creation (ajout uniquement) */}
              {dialog.mode === 'add' && (
                <div>
                  <Label className="text-xs">Mode de creation</Label>
                  <Select
                    value={dialog.data.creation_mode}
                    onValueChange={(v) => setDialog({
                      ...dialog, data: { ...dialog.data, creation_mode: v }
                    })}
                  >
                    <SelectTrigger data-testid="team-select-mode"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="direct">Creation directe (nom + email + mot de passe)</SelectItem>
                      <SelectItem value="invitation">Invitation par email</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}

              <div>
                <Label className="text-xs">Nom complet *</Label>
                <Input
                  value={dialog.data.name}
                  onChange={(e) => setDialog({ ...dialog, data: { ...dialog.data, name: e.target.value } })}
                  placeholder="Jean Dupont"
                  data-testid="team-input-name"
                />
              </div>
              <div>
                <Label className="text-xs">Email *</Label>
                <Input
                  type="email"
                  value={dialog.data.email}
                  onChange={(e) => setDialog({ ...dialog, data: { ...dialog.data, email: e.target.value } })}
                  placeholder="jean@bureau.be"
                  disabled={dialog.mode === 'edit'}
                  data-testid="team-input-email"
                />
              </div>

              {/* Mot de passe (creation directe ou reset) */}
              {(dialog.mode === 'add' && dialog.data.creation_mode === 'direct') || dialog.mode === 'edit' ? (
                <div>
                  <Label className="text-xs">
                    {dialog.mode === 'add' ? 'Mot de passe temporaire *' : 'Nouveau mot de passe (optionnel)'}
                  </Label>
                  <Input
                    type="password"
                    value={dialog.data.password}
                    onChange={(e) => setDialog({ ...dialog, data: { ...dialog.data, password: e.target.value } })}
                    placeholder={dialog.mode === 'edit' ? 'Laisser vide pour ne pas changer' : 'Mot de passe temporaire'}
                    data-testid="team-input-password"
                  />
                </div>
              ) : null}

              {/* Profil (role_template) */}
              <div>
                <Label className="text-xs">Profil / Role</Label>
                <Select
                  value={dialog.data.role_template_id || '_none'}
                  onValueChange={(v) => setDialog({
                    ...dialog, data: { ...dialog.data, role_template_id: v === '_none' ? '' : v }
                  })}
                >
                  <SelectTrigger data-testid="team-select-profile"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_none">Aucun profil (acces complet)</SelectItem>
                    {templates.map(t => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.name}
                        {t.description ? ` - ${t.description}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* ACPs */}
              <div>
                <Label className="text-xs">
                  Coproprietes accessibles
                  <span className="text-slate-400 ml-1">
                    ({(dialog.data.copropriete_ids || []).length}/{copros.length} selectionnees)
                  </span>
                </Label>
                {copros.length === 0 ? (
                  <p className="text-xs text-slate-400 mt-1">Aucune copropriete disponible.</p>
                ) : (
                  <div className="mt-1 max-h-40 overflow-y-auto border rounded-md p-2 space-y-1">
                    {/* Tout selectionner / deselectionner */}
                    <label className="flex items-center gap-2 text-xs font-medium text-slate-700 border-b pb-1 mb-1 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={(dialog.data.copropriete_ids || []).length === copros.length && copros.length > 0}
                        onChange={() => {
                          const allIds = copros.map(c => c.id);
                          const next = (dialog.data.copropriete_ids || []).length === copros.length ? [] : allIds;
                          setDialog({ ...dialog, data: { ...dialog.data, copropriete_ids: next } });
                        }}
                        data-testid="team-toggle-all-acps"
                      />
                      Toutes les coproprietes
                    </label>
                    {copros.map(c => (
                      <label key={c.id} className="flex items-center gap-2 text-xs cursor-pointer hover:bg-slate-50 rounded p-0.5">
                        <input
                          type="checkbox"
                          checked={(dialog.data.copropriete_ids || []).includes(c.id)}
                          onChange={() => toggleCopro(c.id)}
                          data-testid={`team-acp-${c.id}`}
                        />
                        <span className="truncate">{c.name || c.reference}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
          <DialogFooter className="mt-2">
            <Button variant="ghost" onClick={() => setDialog(null)} disabled={saving}>Annuler</Button>
            <Button
              onClick={handleSave}
              disabled={saving}
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
              data-testid="team-dialog-save"
            >
              {saving ? (
                <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Enregistrement...</>
              ) : dialog?.mode === 'add' ? (
                <><UserPlus className="h-4 w-4 mr-1" /> Ajouter</>
              ) : (
                'Enregistrer'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
