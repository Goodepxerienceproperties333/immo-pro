import { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import api from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AlertTriangle, RefreshCw, Users, Building2, Shield, ChevronLeft,
  CheckCircle2, Loader2, Hash, Merge,
} from 'lucide-react';
import { toast } from 'sonner';
import { fmtDate } from '@/lib/dateFmt';

const TAB_LABELS = {
  suppliers: { label: 'Fournisseurs', icon: Building2, color: 'text-blue-600' },
  owners: { label: 'Proprietaires', icon: Users, color: 'text-emerald-600' },
  users: { label: 'Utilisateurs', icon: Shield, color: 'text-amber-600' },
};

export default function AdminDuplicatesPage() {
  const { isSuperadmin, isAdmin, user } = useAuth();
  const [tab, setTab] = useState('suppliers');
  const [copros, setCopros] = useState([]);
  const [copropro, setCoproId] = useState(''); // '' = toutes mes ACPs
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState({ groups: [], total: 0, scope: '' });
  // Selection par groupe : { [groupIdx]: keepMemberId }
  const [picks, setPicks] = useState({});
  const [merging, setMerging] = useState(null); // groupIdx en cours de fusion

  // Charge la liste des ACPs accessibles au user
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get('/coproprietes');
        if (!cancelled) setCopros(data || []);
      } catch (e) {
        if (!cancelled) setCopros([]);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setPicks({});
    try {
      const params = {};
      if (copropro) params.copropriete_id = copropro;
      const { data: r } = await api.get(`/admin/duplicates/${tab}`, { params });
      const total = r.total_suppliers || r.total_owners || r.total_users || 0;
      setData({ groups: r.groups || [], total, scope: r.scope || '' });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur lors du scan des doublons');
      setData({ groups: [], total: 0, scope: '' });
    } finally {
      setLoading(false);
    }
  }, [tab, copropro]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (!isAdmin) {
    return (
      <div className="max-w-2xl mx-auto mt-8">
        <div className="bg-amber-50 border border-amber-200 rounded-md p-6 text-center">
          <Shield size={40} className="mx-auto text-amber-600 mb-3" />
          <h2 className="text-lg font-semibold text-slate-900 mb-2">Acces reserve a l&apos;administration</h2>
        </div>
      </div>
    );
  }

  const setKeep = (groupIdx, memberId) => {
    setPicks(p => ({ ...p, [groupIdx]: memberId }));
  };

  const doMerge = async (groupIdx, group) => {
    const keepId = picks[groupIdx];
    if (!keepId) {
      toast.error('Selectionnez l\'entree a CONSERVER avant la fusion');
      return;
    }
    const removeIds = group.members.filter(m => m.id !== keepId).map(m => m.id);
    if (!removeIds.length) {
      toast.error('Aucune entree a fusionner');
      return;
    }
    if (tab === 'users') {
      toast.info('La fusion des utilisateurs doit etre faite manuellement depuis la page Utilisateurs (auth-critique).');
      return;
    }
    const url = tab === 'suppliers' ? '/suppliers/merge' : '/admin/duplicates/owners/merge';
    setMerging(groupIdx);
    try {
      const { data: r } = await api.post(url, { keep_id: keepId, remove_ids: removeIds });
      toast.success(r.message || 'Fusion effectuee', {
        description: tab === 'suppliers'
          ? `${r.invoices_migrated || 0} facture(s) migree(s), ${r.bank_transactions_migrated || 0} transaction(s) bancaires`
          : `Lots simples: ${r.lots_simple_updated || 0}, lots multi: ${r.lots_multi_updated || 0}, mutations: ${(r.mutations_from_migrated || 0) + (r.mutations_to_migrated || 0)}`,
        duration: 8000,
      });
      // Recharge la detection
      await fetchData();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Echec de la fusion');
    } finally {
      setMerging(null);
    }
  };

  const renderSupplierMember = (m, isKeep, onPick) => (
    <div
      className={`relative border rounded-md p-3 cursor-pointer transition hover:border-blue-400 ${
        isKeep ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 bg-white'
      }`}
      onClick={onPick}
      data-testid={`dup-supplier-card-${m.id}`}
    >
      {isKeep && (
        <div className="absolute top-1 right-1 text-[10px] font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded-full flex items-center gap-1">
          <CheckCircle2 size={10} /> A CONSERVER
        </div>
      )}
      <div className="font-semibold text-sm text-slate-900 truncate pr-20">{m.name || '(sans nom)'}</div>
      <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
        {m.bce_number && <div>BCE: <span className="font-mono">{m.bce_number}</span></div>}
        {m.vat_number && m.vat_number !== m.bce_number && <div>TVA: <span className="font-mono">{m.vat_number}</span></div>}
        {m.iban && <div>IBAN: <span className="font-mono text-[10px]">{m.iban}</span></div>}
        {m.city && <div>Ville: {m.city}</div>}
        {m.email && <div className="truncate">{m.email}</div>}
        <div className="text-[10px] text-slate-400 mt-1">
          Cree {m.created_at ? fmtDate(m.created_at) : '-'} - id <span className="font-mono">{(m.id || '').slice(0, 8)}</span>
        </div>
        {(m.tier_accounts || []).length > 0 && (
          <div className="text-[10px] text-blue-600 mt-1">
            <Hash size={8} className="inline" /> {m.tier_accounts.length} ACP rattachee(s)
          </div>
        )}
      </div>
    </div>
  );

  const renderOwnerMember = (m, isKeep, onPick) => (
    <div
      className={`relative border rounded-md p-3 cursor-pointer transition hover:border-blue-400 ${
        isKeep ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 bg-white'
      }`}
      onClick={onPick}
      data-testid={`dup-owner-card-${m.id}`}
    >
      {isKeep && (
        <div className="absolute top-1 right-1 text-[10px] font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded-full flex items-center gap-1">
          <CheckCircle2 size={10} /> A CONSERVER
        </div>
      )}
      <div className="font-semibold text-sm text-slate-900 truncate pr-20">{m.name || '(sans nom)'}</div>
      <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
        {m.email && <div className="truncate">{m.email}</div>}
        {m.phone && <div>Tel: <span className="font-mono text-[10px]">{m.phone}</span></div>}
        {m.bce_number && <div>BCE: <span className="font-mono">{m.bce_number}</span></div>}
        {m.iban && <div>IBAN: <span className="font-mono text-[10px]">{m.iban}</span></div>}
        {(m.address || m.city) && (
          <div className="truncate">{[m.address, m.postal_code, m.city].filter(Boolean).join(' ')}</div>
        )}
        {m.vcs_code && <div>VCS: <span className="font-mono">{m.vcs_code}</span></div>}
        <div className="text-[10px] text-slate-400 mt-1 flex items-center gap-2">
          <span>Cree {m.created_at ? fmtDate(m.created_at) : '-'}</span>
          <span className={m.lots_count > 0 ? 'text-blue-600 font-semibold' : 'text-amber-600'}>
            {m.lots_count} lot{m.lots_count > 1 ? 's' : ''}
          </span>
        </div>
      </div>
    </div>
  );

  const renderUserMember = (m, isKeep, onPick) => (
    <div
      className={`relative border rounded-md p-3 ${isKeep ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 bg-white'}`}
      onClick={onPick}
      data-testid={`dup-user-card-${m.id}`}
    >
      {isKeep && (
        <div className="absolute top-1 right-1 text-[10px] font-bold text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded-full flex items-center gap-1">
          <CheckCircle2 size={10} /> A CONSERVER
        </div>
      )}
      <div className="font-semibold text-sm text-slate-900 truncate pr-20">{m.name || '(sans nom)'}</div>
      <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
        <div className="truncate">{m.email}</div>
        <div>Role: <Badge variant="outline" className="text-[10px]">{m.role}</Badge></div>
        <div>{(m.copropriete_ids || []).length} ACP rattachee(s)</div>
        {m.is_suspended && <Badge variant="destructive" className="text-[10px]">Suspendu</Badge>}
        <div className="text-[10px] text-slate-400 mt-1">Cree {m.created_at ? fmtDate(m.created_at) : '-'}</div>
      </div>
    </div>
  );

  const renderMember = (m, groupIdx, isKeep) => {
    const onPick = () => setKeep(groupIdx, m.id);
    if (tab === 'suppliers') return renderSupplierMember(m, isKeep, onPick);
    if (tab === 'owners') return renderOwnerMember(m, isKeep, onPick);
    return renderUserMember(m, isKeep, onPick);
  };

  const Icon = TAB_LABELS[tab].icon;

  return (
    <div className="space-y-6" data-testid="admin-duplicates-page">
      <div className="page-header flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/admin" className="text-slate-400 hover:text-slate-600" data-testid="back-to-admin">
            <ChevronLeft size={20} />
          </Link>
          <div className="w-10 h-10 rounded-md bg-gradient-to-br from-amber-500 to-red-600 flex items-center justify-center text-white">
            <Merge size={20} />
          </div>
          <div>
            <h1 className="page-title">Detection et fusion de doublons</h1>
            <p className="page-subtitle">
              Identifie automatiquement les entites en doublon dans votre perimetre (chinese wall applique).
            </p>
          </div>
        </div>
      </div>

      {/* Filter bar */}
      <Card>
        <CardContent className="p-4 flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <label className="form-label text-xs">Copropriete (ACP)</label>
            <Select value={copropro || '__all__'} onValueChange={v => setCoproId(v === '__all__' ? '' : v)}>
              <SelectTrigger className="h-9" data-testid="dup-acp-filter">
                <SelectValue placeholder="Toutes mes ACPs" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">
                  Toutes mes ACPs ({copros.length})
                </SelectItem>
                {copros.map(c => (
                  <SelectItem key={c.id} value={c.id} data-testid={`dup-acp-option-${c.id}`}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end gap-2">
            <Button variant="outline" size="sm" onClick={fetchData} disabled={loading} data-testid="dup-refresh-btn">
              {loading ? <Loader2 size={14} className="mr-1 animate-spin" /> : <RefreshCw size={14} className="mr-1" />}
              Actualiser
            </Button>
          </div>
          {data.total > 0 && (
            <div className="text-xs text-slate-500 ml-2">
              {data.total} {tab === 'suppliers' ? 'fournisseur(s)' : tab === 'owners' ? 'proprietaire(s)' : 'utilisateur(s)'} dans le scope
              {data.scope === 'platform' && <Badge variant="outline" className="ml-2 text-[10px] border-amber-400 text-amber-700">Plateforme</Badge>}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Tabs */}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          {Object.entries(TAB_LABELS).map(([key, cfg]) => {
            if (key === 'users' && !isSuperadmin) return null;
            const TabIcon = cfg.icon;
            return (
              <TabsTrigger key={key} value={key} data-testid={`dup-tab-${key}`}>
                <TabIcon size={14} className={`mr-1 ${cfg.color}`} />
                {cfg.label}
              </TabsTrigger>
            );
          })}
        </TabsList>

        <TabsContent value={tab} className="mt-4">
          {loading ? (
            <div className="text-center py-12 text-slate-500">
              <Loader2 size={28} className="animate-spin mx-auto mb-2" />
              Scan en cours...
            </div>
          ) : data.groups.length === 0 ? (
            <Card>
              <CardContent className="p-12 text-center text-slate-500">
                <CheckCircle2 size={40} className="mx-auto mb-3 text-emerald-500" />
                <div className="text-base font-medium text-slate-700">Aucun doublon detecte</div>
                <p className="text-xs mt-2">
                  Votre base est propre pour les <b>{TAB_LABELS[tab].label.toLowerCase()}</b>
                  {copropro && ' dans cette ACP'}.
                </p>
              </CardContent>
            </Card>
          ) : (
            <>
              <div className="mb-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 flex items-start gap-2">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <div>
                  <b>{data.groups.length} groupe{data.groups.length > 1 ? 's' : ''} de doublons detecte{data.groups.length > 1 ? 's' : ''}</b>
                  {' - '}Selectionnez la fiche a CONSERVER dans chaque groupe puis cliquez sur Fusionner.
                  Les autres fiches seront absorbees, leurs donnees enrichies dans la fiche conservee,
                  et toutes les references (factures, lots, mutations, paiements) reaffectees automatiquement.
                </div>
              </div>

              <div className="space-y-4">
                {data.groups.map((g, idx) => {
                  const keepId = picks[idx];
                  const canMerge = !!keepId && tab !== 'users';
                  const isMerging = merging === idx;
                  return (
                    <Card key={idx} data-testid={`dup-group-${idx}`}>
                      <CardHeader className="pb-3">
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-sm flex items-center gap-2">
                            <Icon size={16} className={TAB_LABELS[tab].color} />
                            Groupe #{idx + 1} - {g.members.length} entrees
                            <div className="flex gap-1 ml-2">
                              {g.match_on.map(reason => (
                                <Badge key={reason} variant="outline" className="text-[10px]">
                                  match: {reason}
                                </Badge>
                              ))}
                            </div>
                          </CardTitle>
                          <div className="flex items-center gap-2">
                            {tab !== 'users' ? (
                              <Button
                                size="sm"
                                disabled={!canMerge || isMerging}
                                onClick={() => doMerge(idx, g)}
                                className="bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50"
                                data-testid={`dup-merge-btn-${idx}`}
                              >
                                {isMerging ? (
                                  <><Loader2 size={12} className="mr-1 animate-spin" /> Fusion...</>
                                ) : (
                                  <><Merge size={12} className="mr-1" /> Fusionner ({g.members.length - 1} a absorber)</>
                                )}
                              </Button>
                            ) : (
                              <Link to="/admin/users" className="text-xs text-blue-600 hover:underline">
                                Gerer manuellement dans Utilisateurs
                              </Link>
                            )}
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                          {g.members.map(m => renderMember(m, idx, m.id === keepId))}
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
