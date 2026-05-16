import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Shield, Search } from 'lucide-react';

const ROLES = [
  { value: 'superadmin', label: 'Super Admin', color: 'bg-red-50 text-red-700 border-red-200' },
  { value: 'syndic', label: 'Syndic / Gestionnaire', color: 'bg-blue-50 text-blue-700 border-blue-200' },
  { value: 'owner', label: 'Proprietaire', color: 'bg-green-50 text-green-700 border-green-200' },
];

export default function AdminUsersPage() {
  const { isAdmin, user } = useAuth();
  const [users, setUsers] = useState([]);
  const [coproprietes, setCoproprietes] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ email: '', password: '', name: '', role: 'owner', copropriete_ids: [] });

  const load = useCallback(async () => {
    const [u, c] = await Promise.all([api.get('/admin/users'), api.get('/coproprietes')]);
    setUsers(u.data);
    setCoproprietes(c.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = users.filter(u =>
    u.name.toLowerCase().includes(search.toLowerCase()) || u.email.toLowerCase().includes(search.toLowerCase())
  );

  const openCreate = () => { setEditing(null); setForm({ email: '', password: '', name: '', role: 'owner', copropriete_ids: [] }); setDialogOpen(true); };
  const openEdit = (u) => { setEditing(u); setForm({ email: u.email, password: '', name: u.name, role: u.role, copropriete_ids: u.copropriete_ids || [] }); setDialogOpen(true); };

  const toggleCopro = (coproId) => {
    setForm(prev => {
      const ids = prev.copropriete_ids.includes(coproId)
        ? prev.copropriete_ids.filter(id => id !== coproId)
        : [...prev.copropriete_ids, coproId];
      return { ...prev, copropriete_ids: ids };
    });
  };

  const handleSave = async () => {
    try {
      if (editing) {
        const payload = { name: form.name, role: form.role, copropriete_ids: form.copropriete_ids };
        if (form.password) payload.password = form.password;
        await api.put(`/admin/users/${editing.id}`, payload);
        toast.success('Utilisateur modifie');
      } else {
        await api.post('/admin/users', form);
        toast.success('Utilisateur cree');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Supprimer cet utilisateur ?')) return;
    try {
      await api.delete(`/admin/users/${id}`);
      toast.success('Utilisateur supprime');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const getRoleBadge = (role) => {
    const r = ROLES.find(x => x.value === role) || ROLES[2];
    return <Badge variant="outline" className={r.color}>{r.label}</Badge>;
  };

  const getCoproNames = (ids) => {
    if (!ids || ids.length === 0) return '-';
    return ids.map(id => coproprietes.find(c => c.id === id)?.name || id).join(', ');
  };

  return (
    <div data-testid="admin-users-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title"><Shield size={24} className="inline mr-2" />Gestion des utilisateurs</h1>
          <p className="page-subtitle">Administration des comptes et droits d'acces</p>
        </div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-user-btn">
          <Plus size={16} className="mr-2" /> Nouvel utilisateur
        </Button>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="users-search" />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>Email</TableHead><TableHead>Role</TableHead>
            <TableHead>Coproprietes</TableHead><TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucun utilisateur</TableCell></TableRow>
            ) : filtered.map(u => (
              <TableRow key={u.id} className="hover:bg-slate-50/50">
                <TableCell className="font-medium text-slate-900">{u.name}</TableCell>
                <TableCell className="text-slate-600">{u.email}</TableCell>
                <TableCell>{getRoleBadge(u.role)}</TableCell>
                <TableCell className="text-sm text-slate-600 max-w-[200px] truncate">{getCoproNames(u.copropriete_ids)}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(u)} data-testid={`edit-user-${u.id}`}><Pencil size={14} /></Button>
                    {isAdmin && u.id !== user?.id && (
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
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier utilisateur' : 'Nouvel utilisateur'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="user-name-input" /></div>
            <div><label className="form-label">Email *</label><Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} disabled={!!editing} data-testid="user-email-input" /></div>
            <div><label className="form-label">{editing ? 'Nouveau mot de passe (laisser vide pour garder)' : 'Mot de passe *'}</label>
              <Input type="password" value={form.password} onChange={e => setForm({...form, password: e.target.value})} data-testid="user-password-input" />
            </div>
            <div><label className="form-label">Role</label>
              <Select value={form.role} onValueChange={v => setForm({...form, role: v})}>
                <SelectTrigger data-testid="user-role-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ROLES.filter(r => isAdmin || r.value !== 'superadmin').map(r => (
                    <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {coproprietes.length > 0 && (
              <div>
                <label className="form-label mb-2">Coproprietes assignees</label>
                <div className="border rounded-md p-3 space-y-2 max-h-40 overflow-y-auto">
                  {coproprietes.map(c => (
                    <label key={c.id} className="flex items-center gap-2 cursor-pointer text-sm">
                      <Checkbox
                        checked={form.copropriete_ids.includes(c.id)}
                        onCheckedChange={() => toggleCopro(c.id)}
                      />
                      <span>{c.name}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="user-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
