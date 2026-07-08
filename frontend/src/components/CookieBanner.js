import { useEffect, useState } from 'react';
import { Cookie, X } from 'lucide-react';
import { Link } from 'react-router-dom';

const STORAGE_KEY = 'copromgr_cookie_ack_v1';

export default function CookieBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      const ack = localStorage.getItem(STORAGE_KEY);
      if (!ack) setVisible(true);
    } catch {
      setVisible(true);
    }
  }, []);

  const dismiss = () => {
    try {
      localStorage.setItem(STORAGE_KEY, new Date().toISOString());
    } catch {}
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div
      data-testid="cookie-banner"
      className="fixed bottom-4 left-4 right-4 md:left-auto md:right-6 md:max-w-md z-[9999] bg-slate-900 text-slate-100 border border-slate-700 rounded-lg shadow-2xl px-4 py-3 flex items-start gap-3"
      style={{ fontFamily: 'Inter, system-ui, sans-serif' }}
    >
      <Cookie size={18} className="text-amber-400 shrink-0 mt-0.5" />
      <div className="flex-1 text-[13px] leading-snug">
        <div className="font-semibold mb-1">Cookies techniques uniquement</div>
        <div className="text-slate-300">
          CoproManager utilise uniquement des cookies indispensables (authentification, session).
          Aucun cookie de tracking ni publicitaire.{' '}
          <Link
            to="/legal/cookies"
            className="underline text-amber-300 hover:text-amber-200"
            data-testid="cookie-banner-more"
          >
            En savoir plus
          </Link>
        </div>
      </div>
      <div className="flex flex-col gap-1.5 shrink-0">
        <button
          onClick={dismiss}
          data-testid="cookie-banner-accept"
          className="bg-[#2563EB] hover:bg-[#1D4ED8] text-white text-xs font-semibold px-3 py-1.5 rounded transition-colors whitespace-nowrap"
        >
          J&apos;ai compris
        </button>
        <button
          onClick={dismiss}
          data-testid="cookie-banner-close"
          className="text-slate-400 hover:text-white text-xs flex items-center justify-center gap-1"
          aria-label="Fermer"
        >
          <X size={12} /> Fermer
        </button>
      </div>
    </div>
  );
}
