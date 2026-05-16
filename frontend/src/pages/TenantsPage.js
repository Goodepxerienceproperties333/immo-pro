import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search } from 'lucide-react';

export default function TenantsPage() {
  const [tenants, setTenants] = useState([]);
  const [lots, setLots] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', email: '', phone: '', lot_id: '', lease_start: '', lease_end: '', rent_amount: 0 });

  const load = useCallback(async () => {
    const [t, l] = await Promise.all([api.get('/tenants'), api.get('/lots')]);
    setTenants(t.data);
    setLots(l.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = tenants.filter(t => t.name.toLowerCase().includes(search.toLowerCase()));
  const getLotNumber = (lotId) => lots.find(l => l.id === lotId)?.number || '-';

  const openCreate = () => { setEditing(null); setForm({ name: '', email: '', phone: '', lot_id: '', lease_start: '', lease_end: '', rent_amount: 0 }); setDialogOpen(true); };
  const openEdit = (t) => { setEditing(t); setForm({ name: t.name, email: t.email || '', phone: t.phone || '', lot_id: t.lot_id || '', lease_start: t.lease_start || '', lease_end: t.lease_end || '', rent_amount: t.rent_amount || 0 }); setDialogOpen(true); };

  const handleSave = async () => {
    try {
      const payload = { ...form, rent_amount: Number(form.rent_amount) };
      if (editing) { await api.put(`/tenants/${editing.id}`, payload); toast.success('Locataire modifie'); }
      else { await api.post('/tenants', payload); toast.success('Locataire cree'); }
      setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Supprimer ce locataire ?')) return;
    await api.delete(`/tenants/${id}`); toast.success('Locataire supprime'); load();
  };

  return (
    <div data-testid="tenants-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Locataires</h1><p className="page-subtitle">Gestion des locataires</p></div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-tenant-btn"><Plus size={16} className="mr-2" /> Nouveau</Button>
      </div>
      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="tenants-search" />
      </div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>Email</TableHead><TableHead>Telephone</TableHead>
            <TableHead>Lot</TableHead><TableHead>Debut bail</TableHead><TableHead>Fin bail</TableHead>
            <TableHead>Loyer</TableHead><TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Aucun locataire</TableCell></TableRow>
            ) : filtered.map(t => (
              <TableRow key={t.id} className="hover:bg-slate-50/50">
                <TableCell className="font-medium">{t.name}</TableCell>
                <TableCell>{t.email}</TableCell><TableCell>{t.phone}</TableCell>
                <TableCell>{getLotNumber(t.lot_id)}</TableCell>
                <TableCell>{t.lease_start}</TableCell><TableCell>{t.lease_end}</TableCell>
                <TableCell className="font-mono">{t.rent_amount?.toFixed(2)} EUR</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(t)}><Pencil size={14} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(t.id)} className="text-red-500"><Trash2 size={14} /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="tenant-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier locataire' : 'Nouveau locataire'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="tenant-name-input" /></div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Email</label><Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
              <div><label className="form-label">Telephone</label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} /></div>
            </div>
            <div>
              <label className="form-label">Lot</label>
              <Select value={form.lot_id} onValueChange={v => setForm({...form, lot_id: v})}>
                <SelectTrigger><SelectValue placeholder="Selectionner un lot" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">Aucun</SelectItem>
                  {lots.map(l => <SelectItem key={l.id} value={l.id}>Lot {l.number}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Debut bail</label><Input type="date" value={form.lease_start} onChange={e => setForm({...form, lease_start: e.target.value})} /></div>
              <div><label className="form-label">Fin bail</label><Input type="date" value={form.lease_end} onChange={e => setForm({...form, lease_end: e.target.value})} /></div>
              <div><label className="form-label">Loyer (EUR)</label><Input type="number" step="0.01" value={form.rent_amount} onChange={e => setForm({...form, rent_amount: e.target.value})} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="tenant-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
