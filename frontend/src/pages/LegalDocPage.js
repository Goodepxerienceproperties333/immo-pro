import { useEffect, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ArrowLeft, ScrollText, Shield, FileText, Cookie, AlertTriangle, Loader2 } from 'lucide-react';

const API = process.env.REACT_APP_BACKEND_URL;

const SLUG_META = {
  cgu: { label: 'Conditions Generales d\'Utilisation', icon: ScrollText },
  privacy: { label: 'Politique de Confidentialite', icon: Shield },
  mentions: { label: 'Mentions Legales', icon: FileText },
  cookies: { label: 'Politique de Cookies', icon: Cookie },
  disclaimer: { label: 'Disclaimer Comptable', icon: AlertTriangle },
};

const ALL_SLUGS = ['cgu', 'privacy', 'mentions', 'cookies', 'disclaimer'];

export default function LegalDocPage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!slug || !SLUG_META[slug]) {
      setError('Document introuvable');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    // Public endpoint - no credentials needed
    axios.get(`${API}/api/legal/documents/${slug}`)
      .then((r) => { if (!cancelled) setDoc(r.data); })
      .catch(() => { if (!cancelled) setError('Impossible de charger le document.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [slug]);

  const Meta = SLUG_META[slug] || { label: 'Document', icon: FileText };
  const Icon = Meta.icon;

  return (
    <div className="min-h-screen bg-slate-50" style={{ fontFamily: 'Inter, system-ui, sans-serif' }} data-testid={`legal-page-${slug}`}>
      <header className="bg-white border-b border-slate-200 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="text-slate-500 hover:text-slate-800 flex items-center gap-1 text-sm"
            data-testid="legal-back-btn"
          >
            <ArrowLeft size={16} /> Retour
          </button>
          <div className="flex-1" />
          <Link to="/" className="text-[#0055FF] font-semibold text-sm hover:underline">
            CoproManager
          </Link>
        </div>
      </header>

      {/* Cross-links between legal docs */}
      <nav className="max-w-4xl mx-auto px-6 pt-4 flex flex-wrap gap-2">
        {ALL_SLUGS.map((s) => {
          const M = SLUG_META[s];
          const active = s === slug;
          const I = M.icon;
          return (
            <Link
              key={s}
              to={`/legal/${s}`}
              data-testid={`legal-nav-${s}`}
              className={`inline-flex items-center gap-1.5 text-[12px] px-2.5 py-1 rounded border transition-colors ${
                active
                  ? 'bg-[#0055FF] text-white border-[#0055FF]'
                  : 'bg-white text-slate-600 border-slate-200 hover:border-[#0055FF] hover:text-[#0055FF]'
              }`}
            >
              <I size={12} /> {M.label}
            </Link>
          );
        })}
      </nav>

      <main className="max-w-4xl mx-auto px-6 py-6">
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm p-8">
          <div className="flex items-start gap-4 mb-6 pb-4 border-b border-slate-100">
            <div className="w-12 h-12 rounded-full bg-[#0055FF]/10 flex items-center justify-center shrink-0">
              <Icon size={22} className="text-[#0055FF]" />
            </div>
            <div className="flex-1">
              <h1 className="text-2xl font-semibold text-slate-900" style={{ fontFamily: 'Chivo, sans-serif' }}>
                {doc?.title || Meta.label}
              </h1>
              {doc && (
                <p className="text-xs text-slate-500 mt-1">
                  Version {doc.version} — Mise a jour {doc.updated_at ? new Date(doc.updated_at).toLocaleDateString('fr-BE') : ''}
                </p>
              )}
            </div>
          </div>

          {loading && (
            <div className="flex items-center gap-2 text-slate-500 text-sm py-8 justify-center">
              <Loader2 size={16} className="animate-spin" /> Chargement...
            </div>
          )}
          {error && (
            <div className="text-red-600 text-sm py-8 text-center">{error}</div>
          )}
          {doc && !loading && !error && (
            <article className="prose prose-slate prose-sm max-w-none prose-headings:font-semibold prose-headings:text-slate-800 prose-a:text-[#0055FF] prose-table:text-xs prose-code:text-[13px] prose-strong:text-slate-900" data-testid="legal-doc-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.content || ''}</ReactMarkdown>
            </article>
          )}
        </div>

        <p className="text-center text-[11px] text-slate-400 mt-4">
          CoproManager - Ces documents sont fournis a titre indicatif et peuvent evoluer.
          Consultez regulierement leur derniere version.
        </p>
      </main>
    </div>
  );
}
