import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, X, ArrowRightLeft, UserPlus } from 'lucide-react';

const LOT_TYPES = [
  { value: 'apartment', label: 'Appartement' },
  { value: 'parking', label: 'Parking' },
  { value: 'cave', label: 'Cave' },
  { value: 'commercial', label: 'Commercial' },
  { value: 'other', label: 'Autre' },
];

// ----- Inline owner picker (search + tag + inline create) -----
function OwnerPicker({ owners, selectedIds, onChange, multi = true, onOwnerCreated, dataTestPrefix = 'owner' }) {
  const [q, setQ] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [newOwner, setNewOwner] = useState({ first_name: '', last_name: '', email: '', phone: '' });

  const ownerById = useMemo(() => Object.fromEntries(owners.map(o => [o.id, o])), [owners]);
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    return owners.filter(o =>
      !selectedIds.includes(o.id) && (
        (o.name || '').toLowerCase().includes(s) ||
        (o.first_name || '').toLowerCase().includes(s) ||
        (o.last_name || '').toLowerCase().includes(s) ||
        (o.email || '').toLowerCase().includes(s) ||
        (o.vcs_code || '').includes(s)
      )
    ).slice(0, 8);
  }, [q, owners, selectedIds]);

  const addOwner = (o) => {
    onChange(multi ? [...selectedIds, o.id] : [o.id]);
    setQ('');
  };
  const removeOwner = (id) => onChange(selectedIds.filter(x => x !== id));

  const handleCreateOwner = async () => {
    try {
      const payload = {
        first_name: newOwner.first_name.trim(),
        last_name: newOwner.last_name.trim(),
        name: `${newOwner.last_name} ${newOwner.first_name}`.trim(),
        email: newOwner.email.trim(),
        phone: newOwner.phone.trim(),
      };
      if (!payload.last_name && !payload.first_name) {
        toast.error('Nom ou prenom requis');
        return;
      }
      const { data } = await api.post('/owners', payload);
      toast.success('Proprietaire cree');
      setCreateOpen(false);
      setNewOwner({ first_name: '', last_name: '', email: '', phone: '' });
      // refresh parent list & select it
      if (onOwnerCreated) onOwnerCreated(data);
      onChange(multi ? [...selectedIds, data.id] : [data.id]);
      setQ('');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur creation proprietaire');
    }
  };

  return (
    <div>
      {selectedIds.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {selectedIds.map(id => {
            const o = ownerById[id];
            return (
              <Badge key={id} variant="outline" className="bg-[#0055FF]/10 border-[#0055FF]/30 text-slate-700 gap-1.5 pl-2 pr-1 py-1" data-testid={`${dataTestPrefix}-tag-${id}`}>
                <span>{o?.name || '(inconnu)'}</span>
                {o?.vcs_code && <span className="font-mono text-[9px] text-[#0055FF]">{o.vcs_code.replace(/\+/g,'').slice(0, 10)}</span>}
                <button onClick={() => removeOwner(id)} className="text-slate-400 hover:text-red-500"><X size={11} /></button>
              </Badge>
            );
          })}
        </div>
      )}
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Tapez un nom, email ou code VCS..."
          className="pl-9"
          data-testid={`${dataTestPrefix}-search-input`}
        />
        {filtered.length > 0 && (
          <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-56 overflow-y-auto">
            {filtered.map(o => (
              <button
                key={o.id}
                type="button"
                onClick={() => addOwner(o)}
                className="w-full text-left px-3 py-2 hover:bg-[#0055FF]/5 border-b last:border-b-0 border-slate-100 flex items-center justify-between text-sm"
                data-testid={`${dataTestPrefix}-suggestion-${o.id}`}
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
        {q && filtered.length === 0 && !createOpen && (
          <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg p-3 text-center text-xs">
            <div className="text-slate-400 mb-2">Aucun proprietaire trouve.</div>
            <Button
              type="button"
              size="sm"
              className="bg-[#0055FF] hover:bg-[#0040CC] text-white"
              onClick={() => {
                // Prefill name from search query if possible
                const parts = q.trim().split(/\s+/);
                setNewOwner({
                  first_name: parts.length > 1 ? parts[1] : '',
                  last_name: parts[0] || '',
                  email: '',
                  phone: '',
                });
                setCreateOpen(true);
              }}
              data-testid={`${dataTestPrefix}-create-trigger`}
            >
              <UserPlus size={14} className="mr-1" /> Creer ce proprietaire
            </Button>
          </div>
        )}
        {createOpen && (
          <div className="mt-2 border border-[#0055FF]/30 rounded-md bg-[#0055FF]/5 p-3 space-y-2">
            <div className="text-xs font-semibold text-[#0055FF] uppercase tracking-wider">Nouveau proprietaire</div>
            <div className="grid grid-cols-2 gap-2">
              <Input placeholder="Nom" value={newOwner.last_name} onChange={e => setNewOwner({...newOwner, last_name: e.target.value})} data-testid={`${dataTestPrefix}-create-lastname`} />
              <Input placeholder="Prenom" value={newOwner.first_name} onChange={e => setNewOwner({...newOwner, first_name: e.target.value})} data-testid={`${dataTestPrefix}-create-firstname`} />
            </div>
            <Input placeholder="Email" value={newOwner.email} onChange={e => setNewOwner({...newOwner, email: e.target.value})} data-testid={`${dataTestPrefix}-create-email`} />
            <Input placeholder="Telephone" value={newOwner.phone} onChange={e => setNewOwner({...newOwner, phone: e.target.value})} data-testid={`${dataTestPrefix}-create-phone`} />
            <div className="flex gap-2 justify-end">
              <Button type="button" size="sm" variant="outline" onClick={() => setCreateOpen(false)}>Annuler</Button>
              <Button type="button" size="sm" className="bg-[#0055FF] hover:bg-[#0040CC]" onClick={handleCreateOwner} data-testid={`${dataTestPrefix}-create-submit`}>
                Creer et selectionner
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ----- Mutation dialog -----
function MutationDialog({ lot, owners, ownersRefresh, onClose, onDone }) {
  const [newOwnerIds, setNewOwnerIds] = useState([]);
  const [saleDate, setSaleDate] = useState(new Date().toISOString().slice(0, 10));
  const [salePrice, setSalePrice] = useState('');
  const [note, setNote] = useState('');
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const newOwnerId = newOwnerIds[0] || '';

  // Auto preview when newOwnerId and saleDate set
  useEffect(() => {
    if (!lot || !newOwnerId || !saleDate) { setPreview(null); return; }
    setLoading(true);
    api.post(`/lots/${lot.id}/mutate-preview`, {
      new_owner_id: newOwnerId,
      sale_date: saleDate,
      sale_price: 0,
    }).then(r => setPreview(r.data))
      .catch(err => toast.error(err.response?.data?.detail || 'Erreur preview'))
      .finally(() => setLoading(false));
  }, [lot, newOwnerId, saleDate]);

  const handleConfirm = async () => {
    if (!newOwnerId) { toast.error('Selectionnez un acquereur'); return; }
    setBusy(true);
    try {
      await api.post(`/lots/${lot.id}/mutate`, {
        new_owner_id: newOwnerId,
        sale_date: saleDate,
        sale_price: Number(salePrice) || 0,
        note,
      });
      toast.success('Mutation enregistree');
      onDone();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur mutation');
    } finally {
      setBusy(false);
    }
  };

  const currentOwnerName = owners.find(o => o.id === lot?.owner_id)?.name || '(inconnu)';

  return (
    <Dialog open={!!lot} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl" data-testid="mutation-dialog">
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            Mutation du lot {lot?.number} (vente)
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 mt-2">
          <div className="rounded-md bg-slate-50 border border-slate-200 p-3 text-sm">
            <span className="text-slate-500">Vendeur actuel : </span>
            <span className="font-medium text-slate-900">{currentOwnerName}</span>
            <span className="text-slate-400 ml-3">Quotites : <span className="font-mono">{lot?.quotity}</span></span>
          </div>

          <div>
            <label className="form-label">Acquereur *</label>
            <OwnerPicker
              owners={owners}
              selectedIds={newOwnerIds}
              onChange={(ids) => setNewOwnerIds(ids.slice(-1))}
              multi={false}
              onOwnerCreated={() => ownersRefresh && ownersRefresh()}
              dataTestPrefix="mutation-owner"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="form-label">Date de la vente *</label>
              <Input type="date" value={saleDate} onChange={e => setSaleDate(e.target.value)} data-testid="mutation-sale-date" />
            </div>
            <div>
              <label className="form-label">Prix de vente (info, non comptable)</label>
              <Input type="number" step="0.01" value={salePrice} onChange={e => setSalePrice(e.target.value)} placeholder="EUR" data-testid="mutation-sale-price" />
            </div>
          </div>

          <div>
            <label className="form-label">Note</label>
            <Input value={note} onChange={e => setNote(e.target.value)} placeholder="Ex: acte notarie Me Dupont..." data-testid="mutation-note" />
          </div>

          {/* Preview */}
          {loading && <div className="text-sm text-slate-500">Calcul en cours...</div>}
          {preview && !loading && (
            <div className="rounded-md border border-[#0055FF]/30 bg-[#0055FF]/5 p-4 space-y-2" data-testid="mutation-preview">
              <div className="text-xs font-semibold text-[#0055FF] uppercase tracking-wider">Apercu de l&apos;ecriture comptable</div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div className="text-slate-600">Fonds de roulement (cpt 100, ACP total)</div>
                <div className="text-right font-mono">{preview.fonds_roulement_total?.toFixed(2)} EUR</div>
                <div className="text-slate-600">Quotites lot / ACP</div>
                <div className="text-right font-mono">{preview.lot_quotity} / {preview.total_quotity}</div>
                <div className="text-slate-900 font-semibold">Quote-part roulement transferee</div>
                <div className="text-right font-mono font-semibold text-[#0055FF]">{preview.roulement_quota?.toFixed(2)} EUR</div>
                <div className="text-slate-900 font-semibold">Prorata provisions (jours posterieurs vente)</div>
                <div className="text-right font-mono font-semibold text-[#0055FF]">{preview.prorata_provisions?.toFixed(2)} EUR</div>
                <div className="text-slate-900 font-semibold border-t pt-2 col-span-1">Total transfert</div>
                <div className="text-right font-mono font-bold border-t pt-2 text-[#0055FF]">{preview.total_transfer?.toFixed(2)} EUR</div>
              </div>
              {(preview.prorata_details || []).length > 0 && (
                <details className="text-xs text-slate-600 mt-2">
                  <summary className="cursor-pointer text-slate-500 hover:text-slate-900">Detail prorata ({preview.prorata_details.length} appel(s))</summary>
                  <table className="w-full mt-2 text-[11px]">
                    <thead className="text-slate-500">
                      <tr><th className="text-left">Appel</th><th className="text-right">Periode</th><th className="text-right">Mt vendeur</th><th className="text-right">Jours apres</th><th className="text-right">Prorata</th></tr>
                    </thead>
                    <tbody>
                      {preview.prorata_details.map((d, i) => (
                        <tr key={i} className="border-t border-slate-200">
                          <td className="py-1">{d.fund_call_name}</td>
                          <td className="text-right">{d.period_start} - {d.period_end}</td>
                          <td className="text-right font-mono">{d.owner_amount.toFixed(2)}</td>
                          <td className="text-right font-mono">{d.days_after}/{d.total_days}</td>
                          <td className="text-right font-mono font-semibold">{d.prorata.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              )}
              <div className="text-[11px] text-slate-500 italic">
                Ecriture OD : Dr compte acquereur / Cr compte vendeur pour {preview.total_transfer?.toFixed(2)} EUR.
                Le fonds de reserve n&apos;est PAS impacte. Le lot sera reaffecte a l&apos;acquereur.
              </div>
            </div>
          )}

          <div className="flex gap-3 justify-end">
            <Button variant="outline" onClick={onClose}>Annuler</Button>
            <Button
              onClick={handleConfirm}
              disabled={busy || !newOwnerId}
              className="bg-[#0055FF] hover:bg-[#0040CC]"
              data-testid="mutation-confirm-btn"
            >
              {busy ? 'Traitement...' : 'Confirmer la mutation'}
            </Button>
          </div>

          {/* History */}
          {lot?.mutations && lot.mutations.length > 0 && (
            <details className="mt-4 text-xs text-slate-600">
              <summary className="cursor-pointer text-slate-500 hover:text-slate-900 font-semibold">Historique des mutations ({lot.mutations.length})</summary>
              <div className="mt-2 space-y-2">
                {lot.mutations.slice().reverse().map((m, i) => (
                  <div key={i} className="border border-slate-200 rounded p-2 bg-white">
                    <div className="font-medium text-slate-900">{m.date} : {m.old_owner_name} -&gt; {m.new_owner_name}</div>
                    <div className="text-[11px]">Roulement {m.roulement_quota?.toFixed(2)} EUR + Prorata {m.prorata_provisions?.toFixed(2)} EUR = <b>{m.total_transfer?.toFixed(2)} EUR</b>{m.sale_price ? ` | Prix: ${m.sale_price.toFixed(2)} EUR` : ''}</div>
                    {m.note && <div className="text-[11px] italic text-slate-500">{m.note}</div>}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function LotsPage() {
  const [lots, setLots] = useState([]);
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0, owner_id: '', owner_ids: [] });
  const [mutationLot, setMutationLot] = useState(null);

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
              <TableHead className="w-32">Actions</TableHead>
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
                <TableCell className="text-slate-600">
                  {getOwnerNames(lot)}
                  {lot.mutations && lot.mutations.length > 0 && (
                    <Badge variant="outline" className="ml-2 bg-amber-50 border-amber-200 text-amber-700 text-[10px]">
                      {lot.mutations.length} mutation{lot.mutations.length > 1 ? 's' : ''}
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(lot)} data-testid={`edit-lot-${lot.id}`}><Pencil size={14} /></Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setMutationLot(lot)}
                      className="text-[#0055FF] hover:text-[#0040CC]"
                      title="Muter (vente)"
                      disabled={!lot.owner_id}
                      data-testid={`mutate-lot-${lot.id}`}
                    >
                      <ArrowRightLeft size={14} />
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => handleDelete(lot.id)} className="text-red-500 hover:text-red-700" data-testid={`delete-lot-${lot.id}`}><Trash2 size={14} /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Create / Edit dialog */}
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
              <OwnerPicker
                owners={owners}
                selectedIds={form.owner_ids}
                onChange={(ids) => setForm(prev => ({ ...prev, owner_ids: ids, owner_id: ids[0] || '' }))}
                multi
                onOwnerCreated={load}
                dataTestPrefix="owner"
              />
            </div>
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="lot-save-btn">{editing ? 'Modifier' : 'Creer'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Mutation dialog */}
      {mutationLot && (
        <MutationDialog
          lot={mutationLot}
          owners={owners}
          ownersRefresh={load}
          onClose={() => setMutationLot(null)}
          onDone={() => { setMutationLot(null); load(); }}
        />
      )}
    </div>
  );
}
