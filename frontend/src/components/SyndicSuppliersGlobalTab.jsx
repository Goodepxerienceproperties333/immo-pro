import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { Plus, Pencil, Search, Truck, ArrowUpDown, X } from 'lucide-react';

const emptySupplierForm = {
  name: '', vat_number: '', bce_number: '',
  address: '', postal_code: '', city: '', country: 'Belgique',
  phone: '', email: '', iban: '', bic: '',
  notes: '',
};

export default function SyndicSuppliersGlobalTab({ coproprietes }) {
  const navigate = useNavigate();
  const [suppliers, setSuppliers] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterAcp, setFilterAcp] = useState('_all');
  const [sortMode, setSortMode] = useState('name');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptySupplierForm);
  const [selectedAcpForCreate, setSelectedAcpForCreate] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/suppliers/syndic-global');
      setSuppliers(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement fournisseurs');
      setSuppliers([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const activeAcpsList = useMemo(() => (coproprietes || []).filter(c => c.status !== 'archived'), [coproprietes]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = suppliers.filter(s => {
      if (filterAcp !== '_all' && !(s.acp_ids || []).includes(filterAcp)) return false;
      if (!q) return true;
      return (
        (s.name || '').toLowerCase().includes(q) ||
        (s.email || '').toLowerCase().includes(q) ||
        (s.phone || '').toLowerCase().includes(q) ||
        (s.vat_number || '').toLowerCase().includes(q) ||
        (s.bce_number || '').toLowerCase().includes(q) ||
        (s.iban || '').toLowerCase().includes(q)
      );
    });
    if (sortMode === 'acp_count') {
      list = [...list].sort((a, b) => (b.acp_count || 0) - (a.acp_count || 0) || (a.name || '').localeCompare(b.name || ''));
    } else {
      list = [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    }
    return list;
  }, [suppliers, search, filterAcp, sortMode]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptySupplierForm);
    // Preselectionne la 1ere ACP active pour la creation (obligatoire cote back)
    setSelectedAcpForCreate(activeAcpsList[0]?.id || '');
    setDialogOpen(true);
  };

  const openEdit = (s) => {
    setEditing(s);
    setForm({
      name: s.name || '',
      vat_number: s.vat_number || '',
      bce_number: s.bce_number || '',
      address: s.address || '',
      postal_code: s.postal_code || '',
      city: s.city || '',
      country: s.country || 'Belgique',
      phone: s.phone || '',
      email: s.email || '',
      iban: s.iban || '',
      bic: s.bic || '',
      notes: s.notes || '',
    });
    setSelectedAcpForCreate('');
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name || !form.name.trim()) {
      toast.error('Nom du fournisseur requis');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/suppliers/${editing.id}`, form);
        toast.success('Fournisseur modifie');
      } else {
        if (!selectedAcpForCreate) {
          toast.error('Selectionnez une ACP de rattachement initial');
          setSaving(false);
          return;
        }
        await api.post('/suppliers', {
          ...form,
          copropriete_id: selectedAcpForCreate,
        });
        toast.success('Fournisseur cree');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally {
      setSaving(false);
    }
  };

  const goToAcp = (acpId) => {
    localStorage.setItem('selectedCopro', acpId);
    navigate(`/suppliers?copropriete_id=${acpId}`);
  };

  return (
    <div data-testid="syndic-suppliers-global-tab">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <div className="text-sm text-slate-600 flex items-center gap-2">
          <Truck size={14} className="text-[#022D52]" />
          <strong>{filtered.length}</strong> fournisseur(s)
          {filtered.length !== suppliers.length && (
            <span className="text-slate-400">(filtres actifs, {suppliers.length} au total)</span>
          )}
        </div>
        <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-supplier-global-btn">
          <Plus size={14} className="mr-1" /> Nouveau fournisseur
        </Button>
      </div>

      <div className="flex flex-col md:flex-row gap-2 mb-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Rechercher nom, email, TVA, BCE, IBAN..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="suppliers-global-search"
          />
        </div>
        <div className="min-w-[220px]">
          <Select value={filterAcp} onValueChange={setFilterAcp}>
            <SelectTrigger data-testid="suppliers-global-acp-filter">
              <SelectValue placeholder="Toutes les ACPs" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="_all">Toutes les ACPs</SelectItem>
              {activeAcpsList.map(c => (
                <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          onClick={() => setSortMode(sortMode === 'name' ? 'acp_count' : 'name')}
          data-testid="suppliers-global-sort-btn"
          title={sortMode === 'name' ? 'Trier par nombre d\'ACPs' : 'Trier par nom'}
        >
          <ArrowUpDown size={14} className="mr-1" />
          {sortMode === 'name' ? 'Tri: Nom (A-Z)' : 'Tri: Nb ACPs'}
        </Button>
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[240px]">Nom</TableHead>
              <TableHead>Email / Telephone</TableHead>
              <TableHead>BCE / TVA</TableHead>
              <TableHead>ACPs d&apos;affectation</TableHead>
              <TableHead className="w-24">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Chargement...</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucun fournisseur</TableCell></TableRow>
            ) : filtered.map(s => (
              <TableRow key={s.id} className="hover:bg-slate-50/50" data-testid={`supplier-global-row-${s.id}`}>
                <TableCell className="font-medium">{s.name}</TableCell>
                <TableCell className="text-xs">
                  <div>{s.email || <span className="text-slate-400">-</span>}</div>
                  <div className="text-slate-500">{s.phone || ''}</div>
                </TableCell>
                <TableCell className="text-[11px] font-mono">
                  {s.bce_number && <div className="text-[#022D52]">{s.bce_number}</div>}
                  {s.vat_number && <div className="text-slate-500">{s.vat_number}</div>}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(s.acp_names || []).map(a => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => goToAcp(a.id)}
                        className="inline-flex items-center rounded-full border border-[#022D52]/30 bg-[#022D52]/5 hover:bg-[#022D52]/15 text-[#022D52] px-2 py-0.5 text-[11px] font-medium transition-colors"
                        title={`Ouvrir ${a.name}`}
                        data-testid={`supplier-${s.id}-acp-${a.id}`}
                      >
                        {a.name}
                      </button>
                    ))}
                    <Badge variant="outline" className="text-[10px] text-slate-500 border-slate-200">
                      {s.acp_count || 0} ACP{(s.acp_count || 0) > 1 ? 's' : ''}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openEdit(s)}
                    className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10"
                    data-testid={`edit-supplier-global-${s.id}`}
                    title="Modifier ce fournisseur"
                  >
                    <Pencil size={12} className="mr-1" /> Modifier
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto" data-testid="supplier-global-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              {editing ? 'Modifier fournisseur' : 'Nouveau fournisseur (global)'}
            </DialogTitle>
            {editing && (
              <p className="text-xs text-slate-500">
                Ce fournisseur est lie a {editing.acp_count || 0} ACP(s). Les modifications s&apos;appliquent globalement.
              </p>
            )}
            {!editing && (
              <p className="text-xs text-amber-700">
                Un fournisseur global doit etre rattache initialement a une ACP. Vous pourrez ensuite le reutiliser via ses ecritures dans d&apos;autres ACPs.
              </p>
            )}
          </DialogHeader>
          <div className="space-y-3 mt-2">
            {!editing && (
              <div>
                <label className="form-label">ACP de rattachement initial *</label>
                <Select value={selectedAcpForCreate} onValueChange={setSelectedAcpForCreate}>
                  <SelectTrigger data-testid="supplier-acp-select">
                    <SelectValue placeholder="Choisir une ACP..." />
                  </SelectTrigger>
                  <SelectContent>
                    {activeAcpsList.map(c => (
                      <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            <div>
              <label className="form-label">Nom *</label>
              <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="supplier-name" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">BCE</label>
                <Input value={form.bce_number} onChange={e => setForm({...form, bce_number: e.target.value})} placeholder="0123.456.789" data-testid="supplier-bce" />
              </div>
              <div>
                <label className="form-label">N* TVA</label>
                <Input value={form.vat_number} onChange={e => setForm({...form, vat_number: e.target.value})} placeholder="BE0123456789" data-testid="supplier-vat" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Email</label>
                <Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} data-testid="supplier-email" />
              </div>
              <div>
                <label className="form-label">Telephone</label>
                <Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} data-testid="supplier-phone" />
              </div>
            </div>
            <div>
              <label className="form-label">Adresse</label>
              <Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="form-label">Code postal</label>
                <Input value={form.postal_code} onChange={e => setForm({...form, postal_code: e.target.value})} />
              </div>
              <div>
                <label className="form-label">Ville</label>
                <Input value={form.city} onChange={e => setForm({...form, city: e.target.value})} />
              </div>
              <div>
                <label className="form-label">Pays</label>
                <Input value={form.country} onChange={e => setForm({...form, country: e.target.value})} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">IBAN</label>
                <Input value={form.iban} onChange={e => setForm({...form, iban: e.target.value})} placeholder="BE00 0000 0000 0000" data-testid="supplier-iban" />
              </div>
              <div>
                <label className="form-label">BIC</label>
                <Input value={form.bic} onChange={e => setForm({...form, bic: e.target.value})} />
              </div>
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4 pt-3 border-t">
            <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="cancel-supplier-btn">
              <X size={14} className="mr-1" /> Annuler
            </Button>
            <Button onClick={handleSave} disabled={saving} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="save-supplier-btn">
              {saving ? 'Enregistrement...' : (editing ? 'Enregistrer' : 'Creer')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
