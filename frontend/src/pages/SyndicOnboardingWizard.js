import { useState, useEffect } from 'react';
import api, { extractApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Building2, Image as ImageIcon, FileSignature, Mail, CheckCircle2, ArrowRight,
  Upload, TriangleAlert, ShieldCheck,
} from 'lucide-react';

const BACKEND = process.env.REACT_APP_BACKEND_URL || '';

// ============ Onboarding Wizard (5 etapes) ============
export default function SyndicOnboardingWizard() {
  const { user, syndicConfig, refreshSyndicConfig } = useAuth();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);

  // Form data
  const [logoFile, setLogoFile] = useState(null);
  const [ident, setIdent] = useState({
    legal_name: '', display_name: '', address: '', postal_code: '', city: '',
    country: 'Belgique', email: '', phone: '', bce: '', tva: '', ipi_number: '',
  });
  const [legalMentions, setLegalMentions] = useState('');
  const [emailCfg, setEmailCfg] = useState({
    provider: 'none', graph_tenant_id: '', graph_client_id: '', graph_client_secret: '',
    smtp_host: '', smtp_port: 587, smtp_username: '', smtp_password: '', smtp_use_tls: true,
  });

  // Preload existing config
  useEffect(() => {
    if (!syndicConfig) return;
    setIdent({
      legal_name: syndicConfig.legal_name || '',
      display_name: syndicConfig.display_name || '',
      address: syndicConfig.address || '',
      postal_code: syndicConfig.postal_code || '',
      city: syndicConfig.city || '',
      country: syndicConfig.country || 'Belgique',
      email: syndicConfig.email || '',
      phone: syndicConfig.phone || '',
      bce: syndicConfig.bce || '',
      tva: syndicConfig.tva || '',
      ipi_number: syndicConfig.ipi_number || '',
    });
    setLegalMentions(syndicConfig.legal_mentions || '');
    setEmailCfg((prev) => ({
      ...prev,
      provider: syndicConfig.email_provider || 'none',
      graph_tenant_id: syndicConfig.graph_tenant_id || '',
      graph_client_id: syndicConfig.graph_client_id || '',
      smtp_host: syndicConfig.smtp_host || '',
      smtp_port: syndicConfig.smtp_port || 587,
      smtp_username: syndicConfig.smtp_username || '',
      smtp_use_tls: syndicConfig.smtp_use_tls !== false,
    }));
    // iter90av : Auto-open pour syndic sans onboarding.
    // ATTENTION : attend que le welcome tour (user.onboarding_completed=true)
    // soit termine pour eviter les modaux qui se superposent (iter44 report).
    // iter90bo : ATTEND AUSSI l'acceptation des CGU. Sinon le Radix Dialog
    // de ce wizard desactive pointer-events sur <body>, ce qui bloque tous
    // les clics du LegalAcceptanceModal (div custom, hors portal Radix).
    // Bug prod : "syndic ne peuvent pas cliquer pour accepter les conditions".
    const platformTourDone = user?.onboarding_completed === true;
    if (user?.role === 'syndic' && platformTourDone && !syndicConfig.onboarding_completed) {
      let cancelled = false;
      (async () => {
        try {
          const r = await api.get('/legal/my-acceptance');
          if (cancelled) return;
          // N'ouvre le wizard QUE si les conditions legales sont a jour.
          // Sinon le LegalAcceptanceModal doit passer en premier.
          if (!r.data?.needs_accept) setOpen(true);
        } catch {
          // Fail-open : si l'endpoint plante, on n'ouvre pas le wizard
          // (mieux vaut laisser passer que bloquer les CGU).
        }
      })();
      return () => { cancelled = true; };
    }
  }, [syndicConfig, user]);

  const saveIdent = async () => {
    setSaving(true);
    try {
      await api.put('/syndic-config/me', ident);
      return true;
    } catch (e) { toast.error(extractApiError(e)); return false; }
    finally { setSaving(false); }
  };
  const saveLegal = async () => {
    setSaving(true);
    try {
      await api.put('/syndic-config/me', { legal_mentions: legalMentions });
      return true;
    } catch (e) { toast.error(extractApiError(e)); return false; }
    finally { setSaving(false); }
  };
  const uploadLogo = async () => {
    if (!logoFile) return true;
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append('file', logoFile);
      await api.post('/syndic-config/me/logo', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success('Logo enregistre');
      return true;
    } catch (e) { toast.error(extractApiError(e)); return false; }
    finally { setSaving(false); }
  };
  const saveEmail = async () => {
    if (emailCfg.provider === 'none') return true;
    setSaving(true);
    try {
      await api.put('/syndic-config/me/email', emailCfg);
      return true;
    } catch (e) { toast.error(extractApiError(e)); return false; }
    finally { setSaving(false); }
  };
  const complete = async () => {
    setSaving(true);
    try {
      await api.post('/syndic-config/me/complete-onboarding');
      toast.success('Onboarding termine ! Vos PDF utiliseront desormais votre entete cabinet.');
      await refreshSyndicConfig();
      setOpen(false);
    } catch (e) {
      toast.error(extractApiError(e));
    } finally { setSaving(false); }
  };

  const next = async () => {
    if (step === 1 && !(await uploadLogo())) return;
    if (step === 2 && !(await saveIdent())) return;
    if (step === 3 && !(await saveLegal())) return;
    if (step === 4 && !(await saveEmail())) return;
    if (step === 5) return complete();
    setStep(step + 1);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-3xl" data-testid="onboarding-wizard">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5 text-[#022D52]" />
            Bienvenue ! Configurez votre cabinet syndic
          </DialogTitle>
        </DialogHeader>

        {/* Progress bar */}
        <div className="flex items-center gap-1 mb-2">
          {[1, 2, 3, 4, 5].map((s) => (
            <div
              key={s}
              className={`h-1.5 flex-1 rounded ${step >= s ? 'bg-[#022D52]' : 'bg-slate-200'}`}
              data-testid={`onboarding-step-indicator-${s}`}
            />
          ))}
        </div>
        <div className="text-xs text-slate-500 mb-4">Etape {step} / 5</div>

        {step === 1 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-lg font-semibold">
              <ImageIcon className="h-5 w-5 text-violet-600" /> Logo du cabinet
            </div>
            <p className="text-sm text-slate-600">
              Ce logo apparaitra en <b>haut a gauche</b> de chaque PDF que vous envoyez a vos proprietaires.
              Format PNG/JPEG, largeur max 40mm, taille max 3 Mo.
            </p>
            <label className="border-2 border-dashed border-slate-300 rounded-lg p-6 text-center block cursor-pointer hover:bg-slate-50">
              <Upload className="h-10 w-10 mx-auto text-slate-400 mb-2" />
              <input type="file" accept="image/png,image/jpeg" className="hidden"
                     onChange={(e) => setLogoFile(e.target.files?.[0] || null)}
                     data-testid="onboarding-input-logo" />
              {logoFile ? (
                <div className="text-sm font-medium text-emerald-700">{logoFile.name} ({(logoFile.size / 1024).toFixed(0)} Ko)</div>
              ) : syndicConfig?.has_logo ? (
                <div className="text-sm text-emerald-700 flex items-center justify-center gap-1">
                  <CheckCircle2 className="h-4 w-4" /> Logo deja configure (cliquer pour remplacer)
                </div>
              ) : (
                <div className="text-sm text-slate-500">Cliquer pour selectionner un fichier</div>
              )}
            </label>
            {logoFile && (
              <img src={URL.createObjectURL(logoFile)} alt="preview" className="max-h-20 mx-auto" data-testid="logo-preview" />
            )}
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-lg font-semibold">
              <Building2 className="h-5 w-5 text-[#022D52]" /> Identite du cabinet
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Denomination legale *</Label>
                <Input value={ident.legal_name} onChange={(e) => setIdent({ ...ident, legal_name: e.target.value })}
                       data-testid="onboarding-input-legal-name" />
              </div>
              <div>
                <Label className="text-xs">Nom affiche</Label>
                <Input value={ident.display_name} onChange={(e) => setIdent({ ...ident, display_name: e.target.value })}
                       data-testid="onboarding-input-display-name" />
              </div>
              <div className="col-span-2">
                <Label className="text-xs">Adresse *</Label>
                <Input value={ident.address} onChange={(e) => setIdent({ ...ident, address: e.target.value })}
                       data-testid="onboarding-input-address" />
              </div>
              <div>
                <Label className="text-xs">Code postal</Label>
                <Input value={ident.postal_code} onChange={(e) => setIdent({ ...ident, postal_code: e.target.value })}
                       data-testid="onboarding-input-postal-code" />
              </div>
              <div>
                <Label className="text-xs">Ville *</Label>
                <Input value={ident.city} onChange={(e) => setIdent({ ...ident, city: e.target.value })}
                       data-testid="onboarding-input-city" />
              </div>
              <div>
                <Label className="text-xs">Email cabinet *</Label>
                <Input type="email" value={ident.email} onChange={(e) => setIdent({ ...ident, email: e.target.value })}
                       data-testid="onboarding-input-email" />
              </div>
              <div>
                <Label className="text-xs">Telephone</Label>
                <Input value={ident.phone} onChange={(e) => setIdent({ ...ident, phone: e.target.value })}
                       data-testid="onboarding-input-phone" />
              </div>
              <div>
                <Label className="text-xs">BCE</Label>
                <Input value={ident.bce} onChange={(e) => setIdent({ ...ident, bce: e.target.value })} placeholder="0123.456.789"
                       data-testid="onboarding-input-bce" />
              </div>
              <div>
                <Label className="text-xs">Agrement IPI (syndic professionnel)</Label>
                <Input value={ident.ipi_number} onChange={(e) => setIdent({ ...ident, ipi_number: e.target.value })} placeholder="506.123"
                       data-testid="onboarding-input-ipi" />
              </div>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-lg font-semibold">
              <FileSignature className="h-5 w-5 text-amber-600" /> Mentions legales pied de page
            </div>
            <p className="text-sm text-slate-600">
              Texte qui apparaitra en <b>pied de page</b> de chaque PDF (max 3 lignes).
              Ex : agrement IPI, TVA, RGPD, delai de reclamation.
            </p>
            <Textarea rows={4} value={legalMentions} onChange={(e) => setLegalMentions(e.target.value)}
                      placeholder="Ex : IPI 506.123 - TVA BE0123.456.789 - Reclamation dans les 15 jours"
                      data-testid="onboarding-textarea-legal" />
            <div className="text-xs text-slate-500">Preview :</div>
            <div className="border rounded p-2 bg-slate-50 text-[11px] text-slate-500 text-center">
              {legalMentions || <em>Vide</em>}
            </div>
          </div>
        )}

        {step === 4 && (
          <EmailConfigStep emailCfg={emailCfg} setEmailCfg={setEmailCfg} />
        )}

        {step === 5 && (
          <div className="space-y-4 text-center">
            <ShieldCheck className="h-16 w-16 text-emerald-500 mx-auto" />
            <div className="text-lg font-semibold">Tout est pret !</div>
            <div className="text-sm text-slate-600">
              Vous pourrez modifier ces informations a tout moment dans <b>Communication → Boites & signature</b>.
            </div>
          </div>
        )}

        <DialogFooter className="mt-4">
          {step > 1 && (
            <Button variant="ghost" onClick={() => setStep(step - 1)} disabled={saving}
                    data-testid="onboarding-btn-prev">
              Retour
            </Button>
          )}
          <Button onClick={next} disabled={saving} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="onboarding-btn-next">
            {step === 5 ? 'Terminer' : 'Suivant'} <ArrowRight className="h-4 w-4 ml-1" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Sous-composant Config email (reutilise onboarding + settings) ============
export function EmailConfigStep({ emailCfg, setEmailCfg }) {
  const [testTo, setTestTo] = useState('');
  const [testFrom, setTestFrom] = useState('');
  const [testing, setTesting] = useState(false);
  const { user } = useAuth();

  const sendTest = async () => {
    if (!testTo || !testFrom) return toast.error('Renseignez expediteur et destinataire');
    setTesting(true);
    try {
      // Sauvegarde d'abord la config email
      await api.put('/syndic-config/me/email', emailCfg);
      const r = await api.post('/syndic-config/me/test-email', { from_mailbox: testFrom, to: testTo });
      toast.success(`Email test envoye (provider: ${r.data.provider})`);
    } catch (e) {
      toast.error(extractApiError(e, 'Test echoue'));
    } finally { setTesting(false); }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-lg font-semibold">
        <Mail className="h-5 w-5 text-[#022D52]" /> Configuration serveur mail
      </div>
      <p className="text-sm text-slate-600">
        Optionnel. Sans configuration, la plateforme utilisera son serveur mail global.
      </p>
      <div>
        <Label className="text-xs">Fournisseur</Label>
        <Select value={emailCfg.provider} onValueChange={(v) => setEmailCfg({ ...emailCfg, provider: v })}>
          <SelectTrigger data-testid="onboarding-select-provider"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="none">Aucun (utiliser le fallback plateforme)</SelectItem>
            <SelectItem value="graph">Microsoft 365 / Graph API</SelectItem>
            <SelectItem value="smtp">SMTP (autre)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {emailCfg.provider === 'graph' && (
        <div className="grid grid-cols-1 gap-3">
          <div>
            <Label className="text-xs">Azure Tenant ID</Label>
            <Input value={emailCfg.graph_tenant_id}
                   onChange={(e) => setEmailCfg({ ...emailCfg, graph_tenant_id: e.target.value })}
                   placeholder="00000000-0000-0000-0000-000000000000"
                   data-testid="onboarding-input-graph-tenant" />
          </div>
          <div>
            <Label className="text-xs">Client ID</Label>
            <Input value={emailCfg.graph_client_id}
                   onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_id: e.target.value })}
                   data-testid="onboarding-input-graph-client" />
          </div>
          <div>
            <Label className="text-xs">Client Secret</Label>
            <Input type="password" value={emailCfg.graph_client_secret}
                   onChange={(e) => setEmailCfg({ ...emailCfg, graph_client_secret: e.target.value })}
                   placeholder="Laisser vide pour ne pas modifier"
                   data-testid="onboarding-input-graph-secret" />
          </div>
        </div>
      )}

      {emailCfg.provider === 'smtp' && (
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <Label className="text-xs">Serveur SMTP</Label>
            <Input value={emailCfg.smtp_host}
                   onChange={(e) => setEmailCfg({ ...emailCfg, smtp_host: e.target.value })}
                   placeholder="smtp.office365.com"
                   data-testid="onboarding-input-smtp-host" />
          </div>
          <div>
            <Label className="text-xs">Port</Label>
            <Input type="number" value={emailCfg.smtp_port}
                   onChange={(e) => setEmailCfg({ ...emailCfg, smtp_port: parseInt(e.target.value) || 0 })}
                   data-testid="onboarding-input-smtp-port" />
          </div>
          <div className="flex items-end gap-2">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={emailCfg.smtp_use_tls}
                     onChange={(e) => setEmailCfg({ ...emailCfg, smtp_use_tls: e.target.checked })}
                     data-testid="onboarding-checkbox-smtp-tls" />
              STARTTLS
            </label>
          </div>
          <div>
            <Label className="text-xs">Utilisateur</Label>
            <Input value={emailCfg.smtp_username}
                   onChange={(e) => setEmailCfg({ ...emailCfg, smtp_username: e.target.value })}
                   data-testid="onboarding-input-smtp-user" />
          </div>
          <div>
            <Label className="text-xs">Mot de passe</Label>
            <Input type="password" value={emailCfg.smtp_password}
                   onChange={(e) => setEmailCfg({ ...emailCfg, smtp_password: e.target.value })}
                   placeholder="Laisser vide pour ne pas modifier"
                   data-testid="onboarding-input-smtp-password" />
          </div>
        </div>
      )}

      {emailCfg.provider !== 'none' && (
        <Card className="mt-4">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Tester la configuration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <Input placeholder={`Expediteur (${user?.email || 'boite@cabinet.be'})`} value={testFrom}
                     onChange={(e) => setTestFrom(e.target.value)}
                     data-testid="onboarding-input-test-from" />
              <Input type="email" placeholder="Destinataire test" value={testTo}
                     onChange={(e) => setTestTo(e.target.value)}
                     data-testid="onboarding-input-test-to" />
            </div>
            <Button onClick={sendTest} disabled={testing} variant="outline" size="sm"
                    data-testid="onboarding-btn-test-email">
              <Mail className="h-4 w-4 mr-1" /> Envoyer email test
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ============ Petit banner d'invitation (affiche si onboarding pas complete) ============
export function OnboardingBanner() {
  const { user, syndicConfig } = useAuth();
  if (!user || user.role !== 'syndic') return null;
  if (!syndicConfig || syndicConfig.onboarding_completed) return null;
  return (
    <div className="bg-gradient-to-r from-blue-50 to-violet-50 border-l-4 border-blue-500 p-3 text-sm flex items-center gap-2"
         data-testid="onboarding-banner">
      <TriangleAlert className="h-5 w-5 text-[#022D52] shrink-0" />
      <div className="flex-1">
        <b>Configurez votre cabinet</b> pour personnaliser vos PDF et emails (logo, adresse, mentions legales).
      </div>
      <Badge className="bg-[#022D52]">A completer</Badge>
    </div>
  );
}
