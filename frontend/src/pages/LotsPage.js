import { useState, useEffect, useCallback, useMemo } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Search, X, ArrowRightLeft, UserPlus, Link2, Link2Off, FileDown } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const LOT_TYPES = [
  { value: 'apartment', label: 'Appartement' },
  { value: 'parking', label: 'Parking' },
  { value: 'cave', label: 'Cave' },
  { value: 'commercial', label: 'Commercial' },
  { value: 'other', label: 'Autre' },
];

// ----- Inline owner picker (search + tag + inline create) -----
function OwnerPicker({ owners, selectedIds, onChange, multi = true, onOwnerCreated, dataTestPrefix = 'owner', coproproId = '' }) {
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

  // iter90c : rattache idempotemment l'owner a l'ACP courante. Ne lance jamais
  // d'erreur silencieuse : tout 4xx/5xx est toaste.
  const ensureAttached = async (ownerId) => {
    if (!coproproId || !ownerId) return;
    try {
      await api.post(`/owners/${ownerId}/attach-to-copro`, { copropriete_id: coproproId });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec du rattachement a l\'ACP courante');
    }
  };

  const addOwner = async (o) => {
    onChange(multi ? [...selectedIds, o.id] : [o.id]);
    setQ('');
    await ensureAttached(o.id);
    // Force a parent refresh so the owner instantly appears under the current ACP's list
    if (onOwnerCreated) onOwnerCreated(o);
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
        // iter90c : create directement rattache a l'ACP courante (pas apres)
        ...(coproproId ? { copropriete_id: coproproId } : {}),
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
      const detail = err.response?.data?.detail || '';
      // iter89c : si le backend refuse pour cause de doublon, on extrait l'id
      // du proprio existant (format "... id XXXX) ...") et on le selectionne.
      // iter90c : on rattache aussi explicitement a l'ACP courante.
      const idMatch = detail.match(/id\s+([a-f0-9]{8})/i);
      if (err.response?.status === 409 && idMatch) {
        const partialId = idMatch[1];
        try {
          // Recherche le proprio existant via syndic-wide
          const { data: allOwners } = await api.get('/owners', { params: { syndic_wide: true } });
          const existing = allOwners.find(o => (o.id || '').toLowerCase().startsWith(partialId.toLowerCase()));
          if (existing) {
            toast.success(`Proprietaire existant selectionne : ${existing.name || `${existing.last_name} ${existing.first_name}`.trim()}`);
            setCreateOpen(false);
            setNewOwner({ first_name: '', last_name: '', email: '', phone: '' });
            await ensureAttached(existing.id);
            if (onOwnerCreated) onOwnerCreated(existing);
            onChange(multi ? [...selectedIds, existing.id] : [existing.id]);
            setQ('');
            return;
          }
        } catch (innerErr) {
          toast.error(innerErr.response?.data?.detail || 'Echec recherche proprio existant');
        }
      }
      toast.error(detail || 'Erreur creation proprietaire');
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

// ----- Link dialog (lier des lots enfants à un lot parent) -----
function LinkDialog({ parentLot, allLots, owners, onClose, onDone }) {
  const [selectedIds, setSelectedIds] = useState([]);
  const [busy, setBusy] = useState(false);

  const ownerName = useMemo(() =>
    owners.find(o => o.id === parentLot?.owner_id)?.name || '(inconnu)',
    [owners, parentLot]
  );

  // Lots eligibles : meme ACP, meme owner, pas le parent lui-meme,
  // pas deja un parent (sans enfants), pas deja lie ailleurs (sauf au parent courant)
  const eligible = useMemo(() => {
    if (!parentLot) return [];
    return allLots.filter(l =>
      l.id !== parentLot.id &&
      l.copropriete_id === parentLot.copropriete_id &&
      l.owner_id === parentLot.owner_id &&
      (!l.parent_lot_id || l.parent_lot_id === parentLot.id) &&
      !allLots.some(other => other.parent_lot_id === l.id)  // n'est pas deja un parent
    );
  }, [parentLot, allLots]);

  // Enfants deja lies au parent courant
  const currentChildren = useMemo(() =>
    allLots.filter(l => l.parent_lot_id === parentLot?.id),
    [allLots, parentLot]
  );

  const toggle = (id) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const handleLink = async () => {
    if (selectedIds.length === 0) { toast.error('Aucun lot selectionne'); return; }
    setBusy(true);
    try {
      await api.post(`/lots/${parentLot.id}/link`, { child_lot_ids: selectedIds });
      toast.success(`${selectedIds.length} lot(s) lie(s)`);
      onDone();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lien');
    } finally {
      setBusy(false);
    }
  };

  const handleUnlink = async (childId) => {
    if (!window.confirm('Delier ce lot du parent ?')) return;
    setBusy(true);
    try {
      await api.post(`/lots/${parentLot.id}/unlink`, { child_lot_ids: [childId] });
      toast.success('Lot delie');
      onDone();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur unlink');
    } finally {
      setBusy(false);
    }
  };

  if (!parentLot) return null;
  const notYetLinked = eligible.filter(l => l.parent_lot_id !== parentLot.id);
  return (
    <Dialog open={!!parentLot} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto" data-testid="link-dialog">
        <DialogHeader>
          <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
            Lots lies au lot {parentLot.number}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 mt-2">
          <div className="rounded-md bg-slate-50 border border-slate-200 p-3 text-sm">
            <span className="text-slate-500">Lot parent : </span>
            <span className="font-medium text-slate-900">{parentLot.number}</span>
            <span className="text-slate-400 mx-2">·</span>
            <span className="text-slate-500">Type : </span>
            <span className="font-medium text-slate-900">{LOT_TYPES.find(t => t.value === parentLot.lot_type)?.label || parentLot.lot_type}</span>
            <span className="text-slate-400 mx-2">·</span>
            <span className="text-slate-500">Proprietaire : </span>
            <span className="font-medium text-slate-900">{ownerName}</span>
          </div>

          {/* Enfants deja lies */}
          {currentChildren.length > 0 && (
            <div className="rounded-md border border-indigo-200 bg-indigo-50 p-3">
              <div className="text-xs font-bold text-indigo-800 uppercase tracking-wider mb-2">
                Lots actuellement lies ({currentChildren.length})
              </div>
              <div className="space-y-1.5" data-testid="linked-children-list">
                {currentChildren.map(c => (
                  <div key={c.id} className="flex items-center justify-between bg-white rounded border border-indigo-100 px-2 py-1.5 text-sm">
                    <div>
                      <span className="font-mono font-medium">{c.number}</span>
                      <span className="text-slate-400 mx-2">·</span>
                      <span className="text-slate-600">{LOT_TYPES.find(t => t.value === c.lot_type)?.label || c.lot_type}</span>
                      {c.description && <span className="text-slate-400 ml-2">{c.description}</span>}
                    </div>
                    <Button
                      variant="ghost" size="sm"
                      onClick={() => handleUnlink(c.id)}
                      disabled={busy}
                      className="text-red-500 hover:text-red-700 h-7"
                      data-testid={`unlink-child-${c.id}`}
                    >
                      <Link2Off size={12} className="mr-1" /> Delier
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Ajouter de nouveaux enfants */}
          <div>
            <div className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">
              Ajouter des lots a lier ({notYetLinked.length} eligible{notYetLinked.length > 1 ? 's' : ''})
            </div>
            <p className="text-[11px] text-slate-500 mb-2">
              Seuls les lots de la meme ACP, appartenant au meme proprietaire et non lies ailleurs sont eligibles.
            </p>
            {notYetLinked.length === 0 ? (
              <div className="text-sm text-slate-400 italic p-3 bg-slate-50 rounded">
                Aucun lot eligible. Pour pouvoir lier un autre lot, il doit avoir le meme proprietaire que ce lot parent.
              </div>
            ) : (
              <div className="max-h-72 overflow-y-auto border border-slate-200 rounded">
                {notYetLinked.map(l => (
                  <label key={l.id} className="flex items-center gap-2 px-3 py-2 hover:bg-slate-50 cursor-pointer border-b border-slate-100 last:border-b-0">
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(l.id)}
                      onChange={() => toggle(l.id)}
                      className="rounded"
                      data-testid={`link-select-${l.id}`}
                    />
                    <span className="font-mono font-medium text-sm">{l.number}</span>
                    <Badge variant="outline" className="text-[10px]">{LOT_TYPES.find(t => t.value === l.lot_type)?.label || l.lot_type}</Badge>
                    {l.description && <span className="text-xs text-slate-500">{l.description}</span>}
                    <span className="ml-auto text-[10px] text-slate-400 font-mono">{l.quotity}/qt</span>
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="sticky bottom-0 -mx-6 px-6 pt-3 pb-1 bg-white border-t border-slate-200 flex gap-3 justify-end z-10">
            <Button variant="outline" onClick={onClose}>Fermer</Button>
            <Button
              onClick={handleLink}
              disabled={busy || selectedIds.length === 0}
              className="bg-indigo-600 hover:bg-indigo-700"
              data-testid="link-confirm-btn"
            >
              <Link2 size={14} className="mr-1" />
              {busy ? 'Traitement...' : `Lier ${selectedIds.length} lot${selectedIds.length > 1 ? 's' : ''}`}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ----- Mutation dialog -----
function MutationDialog({ lot, owners, ownersRefresh, onClose, onDone }) {
  const [newOwnerIds, setNewOwnerIds] = useState([]);
  const [saleDate, setSaleDate] = useState(new Date().toISOString().slice(0, 10));
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

  const handleCancelMutation = async (mutationId) => {
    if (!window.confirm('Annuler cette mutation ? Le proprietaire precedent sera restaure et l\'ecriture comptable supprimee.')) return;
    setBusy(true);
    try {
      await api.delete(`/lots/${lot.id}/mutate/${mutationId}`);
      toast.success('Mutation annulee');
      onDone();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur annulation');
    } finally {
      setBusy(false);
    }
  };

  const currentOwnerName = owners.find(o => o.id === lot?.owner_id)?.name || '(inconnu)';

  return (
    <Dialog open={!!lot} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        className="max-w-3xl w-[95vw] max-h-[92vh] overflow-y-auto"
        data-testid="mutation-dialog"
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
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

          {/* Bandeau mutation groupee */}
          {preview && preview.linked_lots_count > 0 && (
            <div className="rounded-md border-2 border-indigo-300 bg-indigo-50 p-3" data-testid="grouped-mutation-banner">
              <div className="flex items-center gap-2 text-sm">
                <Link2 size={16} className="text-indigo-700" />
                <span className="font-semibold text-indigo-900">
                  Mutation groupee : {preview.linked_lots_count + 1} lots seront mutes ensemble
                </span>
              </div>
              <div className="text-xs text-indigo-700 mt-1.5">
                Lot principal : <b>{lot?.number}</b> + {preview.linked_lots_count} lot(s) lie(s).
                Chaque lot recevra sa propre ecriture OD.
              </div>
              {preview.per_lot_breakdowns && preview.per_lot_breakdowns.length > 0 && (
                <table className="w-full mt-2 text-[11px] bg-white rounded">
                  <thead className="text-slate-500">
                    <tr><th className="text-left px-2 py-1">Lot</th><th className="text-right px-2 py-1">Quotite</th><th className="text-right px-2 py-1">Roulement</th><th className="text-right px-2 py-1">Prorata courant</th><th className="text-right px-2 py-1">Transfert OD</th></tr>
                  </thead>
                  <tbody>
                    {preview.per_lot_breakdowns.map((b, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        <td className="px-2 py-1 font-mono">{b.lot_number}</td>
                        <td className="text-right px-2 py-1 font-mono">{b.lot_quotity}</td>
                        <td className="text-right px-2 py-1 font-mono">{b.roulement_quota?.toFixed(2)}</td>
                        <td className="text-right px-2 py-1 font-mono">{b.current_period_prorata?.toFixed(2)}</td>
                        <td className="text-right px-2 py-1 font-mono font-semibold text-indigo-700">{b.total_transfer?.toFixed(2)}</td>
                      </tr>
                    ))}
                    <tr className="border-t-2 border-indigo-300 bg-indigo-50">
                      <td colSpan="4" className="px-2 py-1 text-right font-semibold">Total groupe :</td>
                      <td className="text-right px-2 py-1 font-mono font-bold text-indigo-900" data-testid="grouped-total-transfer">
                        {preview.grouped_total_transfer?.toFixed(2)} EUR
                      </td>
                    </tr>
                  </tbody>
                </table>
              )}
            </div>
          )}

          <div>
            <label className="form-label">Acquereur *</label>
            <OwnerPicker
              owners={owners}
              selectedIds={newOwnerIds}
              onChange={(ids) => setNewOwnerIds(ids.slice(-1))}
              multi={false}
              onOwnerCreated={() => ownersRefresh && ownersRefresh()}
              dataTestPrefix="mutation-owner"
              coproproId={lot?.copropriete_id || ''}
            />
          </div>

          <div>
            <label className="form-label">Date de la vente *</label>
            <Input type="date" value={saleDate} onChange={e => setSaleDate(e.target.value)} data-testid="mutation-sale-date" />
          </div>

          <div>
            <label className="form-label">Note</label>
            <Input value={note} onChange={e => setNote(e.target.value)} placeholder="Ex: acte notarie Me Dupont..." data-testid="mutation-note" />
          </div>

          {/* Preview */}
          {loading && <div className="text-sm text-slate-500">Calcul en cours...</div>}
          {preview && !loading && (
            <div className="space-y-3" data-testid="mutation-preview">
              {/* BLOC 1 : Fonds de roulement (jamais au prorata, sur quotites) */}
              <div className="rounded-md border border-emerald-300 bg-emerald-50 p-4 space-y-2" data-testid="mutation-block-roulement">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-bold text-emerald-800 uppercase tracking-wider">
                    Bloc 1 - Transfert du fonds de roulement
                  </div>
                  <div className="text-[10px] text-emerald-700 font-medium">
                    Quote-part sur quotites (non temporel)
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div className="text-slate-600">Solde fonds de roulement (cpt 100, ACP)</div>
                  <div className="text-right font-mono">{preview.fonds_roulement_total?.toFixed(2)} EUR</div>
                  <div className="text-slate-600">Quotites lot / total ACP</div>
                  <div className="text-right font-mono">{preview.lot_quotity} / {preview.total_quotity}</div>
                  <div className="text-slate-900 font-semibold border-t pt-2">Quote-part transferee</div>
                  <div className="text-right font-mono font-bold border-t pt-2 text-emerald-700" data-testid="mutation-roulement-quota">
                    {preview.roulement_quota?.toFixed(2)} EUR
                  </div>
                </div>
                <div className="text-[11px] text-slate-500 italic">
                  Capital permanent transfere du vendeur a l&apos;acquereur (compte 100).
                </div>
              </div>

              {/* BLOC 2 : Appels de provisions a prevoir (prorata appel en cours + futurs) */}
              <div className="rounded-md border border-[#0055FF]/30 bg-[#0055FF]/5 p-4 space-y-3" data-testid="mutation-block-provisions">
                <div className="flex items-center justify-between">
                  <div className="text-xs font-bold text-[#0055FF] uppercase tracking-wider">
                    Bloc 2 - Appels de provisions a prevoir
                  </div>
                  {preview.budget_frequency_label && (
                    <div className="text-[10px] text-[#0055FF] font-medium">
                      Periodicite : {preview.budget_frequency_label}
                    </div>
                  )}
                </div>

                {/* 2a : Prorata sur l'appel en cours */}
                <div>
                  <div className="text-xs font-semibold text-slate-700 mb-1">
                    a) Prorata appel en cours (portion apres la vente, transferee a l&apos;acquereur)
                  </div>
                  {(preview.current_period_details || []).length === 0 ? (
                    <div className="text-xs text-slate-500 italic">Aucun appel en cours a cette date.</div>
                  ) : (
                    <table className="w-full text-[11px]">
                      <thead className="text-slate-500">
                        <tr><th className="text-left">Appel</th><th className="text-right">Date appel</th><th className="text-right">Periode</th><th className="text-right">Mt vendeur</th><th className="text-right">Jours apres</th><th className="text-right">Prorata</th></tr>
                      </thead>
                      <tbody>
                        {(preview.current_period_details || []).map((d, i) => (
                          <tr key={i} className="border-t border-slate-200">
                            <td className="py-1">{d.fund_call_name}</td>
                            <td className="text-right font-mono text-slate-500">{d.call_date || '-'}</td>
                            <td className="text-right">{d.period_start} -&gt; {d.period_end}</td>
                            <td className="text-right font-mono">{d.owner_amount.toFixed(2)}</td>
                            <td className="text-right font-mono">{d.days_after}/{d.total_days}</td>
                            <td className="text-right font-mono font-semibold">{d.prorata.toFixed(2)}</td>
                          </tr>
                        ))}
                        <tr className="border-t-2 border-slate-300 bg-white">
                          <td colSpan="5" className="py-1 text-right font-semibold">Sous-total prorata appel courant</td>
                          <td className="text-right font-mono font-bold text-[#0055FF]" data-testid="mutation-current-prorata">
                            {preview.current_period_prorata?.toFixed(2)} EUR
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  )}
                  <p className="text-[10px] text-slate-500 italic mt-1">
                    Les ecritures OD de prorata utilisent la <b>date originale de l&apos;appel</b> (et non la date de vente)
                    pour preserver la coherence des situations de compte.
                  </p>
                </div>

                {/* 2b : Appels futurs */}
                <div>
                  <div className="text-xs font-semibold text-slate-700 mb-1">
                    b) Appels de provisions futurs (information - factures normalement a l&apos;acquereur)
                  </div>
                  {(preview.future_calls || []).length === 0 ? (
                    <div className="text-xs text-slate-500 italic">Aucun appel futur planifie apres la date de mutation.</div>
                  ) : (
                    <table className="w-full text-[11px]">
                      <thead className="text-slate-500">
                        <tr><th className="text-left">Appel</th><th className="text-right">Periode</th><th className="text-right">Echeance</th><th className="text-right">Montant (acquereur)</th></tr>
                      </thead>
                      <tbody>
                        {(preview.future_calls || []).map((fc, i) => (
                          <tr key={i} className="border-t border-slate-200">
                            <td className="py-1">{fc.fund_call_name}</td>
                            <td className="text-right">{fc.period_start} -&gt; {fc.period_end}</td>
                            <td className="text-right">{fc.due_date || '-'}</td>
                            <td className="text-right font-mono">{fc.amount.toFixed(2)}</td>
                          </tr>
                        ))}
                        <tr className="border-t-2 border-slate-300 bg-white">
                          <td colSpan="3" className="py-1 text-right font-semibold">Total appels futurs ({preview.future_calls.length})</td>
                          <td className="text-right font-mono font-bold text-[#0055FF]" data-testid="mutation-future-calls-total">
                            {preview.future_calls_total?.toFixed(2)} EUR
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  )}
                  <div className="text-[10px] text-slate-500 italic mt-1">
                    Ces montants ne sont PAS inclus dans l&apos;ecriture OD - ils seront appeles normalement par le syndic a l&apos;acquereur.
                  </div>
                </div>
              </div>

              {/* SYNTHESE - Ecriture OD */}
              <div className="rounded-md border-2 border-slate-700 bg-slate-50 p-4 space-y-1" data-testid="mutation-block-summary">
                <div className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">
                  Synthese - Ecriture comptable OD
                </div>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div className="text-slate-700">Fonds de roulement (Bloc 1)</div>
                  <div className="text-right font-mono">{preview.roulement_quota?.toFixed(2)} EUR</div>
                  <div className="text-slate-700">+ Prorata appel en cours (Bloc 2.a)</div>
                  <div className="text-right font-mono">{preview.current_period_prorata?.toFixed(2)} EUR</div>
                  <div className="text-slate-900 font-bold border-t border-slate-700 pt-2">= Total transfert OD</div>
                  <div className="text-right font-mono font-bold border-t border-slate-700 pt-2 text-slate-900 text-base" data-testid="mutation-total-transfer">
                    {preview.total_transfer?.toFixed(2)} EUR
                  </div>
                </div>
                <div className="text-[11px] text-slate-500 italic mt-2">
                  Ecriture : Dr compte acquereur / Cr compte vendeur pour {preview.total_transfer?.toFixed(2)} EUR.
                  Le fonds de reserve n&apos;est PAS impacte. Le lot sera reaffecte a l&apos;acquereur.
                </div>
              </div>
            </div>
          )}

          <div className="sticky bottom-0 -mx-6 px-6 pt-3 pb-1 bg-white border-t border-slate-200 flex gap-3 justify-end z-10">
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
            <details className="mt-4 text-xs text-slate-600" open>
              <summary className="cursor-pointer text-slate-500 hover:text-slate-900 font-semibold">Historique des mutations ({lot.mutations.length})</summary>
              <div className="mt-2 space-y-2">
                {lot.mutations.slice().reverse().map((m, i) => {
                  const isLast = i === 0; // reversed, so the first item is the most recent
                  return (
                    <div key={m.id || i} className="border border-slate-200 rounded p-2 bg-white">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1">
                          <div className="font-medium text-slate-900">{fmtDate(m.date)} : {m.old_owner_name} -&gt; {m.new_owner_name}</div>
                          <div className="text-[11px] grid grid-cols-2 gap-x-2">
                            <span className="text-slate-500">Fonds de roulement :</span>
                            <span className="font-mono text-right">{m.roulement_quota?.toFixed(2)} EUR</span>
                            <span className="text-slate-500">Prorata appel courant :</span>
                            <span className="font-mono text-right">{(m.current_period_prorata ?? m.prorata_provisions)?.toFixed(2)} EUR</span>
                            <span className="font-semibold border-t pt-1">Total OD :</span>
                            <span className="font-mono text-right font-bold border-t pt-1">{m.total_transfer?.toFixed(2)} EUR</span>
                            {m.future_calls_total > 0 && (
                              <>
                                <span className="text-slate-500 italic">Appels futurs ({m.future_calls?.length || 0}) :</span>
                                <span className="font-mono text-right italic text-slate-500">{m.future_calls_total?.toFixed(2)} EUR</span>
                              </>
                            )}
                          </div>
                          {m.note && <div className="text-[11px] italic text-slate-500 mt-1">{m.note}</div>}
                        </div>
                        {isLast && (
                          <div className="flex flex-col gap-1">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="text-blue-700 border-blue-200 hover:bg-blue-50 h-7 text-[11px]"
                              onClick={async () => {
                                try {
                                  const res = await api.get(
                                    `/lots/${lot.id}/mutations/${m.id || 'last'}/decompte.pdf`,
                                    { responseType: 'blob' }
                                  );
                                  const url = window.URL.createObjectURL(
                                    new Blob([res.data], { type: 'application/pdf' })
                                  );
                                  const a = document.createElement('a');
                                  a.href = url;
                                  a.download = `decompte_mutation_lot_${(lot.number || '').replace(/[\/ ]/g, '_')}_${(m.date || '').replace(/-/g, '')}.pdf`;
                                  document.body.appendChild(a);
                                  a.click();
                                  a.remove();
                                  window.URL.revokeObjectURL(url);
                                  toast.success('PDF telecharge');
                                } catch (err) {
                                  toast.error('Erreur de generation du PDF');
                                }
                              }}
                              data-testid={`download-mutation-pdf-${m.id || 'last'}`}
                              disabled={busy}
                            >
                              <FileDown size={12} className="mr-1" /> PDF
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              className="text-red-600 border-red-200 hover:bg-red-50 h-7 text-[11px]"
                              onClick={() => handleCancelMutation(m.id || 'last')}
                              data-testid={`cancel-mutation-${m.id || 'last'}`}
                              disabled={busy}
                            >
                              Annuler
                            </Button>
                          </div>
                        )}
                        {!isLast && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="text-blue-700 border-blue-200 hover:bg-blue-50 h-7 text-[11px]"
                            onClick={async () => {
                              try {
                                const res = await api.get(
                                  `/lots/${lot.id}/mutations/${m.id}/decompte.pdf`,
                                  { responseType: 'blob' }
                                );
                                const url = window.URL.createObjectURL(
                                  new Blob([res.data], { type: 'application/pdf' })
                                );
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = `decompte_mutation_lot_${(lot.number || '').replace(/[\/ ]/g, '_')}_${(m.date || '').replace(/-/g, '')}.pdf`;
                                document.body.appendChild(a);
                                a.click();
                                a.remove();
                                window.URL.revokeObjectURL(url);
                                toast.success('PDF telecharge');
                              } catch (err) {
                                toast.error('Erreur de generation du PDF');
                              }
                            }}
                            data-testid={`download-mutation-pdf-${m.id}`}
                            disabled={busy}
                          >
                            <FileDown size={12} className="mr-1" /> PDF
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="text-[10px] italic text-slate-400 mt-1">Seule la mutation la plus recente peut etre annulee.</div>
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
  const [linkLot, setLinkLot] = useState(null);

  // Derived : map lot.id -> children + parent lookup
  const childrenByParent = useMemo(() => {
    const m = {};
    for (const l of lots) {
      if (l.parent_lot_id) {
        (m[l.parent_lot_id] = m[l.parent_lot_id] || []).push(l);
      }
    }
    return m;
  }, [lots]);
  const lotById = useMemo(() => Object.fromEntries(lots.map(l => [l.id, l])), [lots]);

  const load = useCallback(async () => {
    // iter89b : owners en mode syndic-wide pour que le MutationDialog puisse
    // trouver un acquereur deja proprio dans une AUTRE ACP du syndic. Sans
    // ca, l'utilisateur cree un doublon car le picker ne voit que les proprios
    // ayant deja un lot dans l'ACP courante (chinese wall iter85k).
    // Le backend assign_owner_accounts() ajoute automatiquement l'ACP a
    // owner.copropriete_ids[] au moment de la mutation -> integration propre.
    const [lotsRes, ownersRes] = await Promise.all([
      api.get('/lots'),
      api.get('/owners', { params: { syndic_wide: true } }),
    ]);
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
                  {(childrenByParent[lot.id] || []).length > 0 && (
                    <Badge variant="outline" className="ml-2 bg-indigo-50 border-indigo-200 text-indigo-700 text-[10px]" data-testid={`lot-parent-badge-${lot.id}`}>
                      <Link2 size={10} className="inline mr-0.5" /> {childrenByParent[lot.id].length} lot{childrenByParent[lot.id].length > 1 ? 's' : ''} lie{childrenByParent[lot.id].length > 1 ? 's' : ''}
                    </Badge>
                  )}
                  {lot.parent_lot_id && (
                    <Badge variant="outline" className="ml-2 bg-slate-50 border-slate-300 text-slate-600 text-[10px]" data-testid={`lot-child-badge-${lot.id}`}>
                      <Link2 size={10} className="inline mr-0.5" /> Lie a {lotById[lot.parent_lot_id]?.number || '?'}
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(lot)} data-testid={`edit-lot-${lot.id}`}><Pencil size={14} /></Button>
                    {!lot.parent_lot_id && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setLinkLot(lot)}
                        className="text-indigo-600 hover:text-indigo-800"
                        title={(childrenByParent[lot.id] || []).length > 0 ? `Gerer les ${childrenByParent[lot.id].length} lot(s) lie(s)` : 'Lier des lots (cave, parking...)'}
                        disabled={!lot.owner_id}
                        data-testid={`link-lot-${lot.id}`}
                      >
                        <Link2 size={14} />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setMutationLot(lot)}
                      className="text-[#0055FF] hover:text-[#0040CC]"
                      title={lot.parent_lot_id ? `Lie a ${lotById[lot.parent_lot_id]?.number || '?'} - mutez le lot parent` : 'Muter (vente)'}
                      disabled={!lot.owner_id || !!lot.parent_lot_id}
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
                coproproId={editing?.copropriete_id || form.copropriete_id || ''}
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

      {/* Link dialog */}
      {linkLot && (
        <LinkDialog
          parentLot={linkLot}
          allLots={lots}
          owners={owners}
          onClose={() => setLinkLot(null)}
          onDone={() => { load(); }}
        />
      )}
    </div>
  );
}
