import { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import axios from 'axios';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Building2, KeyRound, Eye, EyeOff, AlertTriangle, ArrowLeft } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

function formatError(detail) {
  if (detail == null) return 'Une erreur est survenue.';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((e) => e?.msg || JSON.stringify(e)).join(' ');
  if (detail?.msg) return detail.msg;
  return String(detail);
}

export default function ResetPasswordPage() {
  const navigate = useNavigate();
  const [token, setToken] = useState('');
  const [password, setPassword] = useState('');
  const [password2, setPassword2] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [missingToken, setMissingToken] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get('token');
    if (!t || t.length < 16) {
      setMissingToken(true);
    } else {
      setToken(t);
    }
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 6) {
      setError('Le mot de passe doit contenir au moins 6 caracteres.');
      return;
    }
    if (password !== password2) {
      setError('Les deux mots de passe ne correspondent pas.');
      return;
    }
    setLoading(true);
    try {
      const { data } = await axios.post(
        `${API}/api/auth/reset-password`,
        { token, new_password: password },
        { withCredentials: true }
      );
      // Logged in via cookies set by the backend response. Redirect by role.
      const target = data?.role === 'owner' ? '/portal' : '/';
      window.location.href = target;
    } catch (err) {
      setError(formatError(err.response?.data?.detail) || err.message);
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6" data-testid="reset-password-page">
      <div className="w-full max-w-md bg-white rounded-xl shadow-sm border border-slate-200 p-8">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded bg-[#2563EB] flex items-center justify-center text-white">
            <Building2 size={22} />
          </div>
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-slate-950" style={{ fontFamily: 'Chivo,sans-serif' }}>
              CoproManager
            </h1>
            <p className="text-xs text-slate-500">Reinitialisation de mot de passe</p>
          </div>
        </div>

        {missingToken ? (
          <div data-testid="reset-missing-token">
            <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-md p-4 mb-4">
              <AlertTriangle size={20} className="text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-amber-800">
                <div className="font-semibold mb-1">Lien invalide</div>
                Le lien de reinitialisation est manquant ou invalide. Demandez un nouveau lien.
              </div>
            </div>
            <button
              onClick={() => navigate('/forgot-password')}
              className="w-full bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-semibold rounded-md px-4 py-2"
              data-testid="reset-request-new-link"
            >
              Demander un nouveau lien
            </button>
          </div>
        ) : (
          <>
            <h2 className="text-xl font-bold text-slate-900 mb-1" style={{ fontFamily: 'Chivo,sans-serif' }}>
              Choisissez votre nouveau mot de passe
            </h2>
            <p className="text-sm text-slate-500 mb-6">
              Le lien est valable <b>1 heure</b> et ne peut etre utilise qu&apos;une seule fois.
            </p>

            {error && (
              <div className="bg-red-50 text-red-700 text-sm px-4 py-3 rounded-md mb-4 border border-red-200" data-testid="reset-error">
                {error}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="form-label">Nouveau mot de passe</label>
                <div className="relative">
                  <Input
                    data-testid="reset-password-input"
                    type={showPassword ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Au moins 6 caracteres"
                    className="pr-10"
                    required
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-500 hover:text-slate-900"
                    tabIndex={-1}
                    data-testid="reset-toggle-password"
                  >
                    {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>
              <div>
                <label className="form-label">Confirmer le mot de passe</label>
                <Input
                  data-testid="reset-password-confirm-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password2}
                  onChange={(e) => setPassword2(e.target.value)}
                  placeholder="Re-saisissez le mot de passe"
                  required
                />
              </div>
              <Button
                type="submit"
                disabled={loading || !password || !password2}
                className="w-full bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-semibold"
                data-testid="reset-submit-btn"
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Reinitialisation...
                  </span>
                ) : (
                  <span className="flex items-center gap-2"><KeyRound size={16} /> Definir et me connecter</span>
                )}
              </Button>
            </form>
            <div className="mt-6 text-center">
              <Link to="/login" className="text-sm text-slate-600 hover:text-[#2563EB] hover:underline" data-testid="back-to-login-link">
                <ArrowLeft size={14} className="inline mr-1" /> Retour a la connexion
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
