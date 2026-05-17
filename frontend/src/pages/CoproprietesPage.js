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
import { Plus, Pencil, Trash2, Home, Search, Archive, RotateCcw, Landmark, PlusCircle, X, Eraser } from 'lucide-react';

const emptyBank = { iban: '', bic: '', account_type: 'vue', is_default: false, label: '' };
const emptyLot = { number: '', description: '', lot_type: 'apartment', floor: 0, area: 0, quotity: 0 };
const emptyForm = { name: '', bce: '', address: '', postal_code: '', city: '', country: 'Belgique', description: '', bank_accounts: [], quarterly_closing: true, default_provisions: true, lots: [] };

export default function CoproprietesPage() {
  const { isAdmin, isManager } = useAuth();
  const [coproprietes, setCoproprietes] = useState([]);
  const [owners, setOwners] = useState([]);
  const [search, setSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [step, setStep] = useState(1);
  const [ownerSearchByLot, setOwnerSearchByLot] = useState({});  // {lotIdx: 'query'}

  const load = useCallback(async () => {
    const { data } = await api.get('/coproprietes', { params: { show_archived: showArchived } });
    setCoproprietes(data);
  }, [showArchived]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.get('/owners').then(r => setOwners(r.data)).catch(() => {}); }, [dialogOpen]);

  const filtered = coproprietes.filter(c => c.name.toLowerCase().includes(search.toLowerCase()) || (c.reference || '').toLowerCase().includes(search.toLowerCase()) || (c.bce || '').includes(search));

  const openCreate = () => { setEditing(null); setForm({...emptyForm, bank_accounts: [], lots: []}); setStep(1); setDialogOpen(true); };
  const openEdit = (c) => {
    setEditing(c);
    setForm({
      name: c.name || '', bce: c.bce || '', address: c.address || '', postal_code: c.postal_code || '',
      city: c.city || '', country: c.country || 'Belgique', description: c.description || '',
      bank_accounts: c.bank_accounts || [], quarterly_closing: c.quarterly_closing !== false,
      default_provisions: c.default_provisions !== false, lots: [],
    });
    setStep(1);
    setDialogOpen(true);
  };

  // Lots on the fly (only used at creation time)
  const addLot = () => setForm({ ...form, lots: [...(form.lots || []), { ...emptyLot, owner_ids: [] }] });
  const removeLot = (i) => setForm({ ...form, lots: form.lots.filter((_, idx) => idx !== i) });
  const updateLot = (i, field, value) => {
    const ls = [...form.lots];
    ls[i] = { ...ls[i], [field]: value };
    setForm({ ...form, lots: ls });
  };
  const addOwnerToLot = (i, ownerId) => {
    const ls = [...form.lots];
    const current = ls[i].owner_ids || [];
    if (!current.includes(ownerId)) {
      ls[i] = { ...ls[i], owner_ids: [...current, ownerId] };
      setForm({ ...form, lots: ls });
    }
    setOwnerSearchByLot({ ...ownerSearchByLot, [i]: '' });
  };
  const removeOwnerFromLot = (i, ownerId) => {
    const ls = [...form.lots];
    ls[i] = { ...ls[i], owner_ids: (ls[i].owner_ids || []).filter(x => x !== ownerId) };
    setForm({ ...form, lots: ls });
  };
  const getOwnerSuggestions = (i) => {
    const q = (ownerSearchByLot[i] || '').trim().toLowerCase();
    if (!q) return [];
    const taken = form.lots[i].owner_ids || [];
    return owners.filter(o =>
      !taken.includes(o.id) && (
        (o.name || '').toLowerCase().includes(q) ||
        (o.email || '').toLowerCase().includes(q) ||
        (o.vcs_code || '').includes(q)
      )
    ).slice(0, 6);
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
  const handleResetData = async (c) => {
    const msg = `Vider TOUTES les donnees comptables de "${c.name}" ?\n\n` +
                `Seront SUPPRIMES : factures, ecritures, appels de fonds, transactions bancaires,\n` +
                `budgets, exercices, regularisations, natures de depense, documents uploades.\n\n` +
                `Seront GARDES : lots, proprietaires, fournisseurs, PCMN, cles de repartition, banques.\n\n` +
                `Tape "VIDER" pour confirmer :`;
    const confirm = window.prompt(msg);
    if (confirm !== 'VIDER') {
      if (confirm !== null) toast.error('Reset annule (confirmation incorrecte)');
      return;
    }
    try {
      const { data } = await api.post(`/coproprietes/${c.id}/reset-financial-data`);
      const s = data.stats || {};
      const total = (s.invoices||0) + (s.journal_entries||0) + (s.fund_calls||0) +
                    (s.bank_transactions||0) + (s.budgets||0) + (s.fiscal_years||0) +
                    (s.expense_categories||0) + (s.documents||0) + (s.regularizations||0);
      toast.success(`ACP videe : ${total} elements supprimes (${s.deleted_files||0} fichier(s) disque)`);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

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
            <TableHead>Ref</TableHead><TableHead>Nom</TableHead><TableHead>BCE</TableHead><TableHead>Ville</TableHead><TableHead>Compte defaut</TableHead><TableHead>Statut</TableHead><TableHead className="w-32">Actions</TableHead>
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
                  {isManager && <Button variant="ghost" size="sm" onClick={() => openEdit(c)} title="Modifier"><Pencil size={13} /></Button>}
                  {isManager && <Button variant="ghost" size="sm" onClick={() => handleResetData(c)} className="text-amber-600 hover:text-amber-700" title="Vider les donnees comptables (test)" data-testid={`reset-data-${c.id}`}><Eraser size={13} /></Button>}
                  {isManager && c.status !== 'archived' && <Button variant="ghost" size="sm" onClick={() => handleArchive(c.id)} className="text-orange-500" title="Archiver"><Archive size={13} /></Button>}
                  {isManager && c.status === 'archived' && <Button variant="ghost" size="sm" onClick={() => handleUnarchive(c.id)} className="text-green-600" title="Reactiver"><RotateCcw size={13} /></Button>}
                  {isAdmin && <Button variant="ghost" size="sm" onClick={() => handleDelete(c.id)} className="text-red-500" title="Supprimer (cascade)"><Trash2 size={13} /></Button>}
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
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier ACP' : 'Assistant de creation ACP'}</DialogTitle>
            {editing?.reference && <p className="font-mono text-sm text-[#0055FF]">Ref: {editing.reference}</p>}
          </DialogHeader>

          {/* Wizard step indicator (only for creation, not edit) */}
          {!editing && (
            <div className="flex items-center justify-between mb-4 mt-2 px-2">
              {[
                { n: 1, label: 'Identite & banques' },
                { n: 2, label: 'Lots & proprietaires' },
                { n: 3, label: 'Options & validation' },
              ].map((s, idx, arr) => (
                <div key={s.n} className="flex items-center flex-1">
                  <div className={`flex items-center gap-2 ${step === s.n ? 'text-[#0055FF]' : step > s.n ? 'text-green-600' : 'text-slate-400'}`}>
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 ${step === s.n ? 'bg-[#0055FF] text-white border-[#0055FF]' : step > s.n ? 'bg-green-500 text-white border-green-500' : 'bg-white border-slate-300'}`} data-testid={`step-indicator-${s.n}`}>
                      {step > s.n ? '✓' : s.n}
                    </div>
                    <span className="text-xs font-medium hidden sm:inline">{s.label}</span>
                  </div>
                  {idx < arr.length - 1 && <div className={`flex-1 h-px mx-2 ${step > s.n ? 'bg-green-500' : 'bg-slate-200'}`} />}
                </div>
              ))}
            </div>
          )}

          <div className="space-y-5 mt-2">
            {/* STEP 1: Identification + adresse + banques (always shown in edit mode) */}
            {(editing || step === 1) && <>
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
            </>}

            {/* STEP 2: Lots with owner autocomplete */}
            {!editing && step === 2 && (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Lots et proprietaires</div>
                  <Button variant="outline" size="sm" onClick={addLot} data-testid="add-lot-btn"><PlusCircle size={14} className="mr-1" /> Ajouter lot</Button>
                </div>
                <div className="bg-blue-50/40 border border-blue-100 text-xs text-blue-700 p-2 rounded mb-3">
                  Astuce: les proprietaires sont globaux. S'ils n'existent pas encore, allez d'abord dans <strong>Proprietaires</strong> pour les creer (ils seront alors disponibles dans la recherche).
                </div>
                {(form.lots || []).length === 0 ? (
                  <p className="text-sm text-slate-400 text-center py-3 border rounded-md">Aucun lot - vous pourrez en ajouter plus tard via le menu Lots</p>
                ) : (
                  <div className="space-y-3">
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
                          <div><label className="form-label">Quotite</label><Input type="number" step="0.01" value={lot.quotity} onChange={e => updateLot(i, 'quotity', parseFloat(e.target.value || '0'))} placeholder="125.50" /></div>
                        </div>

                        {/* Owner autocomplete per lot */}
                        <div className="mt-2 pt-2 border-t border-slate-200/70">
                          <label className="form-label">Proprietaires <span className="text-slate-400 font-normal">(recherche par nom)</span></label>
                          {(lot.owner_ids || []).length > 0 && (
                            <div className="flex flex-wrap gap-1.5 mb-2">
                              {lot.owner_ids.map(oid => {
                                const o = owners.find(x => x.id === oid);
                                return (
                                  <Badge key={oid} variant="outline" className="bg-[#0055FF]/10 border-[#0055FF]/30 text-slate-700 gap-1 pl-2 pr-1 py-0.5" data-testid={`lot-${i}-owner-${oid}`}>
                                    <span className="text-[11px]">{o?.name || '(inconnu)'}</span>
                                    <button onClick={() => removeOwnerFromLot(i, oid)} className="text-slate-400 hover:text-red-500"><X size={10} /></button>
                                  </Badge>
                                );
                              })}
                            </div>
                          )}
                          <div className="relative">
                            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                            <Input
                              value={ownerSearchByLot[i] || ''}
                              onChange={e => setOwnerSearchByLot({...ownerSearchByLot, [i]: e.target.value})}
                              placeholder="Tapez nom, email, VCS..."
                              className="pl-8 h-8 text-sm"
                              data-testid={`lot-${i}-owner-search`}
                            />
                            {getOwnerSuggestions(i).length > 0 && (
                              <div className="absolute z-50 left-0 right-0 mt-1 bg-white border border-slate-200 rounded-md shadow-lg max-h-40 overflow-y-auto">
                                {getOwnerSuggestions(i).map(o => (
                                  <button key={o.id} onClick={() => addOwnerToLot(i, o.id)} className="w-full text-left px-2 py-1.5 hover:bg-[#0055FF]/5 border-b last:border-b-0 border-slate-100 text-xs flex items-center justify-between" data-testid={`lot-${i}-suggestion-${o.id}`}>
                                    <span className="font-medium">{o.name}</span>
                                    {o.vcs_code && <span className="font-mono text-[9px] text-[#0055FF]">{o.vcs_code}</span>}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
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

            {/* STEP 3: Options + description + summary */}
            {(editing || step === 3) && <>
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

            {/* Summary recap (only in wizard mode) */}
            {!editing && (
              <div className="bg-slate-50 border border-slate-200 rounded-md p-3 text-xs">
                <div className="font-semibold text-slate-700 mb-1">Recapitulatif</div>
                <ul className="space-y-0.5 text-slate-600">
                  <li><strong>Nom:</strong> {form.name || '(non defini)'}</li>
                  <li><strong>Adresse:</strong> {form.address}, {form.postal_code} {form.city}</li>
                  <li><strong>Comptes bancaires:</strong> {form.bank_accounts.length}</li>
                  <li><strong>Lots:</strong> {(form.lots || []).filter(l => l.number?.trim()).length} (total quotites: {form.lots.reduce((s, l) => s + (parseFloat(l.quotity) || 0), 0).toFixed(2)})</li>
                  <li><strong>Total proprietaires affectes:</strong> {new Set(form.lots.flatMap(l => l.owner_ids || [])).size}</li>
                </ul>
                <div className="mt-2 text-blue-700">A la creation: 95 comptes PCMN belges + 10 categories documents seront automatiquement seedes.</div>
              </div>
            )}
            </>}

            {/* Navigation buttons */}
            <div className="flex gap-3 justify-between pt-2 border-t">
              {!editing && step > 1 ? (
                <Button variant="outline" onClick={() => setStep(step - 1)} data-testid="wizard-prev-btn">Precedent</Button>
              ) : <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>}
              <div className="flex gap-2">
                {!editing && step < 3 ? (
                  <Button onClick={() => setStep(step + 1)} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="wizard-next-btn" disabled={step === 1 && !form.name.trim()}>Suivant</Button>
                ) : (
                  <Button onClick={handleSave} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="copro-save-btn">{editing ? 'Modifier' : 'Creer l\'ACP'}</Button>
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
