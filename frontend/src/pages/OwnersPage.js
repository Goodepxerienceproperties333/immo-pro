import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, AlertTriangle } from 'lucide-react';
import OwnerAccessSection from '@/components/OwnerAccessSection';

const emptyForm = { first_name: '', last_name: '', address: '', postal_code: '', city: '', country: 'Belgique', email: '', email2: '', phone: '', phone2: '' };

export default function OwnersPage() {
  // iter85k : selectedCopro vient du AuthContext (source de verite unique).
  // Plus de listener legacy 'copropriete-changed' ni de useState local.
  const { selectedCopro } = useAuth();
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [duplicates, setDuplicates] = useState([]);
  const [showAll, setShowAll] = useState(false);

  const load = useCallback(async () => {
    // iter90ad : "Afficher tous" = ignore selectedCopro et charge en global.
    // Utilise `syndic_wide=true` pour bypasser aussi le header X-Copropriete-Id
    // injecte par le middleware (indispensable, sinon retour du scope ACP seul).
    if (showAll) {
      const { data } = await api.get('/owners', { params: { syndic_wide: true } });
      setOwners(data);
      return;
    }
    if (!selectedCopro || selectedCopro === 'all') {
      setOwners([]);
      return;
    }
    const params = { copropriete_id: selectedCopro };
    const { data } = await api.get('/owners', { params });
    setOwners(data);
  }, [selectedCopro, showAll]);
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

  // iter88d : "selectionner" un proprio doublon -> on bascule en mode edition
  // sur lui (sans creer un nouveau). Utile quand on a tape un email et qu'un
  // proprio existe deja avec cet email (eviter le doublon dans la DB).
  const handleUseDuplicate = async (dup) => {
    try {
      const { data } = await api.get(`/owners/${dup.owner_id}`);
      if (!data) {
        toast.error('Proprietaire introuvable');
        return;
      }
      // Ferme le dialog de creation, recharge la liste, ouvre l'edition
      setDialogOpen(false);
      setDuplicates([]);
      // Si on est dans une ACP precise mais le doublon est dans une autre ACP,
      // on previent l'utilisateur
      const dupCopros = dup.copropriete_ids || (dup.copropriete_id ? [dup.copropriete_id] : []);
      if (selectedCopro && selectedCopro !== 'all' && dupCopros.length > 0 && !dupCopros.includes(selectedCopro)) {
        toast.warning(`Ce proprietaire appartient a une autre ACP (${dupCopros.length})`);
      } else {
        toast.success(`Proprietaire ${data.name || `${data.last_name} ${data.first_name}`} selectionne`);
      }
      // Ouvrir la fiche en mode edition (sans dependre du load() qui peut
      // ne pas inclure le proprio si scope ACP different).
      openEdit(data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors du chargement');
    }
  };

  const F = (field, label, props = {}) => (<div {...(props.className ? {className: props.className} : {})}><label className="form-label">{label}</label><Input value={form[field]} onChange={e => setForm({...form, [field]: e.target.value})} data-testid={`owner-${field}-input`} {...props} /></div>);

  return (
    <div data-testid="owners-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title">Proprietaires</h1><p className="page-subtitle">Gestion des coproprietaires</p></div>
        <Button onClick={openCreate} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="create-owner-btn"><Plus size={16} className="mr-2" /> Nouveau</Button>
      </div>
      <div className="mb-4 flex items-center gap-3 flex-wrap">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input placeholder="Rechercher nom, email, VCS..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" data-testid="owners-search" />
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer" data-testid="owners-show-all-toggle">
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} className="rounded" />
          <span>Afficher tous les proprietaires (toutes ACPs)</span>
        </label>
        <Badge variant="outline" className="text-[11px] bg-slate-50">
          {filtered.length} proprietaire{filtered.length > 1 ? 's' : ''}{!showAll && selectedCopro && selectedCopro !== 'all' ? ' (ACP active)' : ' (toutes ACPs)'}
        </Badge>
        <Button
          variant="outline"
          size="sm"
          onClick={() => window.location.assign('/admin/duplicates?tab=owners')}
          className="text-xs border-orange-300 text-orange-700 hover:bg-orange-50"
          data-testid="owners-detect-duplicates-btn"
        >
          <AlertTriangle size={13} className="mr-1.5" /> Detecter les doublons
        </Button>
      </div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        {(!showAll && (!selectedCopro || selectedCopro === '')) ? (
          <div className="text-center py-12 px-6" data-testid="owners-no-acp-selected">
            <AlertTriangle size={32} className="mx-auto text-amber-500 mb-3" />
            <p className="text-sm text-slate-700 font-medium">Selectionnez une ACP</p>
            <p className="text-xs text-slate-500 mt-1">
              La liste des proprietaires est cloisonnee par ACP (chinese wall).
              Utilisez le selecteur en haut a droite pour choisir une ACP,
              ou cochez &laquo; Afficher tous &raquo; pour une vue globale.
            </p>
          </div>
        ) : (
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
                <TableCell className="font-mono text-xs text-[#2563EB]">{o.vcs_code || '-'}</TableCell>
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
        )}
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="owner-dialog">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier proprietaire' : 'Nouveau proprietaire'}</DialogTitle>
            {editing?.vcs_code && <p className="font-mono text-sm text-[#2563EB] mt-1">VCS: {editing.vcs_code}</p>}
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
              <div className="bg-yellow-50 border border-yellow-300 rounded-md p-3" data-testid="duplicate-warning">
                <div className="flex items-start gap-2 mb-2">
                  <AlertTriangle size={16} className="text-yellow-600 flex-shrink-0 mt-0.5" />
                  <div className="font-semibold text-yellow-800">Doublon detecte !</div>
                </div>
                {/* iter88d : dedupliquer par owner_id (un meme proprio peut matcher
                    sur email ET phone, on l'affiche 1 seule fois avec tous les champs) */}
                {(() => {
                  const byId = {};
                  duplicates.forEach(d => {
                    if (!d.owner_id) return;
                    if (!byId[d.owner_id]) byId[d.owner_id] = { ...d, matches: [] };
                    byId[d.owner_id].matches.push(`${d.field === 'email' ? 'Email' : 'GSM'} "${d.value}"`);
                  });
                  const groups = Object.values(byId);
                  return groups.map((d, i) => (
                    <div key={d.owner_id || i} className="bg-white rounded border border-yellow-200 p-3 mt-2 first:mt-0">
                      <div className="flex items-start justify-between gap-3 flex-wrap">
                        <div className="flex-1 min-w-0">
                          <div className="text-yellow-800 font-semibold text-sm">{d.owner_name || `${d.owner_last_name || ''} ${d.owner_first_name || ''}`.trim()}</div>
                          <div className="text-xs text-slate-600 mt-1 space-y-0.5">
                            {d.owner_email && <div>Email : <span className="font-mono">{d.owner_email}</span></div>}
                            {d.owner_phone && <div>GSM : <span className="font-mono">{d.owner_phone}</span></div>}
                            {d.owner_vcs_code && <div>VCS : <span className="font-mono">{d.owner_vcs_code}</span></div>}
                            <div className="text-yellow-700 italic">Correspondance : {d.matches.join(', ')}</div>
                          </div>
                        </div>
                        <Button
                          size="sm"
                          onClick={() => handleUseDuplicate(d)}
                          className="bg-yellow-600 hover:bg-yellow-700 text-white whitespace-nowrap"
                          data-testid={`use-duplicate-${d.owner_id}`}
                        >
                          Utiliser ce proprietaire
                        </Button>
                      </div>
                    </div>
                  ));
                })()}
                <div className="text-[11px] text-yellow-700 italic mt-2 pl-1">
                  Cliquez sur &laquo;&nbsp;Utiliser ce proprietaire&nbsp;&raquo; pour ouvrir sa fiche, ou poursuivez la creation en cliquant sur &laquo;&nbsp;Creer&nbsp;&raquo;.
                </div>
              </div>
            )}
            {editing && (
              <OwnerAccessSection ownerId={editing.id} ownerEmail={form.email} />
            )}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="owner-cancel-btn">Annuler</Button>
              <Button onClick={handleSave} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="owner-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
