import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, Truck, AlertTriangle } from 'lucide-react';

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name:'', vat_number:'', address:'', postal_code:'', city:'', country:'Belgique', phone:'', email:'', iban:'', bic:'', default_account:'', notes:'' });
  // iter85g : dialog de confirmation homonymes
  const [similarDialog, setSimilarDialog] = useState(null); // {similar: [...], pendingForm}

  const load = useCallback(async () => {
    const { data } = await api.get('/suppliers', { params: search ? { search } : {} });
    setSuppliers(data);
  }, [search]);
  useEffect(() => { load(); }, [load]);

  const filtered = suppliers;
  const openCreate = () => { setEditing(null); setForm({ name:'', vat_number:'', address:'', postal_code:'', city:'', country:'Belgique', phone:'', email:'', iban:'', bic:'', default_account:'', notes:'' }); setDialogOpen(true); };
  const openEdit = (s) => { setEditing(s); setForm({ name:s.name, vat_number:s.vat_number||'', address:s.address||'', postal_code:s.postal_code||'', city:s.city||'', country:s.country||'Belgique', phone:s.phone||'', email:s.email||'', iban:s.iban||'', bic:s.bic||'', default_account:s.default_account||'', notes:s.notes||'' }); setDialogOpen(true); };

  const performCreate = async (formToUse, forceDespiteSimilar = false) => {
    try {
      await api.post('/suppliers', { ...formToUse, force_create_despite_similar: forceDespiteSimilar });
      toast.success('Fournisseur cree');
      setDialogOpen(false);
      setSimilarDialog(null);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleSave = async () => {
    try {
      if (editing) {
        await api.put(`/suppliers/${editing.id}`, form);
        toast.success('Fournisseur modifie');
        setDialogOpen(false);
        load();
        return;
      }
      // Creation : pre-verifie les homonymes pour proposer un dialog explicite
      const copro = localStorage.getItem('copropriete_id') || '';
      const { data } = await api.post('/suppliers/check-duplicate', {
        name: form.name,
        vat_number: form.vat_number || '',
        bce_number: form.bce_number || '',
        iban: form.iban || '',
        copropriete_id: copro,
      });
      if (data.exact) {
        // Doublon strict -> on bloque (le POST renverrait 409 de toute facon)
        const e = data.exact;
        const label = ({ bce_number: 'numero BCE', vat_number: 'numero TVA', iban: 'IBAN', name: 'nom' })[e.field] || e.field;
        toast.error(`Doublon strict : un fournisseur avec le meme ${label} existe deja (${e.supplier?.name || ''}).`);
        return;
      }
      if (data.similar && data.similar.length > 0) {
        // Homonymes proches -> dialog de confirmation
        setSimilarDialog({ similar: data.similar, pendingForm: form });
        return;
      }
      // Aucune similitude -> creation directe
      await performCreate(form, false);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const handleDelete = async (id) => { if (!window.confirm('Supprimer ?')) return; await api.delete(`/suppliers/${id}`); toast.success('Supprime'); load(); };

  return (
    <div data-testid="suppliers-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Truck size={24} className="inline mr-2" />Fournisseurs</h1><p className="page-subtitle">Gestion des fournisseurs et prestataires</p></div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-supplier-btn"><Plus size={16} className="mr-2" /> Nouveau</Button>
      </div>
      <div className="mb-4 relative max-w-sm">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input placeholder="Rechercher..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="suppliers-search" />
      </div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Nom</TableHead><TableHead>N TVA</TableHead><TableHead>Ville</TableHead>
            <TableHead>IBAN</TableHead><TableHead>Email</TableHead><TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">Aucun fournisseur</TableCell></TableRow>
            ) : filtered.map(s => (
              <TableRow key={s.id} className="hover:bg-slate-50/50">
                <TableCell className="font-medium">{s.name}</TableCell>
                <TableCell className="font-mono text-sm">{s.vat_number || '-'}</TableCell>
                <TableCell>{s.city}</TableCell>
                <TableCell className="font-mono text-sm">{s.iban || '-'}</TableCell>
                <TableCell>{s.email}</TableCell>
                <TableCell><div className="flex gap-1">
                  <Button variant="ghost" size="sm" onClick={() => openEdit(s)}><Pencil size={14} /></Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(s.id)} className="text-red-500"><Trash2 size={14} /></Button>
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl" data-testid="supplier-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier fournisseur' : 'Nouveau fournisseur'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Nom *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="supplier-name" /></div>
              <div><label className="form-label">N TVA</label><Input value={form.vat_number} onChange={e => setForm({...form, vat_number: e.target.value})} placeholder="BE0123.456.789" /></div>
            </div>
            <div><label className="form-label">Adresse</label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} /></div>
            <div className="grid grid-cols-3 gap-4">
              <div><label className="form-label">Code postal</label><Input value={form.postal_code} onChange={e => setForm({...form, postal_code: e.target.value})} /></div>
              <div><label className="form-label">Ville</label><Input value={form.city} onChange={e => setForm({...form, city: e.target.value})} /></div>
              <div><label className="form-label">Pays</label><Input value={form.country} onChange={e => setForm({...form, country: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">IBAN</label><Input value={form.iban} onChange={e => setForm({...form, iban: e.target.value})} placeholder="BE00 0000 0000 0000" /></div>
              <div><label className="form-label">BIC</label><Input value={form.bic} onChange={e => setForm({...form, bic: e.target.value})} /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><label className="form-label">Telephone</label><Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} /></div>
              <div><label className="form-label">Email</label><Input value={form.email} onChange={e => setForm({...form, email: e.target.value})} /></div>
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="supplier-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* iter85g : Dialog de confirmation homonymes */}
      <Dialog open={!!similarDialog} onOpenChange={(o) => !o && setSimilarDialog(null)}>
        <DialogContent className="max-w-2xl" data-testid="similar-suppliers-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}} className="flex items-center gap-2 text-amber-700">
              <AlertTriangle size={20} /> Homonymes potentiels detectes
            </DialogTitle>
          </DialogHeader>
          {similarDialog && (
            <div className="space-y-4 mt-2">
              <p className="text-sm text-slate-700">
                Vous etes sur le point de creer le fournisseur <b>&quot;{similarDialog.pendingForm.name}&quot;</b>.
                Les fournisseurs suivants existent deja avec un nom similaire :
              </p>
              <div className="bg-amber-50 border border-amber-200 rounded p-3 space-y-2 max-h-72 overflow-auto">
                {similarDialog.similar.map((m, i) => (
                  <div key={i} className="flex items-center justify-between bg-white border border-amber-100 rounded p-2" data-testid={`similar-supplier-${i}`}>
                    <div className="flex-1">
                      <div className="font-medium text-sm">{m.supplier.name}</div>
                      <div className="text-[11px] text-slate-500 space-x-3">
                        {m.supplier.vat_number && <span>TVA: {m.supplier.vat_number}</span>}
                        {m.supplier.city && <span>{m.supplier.city}</span>}
                        {m.supplier.iban && <span className="font-mono">{m.supplier.iban}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-amber-700">{Math.round(m.score * 100)}% similarite</span>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-blue-700 border-blue-200 h-7 text-[11px]"
                        onClick={() => {
                          // Pre-rempli le formulaire avec le supplier existant (mode edit) puis annule la creation
                          openEdit(m.supplier);
                          setSimilarDialog(null);
                        }}
                        data-testid={`use-existing-supplier-${i}`}
                      >
                        Utiliser celui-ci
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-slate-500 italic">
                Si aucun ne correspond, vous pouvez creer un nouveau fournisseur quand meme.
              </p>
              <div className="flex gap-3 justify-end">
                <Button variant="outline" onClick={() => setSimilarDialog(null)} data-testid="similar-cancel-btn">
                  Annuler
                </Button>
                <Button
                  onClick={() => performCreate(similarDialog.pendingForm, true)}
                  className="bg-amber-600 hover:bg-amber-700 text-white"
                  data-testid="similar-force-create-btn"
                >
                  Creer quand meme
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
