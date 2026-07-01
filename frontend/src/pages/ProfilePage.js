import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { UserCog, Save, KeyRound, Mail, Shield, FileText, ScrollText, Cookie } from 'lucide-react';
import RgpdSection from '@/components/RgpdSection';

const ROLE_META = {
  superadmin: { label: 'Super Administrateur', color: 'bg-purple-100 text-purple-800 border-purple-300', desc: "Gere les acces a la plateforme (creation/modification des utilisateurs)." },
  admin: { label: 'Super Administrateur', color: 'bg-purple-100 text-purple-800 border-purple-300', desc: "Gere les acces a la plateforme." },
  syndic: { label: 'Syndic', color: 'bg-red-50 text-red-700 border-red-200', desc: "Acces total aux donnees comptables et a la gestion des coproprietes." },
  gestionnaire: { label: 'Gestionnaire', color: 'bg-blue-50 text-blue-700 border-blue-200', desc: "Gestion operationnelle des coproprietes." },
  owner: { label: 'Proprietaire', color: 'bg-green-50 text-green-700 border-green-200', desc: "Acces a son portail proprietaire personnel." },
};

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const [name, setName] = useState(user?.name || '');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingName, setSavingName] = useState(false);
  const [savingPwd, setSavingPwd] = useState(false);

  const meta = ROLE_META[user?.role] || ROLE_META.owner;

  const saveName = async () => {
    if (!name.trim()) { toast.error('Le nom ne peut pas etre vide'); return; }
    setSavingName(true);
    try {
      await api.put('/auth/me', { name: name.trim() });
      toast.success('Profil mis a jour. Reconnectez-vous pour voir le changement dans le menu.');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally {
      setSavingName(false);
    }
  };

  const changePassword = async () => {
    if (!currentPassword) { toast.error('Saisissez votre mot de passe actuel'); return; }
    if (!newPassword || newPassword.length < 6) { toast.error('Le nouveau mot de passe doit contenir au moins 6 caracteres'); return; }
    if (newPassword !== confirmPassword) { toast.error('Les 2 mots de passe ne correspondent pas'); return; }
    setSavingPwd(true);
    try {
      await api.put('/auth/me', { current_password: currentPassword, new_password: newPassword });
      toast.success('Mot de passe modifie. Vous allez etre deconnecte.');
      setCurrentPassword(''); setNewPassword(''); setConfirmPassword('');
      setTimeout(() => { logout(); window.location.href = '/login'; }, 1500);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally {
      setSavingPwd(false);
    }
  };

  return (
    <div data-testid="profile-page" className="max-w-3xl mx-auto">
      <div className="page-header">
        <h1 className="page-title"><UserCog size={24} className="inline mr-2" />Mon profil</h1>
        <p className="page-subtitle">Gerer vos informations personnelles et votre mot de passe</p>
      </div>

      {/* Identite & role (read-only) */}
      <Card className="mb-6 border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
            <Shield size={16} className="text-slate-500" />
            Identite & role
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <Mail size={16} className="text-slate-400" />
            <div className="flex-1">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Adresse email</div>
              <div className="text-sm font-mono text-slate-800" data-testid="profile-email">{user?.email}</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Shield size={16} className="text-slate-400" />
            <div className="flex-1">
              <div className="text-[11px] uppercase tracking-wide text-slate-500">Role sur la plateforme</div>
              <div className="flex items-center gap-2 mt-1">
                <Badge variant="outline" className={`${meta.color} border`} data-testid="profile-role">{meta.label}</Badge>
                <span className="text-xs text-slate-500">{meta.desc}</span>
              </div>
            </div>
          </div>
          <div className="text-[11px] text-slate-400 italic pt-2 border-t border-slate-100">
            L&apos;email et le role ne peuvent etre modifies que par un super administrateur.
          </div>
        </CardContent>
      </Card>

      {/* Nom */}
      <Card className="mb-6 border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
            <UserCog size={16} className="text-slate-500" />
            Nom affiche
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <label className="form-label">Nom complet</label>
            <Input value={name} onChange={e => setName(e.target.value)} data-testid="profile-name-input" />
          </div>
          <div className="flex justify-end">
            <Button onClick={saveName} disabled={savingName} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="profile-save-name-btn">
              <Save size={14} className="mr-2" /> {savingName ? 'Sauvegarde...' : 'Enregistrer'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Mot de passe */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
            <KeyRound size={16} className="text-slate-500" />
            Changer mon mot de passe
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <label className="form-label">Mot de passe actuel</label>
            <Input type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)} data-testid="profile-current-pwd" />
          </div>
          <div>
            <label className="form-label">Nouveau mot de passe (6 caracteres min)</label>
            <Input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} data-testid="profile-new-pwd" />
          </div>
          <div>
            <label className="form-label">Confirmer le nouveau mot de passe</label>
            <Input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)} data-testid="profile-confirm-pwd" />
          </div>
          <div className="text-[11px] text-amber-600 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5">
            Apres modification, vous serez automatiquement deconnecte et devrez vous reconnecter.
          </div>
          <div className="flex justify-end">
            <Button onClick={changePassword} disabled={savingPwd} className="bg-[#0055FF] hover:bg-[#0040CC]" data-testid="profile-change-pwd-btn">
              <KeyRound size={14} className="mr-2" /> {savingPwd ? 'Modification...' : 'Modifier le mot de passe'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* RGPD - Mes donnees */}
      <div className="mt-6">
        <RgpdSection />
      </div>

      {/* Liens documents legaux */}
      <Card className="mt-6 border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base flex items-center gap-2" style={{fontFamily:'Chivo,sans-serif'}}>
            <FileText size={16} className="text-slate-500" />
            Documents legaux
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <Link to="/legal/cgu" target="_blank" data-testid="profile-link-cgu" className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF] transition-colors">
              <ScrollText size={12} /> CGU
            </Link>
            <Link to="/legal/privacy" target="_blank" data-testid="profile-link-privacy" className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF] transition-colors">
              <Shield size={12} /> Politique de Confidentialite
            </Link>
            <Link to="/legal/mentions" target="_blank" data-testid="profile-link-mentions" className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF] transition-colors">
              <FileText size={12} /> Mentions Legales
            </Link>
            <Link to="/legal/cookies" target="_blank" data-testid="profile-link-cookies" className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF] transition-colors">
              <Cookie size={12} /> Cookies
            </Link>
            <Link to="/legal/disclaimer" target="_blank" data-testid="profile-link-disclaimer" className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded border border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF] transition-colors">
              <FileText size={12} /> Disclaimer
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
