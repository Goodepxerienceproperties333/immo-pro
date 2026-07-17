import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Shield, Search, Building, Info, Mail, AlertTriangle } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

// iter90h0 : le superadmin peut creer des comptes 'syndic' OU 'superadmin'.
// Les gestionnaires (utilisateurs sous un syndic) sont crees par le syndic dans /team.
const CREATABLE_ROLES = [
  { value: 'syndic', label: 'Syndic (responsable d\'agence)' },
  { value: 'superadmin', label: 'Super Administrateur (co-gestionnaire plateforme)' },
];

export default function AdminUsersPage() {
  const { isSuperadmin, user } = useAuth();
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ email: '', password: '', name: '', role: 'syndic', must_change_password: true });

  const load = useCallback(async () => {
    if (!isSuperadmin) return;
    const u = await api.get('/admin/users');
    setUsers(u.data);
  }, [isSuperadmin]);

  useEffect(() => { load(); }, [load]);

  // Acces refuse pour les non-superadmin (syndic, owner, etc.)
  if (!isSuperadmin) {
    return (
      <div data-testid="admin-users-page" className="max-w-2xl mx-auto mt-8">
        <div className="bg-amber-50 border border-amber-200 rounded-md p-6 text-center">
          <Shield size={40} className="mx-auto text-amber-600 mb-3" />
          <h2 className="text-lg font-semibold text-slate-900 mb-2" style={{fontFamily:'Chivo,sans-serif'}}>
            Acces reserve au super administrateur
          </h2>
          <p className="text-sm text-slate-600 mb-3">
            Seul le super administrateur de la plateforme peut creer les comptes syndic principaux.
          </p>
          <p className="text-sm text-slate-600">
            Pour modifier vos propres informations (nom, mot de passe), rendez-vous sur la page <a href="/profile" className="text-[#022D52] hover:underline font-medium">Mon profil</a>.
          </p>
        </div>
      </div>
    );
  }

  // On filtre la liste pour n'afficher que les comptes syndic (et superadmin pour info).
  // Les gestionnaires sont geres par leur syndic dans /team.
  const visible = users.filter(u => u.role === 'syndic' || u.role === 'superadmin');
  const filtered = visible.filter(u =>
    u.name.toLowerCase().includes(search.toLowerCase()) || u.email.toLowerCase().includes(search.toLowerCase())
  );

  const openCreate = () => {
    setEditing(null);
    setForm({ email: '', password: '', name: '', role: 'syndic', must_change_password: true });
    setDialogOpen(true);
  };
  const openEdit = (u) => {
    setEditing(u);
    setForm({ email: u.email, password: '', name: u.name, role: u.role, must_change_password: false });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name || !form.email) {
      toast.error('Nom et email requis');
      return;
    }
    try {
      if (editing) {
        const payload = { name: form.name };
        if (form.password) payload.password = form.password;
        if (form.must_change_password) payload.must_change_password = true;
        // iter90h0 : permet la bascule syndic <-> superadmin
        if (form.role && form.role !== editing.role) payload.role = form.role;
        await api.put(`/admin/users/${editing.id}`, payload);
        toast.success('Compte modifie');
      } else {
        const payload = {
          email: form.email,
          name: form.name,
          role: form.role,  // iter90h0 : 'syndic' ou 'superadmin'
          copropriete_ids: [],  // PAS d'affectation ACP - delegue au syndic
          must_change_password: form.must_change_password,
        };
        if (!form.must_change_password) {
          if (!form.password) {
            toast.error('Saisissez un mot de passe ou cochez "definir lors de la 1ere connexion".');
            return;
          }
          payload.password = form.password;
        }
        await api.post('/admin/users', payload);
        const roleLabel = form.role === 'superadmin' ? 'Super Administrateur' : 'syndic';
        toast.success(form.must_change_password
          ? `Compte ${roleLabel} cree. L'utilisateur devra definir son mot de passe a la 1ere connexion.`
          : `Compte ${roleLabel} cree.`);
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDelete = async (id) => {
    const target = users.find(x => x.id === id);
    const isSuper = target && target.role === 'superadmin';
    const msg = isSuper
      ? 'Supprimer ce Super Administrateur ?\n\nATTENTION : action DEFINITIVE. Ce compte perdra tout acces a la plateforme.'
      : 'Supprimer ce compte syndic ?\n\nATTENTION : ses ACPs et son equipe restent en base mais deviendront orphelines. Verifiez d\'avoir rattache au prealable.';
    if (!window.confirm(msg)) return;
    try {
      await api.delete(`/admin/users/${id}`);
      toast.success('Compte supprime');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleResendInvitation = async (u) => {
    if (!window.confirm(`Renvoyer un email d'invitation a ${u.email} ?`)) return;
    try {
      await api.post(`/admin/users/${u.id}/resend-invitation`);
      toast.success(`Invitation renvoyee a ${u.email}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de l\'envoi');
    }
  };

  const roleBadge = (role) => {
    if (role === 'superadmin') {
      return <Badge variant="outline" className="bg-purple-100 text-purple-800 border-purple-300">Super Administrateur</Badge>;
    }
    return <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200">Syndic</Badge>;
  };

  return (
    <div data-testid="admin-users-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title"><Shield size={24} className="inline mr-2" />Comptes plateforme</h1>
          <p className="page-subtitle">Creez et gerez les comptes syndic et super administrateur de la plateforme.</p>
        </div>
        <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-user-btn">
          <Plus size={16} className="mr-2" /> Nouveau compte
        </Button>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded-md p-3 mb-4 flex items-start gap-2">
        <Info size={16} className="text-[#022D52] mt-0.5 flex-shrink-0" />
        <div className="text-xs text-blue-900">
          <strong>Perimetre de cette page :</strong> creation des comptes <em>Syndic</em>
          (responsable d&apos;agence) et <em>Super Administrateur</em> (co-gestionnaire
          plateforme). Les gestionnaires d&apos;une agence sont crees par leur syndic
          dans <a href="/team" className="underline">/team</a>. Les proprietaires
          sont crees via la gestion des proprietaires de chaque ACP.
        </div>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher un compte..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="users-search" />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead>
            <TableHead>Email</TableHead>
            <TableHead>Role</TableHead>
            <TableHead className="text-center"><Building size={12} className="inline" /> ACPs</TableHead>
            <TableHead>Cree le</TableHead>
            <TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucun compte</TableCell></TableRow>
            ) : filtered.map(u => (
              <TableRow key={u.id} className="hover:bg-slate-50/50">
                <TableCell className="font-medium text-slate-900">{u.name}</TableCell>
                <TableCell className="text-slate-600 font-mono text-xs">{u.email}</TableCell>
                <TableCell>{roleBadge(u.role)}</TableCell>
                <TableCell className="text-center text-xs text-slate-500">{(u.copropriete_ids || []).length}</TableCell>
                <TableCell className="text-xs text-slate-500">{fmtDate(u.created_at)}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {u.must_change_password && (
                      <Button variant="ghost" size="sm" onClick={() => handleResendInvitation(u)} className="text-[#022D52] hover:text-[#01213e]" title="Renvoyer l'email d'invitation" data-testid={`resend-invite-${u.id}`}><Mail size={14} /></Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => openEdit(u)} data-testid={`edit-user-${u.id}`}><Pencil size={14} /></Button>
                    {isSuperadmin && u.id !== user?.id && (
                      <Button variant="ghost" size="sm" onClick={() => handleDelete(u.id)} className="text-red-500" data-testid={`delete-user-${u.id}`}><Trash2 size={14} /></Button>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg" data-testid="user-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              {editing ? `Modifier ${editing.name}` : 'Nouveau compte'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <label className="form-label">Type de compte *</label>
              <Select
                value={form.role}
                onValueChange={(v) => setForm({...form, role: v})}
              >
                <SelectTrigger data-testid="user-role-select">
                  <SelectValue placeholder="Choisir le role" />
                </SelectTrigger>
                <SelectContent>
                  {CREATABLE_ROLES.map(r => (
                    <SelectItem key={r.value} value={r.value} data-testid={`user-role-option-${r.value}`}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {form.role === 'superadmin' ? (
                <div className="mt-2 bg-purple-50 border border-purple-200 rounded p-2 text-xs text-purple-800 flex items-start gap-2">
                  <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
                  <div>
                    <strong>Attention :</strong> un Super Administrateur a acces
                    TOTAL a la plateforme : toutes les ACPs, tous les utilisateurs,
                    toutes les donnees comptables. A creer uniquement pour un
                    co-gestionnaire de confiance de la plateforme.
                  </div>
                </div>
              ) : (
                <div className="mt-2 bg-slate-50 border border-slate-200 rounded p-2 text-xs text-slate-600">
                  <strong>Syndic :</strong> responsable d&apos;agence. Il pourra creer
                  ses propres ACPs, son equipe de gestionnaires et voir uniquement
                  ses propres coproprietes.
                </div>
              )}
            </div>
            <div>
              <label className="form-label">Nom complet *</label>
              <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="user-name-input" placeholder={form.role === 'superadmin' ? 'Nom du co-gestionnaire' : 'Marie Dupont'} />
            </div>
            <div>
              <label className="form-label">Email *</label>
              <Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} disabled={!!editing} data-testid="user-email-input" placeholder={form.role === 'superadmin' ? 'admin@plateforme.be' : 'syndic@agence.be'} />
            </div>
            <div>
              <label className="form-label">
                {editing ? 'Nouveau mot de passe (laisser vide pour garder)' : (form.must_change_password ? 'Mot de passe (sera defini par l\'utilisateur)' : 'Mot de passe initial *')}
              </label>
              <Input
                type="password"
                value={form.password}
                onChange={e => setForm({...form, password: e.target.value})}
                disabled={!editing && form.must_change_password}
                placeholder={!editing && form.must_change_password ? 'Il/elle le definira a sa 1ere connexion' : 'Min 6 caracteres'}
                data-testid="user-password-input"
              />
            </div>
            <label className="flex items-center gap-2 cursor-pointer text-sm text-slate-700">
              <Checkbox
                checked={form.must_change_password}
                onCheckedChange={(v) => setForm({...form, must_change_password: !!v, password: v ? '' : form.password})}
                data-testid="user-must-change-password"
              />
              <span>{editing ? 'Forcer la redefinition du mot de passe a la prochaine connexion' : 'L\'utilisateur definira son mot de passe a la 1ere connexion'}</span>
            </label>
            <div className="flex gap-3 justify-end pt-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="user-cancel">Annuler</Button>
              <Button onClick={handleSave} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="user-save-btn">
                {editing ? 'Mettre a jour' : (form.role === 'superadmin' ? 'Creer le Super Admin' : 'Creer le compte syndic')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
