import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Home, Search } from 'lucide-react';

export default function CoproprietesPage() {
  const { isAdmin, isManager } = useAuth();
  const [coproprietes, setCoproprietes] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', address: '', description: '', bank_account: '' });

  const load = useCallback(async () => {
    const { data } = await api.get('/coproprietes');
    setCoproprietes(data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = coproprietes.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase()) || c.address?.toLowerCase().includes(search.toLowerCase())
  );

  const openCreate = () => { setEditing(null); setForm({ name: '', address: '', description: '', bank_account: '' }); setDialogOpen(true); };
  const openEdit = (c) => { setEditing(c); setForm({ name: c.name, address: c.address || '', description: c.description || '', bank_account: c.bank_account || '' }); setDialogOpen(true); };

  const handleSave = async () => {
    try {
      if (editing) {
        await api.put(`/coproprietes/${editing.id}`, form);
        toast.success('Copropriete modifiee');
      } else {
        await api.post('/coproprietes', form);
        toast.success('Copropriete creee');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Supprimer cette copropriete ?')) return;
    try {
      await api.delete(`/coproprietes/${id}`);
      toast.success('Copropriete supprimee');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  return (
    <div data-testid="coproprietes-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title"><Home size={24} className="inline mr-2" />Coproprietes</h1>
          <p className="page-subtitle">Gestion des immeubles en copropriete</p>
        </div>
        {isManager && (
          <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-copro-btn">
            <Plus size={16} className="mr-2" /> Nouvelle copropriete
          </Button>
        )}
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="copro-search" />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>Adresse</TableHead>
            <TableHead>Compte bancaire</TableHead><TableHead>Description</TableHead>
            <TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucune copropriete</TableCell></TableRow>
            ) : filtered.map(c => (
              <TableRow key={c.id} className="hover:bg-slate-50/50" data-testid={`copro-row-${c.id}`}>
                <TableCell className="font-medium text-slate-900">{c.name}</TableCell>
                <TableCell className="text-slate-600">{c.address}</TableCell>
                <TableCell className="font-mono text-sm">{c.bank_account || '-'}</TableCell>
                <TableCell className="text-slate-600 max-w-[200px] truncate">{c.description}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    {isManager && <Button variant="ghost" size="sm" onClick={() => openEdit(c)} data-testid={`edit-copro-${c.id}`}><Pencil size={14} /></Button>}
                    {isAdmin && <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-500" data-testid={`delete-copro-${c.id}`}><Trash2 size={14} /></Button>}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="copro-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier copropriete' : 'Nouvelle copropriete'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="copro-name-input" /></div>
            <div><label className="form-label">Adresse</label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} data-testid="copro-address-input" /></div>
            <div><label className="form-label">Compte bancaire</label><Input value={form.bank_account} onChange={e => setForm({...form, bank_account: e.target.value})} placeholder="BE00 0000 0000 0000" /></div>
            <div><label className="form-label">Description</label><Textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})} rows={3} /></div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="copro-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
