import { useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Building2, LogIn, UserPlus } from 'lucide-react';

function formatError(detail) {
  if (detail == null) return "Une erreur est survenue.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(e => e?.msg || JSON.stringify(e)).join(" ");
  if (detail?.msg) return detail.msg;
  return String(detail);
}

export default function LoginPage() {
  const { login, register } = useAuth();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      if (isRegister) {
        await register(email, password, name);
      } else {
        await login(email, password);
      }
    } catch (err) {
      setError(formatError(err.response?.data?.detail) || err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex">
      {/* Left panel - form */}
      <div className="w-full lg:w-1/2 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          <div className="flex items-center gap-3 mb-8">
            <div className="w-10 h-10 rounded bg-[#0055FF] flex items-center justify-center text-white font-bold">
              <Building2 size={22} />
            </div>
            <div>
              <h1 className="text-2xl font-black tracking-tighter text-slate-950" style={{fontFamily:'Chivo,sans-serif'}}>
                CoproManager
              </h1>
              <p className="text-xs text-slate-500">Gestion de copropriete</p>
            </div>
          </div>

          <h2 className="text-xl font-bold text-slate-900 mb-1" style={{fontFamily:'Chivo,sans-serif'}}>
            {isRegister ? 'Creer un compte' : 'Connexion'}
          </h2>
          <p className="text-sm text-slate-500 mb-6">
            {isRegister ? 'Remplissez les informations ci-dessous' : 'Connectez-vous a votre espace'}
          </p>

          {error && (
            <div className="bg-red-50 text-red-700 text-sm px-4 py-3 rounded-md mb-4 border border-red-200" data-testid="auth-error">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {isRegister && (
              <div>
                <label className="form-label">Nom</label>
                <Input
                  data-testid="register-name-input"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="Votre nom"
                  required
                />
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
                required
              />
            </div>
            <div>
              <label className="form-label">Mot de passe</label>
              <Input
                data-testid="login-password-input"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Votre mot de passe"
                required
              />
            </div>
            <Button
              type="submit"
              data-testid="login-submit-btn"
              disabled={loading}
              className="w-full bg-[#0055FF] hover:bg-[#0040CC] text-white font-semibold"
            >
              {loading ? (
                <span className="flex items-center gap-2">
                  <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Chargement...
                </span>
              ) : isRegister ? (
                <span className="flex items-center gap-2"><UserPlus size={16} /> Creer le compte</span>
              ) : (
                <span className="flex items-center gap-2"><LogIn size={16} /> Se connecter</span>
              )}
            </Button>
          </form>

          <div className="mt-6 text-center">
            <button
              onClick={() => { setIsRegister(!isRegister); setError(''); }}
              className="text-sm text-[#0055FF] hover:underline"
              data-testid="toggle-auth-mode"
            >
              {isRegister ? 'Deja un compte ? Se connecter' : 'Pas de compte ? Creer un compte'}
            </button>
          </div>
        </div>
      </div>

      {/* Right panel - hero image */}
      <div
        className="hidden lg:block lg:w-1/2 bg-cover bg-center relative"
        style={{backgroundImage: 'url(https://static.prod-images.emergentagent.com/jobs/ac7212f5-7097-4143-9040-c86e8f3b4207/images/44f29d66e326ce8d9c843c10ae9d5d76da988703cdae641f58ec60defdc6aad7.png)'}}
      >
        <div className="absolute inset-0 bg-slate-950/20" />
        <div className="absolute bottom-8 left-8 right-8">
          <p className="text-white/80 text-sm">
            Gestion professionnelle de copropriete selon le droit belge
          </p>
        </div>
      </div>
    </div>
  );
}
