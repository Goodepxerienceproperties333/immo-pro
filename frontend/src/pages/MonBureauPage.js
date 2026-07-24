/**
 * iter90dj : Page self-service "Mon bureau" pour le syndic.
 *
 * Permet au syndic connecte de modifier a tout moment (hors onboarding) :
 * - Logo (upload PNG/JPEG) affiche en tete de tous les PDFs
 * - Coordonnees legales : denomination, adresse, BCE, TVA, IPI
 * - Mentions legales : texte du pied de page de tous les PDFs
 *
 * Endpoints backend deja existants :
 * - GET  /api/syndic-config/me
 * - PUT  /api/syndic-config/me                 (identite + mentions legales)
 * - POST /api/syndic-config/me/logo            (upload multipart)
 * - GET  /api/syndic-config/{uid}/logo         (recup logo pour preview)
 */
import { useEffect, useState, useCallback, useRef } from 'react';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Building2, Save, ImageIcon, FileText, Upload, CheckCircle2,
  Info, Mail, Send, Lock, Loader2, AlertTriangle,
} from 'lucide-react';
import { TeamSection } from '@/components/TeamSection';

export default function MonBureauPage() {
  const [cfg, setCfg] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [logoCacheBuster, setLogoCacheBuster] = useState(Date.now());
  const fileInputRef = useRef(null);
  // iter90hc : self-service config email (Graph / SMTP)
  const [emailCfg, setEmailCfg] = useState({ provider: 'none' });
  const [savingEmail, setSavingEmail] = useState(false);
  const [testDialog, setTestDialog] = useState(null);
  const [testSending, setTestSending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/syndic-config/me');
      setCfg(r.data || {});
      // iter90hc : synchroniser emailCfg avec le retour serveur
      setEmailCfg({
        provider: r.data?.email_provider || 'none',
        graph_tenant_id: r.data?.graph_tenant_id || '',
        graph_client_id: r.data?.graph_client_id || '',
        graph_client_secret: '',
        smtp_host: r.data?.smtp_host || '',
        smtp_port: r.data?.smtp_port || 587,
        smtp_username: r.data?.smtp_username || '',
        smtp_password: '',
        smtp_use_tls: r.data?.smtp_use_tls !== false,
      });
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const saveIdent = async () => {
    setSaving(true);
    try {
      const payload = {
        legal_name: cfg.legal_name || '',
        display_name: cfg.display_name || '',
        address: cfg.address || '',
        postal_code: cfg.postal_code || '',
        city: cfg.city || '',
        country: cfg.country || '',
        email: cfg.email || '',
        phone: cfg.phone || '',
        bce: cfg.bce || '',
        tva: cfg.tva || '',
        ipi_number: cfg.ipi_number || '',
        legal_mentions: cfg.legal_mentions || '',
      };
      await api.put('/syndic-config/me', payload);
      toast.success('Coordonnees et mentions legales mises a jour');
      await load();
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setSaving(false);
    }
  };

  // iter90hc : self-service save + test email config
  const saveEmailConfig = async () => {
    setSavingEmail(true);
    try {
      const payload = { ...emailCfg };
      // Ne pas envoyer les secrets vides -> conserve la valeur en DB
      if (!payload.graph_client_secret) delete payload.graph_client_secret;
      if (!payload.smtp_password) delete payload.smtp_password;
      await api.put('/syndic-config/me/email', payload);
      toast.success('Configuration email enregistree. Testez-la maintenant pour valider les credentials.');
      await load();
    } catch (e) {
      toast.error(extractApiError(e), { duration: 8000 });
    } finally {
      setSavingEmail(false);
    }
  };

  const openTestDialog = () => {
    setTestDialog({ from: cfg.email || '', to: cfg.email || '' });
  };

  const runTestEmail = async () => {
    if (!testDialog?.from || !testDialog?.to) {
      toast.error('Boite d\'envoi et destinataire requis');
      return;
    }
    setTestSending(true);
    try {
      // On utilise l'endpoint self : POST /api/syndic-config/me/test-email
      await api.post('/syndic-config/me/test-email', {
        from_mailbox: testDialog.from,
        to: testDialog.to,
      });
      toast.success(`Email de test envoye a ${testDialog.to}. Verifiez la boite (et le dossier spam).`, { duration: 8000 });
      setTestDialog(null);
      await load();
    } catch (e) {
      toast.error(extractApiError(e), { duration: 10000 });
    } finally {
      setTestSending(false);
    }
  };

  const graphSecretConfigured = Boolean(cfg.graph_client_secret);
  const smtpPasswordConfigured = Boolean(cfg.smtp_password);
  const emailLocked = Boolean(cfg.email_config_locked);

  const uploadLogo = async (file) => {
    if (!file) return;
    const maxSize = 3 * 1024 * 1024;
    if (file.size > maxSize) {
      toast.error('Logo trop volumineux (max 3 Mo)');
      return;
    }
    setUploadingLogo(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      await api.post('/syndic-config/me/logo', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success('Logo mis a jour - visible sur tous les prochains PDFs');
      setLogoCacheBuster(Date.now());
      await load();
    } catch (e) {
      toast.error(extractApiError(e));
    } finally {
      setUploadingLogo(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const backendUrl = process.env.REACT_APP_BACKEND_URL;
  const logoUrl = cfg.has_logo && cfg.syndic_user_id
    ? `${backendUrl}/api/syndic-config/${cfg.syndic_user_id}/logo?v=${logoCacheBuster}`
    : null;

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-4" data-testid="mon-bureau-page">
      <header>
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <Building2 className="h-6 w-6 text-[#022D52]" />
          Mon bureau
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Personnalisez votre logo, vos coordonnees et les mentions legales qui apparaitront
          en tete et en pied de page de tous les documents PDF generes par NextGe Copro
          (situations de compte, decomptes, bilans, budgets, journaux, factures, RGPD...).
        </p>
      </header>

      {loading && (
        <div className="text-center p-6 text-slate-500" data-testid="mon-bureau-loading">
          Chargement de votre configuration...
        </div>
      )}

      {!loading && (
        <>
          {/* Bandeau info */}
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 flex items-start gap-2"
               data-testid="mon-bureau-info-banner">
            <Info className="h-4 w-4 text-[#022D52] mt-0.5 shrink-0" />
            <div className="text-xs text-blue-900">
              Le logo s&apos;affichera <b>en tete de la premiere page</b> de chaque PDF, et
              les mentions legales apparaitront <b>en pied de page de toutes les pages</b>
              (avec numerotation). Toutes les modifications sont prises en compte
              immediatement sur les prochains documents.
            </div>
          </div>

          {/* Logo */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <ImageIcon className="h-4 w-4 text-[#022D52]" />
                Logo du bureau
                {cfg.has_logo && (
                  <Badge className="bg-emerald-100 text-emerald-700 ml-2" data-testid="badge-has-logo">
                    <CheckCircle2 className="h-3 w-3 mr-1" /> Configure
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-start gap-6">
                <div className="w-40 h-24 border-2 border-dashed border-slate-300 rounded-lg
                                flex items-center justify-center bg-slate-50 overflow-hidden"
                     data-testid="mon-bureau-logo-preview">
                  {logoUrl ? (
                    <img src={logoUrl} alt="Logo actuel" className="max-h-full max-w-full object-contain" />
                  ) : (
                    <ImageIcon className="h-8 w-8 text-slate-300" />
                  )}
                </div>
                <div className="flex-1 space-y-2">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/jpg"
                    onChange={(e) => uploadLogo(e.target.files?.[0])}
                    className="text-sm block"
                    data-testid="mon-bureau-logo-input"
                    disabled={uploadingLogo}
                  />
                  <p className="text-xs text-slate-500">
                    PNG ou JPEG uniquement. Max 3 Mo. Ratio recommande : environ 3:2 (logo horizontal).
                    <br />Dimensions PDF : 40mm x 22mm maximum, redimensionne en preservant le ratio.
                  </p>
                  {uploadingLogo && (
                    <p className="text-sm text-[#022D52] flex items-center gap-2">
                      <Upload className="h-4 w-4 animate-pulse" /> Envoi en cours...
                    </p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Identite */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Building2 className="h-4 w-4 text-[#022D52]" /> Identite du bureau
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Denomination legale *</Label>
                <Input
                  value={cfg.legal_name || ''}
                  onChange={(e) => setCfg({ ...cfg, legal_name: e.target.value })}
                  placeholder="Ex : Cabinet Immosud SPRL"
                  data-testid="mon-bureau-input-legal-name"
                />
              </div>
              <div>
                <Label className="text-xs">Nom affiche (marketing)</Label>
                <Input
                  value={cfg.display_name || ''}
                  onChange={(e) => setCfg({ ...cfg, display_name: e.target.value })}
                  placeholder="Ex : Immosud"
                  data-testid="mon-bureau-input-display-name"
                />
              </div>
              <div className="md:col-span-2">
                <Label className="text-xs">Adresse</Label>
                <Input
                  value={cfg.address || ''}
                  onChange={(e) => setCfg({ ...cfg, address: e.target.value })}
                  placeholder="Rue de l'Etoile 1"
                  data-testid="mon-bureau-input-address"
                />
              </div>
              <div>
                <Label className="text-xs">Code postal</Label>
                <Input
                  value={cfg.postal_code || ''}
                  onChange={(e) => setCfg({ ...cfg, postal_code: e.target.value })}
                  placeholder="1000"
                  data-testid="mon-bureau-input-postal"
                />
              </div>
              <div>
                <Label className="text-xs">Ville *</Label>
                <Input
                  value={cfg.city || ''}
                  onChange={(e) => setCfg({ ...cfg, city: e.target.value })}
                  placeholder="Bruxelles"
                  data-testid="mon-bureau-input-city"
                />
              </div>
              <div>
                <Label className="text-xs">Email du bureau *</Label>
                <Input
                  type="email"
                  value={cfg.email || ''}
                  onChange={(e) => setCfg({ ...cfg, email: e.target.value })}
                  placeholder="contact@monbureau.be"
                  data-testid="mon-bureau-input-email"
                />
              </div>
              <div>
                <Label className="text-xs">Telephone</Label>
                <Input
                  value={cfg.phone || ''}
                  onChange={(e) => setCfg({ ...cfg, phone: e.target.value })}
                  placeholder="+32 2 123 45 67"
                  data-testid="mon-bureau-input-phone"
                />
              </div>
              <div>
                <Label className="text-xs">Numero BCE</Label>
                <Input
                  value={cfg.bce || ''}
                  onChange={(e) => setCfg({ ...cfg, bce: e.target.value })}
                  placeholder="0123.456.789"
                  data-testid="mon-bureau-input-bce"
                />
              </div>
              <div>
                <Label className="text-xs">Numero de TVA</Label>
                <Input
                  value={cfg.tva || ''}
                  onChange={(e) => setCfg({ ...cfg, tva: e.target.value })}
                  placeholder="BE0123456789"
                  data-testid="mon-bureau-input-tva"
                />
              </div>
              <div className="md:col-span-2">
                <Label className="text-xs">Numero d&apos;agrement IPI (syndic professionnel)</Label>
                <Input
                  value={cfg.ipi_number || ''}
                  onChange={(e) => setCfg({ ...cfg, ipi_number: e.target.value })}
                  placeholder="509.123"
                  data-testid="mon-bureau-input-ipi"
                />
              </div>
            </CardContent>
          </Card>

          {/* iter90hc : Config email self-service */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <Mail className="h-4 w-4 text-[#022D52]" /> Configuration email (Microsoft Graph / SMTP)
                {cfg.email_verified && (
                  <Badge className="bg-emerald-100 text-emerald-700 ml-2" data-testid="mon-bureau-badge-email-verified">
                    <CheckCircle2 className="h-3 w-3 mr-1" /> Verifiee
                  </Badge>
                )}
                {emailLocked && (
                  <Badge className="bg-red-100 text-red-800 border border-red-200 ml-1" data-testid="mon-bureau-badge-email-locked">
                    <Lock className="h-3 w-3 mr-1" /> Verrouillee par admin
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="bg-blue-50 border border-blue-200 rounded p-3 text-xs text-blue-900">
                Configurez ici vos credentials Microsoft Graph (recommande) ou SMTP
                pour que <strong>vos envois email</strong> (appels de fonds,
                communications, decomptes) partent depuis <strong>votre propre boite</strong>.
                Une fois configure, cliquez sur <em>Tester la configuration</em> pour valider.
              </div>

              {emailLocked && (
                <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-900" data-testid="mon-bureau-lock-banner">
                  <div className="flex items-center gap-2 font-semibold mb-1">
                    <Lock className="h-4 w-4" /> Configuration verrouillee
                  </div>
                  <div>
                    Verrouillee le {(cfg.email_locked_at || '').slice(0, 19)} par
                    <span className="font-mono ml-1">{cfg.email_locked_by_email || 'un administrateur'}</span>.
                    Contactez votre administrateur pour la deverrouiller avant modification.
                  </div>
                </div>
              )}

              <div>
                <Label className="text-xs">
                  <span className="text-slate-900">Fournisseur d&apos;envoi</span>
                  <span className="text-slate-500 ml-1">/ Email Provider</span>
                </Label>
                <Select
                  value={emailCfg.provider}
                  onValueChange={(v) => setEmailCfg({ ...emailCfg, provider: v })}
                  disabled={emailLocked}
                >
                  <SelectTrigger data-testid="mon-bureau-select-provider"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Aucun / Fallback (utilise la config globale)</SelectItem>
                    <SelectItem value="graph">Microsoft Graph (Azure AD + Office 365)</SelectItem>
                    <SelectItem value="smtp">SMTP (serveur email standard)</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {emailCfg.provider === 'graph' && (
                <>
                  <div className="bg-slate-50 border border-slate-200 rounded p-2 text-[11px] text-slate-700">
                    <strong>Azure Portal :</strong> <em>portal.azure.com</em> &rarr; Azure Active Directory
                    &rarr; App registrations &rarr; votre application. Les 3 valeurs sous
                    <em> Overview</em> (Tenant ID, Client ID) et <em>Certificates &amp; secrets</em> (Client Secret Value).
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Identifiant du repertoire (locataire)</span>
                      <span className="text-slate-500 ml-1">/ Directory (tenant) ID</span>
                    </Label>
                    <Input
                      value={emailCfg.graph_tenant_id || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, graph_tenant_id: e.target.value.trim() })}
                      placeholder="UUID Azure AD (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-tenant"
                    />
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Identifiant de l&apos;application (client)</span>
                      <span className="text-slate-500 ml-1">/ Application (client) ID</span>
                    </Label>
                    <Input
                      value={emailCfg.graph_client_id || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_id: e.target.value.trim() })}
                      placeholder="UUID de l'App registration"
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-client-id"
                    />
                  </div>
                  <div>
                    <Label className="text-xs flex items-center gap-2">
                      <span className="text-slate-900">Cle secrete client</span>
                      <span className="text-slate-500">/ Client Secret (Value)</span>
                      {graphSecretConfigured && (
                        <span className="inline-flex items-center gap-1 text-emerald-700 text-[10px] font-medium">
                          <CheckCircle2 className="h-3 w-3" /> Secret configure
                        </span>
                      )}
                    </Label>
                    <Input
                      type="password"
                      value={emailCfg.graph_client_secret || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_secret: e.target.value })}
                      placeholder={graphSecretConfigured ? 'Laisser vide pour conserver le secret existant' : 'Colle ici le Value (pas le Secret ID)'}
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-graph-secret"
                    />
                    <p className="text-[10px] text-amber-700 mt-1">
                      <strong>Attention :</strong> copiez la colonne <em>Value</em>
                      (pas <em>Secret ID</em>) apres avoir cree le secret dans
                      Azure Portal. Elle n&apos;est visible qu&apos;une seule fois.
                    </p>
                  </div>
                </>
              )}

              {emailCfg.provider === 'smtp' && (
                <div className="grid grid-cols-2 gap-2">
                  <div className="col-span-2 bg-slate-50 border border-slate-200 rounded p-2 text-[11px] text-slate-700">
                    <strong>Serveur SMTP :</strong> ports typiques : <em>587</em> (STARTTLS, recommande) ou <em>465</em> (SSL/TLS).
                  </div>
                  <div className="col-span-2">
                    <Label className="text-xs">
                      <span className="text-slate-900">Serveur SMTP</span>
                      <span className="text-slate-500 ml-1">/ SMTP Host</span>
                    </Label>
                    <Input
                      value={emailCfg.smtp_host || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, smtp_host: e.target.value.trim() })}
                      placeholder="smtp.office365.com, smtp.gmail.com, ..."
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-smtp-host"
                    />
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Port</span>
                      <span className="text-slate-500 ml-1">/ Port</span>
                    </Label>
                    <Input
                      type="number"
                      value={emailCfg.smtp_port || 587}
                      onChange={(e) => setEmailCfg({ ...emailCfg, smtp_port: parseInt(e.target.value, 10) || 0 })}
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-smtp-port"
                    />
                  </div>
                  <div className="flex items-end">
                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                      <input
                        type="checkbox"
                        checked={emailCfg.smtp_use_tls !== false}
                        onChange={(e) => setEmailCfg({ ...emailCfg, smtp_use_tls: e.target.checked })}
                        disabled={emailLocked}
                        data-testid="mon-bureau-input-smtp-tls"
                      />
                      <span>Chiffrement STARTTLS <span className="text-slate-500">(port 587)</span></span>
                    </label>
                  </div>
                  <div>
                    <Label className="text-xs">
                      <span className="text-slate-900">Nom d&apos;utilisateur</span>
                      <span className="text-slate-500 ml-1">/ SMTP Username</span>
                    </Label>
                    <Input
                      value={emailCfg.smtp_username || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, smtp_username: e.target.value.trim() })}
                      placeholder="Souvent : votre adresse email complete"
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-smtp-username"
                    />
                  </div>
                  <div>
                    <Label className="text-xs flex items-center gap-2">
                      <span className="text-slate-900">Mot de passe</span>
                      <span className="text-slate-500">/ SMTP Password</span>
                      {smtpPasswordConfigured && (
                        <span className="inline-flex items-center gap-1 text-emerald-700 text-[10px] font-medium">
                          <CheckCircle2 className="h-3 w-3" /> Configure
                        </span>
                      )}
                    </Label>
                    <Input
                      type="password"
                      value={emailCfg.smtp_password || ''}
                      onChange={(e) => setEmailCfg({ ...emailCfg, smtp_password: e.target.value })}
                      placeholder={smtpPasswordConfigured ? 'Laisser vide pour conserver' : 'Mot de passe ou App Password'}
                      disabled={emailLocked}
                      data-testid="mon-bureau-input-smtp-password"
                    />
                  </div>
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-1">
                <Button
                  onClick={saveEmailConfig}
                  disabled={savingEmail || emailLocked}
                  variant="outline"
                  size="sm"
                  data-testid="mon-bureau-btn-save-email"
                >
                  {savingEmail ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Save className="h-4 w-4 mr-1" />}
                  Enregistrer la config email
                </Button>
                <Button
                  onClick={openTestDialog}
                  disabled={emailCfg.provider === 'none'}
                  size="sm"
                  className="bg-[#022D52] hover:bg-[#1D4ED8] text-white"
                  data-testid="mon-bureau-btn-test-email"
                >
                  <Send className="h-4 w-4 mr-1" /> Tester la configuration
                </Button>
              </div>

              {emailCfg.provider !== 'none' && !cfg.email_verified && !emailLocked && (
                <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded p-2 flex items-start gap-1.5">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                  <div>
                    Config enregistree mais <strong>non verifiee</strong>.
                    Cliquez sur &quot;Tester la configuration&quot; pour valider les credentials.
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Section equipe / collaborateurs */}
          <TeamSection />

          {/* Mentions legales */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base flex items-center gap-2">
                <FileText className="h-4 w-4 text-[#022D52]" /> Mentions legales (pied de page PDF)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Textarea
                rows={5}
                value={cfg.legal_mentions || ''}
                onChange={(e) => setCfg({ ...cfg, legal_mentions: e.target.value })}
                placeholder={
                  "Ex :\n"
                  + "Cabinet Immosud SPRL - BCE 0123.456.789 - Agrement IPI 509.123\n"
                  + "Rue de l'Etoile 1, 1000 Bruxelles - Tel +32 2 123 45 67 - contact@monbureau.be\n"
                  + "Assurance RC pro : Ethias RCPro 45.123.456 - Compte tiers IBAN BE68 5390 0754 7034"
                }
                data-testid="mon-bureau-input-legal-mentions"
              />
              <p className="text-xs text-slate-500 mt-2">
                Ce texte s&apos;affiche en bas de <b>chaque page</b> de vos PDFs (max 3 lignes, ~180 caracteres par ligne).
                Recommande : mentions IPI, BCE, RC pro, compte tiers, mentions RGPD.
              </p>
            </CardContent>
          </Card>

          {/* Save */}
          <div className="flex justify-end sticky bottom-0 bg-white/80 backdrop-blur py-3 border-t">
            <Button
              onClick={saveIdent}
              disabled={saving}
              className="bg-[#022D52] hover:bg-[#01213e]"
              data-testid="mon-bureau-btn-save"
            >
              <Save className="h-4 w-4 mr-1" />
              {saving ? 'Enregistrement...' : 'Enregistrer'}
            </Button>
          </div>
        </>
      )}

      {/* iter90hc : Dialog de test email */}
      <Dialog open={!!testDialog} onOpenChange={(open) => !open && !testSending && setTestDialog(null)}>
        <DialogContent className="max-w-lg" data-testid="mon-bureau-dialog-test-email">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Send className="h-5 w-5 text-[#022D52]" /> Tester votre configuration email
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <p className="text-sm text-slate-600">
              Un email de test va etre envoye <strong>en utilisant la configuration
              actuellement enregistree</strong>. L&apos;envoi echouera si les credentials
              sont incorrects, ce qui vous permet de valider la configuration.
            </p>
            <div>
              <Label className="text-xs">Boite d&apos;envoi (from)</Label>
              <Input
                value={testDialog?.from || ''}
                onChange={(e) => setTestDialog({ ...testDialog, from: e.target.value })}
                placeholder="votre-email@bureau.be"
                data-testid="mon-bureau-test-from"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Doit correspondre a votre email ou a une boite autorisee dans votre compte Microsoft.
              </p>
            </div>
            <div>
              <Label className="text-xs">Destinataire du test</Label>
              <Input
                value={testDialog?.to || ''}
                onChange={(e) => setTestDialog({ ...testDialog, to: e.target.value })}
                placeholder="test@bureau.be"
                data-testid="mon-bureau-test-to"
              />
              <p className="text-[11px] text-slate-500 mt-1">
                Verifiez cette boite (et le dossier Spam) apres l&apos;envoi.
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setTestDialog(null)} disabled={testSending}>Annuler</Button>
            <Button
              onClick={runTestEmail}
              disabled={testSending}
              className="bg-[#022D52] hover:bg-[#01213e] text-white"
              data-testid="mon-bureau-test-confirm"
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
