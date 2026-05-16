import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search } from 'lucide-react';

const LOT_TYPES = [
  { value: 'apartment', label: 'Appartement' },
  { value: 'parking', label: 'Parking' },
  { value: 'cave', label: 'Cave' },
  { value: 'commercial', label: 'Commercial' },
  { value: 'other', label: 'Autre' },
];

export default function LotsPage() {
  const [lots, setLots] = useState([]);
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, owner_id: '' });

  const load = useCallback(async () => {
    const [lotsRes, ownersRes] = await Promise.all([api.get('/lots'), api.get('/owners')]);
    setLots(lotsRes.data);
    setOwners(ownersRes.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = lots.filter(l => l.number.toLowerCase().includes(search.toLowerCase()) || l.description?.toLowerCase().includes(search.toLowerCase()));

  const getOwnerName = (ownerId) => owners.find(o => o.id === ownerId)?.name || '-';

  const openCreate = () => { setEditing(null); setForm({ number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, owner_id: '' }); setDialogOpen(true); };
  const openEdit = (lot) => { setEditing(lot); setForm({ number: lot.number, description: lot.description || '', lot_type: lot.lot_type || 'apartment', floor: lot.floor || 0, area: lot.area || 0, quotity: lot.quotity || 0, owner_id: lot.owner_id || '' }); setDialogOpen(true); };

  const handleSave = async () => {
    try {
      const payload = { ...form, floor: Number(form.floor), area: Number(form.area), quotity: Number(form.quotity) };
      if (editing) {
        await api.put(`/lots/${editing.id}`, payload);
        toast.success('Lot modifie');
      } else {
        await api.post('/lots', payload);
        toast.success('Lot cree');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Supprimer ce lot ?')) return;
    await api.delete(`/lots/${id}`);
    toast.success('Lot supprime');
    load();
  };

  return (
    <div data-testid="lots-page">
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">Lots</h1>
          <p className="page-subtitle">Gestion des lots de la copropriete</p>
        </div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-lot-btn">
          <Plus size={16} className="mr-2" /> Nouveau lot
        </Button>
      </div>

      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="lots-search" />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>N</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Etage</TableHead>
              <TableHead>Surface (m2)</TableHead>
              <TableHead>Tantiemes</TableHead>
              <TableHead>Proprietaire</TableHead>
              <TableHead className="w-24">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Aucun lot</TableCell></TableRow>
            ) : filtered.map(lot => (
              <TableRow key={lot.id} className="hover:bg-slate-50/50" data-testid={`lot-row-${lot.id}`}>
                <TableCell className="font-medium text-slate-900">{lot.number}</TableCell>
                <TableCell className="text-slate-600">{lot.description}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-xs">{LOT_TYPES.find(t => t.value === lot.lot_type)?.label || lot.lot_type}</Badge>
                </TableCell>
                <TableCell>{lot.floor}</TableCell>
                <TableCell>{lot.area}</TableCell>
                <TableCell className="font-mono text-sm">{lot.quotity}</TableCell>
                <TableCell className="text-slate-600">{getOwnerName(lot.owner_id)}</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(lot)}><Pencil size={14} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(lot.id)} className="text-red-500 hover:text-red-700"><Trash2 size={14} /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="lot-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier lot' : 'Nouveau lot'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="form-label">Numero *</label>
                <Input value={form.number} onChange={e => setForm({...form, number: e.target.value})} data-testid="lot-number-input" />
              </div>
              <div>
                <label className="form-label">Type</label>
                <Select value={form.lot_type} onValueChange={v => setForm({...form, lot_type: v})}>
                  <SelectTrigger data-testid="lot-type-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {LOT_TYPES.map(t => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <label className="form-label">Description</label>
              <Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} data-testid="lot-desc-input" />
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="form-label">Etage</label>
                <Input type="number" value={form.floor} onChange={e => setForm({...form, floor: e.target.value})} data-testid="lot-floor-input" />
              </div>
              <div>
                <label className="form-label">Surface (m2)</label>
                <Input type="number" step="0.01" value={form.area} onChange={e => setForm({...form, area: e.target.value})} data-testid="lot-area-input" />
              </div>
              <div>
                <label className="form-label">Tantiemes</label>
                <Input type="number" step="0.01" value={form.quotity} onChange={e => setForm({...form, quotity: e.target.value})} data-testid="lot-quotity-input" />
              </div>
            </div>
            <div>
              <label className="form-label">Proprietaire</label>
              <Select value={form.owner_id} onValueChange={v => setForm({...form, owner_id: v})}>
                <SelectTrigger data-testid="lot-owner-select"><SelectValue placeholder="Selectionner un proprietaire" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="">Aucun</SelectItem>
                  {owners.map(o => <SelectItem key={o.id} value={o.id}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="lot-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
