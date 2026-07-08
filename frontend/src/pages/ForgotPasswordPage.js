import { useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Building2, Mail, ArrowLeft, CheckCircle2 } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      // Backend is enumeration-safe: always returns 200 with the same generic message
      await axios.post(`${API}/api/auth/forgot-password`, { email: email.trim().toLowerCase() });
    } catch (_) {
      // Ignore: backend never reveals whether the email exists, so treat any error as soft-success
    } finally {
      setSent(true);
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6" data-testid="forgot-password-page">
      <div className="w-full max-w-md bg-white rounded-xl shadow-sm border border-slate-200 p-8">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded bg-[#2563EB] flex items-center justify-center text-white">
            <Building2 size={22} />
          </div>
          <div>
            <h1 className="text-2xl font-black tracking-tighter text-slate-950" style={{ fontFamily: 'Chivo,sans-serif' }}>
              CoproManager
            </h1>
            <p className="text-xs text-slate-500">Mot de passe oublie</p>
          </div>
        </div>

        {sent ? (
          <div data-testid="forgot-password-sent">
            <div className="flex items-start gap-3 bg-emerald-50 border border-emerald-200 rounded-md p-4 mb-4">
              <CheckCircle2 size={20} className="text-emerald-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-emerald-800">
                <div className="font-semibold mb-1">Demande prise en compte.</div>
                Si cette adresse correspond a un compte actif, vous recevrez un email
                de reinitialisation dans quelques minutes. Le lien sera valable <b>1 heure</b>.
              </div>
            </div>
            <p className="text-xs text-slate-500 mb-4">
              Pensez a verifier votre dossier &laquo;&nbsp;courrier indesirable&nbsp;&raquo;.
              Si vous ne recevez rien, contactez votre syndic pour verifier que votre acces est bien actif.
            </p>
            <Link to="/login" className="text-sm text-[#2563EB] hover:underline flex items-center gap-1" data-testid="back-to-login-link">
              <ArrowLeft size={14} /> Retour a la connexion
            </Link>
          </div>
        ) : (
          <>
            <h2 className="text-xl font-bold text-slate-900 mb-1" style={{ fontFamily: 'Chivo,sans-serif' }}>
              Mot de passe oublie ?
            </h2>
            <p className="text-sm text-slate-500 mb-6">
              Saisissez votre adresse email. Nous vous enverrons un lien pour reinitialiser votre mot de passe.
            </p>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="form-label">Email</label>
                <div className="relative">
                  <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input
                    data-testid="forgot-email-input"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="email@exemple.be"
                    className="pl-9"
                    required
                  />
                </div>
              </div>
              <Button
                type="submit"
                disabled={loading || !email}
                className="w-full bg-[#2563EB] hover:bg-[#1D4ED8] text-white font-semibold"
                data-testid="forgot-submit-btn"
              >
                {loading ? 'Envoi en cours...' : 'Envoyer le lien de reinitialisation'}
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
