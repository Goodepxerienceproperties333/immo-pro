import { useState, useEffect } from 'react';
import axios from 'axios';
import { useAuth } from '@/contexts/AuthContext';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Building2, LogIn, UserPlus, Eye, EyeOff, KeyRound, ShieldCheck } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

function formatError(detail) {
  if (detail == null) return 'Une erreur est survenue.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(' ');
  if (detail?.msg) return detail.msg;
  if (detail?.message) return detail.message;
  return String(detail);
}

export default function LoginPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState('login'); // 'login' | 'register' | 'first-set'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Detect ?invite=<email> in URL -> jump directly to first-set mode
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const invite = params.get('invite');
    if (invite) {
      setEmail(invite.toLowerCase().trim());
      setMode('first-set');
      setInfo("Bienvenue ! Veuillez definir votre mot de passe pour activer votre compte.");
    }
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(''); setInfo(''); setLoading(true);
    try {
      if (mode === 'register') {
        await register(email, password, name);
      } else if (mode === 'first-set') {
        if (password !== password2) {
          setError('Les deux mots de passe ne correspondent pas.');
          setLoading(false); return;
        }
        if ((password || '').length < 6) {
          setError('Mot de passe trop court (6 caracteres minimum).');
          setLoading(false); return;
        }
        const { data } = await axios.post(
          `${API}/api/auth/first-set-password`,
          { email, new_password: password },
          { withCredentials: true }
        );
        // Force a full reload so AuthContext re-fetches /api/auth/me with the new cookies
        window.location.href = data?.role === 'owner' ? '/portal' : '/';
      } else {
        await login(email, password);
      }
    } catch (err) {
      const detail = err.response?.data?.detail;
      // First-connection required: auto-switch to "first-set" mode
      if (err.response?.status === 403 && detail?.code === 'PASSWORD_SETUP_REQUIRED') {
        setMode('first-set');
        setPassword(''); setPassword2('');
        setInfo("Premiere connexion : merci de definir votre mot de passe pour " + email);
      } else {
        setError(formatError(detail) || err.message);
      }
    } finally {
      setLoading(false);
    }
  };

  const switchTo = (target) => {
    setMode(target);
    setError(''); setInfo('');
    setPassword(''); setPassword2(''); setName('');
  };

  const isRegister = mode === 'register';
  const isFirstSet = mode === 'first-set';

  let titleText = 'Connexion';
  let subtitleText = 'Connectez-vous a votre espace';
  if (isRegister) { titleText = 'Creer un compte syndic'; subtitleText = 'Reserve aux professionnels du syndic. Les proprietaires doivent utiliser le lien d\'invitation recu par email.'; }
  if (isFirstSet) { titleText = 'Definir mon mot de passe'; subtitleText = '1ere connexion : choisissez votre mot de passe'; }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-4 sm:p-8 bg-slate-50">
      {/* Logo grand en HAUT, au-dessus du formulaire */}
      <img
        src="/logo-nextge.png"
        alt="NextGe Copro"
        className="h-56 md:h-64 w-auto mb-6"
        data-testid="login-logo"
      />

      <div className="w-full max-w-md">
        <div className="bg-white shadow-2xl shadow-slate-900/10 rounded-2xl p-8 border border-slate-200/60">
          <h2 className="text-center text-xl font-bold text-slate-900 mb-1" style={{fontFamily:'Chivo,sans-serif'}}>{titleText}</h2>
          <p className="text-center text-sm text-slate-500 mb-6">{subtitleText}</p>

          {info && (
            <div className="bg-blue-50 text-blue-800 text-sm px-4 py-3 rounded-md mb-4 border border-blue-200" data-testid="auth-info">
              {info}
            </div>
          )}
          {error && (
            <div className="bg-red-50 text-red-700 text-sm px-4 py-3 rounded-md mb-4 border border-red-200" data-testid="auth-error">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {isRegister && (
              <div>
                <label className="form-label">Nom</label>
                <Input data-testid="register-name-input" value={name} onChange={e => setName(e.target.value)} placeholder="Votre nom" required />
              </div>
            )}
            <div>
              <label className="form-label">Email</label>
              <Input
                data-testid="login-email-input"
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="email@exemple.be"
                disabled={isFirstSet}
                required
              />
            </div>
            <div>
              <label className="form-label">{isFirstSet ? 'Nouveau mot de passe' : 'Mot de passe'}</label>
              <div className="relative">
                <Input
                  data-testid="login-password-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder={isFirstSet ? 'Au moins 6 caracteres' : 'Votre mot de passe'}
                  className="pr-10"
                  required
                />
                <button
                  type="button"
                  data-testid="login-toggle-password"
                  onClick={() => setShowPassword(s => !s)}
                  className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-500 hover:text-slate-900"
                  aria-label={showPassword ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>
            {isFirstSet && (
              <div>
                <label className="form-label">Confirmer le mot de passe</label>
                <Input
                  data-testid="login-password-confirm-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password2}
                  onChange={e => setPassword2(e.target.value)}
                  placeholder="Re-saisissez le mot de passe"
                  required
                />
              </div>
            )}
            <Button
              type="submit"
              data-testid="login-submit-btn"
              disabled={loading}
              className="w-full bg-[#022D52] hover:bg-[#1D4ED8] text-white font-semibold"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Chargement...
                </span>
              ) : isRegister ? (
                <span className="flex items-center gap-2"><UserPlus size={16} /> Creer le compte</span>
              ) : isFirstSet ? (
                <span className="flex items-center gap-2"><KeyRound size={16} /> Definir et se connecter</span>
              ) : (
                <span className="flex items-center gap-2"><LogIn size={16} /> Se connecter</span>
              )}
            </Button>
          </form>

          <div className="mt-6 text-center space-y-2">
            {!isFirstSet && (
              <button
                onClick={() => switchTo(isRegister ? 'login' : 'register')}
                className="text-sm text-[#022D52] hover:underline block w-full"
                data-testid="toggle-auth-mode"
              >
                {isRegister ? 'Deja un compte ? Se connecter' : 'Pas de compte ? Creer un compte'}
              </button>
            )}
            {mode === 'login' && (
              <a
                href="/forgot-password"
                className="text-sm text-slate-600 hover:text-[#022D52] hover:underline block w-full"
                data-testid="forgot-password-link"
              >
                Mot de passe oublie ?
              </a>
            )}
            {mode === 'login' && (
              <button
                onClick={() => switchTo('first-set')}
                className="text-sm text-slate-600 hover:text-[#022D52] hover:underline block w-full"
                data-testid="toggle-first-set"
              >
                1ere connexion ? Definir mon mot de passe
              </button>
            )}
            {isFirstSet && (
              <button
                onClick={() => switchTo('login')}
                className="text-sm text-slate-600 hover:text-[#022D52] hover:underline block w-full"
                data-testid="back-to-login"
              >
                Retour a la connexion
              </button>
            )}
          </div>

          {/* Legal footer links */}
          <div className="mt-6 pt-4 border-t border-slate-100">
            <div className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
              <a href="/legal/cgu" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="login-link-cgu">CGU</a>
              <span>·</span>
              <a href="/legal/privacy" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="login-link-privacy">Confidentialite</a>
              <span>·</span>
              <a href="/legal/mentions" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="login-link-mentions">Mentions Legales</a>
              <span>·</span>
              <a href="/legal/cookies" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="login-link-cookies">Cookies</a>
              <span>·</span>
              <a href="https://www.nextgecopro.be/data-act" target="_blank" rel="noopener noreferrer" className="hover:text-[#022D52] hover:underline" data-testid="login-link-data-act">Registre Data Act</a>
            </div>
          </div>
        </div>

        {/* Badge RGPD - donnees hebergees en Europe */}
        <div
          className="mt-4 flex items-center justify-center gap-2 py-2 px-3 rounded-full bg-white/70 backdrop-blur border border-slate-200 shadow-sm w-fit mx-auto"
          data-testid="eu-hosting-badge"
          title="Vos donnees sont stockees exclusivement sur des serveurs situes dans l'Union Europeenne, en conformite avec le RGPD."
        >
          {/* Drapeau UE stylise : cercle de 12 etoiles jaunes sur fond bleu */}
          <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <circle cx="12" cy="12" r="11" fill="#003399" />
            {Array.from({ length: 12 }).map((_, i) => {
              const angle = (i * 30 - 90) * (Math.PI / 180);
              const cx = 12 + Math.cos(angle) * 7;
              const cy = 12 + Math.sin(angle) * 7;
              return <circle key={i} cx={cx} cy={cy} r="0.9" fill="#FFCC00" />;
            })}
          </svg>
          <span className="text-[11px] font-semibold text-slate-700 tracking-tight">
            Donnees hebergees en Europe
          </span>
          <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-teal-700 bg-teal-50 border border-teal-200 rounded-full px-1.5 py-0.5">
            <ShieldCheck size={10} strokeWidth={2.5} />
            RGPD
          </span>
        </div>
      </div>
    </div>
  );
}
