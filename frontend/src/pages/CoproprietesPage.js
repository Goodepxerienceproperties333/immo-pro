import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Plus, Pencil, Trash2, Home, Search, Archive, RotateCcw, Landmark, PlusCircle, X } from 'lucide-react';

const emptyBank = { iban: '', bic: '', account_type: 'vue', is_default: false, label: '' };
const emptyLot = { number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0 };
const emptyForm = { name: '', bce: '', address: '', postal_code: '', city: '', country: 'Belgique', description: '', bank_accounts: [], quarterly_closing: true, default_provisions: true, lots: [] };

export default function CoproprietesPage() {
  const { isAdmin, isManager } = useAuth();
  const [coproprietes, setCoproprietes] = useState([]);
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);

  const load = useCallback(async () => {
    const { data } = await api.get('/coproprietes', { params: { show_archived: showArchived } });
    setCoproprietes(data);
  }, [showArchived]);
  useEffect(() => { load(); }, [load]);

  const filtered = coproprietes.filter(c => c.name.toLowerCase().includes(search.toLowerCase()) || (c.reference || '').toLowerCase().includes(search.toLowerCase()) || (c.bce || '').includes(search));

  const openCreate = () => { setEditing(null); setForm({...emptyForm, bank_accounts: [], lots: []}); setDialogOpen(true); };
  const openEdit = (c) => {
    setEditing(c);
    setForm({
      name: c.name || '', bce: c.bce || '', address: c.address || '', postal_code: c.postal_code || '',
      city: c.city || '', country: c.country || 'Belgique', description: c.description || '',
      bank_accounts: c.bank_accounts || [], quarterly_closing: c.quarterly_closing !== false,
      default_provisions: c.default_provisions !== false, lots: [],
    });
    setDialogOpen(true);
  };

  // Lots on the fly (only used at creation time)
  const addLot = () => setForm({ ...form, lots: [...(form.lots || []), { ...emptyLot }] });
  const removeLot = (i) => setForm({ ...form, lots: form.lots.filter((_, idx) => idx !== i) });
  const updateLot = (i, field, value) => {
    const ls = [...form.lots];
    ls[i] = { ...ls[i], [field]: value };
    setForm({ ...form, lots: ls });
  };

  // Bank accounts management
  const addBankAccount = () => setForm({ ...form, bank_accounts: [...form.bank_accounts, { ...emptyBank }] });
  const removeBankAccount = (i) => setForm({ ...form, bank_accounts: form.bank_accounts.filter((_, idx) => idx !== i) });
  const updateBankAccount = (i, field, value) => {
    const ba = [...form.bank_accounts];
    ba[i] = { ...ba[i], [field]: value };
    if (field === 'is_default' && value === true) {
      ba.forEach((b, idx) => { if (idx !== i) b.is_default = false; });
    }
    setForm({ ...form, bank_accounts: ba });
  };

  const handleSave = async () => {
    try {
      if (editing) { await api.put(`/coproprietes/${editing.id}`, form); toast.success('Copropriete modifiee'); }
      else {
        await api.post('/coproprietes', form);
        const nLots = (form.lots || []).filter(l => l.number && l.number.trim()).length;
        toast.success(nLots > 0 ? `ACP creee avec ${nLots} lot(s)` : 'Copropriete creee');
      }
      setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const handleDelete = async (id) => { if (!window.confirm('Supprimer cette copropriete ?')) return; try { await api.delete(`/coproprietes/${id}`); toast.success('Supprimee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const handleArchive = async (id) => { try { await api.post(`/coproprietes/${id}/archive`); toast.success('Archivee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };
  const handleUnarchive = async (id) => { try { await api.post(`/coproprietes/${id}/unarchive`); toast.success('Reactivee'); load(); } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); } };

  const getDefaultIban = (c) => (c.bank_accounts || []).find(b => b.is_default)?.iban || (c.bank_accounts || [])[0]?.iban || c.bank_account || '-';

  return (
    <div data-testid="coproprietes-page">
      <div className="page-header flex items-center justify-between">
        <div><h1 className="page-title"><Home size={24} className="inline mr-2" />Coproprietes (ACP)</h1><p className="page-subtitle">Gestion des associations de coproprietaires</p></div>
        <div className="flex gap-2">
          <Button variant={showArchived ? "default" : "outline"} size="sm" onClick={() => setShowArchived(!showArchived)} data-testid="toggle-archived"><Archive size={14} className="mr-1" /> {showArchived ? 'Masquer archives' : 'Voir archives'}</Button>
          {isManager && <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-copro-btn"><Plus size={16} className="mr-2" /> Nouvelle ACP</Button>}
        </div>
      </div>
      <div className="mb-4 relative max-w-sm"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><Input placeholder="Rechercher ref, nom, BCE..." value={search} onChange={e => setSearch(e.target.value)} className="pl-9" /></div>
      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Ref</TableHead><TableHead>Nom</TableHead><TableHead>BCE</TableHead><TableHead>Ville</TableHead><TableHead>Compte defaut</TableHead><TableHead>Statut</TableHead><TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">Aucune copropriete</TableCell></TableRow> : filtered.map(c => (
              <TableRow key={c.id} className="hover:bg-slate-50/50" data-testid={`copro-row-${c.id}`}>
                <TableCell className="font-mono text-xs text-[#0055FF]">{c.reference || '-'}</TableCell>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="font-mono text-sm">{c.bce || '-'}</TableCell>
                <TableCell className="text-sm">{c.city}{c.postal_code ? ` (${c.postal_code})` : ''}</TableCell>
                <TableCell className="font-mono text-xs">{getDefaultIban(c)}</TableCell>
                <TableCell><Badge variant="outline" className={c.status === 'archived' ? 'bg-slate-100 text-slate-500' : 'bg-green-50 text-green-700 border-green-200'}>{c.status === 'archived' ? 'Archive' : 'Active'}</Badge></TableCell>
                <TableCell><div className="flex gap-0">
                  {isManager && <Button variant="ghost" size="sm" onClick={() => openEdit(c)}><Pencil size={13} /></Button>}
                  {isManager && c.status !== 'archived' && <Button variant="ghost" size="sm" onClick={() => handleArchive(c.id)} className="text-orange-500"><Archive size={13} /></Button>}
                  {isManager && c.status === 'archived' && <Button variant="ghost" size="sm" onClick={() => handleUnarchive(c.id)} className="text-green-600"><RotateCcw size={13} /></Button>}
                  {isAdmin && <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-500"><Trash2 size={13} /></Button>}
                </div></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto" data-testid="copro-dialog">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier ACP' : 'Nouvelle ACP'}</DialogTitle>
            {editing?.reference && <p className="font-mono text-sm text-[#0055FF]">Ref: {editing.reference}</p>}
          </DialogHeader>
          <div className="space-y-5 mt-2">
            {/* Identification */}
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Identification</div>
              <div className="grid grid-cols-2 gap-4">
                <div><label className="form-label">Nom de l'ACP *</label><Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} data-testid="copro-name-input" /></div>
                <div><label className="form-label">N BCE</label><Input value={form.bce} onChange={e => setForm({...form, bce: e.target.value})} placeholder="0123.456.789" data-testid="copro-bce-input" /></div>
              </div>
            </div>

            {/* Adresse */}
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Adresse postale</div>
              <div><label className="form-label">Adresse</label><Input value={form.address} onChange={e => setForm({...form, address: e.target.value})} /></div>
              <div className="grid grid-cols-3 gap-4 mt-2">
                <div><label className="form-label">Code postal</label><Input value={form.postal_code} onChange={e => setForm({...form, postal_code: e.target.value})} /></div>
                <div><label className="form-label">Ville</label><Input value={form.city} onChange={e => setForm({...form, city: e.target.value})} /></div>
                <div><label className="form-label">Pays</label><Input value={form.country} onChange={e => setForm({...form, country: e.target.value})} /></div>
              </div>
            </div>

            {/* Comptes bancaires */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Comptes bancaires</div>
                <Button variant="outline" size="sm" onClick={addBankAccount}><PlusCircle size={14} className="mr-1" /> Ajouter compte</Button>
              </div>
              {form.bank_accounts.length === 0 ? (
                <p className="text-sm text-slate-400 text-center py-3 border rounded-md">Aucun compte - cliquez "Ajouter compte"</p>
              ) : (
                <div className="space-y-2">
                  {form.bank_accounts.map((ba, i) => (
                    <div key={i} className="border rounded-md p-3 bg-slate-50/50 relative">
                      <button onClick={() => removeBankAccount(i)} className="absolute top-2 right-2 text-red-400 hover:text-red-600"><X size={14} /></button>
                      <div className="grid grid-cols-4 gap-3">
                        <div className="col-span-2"><label className="form-label">IBAN *</label><Input value={ba.iban} onChange={e => updateBankAccount(i, 'iban', e.target.value)} placeholder="BE00 0000 0000 0000" /></div>
                        <div><label className="form-label">BIC</label><Input value={ba.bic} onChange={e => updateBankAccount(i, 'bic', e.target.value)} /></div>
                        <div><label className="form-label">Type</label>
                          <Select value={ba.account_type} onValueChange={v => updateBankAccount(i, 'account_type', v)}>
                            <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                            <SelectContent><SelectItem value="vue">Compte a vue</SelectItem><SelectItem value="epargne">Compte epargne</SelectItem></SelectContent>
                          </Select>
                        </div>
                      </div>
                      <div className="flex items-center gap-4 mt-2">
                        <Input value={ba.label} onChange={e => updateBankAccount(i, 'label', e.target.value)} placeholder="Libelle (optionnel)" className="flex-1 h-8 text-sm" />
                        <label className="flex items-center gap-2 cursor-pointer text-sm whitespace-nowrap">
                          <Checkbox checked={ba.is_default} onCheckedChange={v => updateBankAccount(i, 'is_default', v)} />
                          <span>Compte par defaut</span>
                        </label>
                      </div>
                      {ba.iban && <div className="text-[10px] text-slate-400 mt-1 font-mono">Compte PCMN auto: {ba.account_type === 'epargne' ? '550' : '551'}{(ba.iban.replace(/\s/g, '').slice(-3) || '000')}00</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Lots (only at creation) */}
            {!editing && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Lots (optionnel)</div>
                  <Button variant="outline" size="sm" onClick={addLot} data-testid="add-lot-btn"><PlusCircle size={14} className="mr-1" /> Ajouter lot</Button>
                </div>
                {(form.lots || []).length === 0 ? (
                  <p className="text-sm text-slate-400 text-center py-3 border rounded-md">Aucun lot - vous pourrez en ajouter plus tard via le menu Lots</p>
                ) : (
                  <div className="space-y-2">
                    {form.lots.map((lot, i) => (
                      <div key={i} className="border rounded-md p-3 bg-slate-50/50 relative" data-testid={`lot-row-${i}`}>
                        <button onClick={() => removeLot(i)} className="absolute top-2 right-2 text-red-400 hover:text-red-600"><X size={14} /></button>
                        <div className="grid grid-cols-6 gap-3">
                          <div><label className="form-label">N* *</label><Input value={lot.number} onChange={e => updateLot(i, 'number', e.target.value)} placeholder="A1" data-testid={`lot-number-${i}`} /></div>
                          <div className="col-span-2"><label className="form-label">Description</label><Input value={lot.description} onChange={e => updateLot(i, 'description', e.target.value)} placeholder="Appartement 2 ch" /></div>
                          <div><label className="form-label">Type</label>
                            <Select value={lot.lot_type} onValueChange={v => updateLot(i, 'lot_type', v)}>
                              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                              <SelectContent>
                                <SelectItem value="apartment">Appartement</SelectItem>
                                <SelectItem value="parking">Parking</SelectItem>
                                <SelectItem value="cave">Cave</SelectItem>
                                <SelectItem value="commerce">Commerce</SelectItem>
                                <SelectItem value="bureau">Bureau</SelectItem>
                                <SelectItem value="autre">Autre</SelectItem>
                              </SelectContent>
                            </Select>
                          </div>
                          <div><label className="form-label">Etage</label><Input type="number" value={lot.floor} onChange={e => updateLot(i, 'floor', parseInt(e.target.value || '0'))} /></div>
                          <div><label className="form-label">Quotite (/10000)</label><Input type="number" step="0.01" value={lot.quotity} onChange={e => updateLot(i, 'quotity', parseFloat(e.target.value || '0'))} placeholder="125.50" /></div>
                        </div>
                      </div>
                    ))}
                    <div className="text-[11px] text-slate-500 px-1">
                      Total quotites: <span className="font-mono font-semibold text-slate-700">{form.lots.reduce((s, l) => s + (parseFloat(l.quotity) || 0), 0).toFixed(2)}</span> / 10000
                    </div>
                  </div>
                )}
              </div>
            )}
            <div>
              <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Options</div>
              <div className="flex gap-6">
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <Checkbox checked={form.quarterly_closing} onCheckedChange={v => setForm({...form, quarterly_closing: v})} />
                  <span>Cloture trimestrielle</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <Checkbox checked={form.default_provisions} onCheckedChange={v => setForm({...form, default_provisions: v})} />
                  <span>Appels de provisions par defaut</span>
                </label>
              </div>
            </div>

            {/* Description */}
            <div><label className="form-label">Notes / Description</label><Textarea value={form.description} onChange={e => setForm({...form, description: e.target.value})} rows={2} /></div>

            <div className="flex gap-3 justify-end pt-2 border-t">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="copro-save-btn">{editing ? 'Modifier' : 'Creer l\'ACP'}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
