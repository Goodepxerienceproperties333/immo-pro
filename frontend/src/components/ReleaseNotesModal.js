/**
 * ReleaseNotesModal (iter90br)
 *
 * Popup qui apparait automatiquement au login si l'utilisateur a des
 * notes de version non-lues. Il clique OK pour tout marquer comme lu.
 *
 * ATTENTION : n'utilise PAS Radix Dialog (comme LegalAcceptanceModal)
 * pour eviter de bloquer pointer-events sur les autres modaux legitimes.
 */
import { useState, useEffect } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Sparkles, Bug, Shield, AlertTriangle, TrendingUp, X } from 'lucide-react';

const CATEGORY_META = {
  feature: { label: 'Nouveaute', icon: Sparkles, color: 'bg-blue-100 text-[#01213e] border-blue-200' },
  fix: { label: 'Correction', icon: Bug, color: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  improvement: { label: 'Amelioration', icon: TrendingUp, color: 'bg-violet-100 text-violet-700 border-violet-200' },
  security: { label: 'Securite', icon: Shield, color: 'bg-amber-100 text-amber-700 border-amber-200' },
  breaking: { label: 'Rupture', icon: AlertTriangle, color: 'bg-red-100 text-red-700 border-red-200' },
};

export default function ReleaseNotesModal() {
  const { user } = useAuth();
  const [notes, setNotes] = useState([]);
  const [open, setOpen] = useState(false);
  const [acknowledging, setAcknowledging] = useState(false);

  useEffect(() => {
    if (!user) return;
    // iter90br : delai leger pour laisser passer LegalAcceptanceModal en priorite.
    let cancelled = false;
    (async () => {
      try {
        // Attend que les CGU soient acceptees pour ne pas cumuler les modaux
        const legalR = await api.get('/legal/my-acceptance');
        if (cancelled) return;
        if (legalR.data?.needs_accept) return; // On attend l'acceptation d'abord
        const r = await api.get('/release-notes/unread');
        if (cancelled) return;
        if (Array.isArray(r.data) && r.data.length > 0) {
          setNotes(r.data);
          setOpen(true);
        }
      } catch {
        /* silencieux */
      }
    })();
    return () => { cancelled = true; };
  }, [user]);

  const handleAcknowledge = async () => {
    setAcknowledging(true);
    try {
      await api.post('/release-notes/acknowledge-all');
      setOpen(false);
      setNotes([]);
    } catch {
      // meme en cas d'erreur, on laisse l'user continuer
      setOpen(false);
    } finally {
      setAcknowledging(false);
    }
  };

  if (!open || notes.length === 0) return null;

  return (
    <div
      data-testid="release-notes-modal"
      className="fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-[9998] flex items-center justify-center p-4"
      style={{ pointerEvents: 'auto' }}
    >
      <div className="bg-white rounded-2xl shadow-modal w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="bg-gradient-to-r from-blue-500 to-blue-700 px-6 py-5 text-white flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs uppercase tracking-wider opacity-80 mb-1">
              <Sparkles size={14} /> Nouveautes NextGe Copro
            </div>
            <h2 className="text-xl font-display font-bold">
              {notes.length === 1
                ? "1 mise a jour a decouvrir"
                : `${notes.length} mises a jour a decouvrir`}
            </h2>
          </div>
          <button
            onClick={handleAcknowledge}
            className="rounded-full h-8 w-8 flex items-center justify-center hover:bg-white/10 transition-colors"
            title="Fermer"
            data-testid="release-notes-close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body scrollable */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {notes.map((n) => {
            const meta = CATEGORY_META[n.category] || CATEGORY_META.improvement;
            const Icon = meta.icon;
            return (
              <div
                key={n.id}
                className="border border-slate-200 rounded-xl p-4 hover:border-slate-300 transition-colors"
                data-testid={`release-note-${n.id}`}
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge className={`text-[10px] uppercase font-semibold border ${meta.color} flex items-center gap-1 py-0.5`}>
                      <Icon size={10} /> {meta.label}
                    </Badge>
                    <span className="text-[11px] text-slate-500 font-mono">v{n.version}</span>
                    {n.date && <span className="text-[11px] text-slate-400">•</span>}
                    <span className="text-[11px] text-slate-500">{n.date}</span>
                  </div>
                </div>
                <h3 className="text-sm font-bold text-slate-900 mb-1">{n.title}</h3>
                {n.description && (
                  <p className="text-[13px] text-slate-600 leading-relaxed whitespace-pre-line">
                    {n.description}
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer avec bouton OK */}
        <div className="border-t border-slate-200 px-6 py-4 flex items-center justify-between bg-slate-50/50">
          <p className="text-[12px] text-slate-500">
            Cliquez OK pour continuer. Cette fenetre ne reapparaitra plus.
          </p>
          <Button
            onClick={handleAcknowledge}
            disabled={acknowledging}
            className="bg-[#022D52] hover:bg-[#01213e] text-white px-8"
            data-testid="release-notes-ok"
          >
            {acknowledging ? "..." : "OK, j'ai compris"}
          </Button>
        </div>
      </div>
    </div>
  );
}
