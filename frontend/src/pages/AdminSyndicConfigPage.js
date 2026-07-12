import { useEffect, useState, useCallback } from 'react';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Building2, Mail, CheckCircle2, XCircle, ImageIcon, Settings, RefreshCw, Save,
} from 'lucide-react';

export default function AdminSyndicConfigPage() {
  const [syndics, setSyndics] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState({});
  const [saving, setSaving] = useState(false);
  const [emailCfg, setEmailCfg] = useState({ provider: 'none' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/admin/syndic-config/list');
      setSyndics(r.data.syndics || []);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openDetail = async (syndic) => {
    setSelected(syndic);
    try {
      const r = await api.get(`/admin/syndic-config/${syndic.syndic_user_id}`);
      setDetail(r.data || {});
      setEmailCfg({
        provider: r.data.email_provider || 'none',
        graph_tenant_id: r.data.graph_tenant_id || '',
        graph_client_id: r.data.graph_client_id || '',
        graph_client_secret: '',
        smtp_host: r.data.smtp_host || '',
        smtp_port: r.data.smtp_port || 587,
        smtp_username: r.data.smtp_username || '',
        smtp_password: '',
        smtp_use_tls: r.data.smtp_use_tls !== false,
      });
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const saveIdent = async () => {
    setSaving(true);
    try {
      const payload = { ...detail };
      delete payload.graph_client_secret;
      delete payload.smtp_password;
      delete payload.email_provider;
      await api.put(`/admin/syndic-config/${selected.syndic_user_id}`, payload);
      toast.success('Config identite mise a jour');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  const saveEmail = async () => {
    setSaving(true);
    try {
      // Ne pas envoyer les champs vides pour secrets (garde valeur existante)
      const payload = { ...emailCfg };
      if (!payload.graph_client_secret) delete payload.graph_client_secret;
      if (!payload.smtp_password) delete payload.smtp_password;
      await api.put(`/admin/syndic-config/${selected.syndic_user_id}/email`, payload);
      toast.success('Config email mise a jour');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-4" data-testid="admin-syndic-config-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Settings className="h-6 w-6 text-[#022D52]" />
            Configuration des cabinets syndics
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Gerer l&apos;identite, le logo, les mentions legales et la config email de chaque cabinet.
          </p>
        </div>
        <Button variant="ghost" onClick={load} data-testid="btn-refresh-list">
          <RefreshCw className="h-4 w-4" />
        </Button>
      </header>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cabinet</TableHead>
                <TableHead>Email login</TableHead>
                <TableHead>Ville</TableHead>
                <TableHead className="text-center">Logo</TableHead>
                <TableHead className="text-center">Onboarding</TableHead>
                <TableHead className="text-center">Config email</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={7} className="text-center p-6">Chargement...</TableCell></TableRow>
              ) : syndics.length === 0 ? (
                <TableRow><TableCell colSpan={7} className="text-center p-6 text-slate-500">Aucun syndic</TableCell></TableRow>
              ) : syndics.map(s => (
                <TableRow key={s.syndic_user_id} data-testid={`row-syndic-${s.syndic_user_id}`}>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      <Building2 className="h-4 w-4 text-[#022D52]" />
                      <div>
                        <div>{s.legal_name || s.user_name || <em className="text-slate-400">-</em>}</div>
                        {s.display_name && <div className="text-xs text-slate-500">{s.display_name}</div>}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{s.user_email}</TableCell>
                  <TableCell>{s.city || '-'}</TableCell>
                  <TableCell className="text-center">
                    {s.has_logo ? <CheckCircle2 className="h-4 w-4 text-emerald-500 mx-auto" /> : <XCircle className="h-4 w-4 text-slate-300 mx-auto" />}
                  </TableCell>
                  <TableCell className="text-center">
                    {s.onboarding_completed ? (
                      <Badge className="bg-emerald-100 text-emerald-700">Termine</Badge>
                    ) : (
                      <Badge className="bg-amber-100 text-amber-700">En attente</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-center">
                    {s.email_configured ? (
                      <Badge className="bg-emerald-100 text-emerald-700">
                        {s.email_provider} {s.email_verified && '✓'}
                      </Badge>
                    ) : (
                      <Badge className="bg-slate-100 text-slate-600">Fallback</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="outline" size="sm" onClick={() => openDetail(s)}
                            data-testid={`btn-edit-syndic-${s.syndic_user_id}`}>
                      <Settings className="h-3 w-3 mr-1" /> Editer
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto" data-testid="dialog-syndic-detail">
          <DialogHeader>
            <DialogTitle>{selected?.user_name} ({selected?.user_email})</DialogTitle>
          </DialogHeader>

          {/* Identite */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Building2 className="h-4 w-4 text-[#022D52]" /> Identite cabinet
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <div><Label className="text-xs">Denomination legale</Label>
                <Input value={detail.legal_name || ''} onChange={(e) => setDetail({ ...detail, legal_name: e.target.value })}
                       data-testid="admin-input-legal-name" /></div>
              <div><Label className="text-xs">Nom affiche</Label>
                <Input value={detail.display_name || ''} onChange={(e) => setDetail({ ...detail, display_name: e.target.value })}
                       data-testid="admin-input-display-name" /></div>
              <div className="col-span-2"><Label className="text-xs">Adresse</Label>
                <Input value={detail.address || ''} onChange={(e) => setDetail({ ...detail, address: e.target.value })}
                       data-testid="admin-input-address" /></div>
              <div><Label className="text-xs">CP</Label>
                <Input value={detail.postal_code || ''} onChange={(e) => setDetail({ ...detail, postal_code: e.target.value })} /></div>
              <div><Label className="text-xs">Ville</Label>
                <Input value={detail.city || ''} onChange={(e) => setDetail({ ...detail, city: e.target.value })} /></div>
              <div><Label className="text-xs">Email cabinet</Label>
                <Input value={detail.email || ''} onChange={(e) => setDetail({ ...detail, email: e.target.value })} /></div>
              <div><Label className="text-xs">Telephone</Label>
                <Input value={detail.phone || ''} onChange={(e) => setDetail({ ...detail, phone: e.target.value })} /></div>
              <div><Label className="text-xs">BCE</Label>
                <Input value={detail.bce || ''} onChange={(e) => setDetail({ ...detail, bce: e.target.value })} /></div>
              <div><Label className="text-xs">Agrement IPI</Label>
                <Input value={detail.ipi_number || ''} onChange={(e) => setDetail({ ...detail, ipi_number: e.target.value })}
                       data-testid="admin-input-ipi" /></div>
              <div className="col-span-2"><Label className="text-xs">Mentions legales (pied de page PDF)</Label>
                <Textarea rows={3} value={detail.legal_mentions || ''}
                          onChange={(e) => setDetail({ ...detail, legal_mentions: e.target.value })}
                          data-testid="admin-input-legal-mentions" /></div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Mail className="h-4 w-4 text-[#022D52]" /> Configuration email
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label className="text-xs">Fournisseur</Label>
                <Select value={emailCfg.provider} onValueChange={(v) => setEmailCfg({ ...emailCfg, provider: v })}>
                  <SelectTrigger data-testid="admin-select-provider"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Aucun (fallback)</SelectItem>
                    <SelectItem value="graph">Microsoft Graph</SelectItem>
                    <SelectItem value="smtp">SMTP</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {emailCfg.provider === 'graph' && (
                <>
                  <div><Label className="text-xs">Azure Tenant ID</Label>
                    <Input value={emailCfg.graph_tenant_id}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_tenant_id: e.target.value })}
                           data-testid="admin-input-tenant" /></div>
                  <div><Label className="text-xs">Client ID</Label>
                    <Input value={emailCfg.graph_client_id}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_id: e.target.value })} /></div>
                  <div><Label className="text-xs">Client Secret (laisser vide pour ne pas modifier)</Label>
                    <Input type="password" value={emailCfg.graph_client_secret}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_secret: e.target.value })} /></div>
                </>
              )}
              {emailCfg.provider === 'smtp' && (
                <div className="grid grid-cols-2 gap-2">
                  <div className="col-span-2"><Label className="text-xs">Serveur</Label>
                    <Input value={emailCfg.smtp_host}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_host: e.target.value })} /></div>
                  <div><Label className="text-xs">Port</Label>
                    <Input type="number" value={emailCfg.smtp_port}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_port: parseInt(e.target.value) || 0 })} /></div>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={emailCfg.smtp_use_tls}
                             onChange={(e) => setEmailCfg({ ...emailCfg, smtp_use_tls: e.target.checked })} />
                      STARTTLS
                    </label>
                  </div>
                  <div><Label className="text-xs">Utilisateur</Label>
                    <Input value={emailCfg.smtp_username}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_username: e.target.value })} /></div>
                  <div><Label className="text-xs">Mot de passe (laisser vide pour ne pas modifier)</Label>
                    <Input type="password" value={emailCfg.smtp_password}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_password: e.target.value })} /></div>
                </div>
              )}
              <Button onClick={saveEmail} disabled={saving} variant="outline" size="sm"
                      data-testid="admin-btn-save-email">
                <Save className="h-4 w-4 mr-1" /> Enregistrer config email
              </Button>
            </CardContent>
          </Card>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setSelected(null)}>Fermer</Button>
            <Button onClick={saveIdent} disabled={saving} className="bg-[#022D52] hover:bg-[#01213e]"
                    data-testid="admin-btn-save-ident">
              <Save className="h-4 w-4 mr-1" /> Enregistrer identite
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
