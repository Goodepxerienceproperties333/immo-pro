import { useState, useEffect, useCallback } from 'react';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';
import { Search, Plus, Pencil, Trash2, Database, BadgeCheck, Sparkles } from 'lucide-react';

const CLASS_NAMES = {
  1: 'Capitaux propres',
  2: 'Immobilisations',
  3: 'Stocks',
  4: 'Creances & dettes',
  5: 'Tresorerie',
  6: 'Charges',
  7: 'Produits',
};

export default function AccountingPage() {
  const { user, selectedCopro } = useAuth();
  const isManager = ['superadmin', 'admin', 'syndic', 'gestionnaire'].includes(user?.role);

  const [accounts, setAccounts] = useState([]);
  const [search, setSearch] = useState('');
  const [activeClass, setActiveClass] = useState('all');
  const [showOnlyActive, setShowOnlyActive] = useState(false);
  const [showOnlyCustom, setShowOnlyCustom] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ number: '', name: '', class_num: 6, parent: '', type: 'result', active: true });
  const [migrateBusy, setMigrateBusy] = useState(false);

  const load = useCallback(async () => {
    const params = {};
    if (search) params.search = search;
    if (activeClass !== 'all') params.class_num = Number(activeClass);
    const { data } = await api.get('/accounting/pcmn', { params });
    setAccounts(data);
  }, [search, activeClass]);

  useEffect(() => { load(); }, [load]);

  const filtered = accounts.filter(a => {
    if (showOnlyActive && !a.active) return false;
    if (showOnlyCustom && !a.is_custom) return false;
    return true;
  });

  const openCreate = () => {
    if (!selectedCopro) { toast.error('Selectionnez d\'abord une copropriete'); return; }
    setEditing(null);
    setForm({ number: '', name: '', class_num: 6, parent: '', type: 'result', active: true });
    setDialogOpen(true);
  };

  const openEdit = (acc) => {
    setEditing(acc);
    setForm({
      number: acc.number,
      name: acc.name,
      class_num: acc.class_num,
      parent: acc.parent || '',
      type: acc.type,
      active: acc.active,
    });
    setDialogOpen(true);
  };

  const onNumberChange = (val) => {
    const v = val.replace(/\D/g, '');
    const cls = v ? parseInt(v[0], 10) : 6;
    setForm(f => ({
      ...f,
      number: v,
      class_num: cls,
      type: cls <= 5 ? 'balance' : 'result',
    }));
  };

  const save = async () => {
    if (!form.number || !form.name) { toast.error('Numero et libelle obligatoires'); return; }
    try {
      if (editing) {
        await api.put(`/accounting/pcmn/${editing.number}`, {
          name: form.name,
          class_num: form.class_num,
          parent: form.parent || null,
          type: form.type,
          active: form.active,
        });
        toast.success('Compte modifie');
      } else {
        await api.post('/accounting/pcmn', {
          number: form.number,
          name: form.name,
          class_num: form.class_num,
          parent: form.parent || null,
          type: form.type,
          active: form.active,
        });
        toast.success('Compte cree');
      }
      setDialogOpen(false);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const toggleActive = async (acc) => {
    try {
      await api.patch(`/accounting/pcmn/${acc.number}/toggle-active`, { active: !acc.active });
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const remove = async (acc) => {
    if (!window.confirm(`Supprimer le compte ${acc.number} - ${acc.name} ?`)) return;
    try {
      await api.delete(`/accounting/pcmn/${acc.number}`);
      toast.success('Compte supprime');
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const runMigration = async () => {
    if (!window.confirm('Importer le PCMN belge complet (337 comptes) dans toutes les ACPs ? Idempotent.')) return;
    setMigrateBusy(true);
    try {
      const { data } = await api.post('/admin/migrate/pcmn-import');
      toast.success(`Migration OK : +${data.total_added} comptes ajoutes sur ${data.acps.length} ACP`);
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally {
      setMigrateBusy(false);
    }
  };

  return (
    <div data-testid="accounting-page">
      <div className="page-header flex flex-col md:flex-row md:items-center md:justify-between gap-3">
        <div>
          <h1 className="page-title">Plan Comptable PCMN</h1>
          <p className="page-subtitle">Plan Comptable Minimum Normalise - Belgique (337 comptes officiels)</p>
        </div>
        <div className="flex gap-2">
          {user?.role === 'superadmin' && (
            <Button variant="outline" onClick={runMigration} disabled={migrateBusy} data-testid="pcmn-migrate-btn">
              <Database size={16} className="mr-2" />
              {migrateBusy ? 'Import...' : 'Importer PCMN complet'}
            </Button>
          )}
          {isManager && (
            <Button onClick={openCreate} data-testid="pcmn-create-btn">
              <Plus size={16} className="mr-2" />Nouveau compte
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-col sm:flex-row gap-4 mb-4 items-start sm:items-center">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Rechercher un compte..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="pcmn-search"
          />
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Switch checked={showOnlyActive} onCheckedChange={setShowOnlyActive} data-testid="pcmn-filter-active" />
          <span>Actifs uniquement</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Switch checked={showOnlyCustom} onCheckedChange={setShowOnlyCustom} data-testid="pcmn-filter-custom" />
          <span>Comptes personnalises</span>
        </div>
      </div>

      <Tabs value={activeClass} onValueChange={setActiveClass}>
        <TabsList className="mb-4 flex-wrap h-auto gap-1" data-testid="pcmn-class-tabs">
          <TabsTrigger value="all" className="text-xs">Tous</TabsTrigger>
          {Object.entries(CLASS_NAMES).map(([num, name]) => (
            <TabsTrigger key={num} value={num} className="text-xs" data-testid={`pcmn-tab-class-${num}`}>
              Cl. {num} - {name}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value={activeClass} className="mt-0">
          <div className="bg-white rounded-md border border-slate-200 overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-28">Numero</TableHead>
                  <TableHead>Libelle</TableHead>
                  <TableHead className="w-20">Classe</TableHead>
                  <TableHead className="w-24">Type</TableHead>
                  <TableHead className="w-28">Statut</TableHead>
                  <TableHead className="w-20">Actif</TableHead>
                  {isManager && <TableHead className="w-28 text-right">Actions</TableHead>}
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.length === 0 ? (
                  <TableRow><TableCell colSpan={isManager ? 7 : 6} className="text-center py-8 text-slate-400">Aucun compte trouve</TableCell></TableRow>
                ) : filtered.map((acc) => {
                  const indent = (acc.number.length >= 5) ? 'pl-8' : (acc.number.length >= 3 ? 'pl-4' : 'pl-2');
                  return (
                    <TableRow key={`${acc.number}-${acc.copropriete_id || 'g'}`} className="hover:bg-slate-50/50" data-testid={`pcmn-row-${acc.number}`}>
                      <TableCell className={`font-mono text-sm font-semibold text-slate-900 ${indent}`}>{acc.number}</TableCell>
                      <TableCell className={`text-slate-700 ${acc.number.length >= 4 ? 'text-sm' : 'font-medium'}`}>{acc.name}</TableCell>
                      <TableCell><Badge variant="outline" className="text-xs font-mono">{acc.class_num}</Badge></TableCell>
                      <TableCell>
                        <Badge className={acc.type === 'balance' ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-green-50 text-green-700 border-green-200'} variant="outline">
                          {acc.type === 'balance' ? 'Bilan' : 'Resultat'}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {acc.is_tier_account ? (
                          <Badge variant="outline" className="bg-purple-50 text-purple-700 border-purple-200 text-xs">
                            <BadgeCheck size={10} className="mr-1" />Tiers auto
                          </Badge>
                        ) : acc.is_custom ? (
                          <Badge variant="outline" className="bg-amber-50 text-amber-700 border-amber-200 text-xs">
                            <Sparkles size={10} className="mr-1" />Custom
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="bg-slate-50 text-slate-600 border-slate-200 text-xs">Officiel</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        {isManager ? (
                          <Switch
                            checked={!!acc.active}
                            onCheckedChange={() => toggleActive(acc)}
                            data-testid={`pcmn-toggle-${acc.number}`}
                          />
                        ) : (
                          acc.active ? <Badge className="bg-green-50 text-green-700 border-green-200" variant="outline">Oui</Badge>
                                     : <Badge variant="outline" className="text-slate-400">Non</Badge>
                        )}
                      </TableCell>
                      {isManager && (
                        <TableCell className="text-right">
                          <Button variant="ghost" size="sm" onClick={() => openEdit(acc)} data-testid={`pcmn-edit-${acc.number}`}>
                            <Pencil size={14} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => remove(acc)}
                            disabled={acc.is_tier_account}
                            title={acc.is_tier_account ? 'Compte tiers auto: supprimer le tiers d\'abord' : 'Supprimer'}
                            data-testid={`pcmn-delete-${acc.number}`}
                          >
                            <Trash2 size={14} className="text-red-500" />
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <div className="mt-2 text-xs text-slate-400">{filtered.length} compte(s) affiche(s) sur {accounts.length}</div>
        </TabsContent>
      </Tabs>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md" data-testid="pcmn-dialog">
          <DialogHeader>
            <DialogTitle>{editing ? `Modifier ${editing.number}` : 'Nouveau compte PCMN'}</DialogTitle>
            <DialogDescription>
              {editing
                ? 'Modifier le libelle, la classe ou le statut du compte.'
                : 'Creer un compte comptable personnalise pour cette copropriete.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label htmlFor="num">Numero du compte *</Label>
              <Input
                id="num"
                value={form.number}
                onChange={e => onNumberChange(e.target.value)}
                disabled={!!editing}
                placeholder="Ex: 612999"
                className="font-mono"
                data-testid="pcmn-input-number"
              />
              <p className="text-xs text-slate-500 mt-1">
                Classe deduite du 1er chiffre : 1-5 = Bilan, 6-7 = Resultat
              </p>
            </div>
            <div>
              <Label htmlFor="name">Libelle *</Label>
              <Input
                id="name"
                value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Ex: Frais speciaux ascenseur Bat. A"
                data-testid="pcmn-input-name"
              />
            </div>
            <div>
              <Label htmlFor="parent">Compte parent (optionnel)</Label>
              <Input
                id="parent"
                value={form.parent}
                onChange={e => setForm(f => ({ ...f, parent: e.target.value }))}
                placeholder="Ex: 6120 (ce compte est un sous-compte de 6120)"
                className="font-mono"
                data-testid="pcmn-input-parent"
              />
            </div>
            <div className="flex items-center gap-2">
              <Switch
                checked={form.active}
                onCheckedChange={v => setForm(f => ({ ...f, active: v }))}
                data-testid="pcmn-input-active"
              />
              <Label>Compte actif (visible dans les selecteurs)</Label>
            </div>
            <div className="flex gap-3 text-xs text-slate-500">
              <span>Classe deduite : <b className="text-slate-700">{form.class_num}</b></span>
              <span>Type : <b className="text-slate-700">{form.type === 'balance' ? 'Bilan' : 'Resultat'}</b></span>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} data-testid="pcmn-cancel-btn">Annuler</Button>
            <Button onClick={save} data-testid="pcmn-save-btn">Enregistrer</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
