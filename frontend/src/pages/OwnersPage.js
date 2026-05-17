import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, AlertTriangle } from 'lucide-react';

const emptyForm = { first_name: '', last_name: '', address: '', postal_code: '', city: '', country: 'Belgique', email: '', email2: '', phone: '', phone2: '' };

export default function OwnersPage() {
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [duplicates, setDuplicates] = useState([]);

  const load = useCallback(async () => { const { data } = await api.get('/owners'); setOwners(data); }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = owners.filter(o => {
    const s = search.toLowerCase();
    return (o.name || '').toLowerCase().includes(s) || (o.last_name || '').toLowerCase().includes(s) || (o.first_name || '').toLowerCase().includes(s) || (o.email || '').toLowerCase().includes(s) || (o.vcs_code || '').includes(s);
  });

  const openCreate = () => { setEditing(null); setForm(emptyForm); setDuplicates([]); setDialogOpen(true); };
  const openEdit = (o) => { setEditing(o); setForm({ first_name: o.first_name || '', last_name: o.last_name || o.name || '', address: o.address || '', postal_code: o.postal_code || '', city: o.city || '', country: o.country || 'Belgique', email: o.email || '', email2: o.email2 || '', phone: o.phone || '', phone2: o.phone2 || '' }); setDuplicates([]); setDialogOpen(true); };

  // Duplicate detection
  const checkDuplicate = async (field, value) => {
    if (!value || value.length < 3 || editing) return;
    try {
      const params = {};
      params[field] = value;
      const { data } = await api.get('/owners/check-duplicate', { params });
      if (data.has_duplicates) setDuplicates(data.duplicates);
      else setDuplicates(prev => prev.filter(d => d.field !== field));
    } catch {}
  };

  const handleSave = async () => {
    try {
      const payload = { ...form, name: `${form.last_name} ${form.first_name}`.trim() };
      if (editing) { await api.put(`/owners/${editing.id}`, payload); toast.success('Proprietaire modifie'); }
      else { await api.post('/owners', payload); toast.success('Proprietaire cree'); }
      setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const handleDelete = async (id) => { if (!window.confirm('Supprimer ce proprietaire ?')) return; await api.delete(`/owners/${id}`); toast.success('Supprime'); load(); };
  const F = (field, label, props = {}) => (<div {...(props.className ? {className: props.className} : {})}><label className="form-label">{label}</label><Input value={form[field]} onChange={e => setForm({...form, [field]: e.target.value})} data-testid={`owner-${field}-input`} {...props} /></div>);

  return (
    <div data-testid="owners-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Proprietaires</h1><p className="page-subtitle">Gestion des coproprietaires</p></div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-owner-btn"><Plus size={16} className="mr-2" /> Nouveau</Button>
      </div>
      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher nom, email, VCS..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="owners-search" />
      </div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>Prenom</TableHead><TableHead>VCS</TableHead>
            <TableHead>Ville</TableHead><TableHead>Email</TableHead><TableHead>GSM</TableHead><TableHead className="w-20">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucun proprietaire</TableCell></TableRow> : filtered.map(o => (
              <TableRow key={o.id} className="hover:bg-slate-50/50" data-testid={`owner-row-${o.id}`}>
                <TableCell className="font-medium text-slate-900">{o.last_name || o.name}</TableCell>
                <TableCell className="text-slate-600">{o.first_name || ''}</TableCell>
                <TableCell className="font-mono text-xs text-[#0055FF]">{o.vcs_code || '-'}</TableCell>
                <TableCell className="text-slate-600 text-sm">{o.city || ''}{o.postal_code ? ` (${o.postal_code})` : ''}</TableCell>
                <TableCell className="text-slate-600 text-sm">{o.email}</TableCell>
                <TableCell className="text-slate-600 text-sm">{o.phone}</TableCell>
                <TableCell><div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(o)} data-testid={`edit-owner-${o.id}`}><Pencil size={14} /></Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(o.id)} className="text-red-500" data-testid={`delete-owner-${o.id}`}><Trash2 size={14} /></Button>
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl" data-testid="owner-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier proprietaire' : 'Nouveau proprietaire'}</DialogTitle>
            {editing?.vcs_code && <p className="font-mono text-sm text-[#0055FF] mt-1">VCS: {editing.vcs_code}</p>}
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              {F('last_name', 'Nom *')}
              {F('first_name', 'Prenom')}
            </div>
            {F('address', 'Adresse')}
            <div className="grid grid-cols-3 gap-4">
              {F('postal_code', 'Code postal')}
              {F('city', 'Ville')}
              {F('country', 'Pays')}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Email</label><Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} onBlur={e => checkDuplicate('email', e.target.value)} data-testid="owner-email-input" /></div>
              <div><label className="form-label">Email 2</label><Input value={form.email2} onChange={e => setForm({...form, email2: e.target.value})} data-testid="owner-email2-input" /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">GSM</label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} onBlur={e => checkDuplicate('phone', e.target.value)} data-testid="owner-phone-input" /></div>
              <div><label className="form-label">GSM 2</label><Input value={form.phone2} onChange={e => setForm({...form, phone2: e.target.value})} data-testid="owner-phone2-input" /></div>
            </div>
            {duplicates.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-md p-3 flex items-start gap-2">
                <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
                <div className="text-sm">
                  <div className="font-semibold text-yellow-800">Doublon detecte !</div>
                  {duplicates.map((d, i) => (
                    <div key={i} className="text-yellow-700">{d.field === 'email' ? 'Email' : 'GSM'} "{d.value}" existe deja pour <strong>{d.owner_name}</strong></div>
                  ))}
                </div>
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="owner-cancel-btn">Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="owner-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
