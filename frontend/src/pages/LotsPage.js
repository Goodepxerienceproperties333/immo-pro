import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, X } from 'lucide-react';

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
  const [form, setForm] = useState({ number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, owner_id: '', owner_ids: [] });
  const [ownerSearch, setOwnerSearch] = useState('');

  const ownerById = useMemo(() => Object.fromEntries(owners.map(o => [o.id, o])), [owners]);
  const filteredOwners = useMemo(() => {
    const q = ownerSearch.trim().toLowerCase();
    if (!q) return [];
    return owners.filter(o =>
      !form.owner_ids.includes(o.id) && (
        (o.name || '').toLowerCase().includes(q) ||
        (o.first_name || '').toLowerCase().includes(q) ||
        (o.last_name || '').toLowerCase().includes(q) ||
        (o.email || '').toLowerCase().includes(q) ||
        (o.vcs_code || '').includes(q)
      )
    ).slice(0, 8);
  }, [ownerSearch, owners, form.owner_ids]);

  const addOwner = (o) => {
    setForm(prev => ({
      ...prev,
      owner_ids: [...prev.owner_ids, o.id],
      owner_id: prev.owner_id || o.id,
    }));
    setOwnerSearch('');
  };
  const removeOwner = (id) => {
    setForm(prev => {
      const next = prev.owner_ids.filter(x => x !== id);
      return { ...prev, owner_ids: next, owner_id: next[0] || '' };
    });
  };

  const load = useCallback(async () => {
    const [lotsRes, ownersRes] = await Promise.all([api.get('/lots'), api.get('/owners')]);
    setLots(lotsRes.data);
    setOwners(ownersRes.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = lots.filter(l => l.number.toLowerCase().includes(search.toLowerCase()) || l.description?.toLowerCase().includes(search.toLowerCase()));

  const getOwnerNames = (lot) => {
    const ids = lot.owner_ids?.length ? lot.owner_ids : (lot.owner_id ? [lot.owner_id] : []);
    return ids.map(id => owners.find(o => o.id === id)?.name || '').filter(Boolean).join(', ') || '-';
  };

  const openCreate = () => { setEditing(null); setForm({ number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, owner_id: '', owner_ids: [] }); setDialogOpen(true); };
  const openEdit = (lot) => { setEditing(lot); setForm({ number: lot.number, description: lot.description || '', lot_type: lot.lot_type || 'apartment', floor: lot.floor || 0, area: lot.area || 0, quotity: lot.quotity || 0, owner_id: lot.owner_id || '', owner_ids: lot.owner_ids || (lot.owner_id ? [lot.owner_id] : []) }); setDialogOpen(true); };

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
                <TableCell className="text-slate-600">{getOwnerNames(lot)}</TableCell>
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
              <label className="form-label">Proprietaires <span className="text-slate-400 font-normal">(recherche par nom, email, VCS)</span></label>

              {/* Selected owners as tags */}
              {form.owner_ids.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {form.owner_ids.map(id => {
                    const o = ownerById[id];
                    return (
                      <Badge key={id} variant="outline" className="bg-[#0055FF]/10 border-[#0055FF]/30 text-slate-700 gap-1.5 pl-2 pr-1 py-1" data-testid={`owner-tag-${id}`}>
                        <span>{o?.name || '(inconnu)'}</span>
                        {o?.vcs_code && <span className="font-mono text-[9px] text-[#0055FF]">{o.vcs_code.replace(/\+/g,'').slice(0, 10)}</span>}
                        <button onClick={() => removeOwner(id)} className="text-slate-400 hover:text-red-500"><X size={11} /></button>
                      </Badge>
                    );
                  })}
                </div>
              )}

              {/* Search field with dropdown suggestions */}
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <Input
                  value={ownerSearch}
                  onChange={e => setOwnerSearch(e.target.value)}
                  placeholder="Tapez un nom, email ou code VCS..."
                  className="pl-9"
                  data-testid="owner-search-input"
                />
                {filteredOwners.length > 0 && (
                  <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-56 overflow-y-auto">
                    {filteredOwners.map(o => (
                      <button
                        key={o.id}
                        onClick={() => addOwner(o)}
                        className="w-full text-left px-3 py-2 hover:bg-[#0055FF]/5 border-b last:border-b-0 border-slate-100 flex items-center justify-between text-sm"
                        data-testid={`owner-suggestion-${o.id}`}
                      >
                        <div>
                          <div className="font-medium text-slate-900">{o.name}</div>
                          {o.email && <div className="text-[11px] text-slate-500">{o.email}</div>}
                        </div>
                        {o.vcs_code && <span className="font-mono text-[10px] text-[#0055FF] flex-shrink-0">{o.vcs_code}</span>}
                      </button>
                    ))}
                  </div>
                )}
                {ownerSearch && filteredOwners.length === 0 && (
                  <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg p-3 text-center text-xs text-slate-400">
                    Aucun proprietaire trouve. Allez dans Proprietaires pour en creer un.
                  </div>
                )}
              </div>
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
