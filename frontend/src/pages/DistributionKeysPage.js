// Iter90da (Feb 2026) : page dediee a la gestion des cles de repartition.
// Extraite de InvoicesPage.js pour figurer dans la section "Comptabilite"
// du menu principal avec un lien direct.
import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Trash2, Pencil, AlertTriangle, Star, Key } from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

export default function DistributionKeysPage() {
  const [distKeys, setDistKeys] = useState([]);
  const [lots, setLots] = useState([]);
  const [keyDialog, setKeyDialog] = useState(false);
  const [keyForm, setKeyForm] = useState({ name: '', code: '', description: '', key_type: 'quotity', lots: [], is_default: false });
  const [editingKey, setEditingKey] = useState(null);
  const [keyUsage, setKeyUsage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [keys, lts] = await Promise.all([
        api.get('/distribution-keys'),
        api.get('/lots'),
      ]);
      setDistKeys(keys.data || []);
      setLots(lts.data || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // --- Handlers ---
  const updateKeyLot = (i, field, value) => {
    const newLots = [...keyForm.lots];
    newLots[i] = { ...newLots[i], [field]: field === 'excluded' ? value : Number(value) };
    setKeyForm({ ...keyForm, lots: newLots });
  };

  const openCreateKey = () => {
    setEditingKey(null); setKeyUsage(null);
    setKeyForm({
      name: '', code: '', description: '', key_type: 'quotity',
      lots: lots.map(l => ({ lot_id: l.id, lot_number: l.number, share: l.quotity || 0, excluded: false })),
      is_default: false,
    });
    setKeyDialog(true);
  };

  const openEditKey = async (k) => {
    setEditingKey(k);
    setKeyForm({
      name: k.name, code: k.code || '', description: k.description || '',
      key_type: k.key_type,
      lots: (k.lots || []).map(l => ({ ...l, excluded: !!l.excluded })),
      is_default: !!k.is_default,
    });
    try {
      const { data } = await api.get(`/distribution-keys/${k.id}/usage`);
      setKeyUsage(data);
    } catch { setKeyUsage(null); }
    setKeyDialog(true);
  };

  const saveKey = async (force = false) => {
    try {
      if (editingKey) {
        await api.put(`/distribution-keys/${editingKey.id}`, keyForm, { params: force ? { force: true } : {} });
        toast.success(force && keyUsage?.invoices?.length > 0
          ? `Cle modifiee - ${keyUsage.invoices.length} facture(s) detachee(s)`
          : 'Cle modifiee');
      } else {
        await api.post('/distribution-keys', keyForm);
        toast.success('Cle creee');
      }
      setKeyDialog(false); setEditingKey(null); setKeyUsage(null); load();
    } catch (err) {
      if (err.response?.status === 409) {
        if (window.confirm(`${err.response.data.detail}\n\nDETACHER les factures et continuer ?`)) {
          await saveKey(true);
        }
      } else {
        toast.error(err.response?.data?.detail || 'Erreur');
      }
    }
  };

  const toggleDefaultKey = async (k, makeDefault) => {
    try {
      const endpoint = makeDefault ? 'set-default' : 'unset-default';
      await api.post(`/distribution-keys/${k.id}/${endpoint}`);
      toast.success(makeDefault ? `'${k.name}' definie comme cle par defaut` : `'${k.name}' n'est plus la cle par defaut`);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const deleteKey = async (id) => {
    if (!window.confirm('Supprimer cette cle ?')) return;
    try {
      await api.delete(`/distribution-keys/${id}`);
      toast.success('Cle supprimee');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  return (
    <div data-testid="distribution-keys-page">
      <div className="page-header flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="page-title flex items-center gap-2">
            <Key size={22} className="text-[#7C3AED]" />
            Cles de repartition
          </h1>
          <p className="page-subtitle">Gestion des cles utilisees pour repartir les charges, budgets et appels de fonds</p>
        </div>
        <Button onClick={openCreateKey} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="create-key-btn">
          <Plus size={16} className="mr-2" /> Nouvelle cle
        </Button>
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-24">N&deg;</TableHead>
              <TableHead>Nom</TableHead>
              <TableHead>Description</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Lots</TableHead>
              <TableHead className="text-right">Total quotites</TableHead>
              <TableHead>Coherence</TableHead>
              <TableHead className="w-28 text-center">Defaut</TableHead>
              <TableHead className="w-20">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {distKeys.length === 0 ? (
              <TableRow><TableCell colSpan={9} className="text-center py-8 text-slate-400">Aucune cle</TableCell></TableRow>
            ) : distKeys.map(k => {
              const activeLots = (k.lots || []).filter(l => !l.excluded);
              const excludedCount = (k.lots || []).length - activeLots.length;
              const total = activeLots.reduce((s, l) => s + (Number(l.share) || 0), 0);
              const hasZero = activeLots.some(l => !Number(l.share));
              const isRound = [1, 100, 1000, 10000].some(t => Math.abs(total - t) < 0.005);
              let coherenceColor = 'bg-slate-50 text-slate-500 border-slate-200';
              let coherenceLabel = `${total.toFixed(2)}`;
              if (hasZero) { coherenceColor = 'bg-amber-50 text-amber-700 border-amber-200'; coherenceLabel = 'Lots a 0'; }
              else if (isRound) { coherenceColor = 'bg-green-50 text-green-700 border-green-200'; coherenceLabel = 'OK'; }
              else if (total > 0) { coherenceColor = 'bg-blue-50 text-[#01213e] border-blue-200'; coherenceLabel = 'Custom'; }
              return (
                <TableRow key={k.id} className={`hover:bg-slate-50/50 ${k.is_default ? 'bg-amber-50/40' : ''}`}>
                  <TableCell className="font-mono text-xs text-slate-600" data-testid={`key-code-${k.id}`}>{k.code || <span className="text-slate-300">—</span>}</TableCell>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      {k.name}
                      {k.is_default && <Star size={12} className="text-amber-500 fill-amber-400" />}
                    </div>
                  </TableCell>
                  <TableCell>{k.description}</TableCell>
                  <TableCell><Badge variant="outline">{k.key_type}</Badge></TableCell>
                  <TableCell className="text-sm">
                    {activeLots.length} lots
                    {excludedCount > 0 && (
                      <span className="ml-1 text-[10px] text-slate-400" title={`${excludedCount} lot(s) exclu(s)`}>
                        (+{excludedCount} exclu{excludedCount > 1 ? 's' : ''})
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm" data-testid={`key-total-${k.id}`}>{total.toFixed(2)}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={coherenceColor}>{coherenceLabel}</Badge>
                  </TableCell>
                  <TableCell className="text-center">
                    {k.is_default ? (
                      <button
                        type="button"
                        onClick={() => toggleDefaultKey(k, false)}
                        className="inline-flex items-center gap-1 text-amber-600 hover:text-amber-700"
                        title="Retirer le statut par defaut"
                        data-testid={`key-default-on-${k.id}`}
                      >
                        <Star size={14} className="fill-amber-400" />
                        <span className="text-[10px] font-semibold">Par defaut</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => toggleDefaultKey(k, true)}
                        className="inline-flex items-center gap-1 text-slate-400 hover:text-amber-600"
                        title="Definir comme cle par defaut"
                        data-testid={`key-default-off-${k.id}`}
                      >
                        <Star size={14} />
                        <span className="text-[10px]">Definir</span>
                      </button>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => openEditKey(k)} data-testid={`edit-key-${k.id}`}><Pencil size={14} /></Button>
                      <Button variant="ghost" size="sm" onClick={() => deleteKey(k.id)} className="text-red-500" data-testid={`delete-key-${k.id}`}><Trash2 size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Distribution Key Dialog (extrait de InvoicesPage.js) */}
      <Dialog open={keyDialog} onOpenChange={(open) => { if (!open) { setEditingKey(null); setKeyUsage(null); } setKeyDialog(open); }}>
        <DialogContent className="max-w-5xl w-[95vw] max-h-[92vh] overflow-y-auto" data-testid="key-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>
              {editingKey ? 'Modifier la cle' : 'Nouvelle cle de repartition'}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            {editingKey && keyUsage && keyUsage.total > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs" data-testid="key-usage-warning">
                <div className="flex items-start gap-2 mb-1 text-amber-900 font-semibold">
                  <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
                  Cle utilisee par {keyUsage.invoices.length} facture(s), {keyUsage.budgets.length} budget(s), {keyUsage.fund_calls.length} appel(s)
                </div>
                <p className="text-amber-800 ml-6">
                  La modification entrainera leur <b>desindexation</b> de la cle. Pensez a reaffecter une cle apres sauvegarde.
                </p>
                {keyUsage.invoices.length > 0 && (
                  <details className="ml-6 mt-2 text-amber-900">
                    <summary className="cursor-pointer">Voir les factures liees ({keyUsage.invoices.length})</summary>
                    <ul className="mt-1 space-y-0.5 text-[11px]">
                      {keyUsage.invoices.slice(0, 8).map(i => (
                        <li key={i.id} className="font-mono">{fmtDate(i.date)} - {i.number} - {i.supplier} ({i.total_amount} EUR)</li>
                      ))}
                      {keyUsage.invoices.length > 8 && <li>... et {keyUsage.invoices.length - 8} autres</li>}
                    </ul>
                  </details>
                )}
              </div>
            )}
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="form-label">N&deg; (optionnel)</label>
                <Input
                  value={keyForm.code}
                  onChange={e => setKeyForm({...keyForm, code: e.target.value})}
                  placeholder="ex: 001"
                  data-testid="key-code"
                  className="font-mono"
                />
              </div>
              <div>
                <label className="form-label">Nom *</label>
                <Input value={keyForm.name} onChange={e => setKeyForm({...keyForm, name: e.target.value})} data-testid="key-name" />
              </div>
              <div>
                <label className="form-label">Type</label>
                <Select value={keyForm.key_type} onValueChange={v => setKeyForm({...keyForm, key_type: v})}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="quotity">Tantiemes</SelectItem>
                    <SelectItem value="equal">Egal</SelectItem>
                    <SelectItem value="custom">Personnalise</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <label className="form-label">Description</label>
              <Input value={keyForm.description} onChange={e => setKeyForm({...keyForm, description: e.target.value})} />
            </div>
            <label className="flex items-center gap-2 p-3 rounded-md border border-amber-200 bg-amber-50/50 cursor-pointer hover:bg-amber-50">
              <input
                type="checkbox"
                checked={!!keyForm.is_default}
                onChange={e => setKeyForm({...keyForm, is_default: e.target.checked})}
                data-testid="key-is-default"
                className="w-4 h-4 accent-amber-500"
              />
              <Star size={14} className={keyForm.is_default ? "text-amber-500 fill-amber-400" : "text-slate-400"} />
              <span className="text-sm font-medium text-slate-700">Definir comme cle par defaut pour cette ACP</span>
              <span className="ml-auto text-[11px] text-slate-500 italic">1 seule cle par defaut par ACP - utilisee comme fallback</span>
            </label>
            {keyForm.lots.length > 0 && (() => {
              const activeLots = keyForm.lots.filter(l => !l.excluded);
              const excludedCount = keyForm.lots.length - activeLots.length;
              const totalShare = activeLots.reduce((s, l) => s + (Number(l.share) || 0), 0);
              const lotsAtZero = activeLots.filter(l => !Number(l.share)).length;
              const isRound = [1, 100, 1000, 10000].some(t => Math.abs(totalShare - t) < 0.005);
              let badgeColor = 'bg-slate-100 text-slate-700 border-slate-300';
              let badgeLabel = `Total : ${totalShare.toFixed(2)}`;
              if (lotsAtZero > 0) { badgeColor = 'bg-amber-50 text-amber-700 border-amber-300'; badgeLabel = `${lotsAtZero} lot(s) a 0 - Total ${totalShare.toFixed(2)}`; }
              else if (isRound) { badgeColor = 'bg-green-50 text-green-700 border-green-300'; badgeLabel = `Total : ${totalShare.toFixed(2)} - coherent`; }
              else if (totalShare > 0) { badgeColor = 'bg-blue-50 text-[#01213e] border-blue-300'; badgeLabel = `Total : ${totalShare.toFixed(2)}`; }

              const fillEqual = () => {
                const n = activeLots.length || 1;
                const share = +(1000 / n).toFixed(4);
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => l.excluded ? l : ({ ...l, share }))});
              };
              const fillFromQuotities = () => {
                const byNumber = Object.fromEntries((lots || []).map(x => [x.number, x.quotity || 0]));
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => l.excluded ? l : ({ ...l, share: byNumber[l.lot_number] || 0 }))});
              };
              const normalize1000 = () => {
                if (totalShare <= 0) return;
                const factor = 1000 / totalShare;
                setKeyForm({...keyForm, lots: keyForm.lots.map(l => l.excluded ? l : ({ ...l, share: +((Number(l.share) || 0) * factor).toFixed(4) }))});
              };

              return (
                <div>
                  <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
                    <label className="form-label mb-0">
                      Repartition par lot
                      {excludedCount > 0 && <span className="ml-2 text-[10px] text-slate-400 font-normal">({excludedCount} exclu{excludedCount > 1 ? 's' : ''})</span>}
                    </label>
                    <div className="flex gap-1.5">
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={fillFromQuotities} data-testid="key-fill-from-quotities">Reprendre tantiemes lots</Button>
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={fillEqual} data-testid="key-fill-equal">Repartir egalement (=1000)</Button>
                      <Button type="button" size="sm" variant="outline" className="text-[11px] h-7" onClick={normalize1000} disabled={totalShare <= 0} data-testid="key-normalize-1000">Normaliser /1000</Button>
                    </div>
                  </div>
                  <div className="border rounded-md overflow-hidden max-h-60 overflow-y-auto">
                    <table className="w-full text-sm">
                      <thead className="bg-slate-50 text-xs text-slate-600 sticky top-0">
                        <tr>
                          <th className="p-2 text-left">Lot</th>
                          <th className="p-2 text-center w-16" title="Un lot exclu ne participe pas au calcul de la cle">Exclu</th>
                          <th className="p-2 text-right">Quote-part</th>
                          <th className="p-2 text-right w-20">% du total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {keyForm.lots.map((l, i) => {
                          const share = Number(l.share) || 0;
                          const pct = (!l.excluded && totalShare > 0) ? (share / totalShare * 100) : 0;
                          return (
                            <tr key={l.lot_id || `${l.lot_number}-${i}`} className={`border-t border-slate-100 ${l.excluded ? 'bg-slate-100 opacity-50' : (!share ? 'bg-amber-50/40' : '')}`}>
                              <td className="p-2">Lot {l.lot_number}</td>
                              <td className="p-2 text-center">
                                <input
                                  type="checkbox"
                                  checked={!!l.excluded}
                                  onChange={e => updateKeyLot(i, 'excluded', e.target.checked)}
                                  className="h-4 w-4 accent-slate-600"
                                  data-testid={`key-lot-excluded-${i}`}
                                  title="Exclure ce lot de la cle (ex : lot commercial exclu des ascenseurs)"
                                />
                              </td>
                              <td className="p-2">
                                <Input
                                  type="number" step="0.01" min="0"
                                  disabled={!!l.excluded}
                                  className={`w-24 ml-auto text-right h-7 text-sm ${(!l.excluded && !share) ? 'border-amber-300' : ''}`}
                                  value={l.share}
                                  onChange={e => updateKeyLot(i, 'share', e.target.value)}
                                  data-testid={`key-lot-share-${i}`}
                                />
                              </td>
                              <td className="p-2 text-right font-mono text-xs text-slate-500">{l.excluded ? '—' : pct.toFixed(2) + '%'}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                      <tfoot>
                        <tr className="bg-slate-50 border-t-2 border-slate-300 text-xs font-semibold">
                          <td className="p-2" colSpan={2}>Total (hors exclus)</td>
                          <td className="p-2 text-right font-mono" data-testid="key-form-total">{totalShare.toFixed(2)}</td>
                          <td className="p-2 text-right font-mono">{totalShare > 0 ? '100.00%' : '0%'}</td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                    <Badge variant="outline" className={badgeColor} data-testid="key-form-coherence">{badgeLabel}</Badge>
                    {lotsAtZero === 0 && !isRound && totalShare > 0 && (
                      <span className="text-slate-400 italic text-[11px]">Total libre : OK pour releves conso. Pour tantiemes, utilisez le bouton Normaliser /1000.</span>
                    )}
                  </div>
                </div>
              );
            })()}
            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setKeyDialog(false)}>Annuler</Button>
              <Button onClick={() => saveKey(false)} className="bg-[#022D52] hover:bg-[#1D4ED8]" data-testid="key-save-btn">
                {editingKey ? 'Modifier' : 'Creer'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
