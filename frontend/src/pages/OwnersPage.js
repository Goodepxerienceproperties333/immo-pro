import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search } from 'lucide-react';

export default function OwnersPage() {
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', phone: '', address: '' });

  const load = useCallback(async () => {
    const { data } = await api.get('/owners');
    setOwners(data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = owners.filter(o =>
    o.name.toLowerCase().includes(search.toLowerCase()) ||
    o.email?.toLowerCase().includes(search.toLowerCase())
  );

  const openCreate = () => { setEditing(null); setForm({ name: '', email: '', phone: '', address: '' }); setDialogOpen(true); };
  const openEdit = (owner) => { setEditing(owner); setForm({ name: owner.name, email: owner.email || '', phone: owner.phone || '', address: owner.address || '' }); setDialogOpen(true); };

  const handleSave = async () => {
    try {
      if (editing) {
        await api.put(`/owners/${editing.id}`, form);
        toast.success('Proprietaire modifie');
      } else {
        await api.post('/owners', form);
        toast.success('Proprietaire cree');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Supprimer ce proprietaire ?')) return;
    await api.delete(`/owners/${id}`);
    toast.success('Proprietaire supprime');
    load();
  };

  return (
    <div data-testid="owners-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">Proprietaires</h1>
          <p className="page-subtitle">Gestion des coproprietaires</p>
        </div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-owner-btn">
          <Plus size={16} className="mr-2" /> Nouveau
        </Button>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input
          placeholder="Rechercher..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="pl-9"
          data-testid="owners-search"
        />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="data-table">Nom</TableHead>
              <TableHead className="data-table">Email</TableHead>
              <TableHead className="data-table">Telephone</TableHead>
              <TableHead className="data-table">Adresse</TableHead>
              <TableHead className="data-table w-24">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucun proprietaire</TableCell></TableRow>
            ) : filtered.map(owner => (
              <TableRow key={owner.id} className="hover:bg-slate-50/50" data-testid={`owner-row-${owner.id}`}>
                <TableCell className="font-medium text-slate-900">{owner.name}</TableCell>
                <TableCell className="text-slate-600">{owner.email}</TableCell>
                <TableCell className="text-slate-600">{owner.phone}</TableCell>
                <TableCell className="text-slate-600 max-w-[200px] truncate">{owner.address}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(owner)} data-testid={`edit-owner-${owner.id}`}>
                      <Pencil size={14} />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(owner.id)} className="text-red-500 hover:text-red-700" data-testid={`delete-owner-${owner.id}`}>
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="owner-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier proprietaire' : 'Nouveau proprietaire'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <label className="form-label">Nom *</label>
              <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="owner-name-input" />
            </div>
            <div>
              <label className="form-label">Email</label>
              <Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} data-testid="owner-email-input" />
            </div>
            <div>
              <label className="form-label">Telephone</label>
              <Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} data-testid="owner-phone-input" />
            </div>
            <div>
              <label className="form-label">Adresse</label>
              <Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} data-testid="owner-address-input" />
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="owner-cancel-btn">Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="owner-save-btn">
                {editing ? 'Modifier' : 'Creer'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
