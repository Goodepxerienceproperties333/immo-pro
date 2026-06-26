import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { toast } from 'sonner';
import { Plus, Trash2, Pencil, Tag, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem } from '@/components/ui/command';

export default function ExpenseCategoriesPage() {
  const [cats, setCats] = useState([]);
  const [pcmnAccounts, setPcmnAccounts] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', code: '', vat_code: '', account_number: '', description: '', default_occupant_pct: 0, default_proprietaire_pct: 100 });
  const [search, setSearch] = useState('');
  const [accountPopoverOpen, setAccountPopoverOpen] = useState(false);

  const load = useCallback(async () => {
    const [c, p6, p7] = await Promise.all([
      api.get('/expense-categories'),
      api.get('/accounting/pcmn', { params: { class_num: 6 } }),
      api.get('/accounting/pcmn', { params: { class_num: 7 } }),
    ]);
    setCats(c.data);
    setPcmnAccounts([...p6.data, ...p7.data].sort((a, b) => a.number.localeCompare(b.number)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', code: '', vat_code: '', account_number: '', description: '', default_occupant_pct: 0, default_proprietaire_pct: 100 });
    setDialogOpen(true);
  };
  const openEdit = (c) => {
    setEditing(c);
    setForm({
      name: c.name,
      code: c.code || '',
      vat_code: c.vat_code || '',
      account_number: c.account_number,
      description: c.description || '',
      default_occupant_pct: c.default_occupant_pct ?? 0,
      default_proprietaire_pct: c.default_proprietaire_pct ?? 100,
    });
    setDialogOpen(true);
  };
  // Auto-complete entre occupant_pct et proprietaire_pct (somme = 100)
  const setOccupant = (val) => {
    const v = Math.max(0, Math.min(100, parseFloat(val) || 0));
    setForm(f => ({ ...f, default_occupant_pct: v, default_proprietaire_pct: +(100 - v).toFixed(2) }));
  };
  const setProprietaire = (val) => {
    const v = Math.max(0, Math.min(100, parseFloat(val) || 0));
    setForm(f => ({ ...f, default_proprietaire_pct: v, default_occupant_pct: +(100 - v).toFixed(2) }));
  };
  const save = async () => {
    if (!form.name || !form.account_number) { toast.error('Nom et compte obligatoires'); return; }
    try {
      if (editing) await api.put(`/expense-categories/${editing.id}`, form);
      else await api.post('/expense-categories', form);
      toast.success('Nature enregistree');
      setDialogOpen(false); load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };
  const remove = async (c) => {
    if (!window.confirm(`Supprimer "${c.name}" ?`)) return;
    try { await api.delete(`/expense-categories/${c.id}`); toast.success('Supprimee'); load(); }
    catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const filtered = cats.filter(c =>
    !search || c.name.toLowerCase().includes(search.toLowerCase()) ||
    c.account_number.includes(search) ||
    (c.account_name || '').toLowerCase().includes(search.toLowerCase()));

  const selectedAcc = pcmnAccounts.find(a => a.number === form.account_number);

  return (
    <div data-testid="expense-categories-page">
      <div className="page-header flex items-start justify-between">
        <div>
          <h1 className="page-title"><Tag size={24} className="inline mr-2" />Natures de depense</h1>
          <p className="page-subtitle">Categories metier liees aux comptes PCMN (1 nature = 1 compte). Classes 6 (Charges) et 7 (Produits).</p>
        </div>
        <Button onClick={openCreate} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="create-category-btn"><Plus size={16} className="mr-2" />Nouvelle nature</Button>
      </div>

      <div className="mb-3 relative max-w-sm">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input className="pl-9" placeholder="Rechercher nom ou compte..." value={search} onChange={e => setSearch(e.target.value)} data-testid="search-category" />
      </div>

      <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
        <Table>
          <TableHeader><TableRow>
            <TableHead className="w-16">Code</TableHead>
            <TableHead>Nature</TableHead><TableHead>Compte PCMN</TableHead>
            <TableHead className="w-20">TVA</TableHead>
            <TableHead className="w-20">Type</TableHead>
            <TableHead className="text-right">Factures liees</TableHead>
            <TableHead className="text-right">Total facture</TableHead>
            <TableHead className="w-24">Actions</TableHead>
          </TableRow></TableHeader>
          <TableBody>
            {filtered.length === 0 ? (
              <TableRow><TableCell colSpan={8} className="text-center py-10 text-slate-400">Aucune nature - creez-en une pour faciliter la saisie des factures</TableCell></TableRow>
            ) : filtered.map(c => (
              <TableRow key={c.id} className="hover:bg-slate-50/50" data-testid={`category-row-${c.id}`}>
                <TableCell className="font-mono text-xs text-slate-500">{c.code || '-'}</TableCell>
                <TableCell className="font-medium">{c.name}</TableCell>
                <TableCell className="font-mono text-sm">
                  {c.account_number}
                  <div className="text-[11px] text-slate-500">{c.account_name}</div>
                </TableCell>
                <TableCell className="text-xs">{c.vat_code || <span className="text-slate-300">-</span>}</TableCell>
                <TableCell>
                  {c.kind === 'produit'
                    ? <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">Produit</Badge>
                    : <Badge variant="outline" className="bg-slate-50 text-slate-600 border-slate-200">Charge</Badge>
                  }
                </TableCell>
                <TableCell className="text-right">{c.invoice_count > 0 ? <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">{c.invoice_count}</Badge> : <span className="text-slate-300">0</span>}</TableCell>
                <TableCell className="text-right font-mono text-sm">{(c.invoice_total || 0).toFixed(2)} EUR</TableCell>
                <TableCell>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => openEdit(c)} data-testid={`edit-category-${c.id}`}><Pencil size={14} /></Button>
                    <Button variant="ghost" size="sm" onClick={() => remove(c)} className="text-red-500" data-testid={`delete-category-${c.id}`}><Trash2 size={14} /></Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editing ? 'Modifier' : 'Nouvelle'} nature de depense</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="form-label">Code (4 chiffres)</label>
                <Input value={form.code} onChange={e => setForm({...form, code: e.target.value})} placeholder="0001" data-testid="category-code-input" />
              </div>
              <div>
                <label className="form-label">Code TVA</label>
                <Input value={form.vat_code} onChange={e => setForm({...form, vat_code: e.target.value})} placeholder="A1 (21%), A2 (6%), A4 (0%)" data-testid="category-vat-input" />
              </div>
            </div>
            <div>
              <label className="form-label">Nom de la nature *</label>
              <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="Ex: Entretien ascenseur cage A" data-testid="category-name-input" />
            </div>
            <div>
              <label className="form-label">Compte PCMN (classe 6 ou 7) *</label>
              <Popover open={accountPopoverOpen} onOpenChange={setAccountPopoverOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" role="combobox" className="w-full justify-between font-normal" data-testid="account-lookup-btn">
                    {selectedAcc ? (
                      <span className="truncate"><span className="font-mono">{selectedAcc.number}</span> - {selectedAcc.name}</span>
                    ) : (
                      <span className="text-slate-400">Rechercher un compte par nom...</span>
                    )}
                    <Search size={14} className="ml-2 opacity-50" />
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[450px] p-0">
                  <Command>
                    <CommandInput placeholder="Tapez le nom du compte..." data-testid="account-lookup-input" />
                    <CommandList>
                      <CommandEmpty>Aucun compte trouve</CommandEmpty>
                      <CommandGroup>
                        {pcmnAccounts.map(a => (
                          <CommandItem key={a.number} value={`${a.number} ${a.name}`} onSelect={() => { setForm({...form, account_number: a.number}); setAccountPopoverOpen(false); }} data-testid={`account-option-${a.number}`}>
                            <span className="font-mono text-xs mr-2">{a.number}</span>
                            <span>{a.name}</span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
              <p className="text-[11px] text-slate-500 mt-1">Un compte ne peut etre lie qu&apos;a UNE seule nature (relation 1:1)</p>
            </div>
            <div>
              <label className="form-label">Description (optionnel)</label>
              <Input value={form.description} onChange={e => setForm({...form, description: e.target.value})} placeholder="Notes internes" />
            </div>

            {/* Repartition par defaut occupant / proprietaire */}
            <div className="rounded-md border border-amber-200 bg-amber-50/40 p-3 space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-amber-700 flex items-center gap-1.5">
                Repartition par defaut occupant / proprietaire
              </div>
              <p className="text-[11px] text-slate-600">
                Pour le decompte locataire annuel. La somme doit etre 100%. Sera utilise par defaut quand vous comptabilisez une facture de ce compte.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="form-label text-xs">% Occupant (locataire)</label>
                  <Input type="number" min={0} max={100} step={1}
                    value={form.default_occupant_pct}
                    onChange={e => setOccupant(e.target.value)}
                    data-testid="cat-occupant-pct"
                  />
                </div>
                <div>
                  <label className="form-label text-xs">% Proprietaire</label>
                  <Input type="number" min={0} max={100} step={1}
                    value={form.default_proprietaire_pct}
                    onChange={e => setProprietaire(e.target.value)}
                    data-testid="cat-proprietaire-pct"
                  />
                </div>
              </div>
              <div className="text-[11px] text-slate-500">
                Exemples : <strong>Chauffage commun</strong> = 100% occupant ; <strong>Toiture</strong> = 100% proprietaire ; <strong>Salaire concierge</strong> = 50/50
              </div>
            </div>

            <div className="flex gap-3 justify-end">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Annuler</Button>
              <Button onClick={save} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="save-category-btn">Enregistrer</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
