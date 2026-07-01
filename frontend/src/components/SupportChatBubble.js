import { useState, useEffect, useRef, useCallback } from 'react';
import { HelpCircle, MessageSquare, X, Send, Plus, Trash2, ArrowLeft, Mail, Bot, User as UserIcon, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import api from '../lib/api';

/**
 * Iter90r - Support chatbot pour syndics.
 * Bouton "?" dans le header + panneau lateral droit avec :
 *   - liste des conversations passees
 *   - fenetre de chat active (IA Claude Sonnet 4.5)
 *   - bouton "Envoyer au support" pour escalade manuelle
 *   - escalade automatique si l'IA marque [[NEEDS_ESCALATION]]
 */
export default function SupportChatBubble() {
  const [open, setOpen] = useState(false);
  const [convs, setConvs] = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const scrollRef = useRef(null);

  const loadConvs = useCallback(async () => {
    try {
      const { data } = await api.get('/support/conversations');
      setConvs(data || []);
    } catch (err) { /* silent */ }
  }, []);

  useEffect(() => { if (open) loadConvs(); }, [open, loadConvs]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [msgs, activeConv]);

  const openConv = async (conv) => {
    setActiveConv(conv);
    setMsgs([]);
    try {
      const { data } = await api.get(`/support/conversations/${conv.id}/messages`);
      setMsgs(data || []);
    } catch (err) { toast.error('Erreur chargement conversation'); }
  };

  const startNew = async () => {
    try {
      const { data } = await api.post('/support/conversations', { title: '' });
      setConvs([data, ...convs]);
      setActiveConv(data);
      setMsgs([]);
    } catch (err) { toast.error('Erreur creation conversation'); }
  };

  const sendMessage = async () => {
    if (!input.trim() || sending) return;
    const text = input.trim();
    setInput('');
    setSending(true);
    // Optimistic user message
    const nowIso = new Date().toISOString();
    const optimistic = { id: 'tmp-' + Date.now(), role: 'user', content: text, created_at: nowIso };
    setMsgs(m => [...m, optimistic]);
    try {
      const { data } = await api.post(`/support/conversations/${activeConv.id}/chat`, { message: text });
      setMsgs(m => [...m.filter(x => x.id !== optimistic.id),
                    { ...optimistic, id: 'u-' + Date.now() },
                    data.assistant_message]);
      if (data.auto_escalated) {
        toast.info('Votre demande a ete automatiquement transmise au support', {
          description: 'Vous recevrez une reponse par email des que possible.',
          duration: 6000,
        });
      }
      // Refresh conv list to reflect updated title / preview
      loadConvs();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
      setMsgs(m => m.filter(x => x.id !== optimistic.id));
      setInput(text);
    } finally { setSending(false); }
  };

  const escalate = async () => {
    if (!activeConv || escalating) return;
    if (activeConv.escalated) { toast.info('Cette conversation a deja ete transmise au support'); return; }
    if (!window.confirm('Envoyer cette conversation au service support ? Vous recevrez une reponse par email.')) return;
    setEscalating(true);
    try {
      await api.post(`/support/conversations/${activeConv.id}/escalate`, { reason: '' });
      toast.success('Conversation envoyee au support', { description: 'Reponse par email dans les meilleurs delais.' });
      setActiveConv(a => ({ ...a, escalated: true }));
      loadConvs();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
    finally { setEscalating(false); }
  };

  const deleteConv = async (conv) => {
    if (!window.confirm(`Supprimer la conversation "${conv.title || 'Sans titre'}" ?`)) return;
    try {
      await api.delete(`/support/conversations/${conv.id}`);
      setConvs(cs => cs.filter(c => c.id !== conv.id));
      if (activeConv?.id === conv.id) { setActiveConv(null); setMsgs([]); }
    } catch (err) { toast.error('Erreur suppression'); }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-7 h-7 rounded-full bg-slate-100 hover:bg-blue-100 text-slate-500 hover:text-[#0055FF] flex items-center justify-center transition-colors"
        title="Aide & support"
        data-testid="support-open-btn"
      >
        <HelpCircle size={16} />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 bg-black/20 z-40"
            onClick={() => setOpen(false)}
          />
          <div
            className="fixed top-0 right-0 h-full w-full sm:w-[520px] bg-white shadow-2xl z-50 flex flex-col border-l border-slate-200"
            data-testid="support-panel"
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-gradient-to-r from-[#0055FF] to-[#0040CC] text-white">
              <div className="flex items-center gap-2">
                {activeConv && (
                  <button
                    onClick={() => { setActiveConv(null); setMsgs([]); }}
                    className="p-1 hover:bg-white/10 rounded"
                    data-testid="support-back-btn"
                    title="Retour a la liste"
                  ><ArrowLeft size={16} /></button>
                )}
                <MessageSquare size={16} />
                <span className="text-sm font-semibold">
                  {activeConv ? (activeConv.title || 'Nouvelle question') : 'Aide & Support'}
                </span>
              </div>
              <button
                onClick={() => setOpen(false)}
                className="p-1 hover:bg-white/10 rounded"
                data-testid="support-close-btn"
              ><X size={16} /></button>
            </div>

            {!activeConv ? (
              <>
                <div className="p-3 border-b border-slate-100">
                  <Button
                    onClick={startNew}
                    className="w-full bg-[#0055FF] hover:bg-[#0040CC] text-white h-9 text-xs"
                    data-testid="support-new-conv-btn"
                  >
                    <Plus size={14} className="mr-1" /> Poser une nouvelle question
                  </Button>
                  <p className="text-[11px] text-slate-500 mt-2 leading-relaxed">
                    L&apos;assistant IA repond aux questions sur CoproManager.
                    Les cas complexes sont transmis directement a notre equipe support.
                  </p>
                </div>
                <div className="flex-1 overflow-y-auto p-2 space-y-1">
                  {convs.length === 0 ? (
                    <div className="text-center text-xs text-slate-400 py-8">
                      Aucune conversation. Cliquez sur &quot;Poser une nouvelle question&quot; pour demarrer.
                    </div>
                  ) : convs.map(c => (
                    <div
                      key={c.id}
                      className="group flex items-start gap-2 p-2 rounded hover:bg-slate-50 cursor-pointer"
                      onClick={() => openConv(c)}
                      data-testid={`support-conv-item-${c.id}`}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-medium text-slate-800 truncate">{c.title || 'Sans titre'}</span>
                          {c.escalated && (
                            <span className="text-[9px] bg-amber-100 text-amber-700 rounded px-1 py-0.5" title="Transmise au support">
                              <Mail size={9} className="inline mr-0.5" />support
                            </span>
                          )}
                        </div>
                        {c.last_message_preview && (
                          <div className="text-[11px] text-slate-500 truncate">{c.last_message_preview}</div>
                        )}
                        <div className="text-[10px] text-slate-400">
                          {new Date(c.updated_at).toLocaleString('fr-BE', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
                          {c.messages_count > 0 && ` — ${c.messages_count} msg`}
                        </div>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteConv(c); }}
                        className="opacity-0 group-hover:opacity-100 text-slate-300 hover:text-red-500 p-1 transition-opacity"
                        title="Supprimer"
                        data-testid={`support-conv-delete-${c.id}`}
                      ><Trash2 size={12} /></button>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <>
                {/* Messages */}
                <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 bg-slate-50/50">
                  {msgs.length === 0 && (
                    <div className="text-center text-xs text-slate-400 py-8">
                      Posez votre question ci-dessous. L&apos;assistant IA repondra en quelques secondes.
                    </div>
                  )}
                  {msgs.map(m => (
                    <div key={m.id} className={`flex gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                      <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${m.role === 'user' ? 'bg-[#0055FF] text-white' : 'bg-slate-200 text-slate-600'}`}>
                        {m.role === 'user' ? <UserIcon size={12} /> : <Bot size={12} />}
                      </div>
                      <div className={`rounded-lg p-2 max-w-[85%] text-xs leading-relaxed whitespace-pre-wrap ${m.role === 'user' ? 'bg-[#0055FF] text-white' : 'bg-white border border-slate-200 text-slate-800'}`}>
                        {m.content}
                        {m.needs_escalation && (
                          <div className="mt-2 pt-2 border-t border-slate-200 flex items-center gap-1 text-[10px] text-amber-700">
                            <AlertCircle size={11} /> Transmise au support
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                  {sending && (
                    <div className="flex gap-2">
                      <div className="w-6 h-6 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center">
                        <Bot size={12} />
                      </div>
                      <div className="rounded-lg p-2 bg-white border border-slate-200">
                        <Loader2 size={14} className="animate-spin text-slate-400" />
                      </div>
                    </div>
                  )}
                </div>

                {/* Escalate bar */}
                <div className="px-3 py-2 border-t border-slate-100 bg-white">
                  {activeConv.escalated ? (
                    <div className="text-[11px] text-emerald-700 flex items-center gap-1">
                      <CheckCircle2 size={12} /> Conversation transmise au support — reponse par email
                    </div>
                  ) : (
                    <button
                      onClick={escalate}
                      disabled={escalating || msgs.length === 0}
                      className="text-[11px] text-slate-500 hover:text-[#0055FF] flex items-center gap-1 disabled:opacity-50"
                      data-testid="support-escalate-btn"
                    >
                      <Mail size={11} />
                      {escalating ? 'Envoi...' : 'Envoyer cette conversation au support'}
                    </button>
                  )}
                </div>

                {/* Input */}
                <div className="p-3 border-t border-slate-100 bg-white">
                  <div className="flex items-end gap-2">
                    <Textarea
                      value={input}
                      onChange={e => setInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                      placeholder="Posez votre question... (Entree pour envoyer)"
                      className="text-xs min-h-[38px] max-h-24 resize-none"
                      rows={1}
                      disabled={sending}
                      data-testid="support-message-input"
                    />
                    <Button
                      onClick={sendMessage}
                      disabled={sending || !input.trim()}
                      className="bg-[#0055FF] hover:bg-[#0040CC] text-white h-9 w-9 p-0 shrink-0"
                      data-testid="support-send-btn"
                    >
                      {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </>
  );
}
