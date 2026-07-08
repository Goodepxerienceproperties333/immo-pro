import { useState, useEffect } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { toast } from 'sonner';
import { LogOut, Home, Wallet, FileText, Receipt, Megaphone, Building2, User, AlertCircle, CheckCircle2, ArrowDownToLine, Copy, UserCog, Users, Plus, Pencil, Trash2, Save } from 'lucide-react';

const fmt = (n) => new Intl.NumberFormat('fr-BE', { style: 'currency', currency: 'EUR' }).format(n || 0);
const fmtDate = (s) => s ? new Date(s).toLocaleDateString('fr-BE') : '-';

export default function OwnerPortalPage() {
  const { user, logout } = useAuth();
  const [dashboard, setDashboard] = useState(null);
  const [coproprietes, setCoproprietes] = useState([]);
  const [fundCalls, setFundCalls] = useState([]);
  const [charges, setCharges] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [selectedAcp, setSelectedAcp] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // iter89 : profil editable + tenants self-service
  const [profileForm, setProfileForm] = useState(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [tenants, setTenants] = useState([]);
  const [myLots, setMyLots] = useState([]);
  const [tenantDialog, setTenantDialog] = useState(false);
  const [editingTenant, setEditingTenant] = useState(null);
  const [tenantForm, setTenantForm] = useState({ name: '', email: '', phone: '', lot_id: '', lease_start: '', lease_end: '', rent_amount: 0 });

  const loadTenants = async () => {
    try {
      const { data } = await api.get('/owner/tenants');
      setTenants(data.tenants || []);
      setMyLots(data.lots || []);
    } catch { /* silent : pas de droit ou pas de lot */ }
  };

  useEffect(() => {
    (async () => {
      try {
        const [dash, copros, fc, inv, docs] = await Promise.all([
          api.get('/owner/dashboard'),
          api.get('/owner/coproprietes'),
          api.get('/owner/fund-calls'),
          api.get('/owner/invoices'),
          api.get('/owner/documents'),
        ]);
        setDashboard(dash.data);
        setCoproprietes(copros.data);
        setFundCalls(fc.data);
        setCharges(inv.data);
        setDocuments(docs.data);
        // iter89 : init profile form from owner data
        const o = dash.data?.owner || {};
        setProfileForm({
          first_name: o.first_name || '', last_name: o.last_name || '',
          address: o.address || '', postal_code: o.postal_code || '',
          city: o.city || '', country: o.country || 'Belgique',
          email: o.email || '', email2: o.email2 || '',
          phone: o.phone || '', phone2: o.phone2 || '',
        });
        await loadTenants();
      } catch (err) {
        setError(err.response?.data?.detail || 'Erreur de chargement');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleLogout = async () => { await logout(); window.location.href = '/login'; };

  const copyVcs = (vcs) => {
    navigator.clipboard.writeText(vcs);
    toast.success('VCS copie dans le presse-papier');
  };

  // iter89 : sauvegarde du profil + notification syndic automatique
  const saveProfile = async () => {
    setSavingProfile(true);
    try {
      const { data } = await api.put('/owner/me', profileForm);
      if (data.updated) {
        toast.success('Coordonnees mises a jour - votre syndic a ete averti par email');
        // Reload dashboard pour rafraichir l'identite affichee
        const dash = await api.get('/owner/dashboard');
        setDashboard(dash.data);
      } else {
        toast.info('Aucune modification a enregistrer');
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la sauvegarde');
    } finally {
      setSavingProfile(false);
    }
  };

  // iter89 : CRUD locataires self-service
  const openCreateTenant = () => {
    setEditingTenant(null);
    setTenantForm({ name: '', email: '', phone: '', lot_id: myLots[0]?.id || '', lease_start: '', lease_end: '', rent_amount: 0 });
    setTenantDialog(true);
  };
  const openEditTenant = (t) => {
    setEditingTenant(t);
    setTenantForm({
      name: t.name || '', email: t.email || '', phone: t.phone || '',
      lot_id: t.lot_id || '', lease_start: t.lease_start || '',
      lease_end: t.lease_end || '', rent_amount: t.rent_amount || 0,
    });
    setTenantDialog(true);
  };
  const saveTenant = async () => {
    if (!tenantForm.name?.trim()) { toast.error('Nom obligatoire'); return; }
    if (!tenantForm.lot_id) { toast.error('Lot obligatoire'); return; }
    try {
      if (editingTenant) {
        await api.put(`/owner/tenants/${editingTenant.id}`, tenantForm);
        toast.success('Locataire modifie - syndic averti');
      } else {
        await api.post('/owner/tenants', tenantForm);
        toast.success('Locataire ajoute - syndic averti');
      }
      setTenantDialog(false);
      await loadTenants();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };
  const deleteTenant = async (t) => {
    if (!window.confirm(`Supprimer le locataire "${t.name}" ?`)) return;
    try {
      await api.delete(`/owner/tenants/${t.id}`);
      toast.success('Locataire supprime - syndic averti');
      await loadTenants();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  if (loading) return <div className="min-h-screen flex items-center justify-center text-slate-500">Chargement de votre espace...</div>;

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-slate-50">
        <Card className="max-w-md border-orange-200">
          <CardContent className="p-6 text-center">
            <AlertCircle size={36} className="mx-auto text-orange-500 mb-3" />
            <h2 className="font-semibold text-slate-900 mb-2" style={{fontFamily:'Chivo,sans-serif'}}>Acces impossible</h2>
            <p className="text-sm text-slate-600 mb-4">{error}</p>
            <Button onClick={handleLogout} variant="outline">Se deconnecter</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const owner = dashboard?.owner || {};
  const stats = dashboard?.stats || {};
  const filteredFundCalls = selectedAcp === 'all' ? fundCalls : fundCalls.filter(fc => fc.copropriete_id === selectedAcp);
  const filteredCharges = selectedAcp === 'all' ? charges : charges.filter(c => c.copropriete_id === selectedAcp);
  const filteredDocs = selectedAcp === 'all' ? documents : documents.filter(d => d.copropriete_id === selectedAcp);

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-md bg-[#2563EB] text-white flex items-center justify-center text-xs font-bold" style={{fontFamily:'Chivo,sans-serif'}}>CP</div>
            <div>
              <h1 className="text-base font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>Espace proprietaire</h1>
              <p className="text-[11px] text-slate-500">{owner.name}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={handleLogout} data-testid="logout-btn" className="text-slate-500"><LogOut size={14} className="mr-1.5" /> Deconnexion</Button>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">

        {/* Owner identity card */}
        <Card className="mb-6 border-slate-200 bg-gradient-to-br from-white to-slate-50" data-testid="owner-id-card">
          <CardContent className="p-5">
            <div className="flex items-start justify-between flex-wrap gap-4">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-full bg-[#2563EB]/10 flex items-center justify-center text-[#2563EB]">
                  <User size={22} />
                </div>
                <div>
                  <h2 className="font-semibold text-slate-900" style={{fontFamily:'Chivo,sans-serif'}}>{owner.name}</h2>
                  <p className="text-xs text-slate-500">{owner.email}{owner.phone ? ` - ${owner.phone}` : ''}</p>
                </div>
              </div>
              {owner.vcs_code && (
                <div className="text-right">
                  <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-0.5">Communication structuree (VCS)</div>
                  <button onClick={() => copyVcs(owner.vcs_code)} className="font-mono text-sm text-[#2563EB] hover:bg-blue-50 px-2 py-1 rounded inline-flex items-center gap-1.5" data-testid="copy-vcs-btn">
                    {owner.vcs_code}
                    <Copy size={11} />
                  </button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Stats */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
          <StatCard icon={<Building2 size={16}/>} label="Coproprietes" value={stats.coproprietes_count || 0} />
          <StatCard icon={<Home size={16}/>} label="Lots" value={stats.lots_count || 0} />
          <StatCard icon={<Wallet size={16}/>} label="Total appele" value={fmt(stats.total_called)} />
          <StatCard
            icon={stats.status === 'debiteur' ? <AlertCircle size={16}/> : <CheckCircle2 size={16}/>}
            label="Solde"
            value={fmt(stats.balance)}
            highlight={stats.status === 'debiteur' ? 'red' : (stats.status === 'crediteur' ? 'green' : 'neutral')}
            badge={stats.status}
          />
        </div>

        {/* Pending alerts */}
        {dashboard?.pending_calls?.length > 0 && (
          <Card className="border-orange-200 bg-orange-50/40 mb-6" data-testid="pending-calls-alert">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-2">
                <AlertCircle size={16} className="text-orange-600" />
                <span className="font-semibold text-orange-900 text-sm">Appels en attente de paiement ({dashboard.pending_calls.length})</span>
              </div>
              <div className="space-y-1.5">
                {dashboard.pending_calls.map((p, i) => (
                  <div key={i} className="flex items-center justify-between text-sm bg-white rounded px-3 py-2 border border-orange-100">
                    <div>
                      <span className="font-medium text-slate-900">{p.fund_call_name}</span>
                      <span className="text-xs text-slate-500 ml-2">Echeance: {fmtDate(p.due_date)}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-orange-700">{fmt(p.amount)}</span>
                      <button onClick={() => copyVcs(p.vcs_code)} className="font-mono text-[10px] text-[#2563EB] bg-blue-50 hover:bg-blue-100 px-2 py-0.5 rounded inline-flex items-center gap-1" title="Copier VCS pour le virement">
                        {p.vcs_code}<Copy size={9} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* ACP selector + Tabs */}
        <Tabs defaultValue="coproprietes" className="space-y-4">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <TabsList>
              <TabsTrigger value="coproprietes" data-testid="tab-coproprietes"><Building2 size={14} className="mr-1.5" /> Mes coproprietes</TabsTrigger>
              <TabsTrigger value="fund-calls" data-testid="tab-fund-calls"><Megaphone size={14} className="mr-1.5" /> Appels de fonds</TabsTrigger>
              <TabsTrigger value="charges" data-testid="tab-charges"><Receipt size={14} className="mr-1.5" /> Charges</TabsTrigger>
              <TabsTrigger value="documents" data-testid="tab-documents"><FileText size={14} className="mr-1.5" /> Documents</TabsTrigger>
              <TabsTrigger value="profile" data-testid="tab-profile"><UserCog size={14} className="mr-1.5" /> Mon profil</TabsTrigger>
              <TabsTrigger value="tenants" data-testid="tab-tenants"><Users size={14} className="mr-1.5" /> Mes locataires</TabsTrigger>
            </TabsList>
            {coproprietes.length > 1 && (
              <select value={selectedAcp} onChange={e => setSelectedAcp(e.target.value)} className="text-sm border border-slate-200 rounded-md px-3 py-1.5 bg-white">
                <option value="all">Toutes les coproprietes</option>
                {coproprietes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            )}
          </div>

          <TabsContent value="coproprietes" className="mt-0">
            {coproprietes.length === 0 ? (
              <Card><CardContent className="p-8 text-center text-slate-400">Aucune copropriete liee a votre compte</CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {coproprietes.map(c => (
                  <Card key={c.id} className="border-slate-200" data-testid={`copro-card-${c.id}`}>
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between">
                        <div>
                          <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>{c.name}</CardTitle>
                          <p className="text-xs text-slate-500 mt-0.5">{c.reference}</p>
                        </div>
                        <Badge variant="outline" className="text-[10px]">{c.my_total_quotity} tantiemes</Badge>
                      </div>
                    </CardHeader>
                    <CardContent className="pt-2">
                      <p className="text-xs text-slate-600 mb-2">{c.address}, {c.postal_code} {c.city}</p>
                      <div className="space-y-1 mb-3">
                        <div className="text-[11px] uppercase tracking-wider text-slate-400">Vos lots ({c.my_lots.length})</div>
                        {c.my_lots.map(l => (
                          <div key={l.id} className="text-xs flex items-center justify-between border-l-2 border-[#2563EB]/30 pl-2 py-0.5">
                            <span className="text-slate-700">Lot {l.number} {l.description ? `- ${l.description}` : ''}</span>
                            <span className="font-mono text-slate-500">{l.quotity}</span>
                          </div>
                        ))}
                      </div>
                      <a
                        href={`${process.env.REACT_APP_BACKEND_URL}/api/owner/decompte/pdf?copropriete_id=${c.id}`}
                        target="_blank" rel="noreferrer"
                        className="inline-flex items-center gap-1.5 text-xs text-[#2563EB] hover:bg-blue-50 px-3 py-1.5 rounded-md border border-[#2563EB]/20"
                        data-testid={`download-decompte-${c.id}`}
                      >
                        <ArrowDownToLine size={12} />
                        Telecharger mon decompte annuel (PDF)
                      </a>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          <TabsContent value="fund-calls" className="mt-0">
            <Card><CardContent className="p-0">
              {filteredFundCalls.length === 0 ? (
                <div className="p-8 text-center text-slate-400">Aucun appel de fonds</div>
              ) : (
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Date</TableHead><TableHead>Nom</TableHead><TableHead>Echeance</TableHead>
                    <TableHead className="text-right">Montant</TableHead><TableHead>VCS</TableHead><TableHead>Statut</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {filteredFundCalls.map(fc => (
                      <TableRow key={fc.id} data-testid={`fc-row-${fc.id}`}>
                        <TableCell className="text-xs">{fmtDate(fc.date)}</TableCell>
                        <TableCell className="text-sm font-medium">{fc.name}</TableCell>
                        <TableCell className="text-xs">{fmtDate(fc.due_date)}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{fmt(fc.my_amount)}</TableCell>
                        <TableCell><button onClick={() => copyVcs(fc.vcs_code)} className="font-mono text-[10px] text-[#2563EB] hover:underline inline-flex items-center gap-1">{fc.vcs_code}<Copy size={9}/></button></TableCell>
                        <TableCell>
                          {fc.paid ? <Badge className="bg-green-100 text-green-700 border-0 text-[10px]">Paye</Badge>
                            : <Badge className="bg-orange-100 text-orange-700 border-0 text-[10px]">A payer</Badge>}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="charges" className="mt-0">
            <Card><CardContent className="p-0">
              {filteredCharges.length === 0 ? (
                <div className="p-8 text-center text-slate-400">Aucune charge enregistree</div>
              ) : (
                <Table>
                  <TableHeader><TableRow>
                    <TableHead>Date</TableHead><TableHead>Fournisseur</TableHead><TableHead>Description</TableHead>
                    <TableHead className="text-right">Total facture</TableHead><TableHead className="text-right">Votre part</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {filteredCharges.map(c => (
                      <TableRow key={c.id} data-testid={`charge-row-${c.id}`}>
                        <TableCell className="text-xs">{fmtDate(c.date)}</TableCell>
                        <TableCell className="text-sm font-medium">{c.supplier}</TableCell>
                        <TableCell className="text-xs text-slate-600 max-w-xs truncate">{c.description}</TableCell>
                        <TableCell className="text-right font-mono text-xs text-slate-400">{fmt(c.total_amount)}</TableCell>
                        <TableCell className="text-right font-mono text-sm text-slate-900 font-semibold">{fmt(c.my_amount)}</TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="bg-slate-50 font-semibold">
                      <TableCell colSpan={4} className="text-right">Total votre quote-part:</TableCell>
                      <TableCell className="text-right font-mono">{fmt(filteredCharges.reduce((s,c) => s + c.my_amount, 0))}</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              )}
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="documents" className="mt-0">
            {filteredDocs.length === 0 ? (
              <Card><CardContent className="p-8 text-center text-slate-400">Aucun document partage</CardContent></Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {filteredDocs.map(d => (
                  <Card key={d.id} className="border-slate-200 hover:shadow-sm transition-shadow" data-testid={`doc-card-${d.id}`}>
                    <CardContent className="p-3">
                      <div className="flex items-start gap-2 mb-1">
                        <FileText size={14} className="text-[#2563EB] mt-0.5 flex-shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium text-slate-900 truncate">{d.title}</div>
                          {d.description && <div className="text-[11px] text-slate-500 line-clamp-2">{d.description}</div>}
                        </div>
                      </div>
                      <div className="flex items-center justify-between mt-2">
                        <Badge variant="outline" className="text-[10px]">{d.category_name || 'Sans categorie'}</Badge>
                        {d.filename && (
                          <a href={`${process.env.REACT_APP_BACKEND_URL}/api/documents/${d.id}/download`} target="_blank" rel="noreferrer" className="text-[#2563EB] hover:bg-blue-50 p-1 rounded" title="Telecharger">
                            <ArrowDownToLine size={13} />
                          </a>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* iter89 : Mon profil - modification self-service */}
          <TabsContent value="profile" className="mt-0">
            <Card data-testid="profile-card">
              <CardHeader>
                <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Mes coordonnees</CardTitle>
                <p className="text-xs text-slate-500">Modifiez vos informations. Toute modification est automatiquement transmise par email a votre syndic.</p>
              </CardHeader>
              <CardContent>
                {profileForm && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Nom</label>
                        <Input value={profileForm.last_name} onChange={e => setProfileForm({...profileForm, last_name: e.target.value})} data-testid="profile-last-name" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Prenom</label>
                        <Input value={profileForm.first_name} onChange={e => setProfileForm({...profileForm, first_name: e.target.value})} data-testid="profile-first-name" />
                      </div>
                    </div>
                    <div>
                      <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Adresse</label>
                      <Input value={profileForm.address} onChange={e => setProfileForm({...profileForm, address: e.target.value})} data-testid="profile-address" />
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Code postal</label>
                        <Input value={profileForm.postal_code} onChange={e => setProfileForm({...profileForm, postal_code: e.target.value})} data-testid="profile-postal" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Ville</label>
                        <Input value={profileForm.city} onChange={e => setProfileForm({...profileForm, city: e.target.value})} data-testid="profile-city" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Pays</label>
                        <Input value={profileForm.country} onChange={e => setProfileForm({...profileForm, country: e.target.value})} data-testid="profile-country" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email principal</label>
                        <Input value={profileForm.email} onChange={e => setProfileForm({...profileForm, email: e.target.value})} data-testid="profile-email" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email secondaire</label>
                        <Input value={profileForm.email2} onChange={e => setProfileForm({...profileForm, email2: e.target.value})} data-testid="profile-email2" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM</label>
                        <Input value={profileForm.phone} onChange={e => setProfileForm({...profileForm, phone: e.target.value})} data-testid="profile-phone" />
                      </div>
                      <div>
                        <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM 2</label>
                        <Input value={profileForm.phone2} onChange={e => setProfileForm({...profileForm, phone2: e.target.value})} data-testid="profile-phone2" />
                      </div>
                    </div>
                    <div className="pt-3 border-t border-slate-100">
                      <Button onClick={saveProfile} disabled={savingProfile} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="profile-save-btn">
                        <Save size={14} className="mr-1.5" />
                        {savingProfile ? 'Enregistrement...' : 'Enregistrer mes modifications'}
                      </Button>
                      <p className="text-[11px] text-slate-500 italic mt-2">
                        Pour modifier votre VCS ou votre code auxiliaire (impacts comptables), contactez directement votre syndic.
                      </p>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* iter89 : Mes locataires - CRUD self-service */}
          <TabsContent value="tenants" className="mt-0">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
                <div>
                  <CardTitle className="text-base" style={{fontFamily:'Chivo,sans-serif'}}>Mes locataires</CardTitle>
                  <p className="text-xs text-slate-500">Toute modification est notifiee a votre syndic par email.</p>
                </div>
                <Button onClick={openCreateTenant} size="sm" className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="add-tenant-btn" disabled={myLots.length === 0}>
                  <Plus size={14} className="mr-1.5" /> Ajouter
                </Button>
              </CardHeader>
              <CardContent className="p-0">
                {tenants.length === 0 ? (
                  <div className="p-8 text-center text-slate-400 text-sm">
                    {myLots.length === 0 ? "Aucun lot enregistre - contactez votre syndic" : "Aucun locataire. Cliquez sur Ajouter pour en creer un."}
                  </div>
                ) : (
                  <Table>
                    <TableHeader><TableRow>
                      <TableHead>Locataire</TableHead><TableHead>Lot</TableHead>
                      <TableHead>Email</TableHead><TableHead>GSM</TableHead>
                      <TableHead>Bail</TableHead><TableHead className="text-right">Loyer</TableHead>
                      <TableHead className="w-24">Actions</TableHead>
                    </TableRow></TableHeader>
                    <TableBody>
                      {tenants.map(t => {
                        const lot = myLots.find(l => l.id === t.lot_id);
                        return (
                          <TableRow key={t.id} data-testid={`tenant-row-${t.id}`}>
                            <TableCell className="font-medium text-sm">{t.name}</TableCell>
                            <TableCell className="text-xs">
                              {lot ? `${lot.number}${lot.description ? ' - ' + lot.description : ''}` : '-'}
                            </TableCell>
                            <TableCell className="text-xs font-mono text-slate-600">{t.email || '-'}</TableCell>
                            <TableCell className="text-xs font-mono text-slate-600">{t.phone || '-'}</TableCell>
                            <TableCell className="text-xs text-slate-600">
                              {t.lease_start ? fmtDate(t.lease_start) : '-'} - {t.lease_end ? fmtDate(t.lease_end) : '-'}
                            </TableCell>
                            <TableCell className="text-right font-mono text-sm">{t.rent_amount ? fmt(t.rent_amount) : '-'}</TableCell>
                            <TableCell>
                              <div className="flex gap-1">
                                <Button variant="ghost" size="sm" onClick={() => openEditTenant(t)} data-testid={`edit-tenant-${t.id}`}><Pencil size={13} /></Button>
                                <Button variant="ghost" size="sm" onClick={() => deleteTenant(t)} className="text-red-500" data-testid={`delete-tenant-${t.id}`}><Trash2 size={13} /></Button>
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* iter89 : Dialog locataire (create / edit) */}
      <Dialog open={tenantDialog} onOpenChange={setTenantDialog}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle style={{fontFamily:'Chivo,sans-serif'}}>{editingTenant ? 'Modifier le locataire' : 'Nouveau locataire'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Nom complet *</label>
              <Input value={tenantForm.name} onChange={e => setTenantForm({...tenantForm, name: e.target.value})} data-testid="tenant-name" />
            </div>
            <div>
              <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Lot *</label>
              <select value={tenantForm.lot_id} onChange={e => setTenantForm({...tenantForm, lot_id: e.target.value})} data-testid="tenant-lot" className="w-full border border-slate-200 rounded-md px-3 py-2 text-sm bg-white">
                <option value="">Selectionner un lot</option>
                {myLots.map(l => <option key={l.id} value={l.id}>Lot {l.number}{l.description ? ' - ' + l.description : ''}</option>)}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Email</label>
                <Input value={tenantForm.email} onChange={e => setTenantForm({...tenantForm, email: e.target.value})} data-testid="tenant-email" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">GSM</label>
                <Input value={tenantForm.phone} onChange={e => setTenantForm({...tenantForm, phone: e.target.value})} data-testid="tenant-phone" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Debut bail</label>
                <Input type="date" value={tenantForm.lease_start} onChange={e => setTenantForm({...tenantForm, lease_start: e.target.value})} data-testid="tenant-lease-start" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Fin bail</label>
                <Input type="date" value={tenantForm.lease_end} onChange={e => setTenantForm({...tenantForm, lease_end: e.target.value})} data-testid="tenant-lease-end" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 block">Loyer (EUR)</label>
                <Input type="number" step="0.01" value={tenantForm.rent_amount} onChange={e => setTenantForm({...tenantForm, rent_amount: parseFloat(e.target.value) || 0})} data-testid="tenant-rent" />
              </div>
            </div>
            <div className="flex gap-2 justify-end pt-2 border-t border-slate-100">
              <Button variant="outline" onClick={() => setTenantDialog(false)}>Annuler</Button>
              <Button onClick={saveTenant} className="bg-[#2563EB] hover:bg-[#1D4ED8]" data-testid="tenant-save-btn">
                {editingTenant ? 'Enregistrer' : 'Ajouter'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <footer className="max-w-6xl mx-auto pt-6 pb-4 border-t border-slate-200 mt-8">
        <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-[10px] text-slate-400" data-testid="owner-portal-legal-footer">
          <a href="/legal/cgu" target="_blank" rel="noopener noreferrer" className="hover:text-[#2563EB] hover:underline">CGU</a>
          <span>·</span>
          <a href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="hover:text-[#2563EB] hover:underline">Confidentialite</a>
          <span>·</span>
          <a href="/legal/mentions" target="_blank" rel="noopener noreferrer" className="hover:text-[#2563EB] hover:underline">Mentions Legales</a>
          <span>·</span>
          <a href="/legal/cookies" target="_blank" rel="noopener noreferrer" className="hover:text-[#2563EB] hover:underline">Cookies</a>
        </div>
      </footer>
    </div>
  );
}

function StatCard({ icon, label, value, highlight, badge }) {
  const color = highlight === 'red' ? 'text-red-600' : highlight === 'green' ? 'text-green-600' : 'text-slate-900';
  return (
    <Card className="border-slate-200">
      <CardContent className="p-4">
        <div className="flex items-center gap-2 mb-1.5 text-slate-400">
          {icon}<span className="text-[10px] uppercase tracking-wider">{label}</span>
        </div>
        <div className={`text-xl font-semibold ${color}`} style={{fontFamily:'Chivo,sans-serif'}}>{value}</div>
        {badge && <Badge variant="outline" className={`mt-1 text-[10px] ${highlight === 'red' ? 'border-red-200 text-red-700' : highlight === 'green' ? 'border-green-200 text-green-700' : ''}`}>{badge}</Badge>}
      </CardContent>
    </Card>
  );
}
