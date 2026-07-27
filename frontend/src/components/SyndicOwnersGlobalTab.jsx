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
import { Plus, Pencil, Search, Users, ArrowUpDown, X } from 'lucide-react';

const emptyOwnerForm = {
  first_name: '', last_name: '', name: '', civility: '',
  address: '', postal_code: '', city: '', country: 'Belgique',
  email: '', email2: '', phone: '', phone2: '', bce_number: '',
};

export default function SyndicOwnersGlobalTab({ coproprietes }) {
  const navigate = useNavigate();
  const [owners, setOwners] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [filterAcp, setFilterAcp] = useState('_all');
  const [sortMode, setSortMode] = useState('name'); // 'name' | 'acp_count'
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyOwnerForm);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/owners/syndic-global');
      setOwners(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement proprietaires');
      setOwners([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const activeAcpsList = useMemo(() => (coproprietes || []).filter(c => c.status !== 'archived'), [coproprietes]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = owners.filter(o => {
      if (filterAcp !== '_all' && !(o.acp_ids || []).includes(filterAcp)) return false;
      if (!q) return true;
      return (
        (o.name || '').toLowerCase().includes(q) ||
        (o.first_name || '').toLowerCase().includes(q) ||
        (o.last_name || '').toLowerCase().includes(q) ||
        (o.email || '').toLowerCase().includes(q) ||
        (o.phone || '').toLowerCase().includes(q) ||
        (o.vcs_code || '').toLowerCase().includes(q) ||
        (o.auxiliary_code || '').toLowerCase().includes(q)
      );
    });
    if (sortMode === 'acp_count') {
      list = [...list].sort((a, b) => (b.acp_count || 0) - (a.acp_count || 0) || (a.name || '').localeCompare(b.name || ''));
    } else {
      list = [...list].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    }
    return list;
  }, [owners, search, filterAcp, sortMode]);

  const openCreate = () => {
    setEditing(null);
    setForm(emptyOwnerForm);
    setDialogOpen(true);
  };

  const openEdit = (o) => {
    setEditing(o);
    setForm({
      first_name: o.first_name || '',
      last_name: o.last_name || '',
      name: o.name || '',
      civility: o.civility || '',
      address: o.address || '',
      postal_code: o.postal_code || '',
      city: o.city || '',
      country: o.country || 'Belgique',
      email: o.email || '',
      email2: o.email2 || '',
      phone: o.phone || '',
      phone2: o.phone2 || '',
      bce_number: o.bce_number || '',
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.last_name && !form.first_name && !form.name) {
      toast.error('Nom ou prenom requis');
      return;
    }
    setSaving(true);
    try {
      const payload = { ...form };
      if (!payload.name) payload.name = `${payload.last_name} ${payload.first_name}`.trim();
      if (editing) {
        await api.put(`/owners/${editing.id}`, payload);
        toast.success('Proprietaire modifie');
      } else {
        await api.post('/owners', payload);
        toast.success('Proprietaire cree');
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
    navigate(`/lots?copropriete_id=${acpId}`);
  };

  return (
    <div data-testid="syndic-owners-global-tab">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <div className="text-sm text-slate-600 flex items-center gap-2">
          <Users size={14} className="text-[#022D52]" />
          <strong>{filtered.length}</strong> proprietaire(s)
          {filtered.length !== owners.length && (
            <span className="text-slate-400">(filtres actifs, {owners.length} au total)</span>
          )}
        </div>
        <Button onClick={openCreate} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-owner-global-btn">
          <Plus size={14} className="mr-1" /> Nouveau proprietaire
        </Button>
      </div>

      <div className="flex flex-col md:flex-row gap-2 mb-3">
        <div className="relative flex-1 min-w-[220px]">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Rechercher nom, email, telephone, code auxiliaire, VCS..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="owners-global-search"
          />
        </div>
        <div className="min-w-[220px]">
          <Select value={filterAcp} onValueChange={setFilterAcp}>
            <SelectTrigger data-testid="owners-global-acp-filter">
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
          data-testid="owners-global-sort-btn"
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
              <TableHead>Codes</TableHead>
              <TableHead>ACPs d&apos;affectation</TableHead>
              <TableHead className="w-24">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Chargement...</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={5} className="text-center py-8 text-slate-400">Aucun proprietaire</TableCell></TableRow>
            ) : filtered.map(o => (
              <TableRow key={o.id} className="hover:bg-slate-50/50" data-testid={`owner-global-row-${o.id}`}>
                <TableCell className="font-medium">
                  {o.name || `${o.last_name || ''} ${o.first_name || ''}`.trim() || '(sans nom)'}
                </TableCell>
                <TableCell className="text-xs">
                  <div>{o.email || <span className="text-slate-400">-</span>}</div>
                  <div className="text-slate-500">{o.phone || ''}</div>
                </TableCell>
                <TableCell className="text-[11px] font-mono">
                  {o.vcs_code && <div className="text-[#022D52]">{o.vcs_code}</div>}
                  {o.auxiliary_code && <div className="text-slate-500">{o.auxiliary_code}</div>}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(o.acp_names || []).map(a => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => goToAcp(a.id)}
                        className="inline-flex items-center rounded-full border border-[#022D52]/30 bg-[#022D52]/5 hover:bg-[#022D52]/15 text-[#022D52] px-2 py-0.5 text-[11px] font-medium transition-colors"
                        title={`Ouvrir ${a.name}`}
                        data-testid={`owner-${o.id}-acp-${a.id}`}
                      >
                        {a.name}
                      </button>
                    ))}
                    <Badge variant="outline" className="text-[10px] text-slate-500 border-slate-200">
                      {o.acp_count || 0} ACP{(o.acp_count || 0) > 1 ? 's' : ''}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => openEdit(o)}
                    className="text-[#022D52] border-[#022D52]/30 hover:bg-[#022D52]/10"
                    data-testid={`edit-owner-global-${o.id}`}
                    title="Modifier ce proprietaire"
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
        <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto" data-testid="owner-global-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              {editing ? 'Modifier proprietaire' : 'Nouveau proprietaire (global)'}
            </DialogTitle>
            {editing && (
              <p className="text-xs text-slate-500">
                Ce proprietaire est actuellement lie a {editing.acp_count || 0} ACP(s).
                Les modifications s&apos;appliquent globalement.
              </p>
            )}
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Prenom</label>
                <Input value={form.first_name} onChange={e => setForm({...form, first_name: e.target.value})} data-testid="owner-first-name" />
              </div>
              <div>
                <label className="form-label">Nom *</label>
                <Input value={form.last_name} onChange={e => setForm({...form, last_name: e.target.value})} data-testid="owner-last-name" />
              </div>
            </div>
            <div>
              <label className="form-label">Nom complet / Denomination</label>
              <Input
                value={form.name}
                onChange={e => setForm({...form, name: e.target.value})}
                placeholder="Auto genere si vide"
                data-testid="owner-full-name"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Email</label>
                <Input type="email" value={form.email} onChange={e => setForm({...form, email: e.target.value})} data-testid="owner-email" />
              </div>
              <div>
                <label className="form-label">Telephone</label>
                <Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} data-testid="owner-phone" />
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
            <div>
              <label className="form-label">BCE (societe)</label>
              <Input value={form.bce_number} onChange={e => setForm({...form, bce_number: e.target.value})} placeholder="0123.456.789" />
            </div>
          </div>
          <div className="flex justify-end gap-2 mt-4 pt-3 border-t">
            <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="cancel-owner-btn">
              <X size={14} className="mr-1" /> Annuler
            </Button>
            <Button onClick={handleSave} disabled={saving} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="save-owner-btn">
              {saving ? 'Enregistrement...' : (editing ? 'Enregistrer' : 'Creer')}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
