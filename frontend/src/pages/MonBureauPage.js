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
import { toast } from 'sonner';
import {
  Building2, Save, ImageIcon, FileText, Upload, CheckCircle2,
  Info,
} from 'lucide-react';

export default function MonBureauPage() {
  const [cfg, setCfg] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [logoCacheBuster, setLogoCacheBuster] = useState(Date.now());
  const fileInputRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/syndic-config/me');
      setCfg(r.data || {});
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
    </div>
  );
}
