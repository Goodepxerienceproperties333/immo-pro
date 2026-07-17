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
  Building2, Mail, CheckCircle2, XCircle, ImageIcon, Settings, RefreshCw, Save, Send, Loader2,
} from 'lucide-react';

export default function AdminSyndicConfigPage() {
  const [syndics, setSyndics] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState({});
  const [saving, setSaving] = useState(false);
  const [emailCfg, setEmailCfg] = useState({ provider: 'none' });
  // iter90h3 : etat pour le dialog "Tester la config email"
  const [testDialog, setTestDialog] = useState(null); // { from, to } | null
  const [testSending, setTestSending] = useState(false);

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

  // iter90h3 : re-charge le detail du compte selectionne (sans changer selection).
  // Appele apres chaque save pour rafraichir les badges "verifie" / "secret set".
  const reloadCurrentDetail = async () => {
    if (!selected) return;
    try {
      const r = await api.get(`/admin/syndic-config/${selected.syndic_user_id}`);
      setDetail(r.data || {});
      setEmailCfg((prev) => ({
        ...prev,
        provider: r.data.email_provider || 'none',
        graph_tenant_id: r.data.graph_tenant_id || '',
        graph_client_id: r.data.graph_client_id || '',
        graph_client_secret: '',  // on ne re-affiche jamais un secret
        smtp_host: r.data.smtp_host || '',
        smtp_port: r.data.smtp_port || 587,
        smtp_username: r.data.smtp_username || '',
        smtp_password: '',
        smtp_use_tls: r.data.smtp_use_tls !== false,
      }));
    } catch (_e) { /* iter90h3 : silent - reloadCurrentDetail is best-effort */ }
  };

  const saveIdent = async () => {
    setSaving(true);
    try {
      const payload = { ...detail };
      delete payload.graph_client_secret;
      delete payload.smtp_password;
      delete payload.email_provider;
      await api.put(`/admin/syndic-config/${selected.syndic_user_id}`, payload);
      toast.success('Config identite enregistree');
      await reloadCurrentDetail();
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
      toast.success('Config email enregistree');
      await reloadCurrentDetail();
      load();
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  // iter90h3 : ouvre le dialog de test avec la boite d'envoi et le destinataire
  // pre-remplis (par defaut : email du compte cible).
  const openTestDialog = () => {
    if (!selected) return;
    setTestDialog({
      from: selected.user_email || '',
      to: selected.user_email || '',
    });
  };

  const runTestEmail = async () => {
    if (!testDialog || !selected) return;
    if (!testDialog.from || !testDialog.to) {
      toast.error('Boite d\'envoi et destinataire requis');
      return;
    }
    setTestSending(true);
    try {
      await api.post(`/admin/syndic-config/${selected.syndic_user_id}/test-email`, {
        from_mailbox: testDialog.from,
        to: testDialog.to,
      });
      toast.success(`Email de test envoye a ${testDialog.to} — verifiez la boite de reception.`, { duration: 6000 });
      setTestDialog(null);
      await reloadCurrentDetail();
      load();
    } catch (e) {
      toast.error(extractApiError(e), { duration: 8000 });
    } finally {
      setTestSending(false);
    }
  };

  // iter90h3 : indicateurs "secret deja configure ? Laisser vide pour conserver"
  const graphSecretConfigured = Boolean(detail.graph_client_secret);
  const smtpPasswordConfigured = Boolean(detail.smtp_password);

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-4" data-testid="admin-syndic-config-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Settings className="h-6 w-6 text-[#022D52]" />
            Configuration des comptes plateforme
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Gerer l&apos;identite, le logo, les mentions legales et la config email de chaque
            cabinet syndic et super administrateur.
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
                <TableHead>Compte</TableHead>
                <TableHead>Role</TableHead>
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
                <TableRow><TableCell colSpan={8} className="text-center p-6">Chargement...</TableCell></TableRow>
              ) : syndics.length === 0 ? (
                <TableRow><TableCell colSpan={8} className="text-center p-6 text-slate-500">Aucun compte</TableCell></TableRow>
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
                  <TableCell>
                    {s.user_role === 'superadmin' ? (
                      <Badge className="bg-purple-100 text-purple-800 border border-purple-200" data-testid={`role-badge-${s.syndic_user_id}`}>
                        Super Admin
                      </Badge>
                    ) : (
                      <Badge className="bg-blue-50 text-blue-700 border border-blue-200" data-testid={`role-badge-${s.syndic_user_id}`}>
                        Syndic
                      </Badge>
                    )}
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
                {detail.email_verified && (
                  <Badge className="bg-emerald-100 text-emerald-700 ml-2" data-testid="badge-email-verified">
                    <CheckCircle2 className="h-3 w-3 mr-1" /> Verifiee
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label className="text-xs">
                  <span className="text-slate-900">Fournisseur d&apos;envoi</span>
                  <span className="text-slate-500 ml-1">/ Email Provider</span>
                </Label>
                <Select value={emailCfg.provider} onValueChange={(v) => setEmailCfg({ ...emailCfg, provider: v })}>
                  <SelectTrigger data-testid="admin-select-provider"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Aucun / Fallback (utilise la config globale)</SelectItem>
                    <SelectItem value="graph">Microsoft Graph (Azure AD + Office 365)</SelectItem>
                    <SelectItem value="smtp">SMTP (serveur email standard)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {emailCfg.provider === 'graph' && (
                <>
                  {/* iter90h4 : bandeau d'aide avec le chemin Azure Portal */}
                  <div className="bg-blue-50 border border-blue-200 rounded p-2 text-[11px] text-blue-900 leading-relaxed">
                    <strong>Azure Portal :</strong> <em>portal.azure.com</em> &rarr; Azure Active Directory
                    &rarr; App registrations &rarr; votre application. Les 3 valeurs
                    ci-dessous se trouvent sous <em>Overview</em> (Tenant ID, Client ID)
                    et <em>Certificates &amp; secrets</em> (Client Secret Value).
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Identifiant du repertoire (locataire)</span>
                      <span className="text-slate-500 ml-1">/ Directory (tenant) ID</span>
                    </Label>
                    <Input value={emailCfg.graph_tenant_id}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_tenant_id: e.target.value.trim() })}
                           placeholder="UUID du tenant Azure AD (ex : xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
                           data-testid="admin-input-tenant" />
                    <p className="text-[10px] text-slate-500 mt-1">
                      Azure Portal &rarr; Azure Active Directory &rarr; Overview &rarr; <em>Tenant ID</em>.
                      C&apos;est l&apos;identifiant de votre organisation Azure (pas de l&apos;app).
                    </p>
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Identifiant de l&apos;application (client)</span>
                      <span className="text-slate-500 ml-1">/ Application (client) ID</span>
                    </Label>
                    <Input value={emailCfg.graph_client_id}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_id: e.target.value.trim() })}
                           placeholder="UUID de l'App registration"
                           data-testid="admin-input-client-id" />
                    <p className="text-[10px] text-slate-500 mt-1">
                      Azure Portal &rarr; App registrations &rarr; votre app &rarr; Overview &rarr;
                      <em> Application (client) ID</em>. C&apos;est l&apos;identifiant de l&apos;app elle-meme.
                    </p>
                  </div>
                  <div>
                    <Label className="text-xs flex items-center gap-2">
                      <span className="text-slate-900">Cle secrete client</span>
                      <span className="text-slate-500">/ Client Secret (Value)</span>
                      {graphSecretConfigured && (
                        <span className="inline-flex items-center gap-1 text-emerald-700 text-[10px] font-medium" data-testid="graph-secret-configured">
                          <CheckCircle2 className="h-3 w-3" /> Secret configure
                        </span>
                      )}
                    </Label>
                    <Input type="password" value={emailCfg.graph_client_secret}
                           onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_secret: e.target.value })}
                           placeholder={graphSecretConfigured ? 'Laisser vide pour conserver le secret existant' : 'Colle ici le Value du secret (pas le Secret ID)'}
                           data-testid="admin-input-graph-secret" />
                    <p className="text-[10px] text-amber-700 mt-1">
                      <strong>Attention :</strong> copiez la colonne <em>Value</em> (et non
                      <em> Secret ID</em>) apres avoir cree le secret. Elle n&apos;est visible qu&apos;une seule fois.
                    </p>
                  </div>
                </>
              )}
              {emailCfg.provider === 'smtp' && (
                <div className="grid grid-cols-2 gap-2">
                  <div className="col-span-2 bg-blue-50 border border-blue-200 rounded p-2 text-[11px] text-blue-900">
                    <strong>Serveur SMTP :</strong> renseignez les parametres fournis
                    par votre hebergeur email. Ports typiques : <em>587</em> (STARTTLS,
                    recommande) ou <em>465</em> (SSL/TLS implicite).
                  </div>
                  <div className="col-span-2">
                    <Label className="text-xs">
                      <span className="text-slate-900">Serveur SMTP</span>
                      <span className="text-slate-500 ml-1">/ SMTP Host</span>
                    </Label>
                    <Input value={emailCfg.smtp_host}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_host: e.target.value.trim() })}
                           placeholder="smtp.office365.com, smtp.gmail.com, ..."
                           data-testid="admin-input-smtp-host" />
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Port</span>
                      <span className="text-slate-500 ml-1">/ Port</span>
                    </Label>
                    <Input type="number" value={emailCfg.smtp_port}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_port: parseInt(e.target.value) || 0 })}
                           placeholder="587 ou 465"
                           data-testid="admin-input-smtp-port" />
                  </div>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" checked={emailCfg.smtp_use_tls}
                             onChange={(e) => setEmailCfg({ ...emailCfg, smtp_use_tls: e.target.checked })}
                             data-testid="admin-input-smtp-tls" />
                      <span>Chiffrement STARTTLS <span className="text-slate-500">(port 587)</span></span>
                    </label>
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Nom d&apos;utilisateur</span>
                      <span className="text-slate-500 ml-1">/ SMTP Username</span>
                    </Label>
                    <Input value={emailCfg.smtp_username}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_username: e.target.value.trim() })}
                           placeholder="Souvent : votre adresse email complete"
                           data-testid="admin-input-smtp-username" />
                  </div>
                  <div>
                    <Label className="text-xs flex items-center gap-2">
                      <span className="text-slate-900">Mot de passe</span>
                      <span className="text-slate-500">/ SMTP Password</span>
                      {smtpPasswordConfigured && (
                        <span className="inline-flex items-center gap-1 text-emerald-700 text-[10px] font-medium" data-testid="smtp-password-configured">
                          <CheckCircle2 className="h-3 w-3" /> Configure
                        </span>
                      )}
                    </Label>
                    <Input type="password" value={emailCfg.smtp_password}
                           onChange={(e) => setEmailCfg({ ...emailCfg, smtp_password: e.target.value })}
                           placeholder={smtpPasswordConfigured ? 'Laisser vide pour conserver' : 'Mot de passe ou App Password'}
                           data-testid="admin-input-smtp-password" />
                  </div>
                </div>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <Button onClick={saveEmail} disabled={saving} variant="outline" size="sm"
                        data-testid="admin-btn-save-email">
                  {saving ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Save className="h-4 w-4 mr-1" />}
                  Enregistrer la config
                </Button>
                <Button
                  onClick={openTestDialog}
                  disabled={saving || emailCfg.provider === 'none'}
                  size="sm"
                  className="bg-[#022D52] hover:bg-[#1D4ED8] text-white"
                  data-testid="admin-btn-test-email"
                >
                  <Send className="h-4 w-4 mr-1" /> Tester la configuration
                </Button>
              </div>
              {emailCfg.provider !== 'none' && !detail.email_verified && (
                <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                  Config enregistree mais <strong>non verifiee</strong>. Cliquez sur
                  &quot;Tester la configuration&quot; pour envoyer un email de test et
                  valider les credentials.
                </p>
              )}
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

      {/* iter90h3 : Dialog de test email admin */}
      <Dialog open={!!testDialog} onOpenChange={(open) => !open && !testSending && setTestDialog(null)}>
        <DialogContent className="max-w-lg" data-testid="dialog-test-email">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Send className="h-5 w-5 text-[#022D52]" />
              Tester la configuration email
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <p className="text-sm text-slate-600">
              Un email de test va etre envoye <strong>en utilisant la configuration
              actuellement enregistree</strong> pour <span className="font-mono text-xs">{selected?.user_email}</span>.
              L&apos;envoi echouera si les credentials sont incorrects, ce qui permet de
              valider la configuration avant activation.
            </p>
            <div>
              <Label className="text-xs">Boite d&apos;envoi (from)</Label>
              <Input
                value={testDialog?.from || ''}
                onChange={(e) => setTestDialog({ ...testDialog, from: e.target.value })}
                placeholder="expediteur@domaine.be"
                data-testid="test-email-from"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Doit correspondre a l&apos;email du compte ou a une boite autorisee.
              </p>
            </div>
            <div>
              <Label className="text-xs">Destinataire du test</Label>
              <Input
                value={testDialog?.to || ''}
                onChange={(e) => setTestDialog({ ...testDialog, to: e.target.value })}
                placeholder="test@domaine.be"
                data-testid="test-email-to"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Verifiez cette boite (et le dossier Spam) apres l&apos;envoi.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setTestDialog(null)} disabled={testSending}>
              Annuler
            </Button>
            <Button
              onClick={runTestEmail}
              disabled={testSending}
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
              data-testid="test-email-confirm"
            >
              {testSending ? (
                <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Envoi en cours...</>
              ) : (
                <><Send className="h-4 w-4 mr-1" /> Envoyer le test</>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
