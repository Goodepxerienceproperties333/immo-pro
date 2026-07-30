import { useState, useEffect, useRef, useCallback } from 'react';
import { HelpCircle, MessageSquare, X, Send, Plus, Trash2, ArrowLeft, Mail, Bot, User as UserIcon, Loader2, CheckCircle2, AlertCircle, Bug, Ticket, MessageCircleQuestion, Paperclip, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import api from '../lib/api';
import BugReportForm from './BugReportForm';
import SupportTicketsList from './SupportTicketsList';
import { useAuth } from '../contexts/AuthContext';

/**
 * Iter90r/90fs - Support chatbot pour syndics.
 * - Onglet "Assistant IA" : conversations existantes + IA Claude Sonnet
 * - Onglet "Mes tickets" : suivi des bugs remontes au support
 * - A la nouvelle question : choix "Question operationnelle" vs "Remontee de bug"
 */
export default function SupportChatBubble() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('chat'); // 'chat' | 'tickets'
  const [mode, setMode] = useState(null); // null | 'picker' | 'bug'
  const [convs, setConvs] = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);
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

  const startNew = () => {
    // Iter90fs : affiche d'abord le mode picker
    setMode('picker');
    setActiveConv(null);
    setMsgs([]);
  };

  const chooseModeOperational = async () => {
    try {
      const { data } = await api.post('/support/conversations', { title: '' });
      setConvs([data, ...convs]);
      setActiveConv(data);
      setMsgs([]);
      setMode(null);
    } catch (err) { toast.error('Erreur creation conversation'); }
  };

  const chooseModeBug = () => {
    setMode('bug');
  };

  const onBugCreated = (ticket) => {
    setMode(null);
    setTab('tickets');
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
      // iter93ai : passe le copropriete_id courant pour permettre au chatbot
      // d'analyser les donnees reelles (bilan, ecritures, extraits...).
      const copro = (typeof window !== 'undefined')
        ? (localStorage.getItem('selected_copro') || '')
        : '';
      const { data } = await api.post(`/support/conversations/${activeConv.id}/chat`,
        { message: text, copropriete_id: copro || null });
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

  const uploadDocument = async (evt) => {
    const files = Array.from(evt.target.files || []);
    if (!files.length || !activeConv) return;
    if (files.length > 3) {
      toast.error('Maximum 3 documents par upload');
      return;
    }
    // Validate all files first
    for (const file of files) {
      const ext = (file.name.split('.').pop() || '').toLowerCase();
      if (!['pdf', 'csv'].includes(ext)) {
        toast.error(`${file.name} : type non supporte (PDF ou CSV uniquement)`);
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        toast.error(`${file.name} : trop volumineux (max 10 MB)`);
        return;
      }
    }
    setUploading(true);
    let uploadedCount = 0;
    let totalChars = 0;
    try {
      // Upload sequentially (server-side extraction can be CPU heavy)
      for (const file of files) {
        const fd = new FormData();
        fd.append('file', file);
        const { data } = await api.post(
          `/support/conversations/${activeConv.id}/attach`,
          fd,
          { headers: { 'Content-Type': 'multipart/form-data' } },
        );
        setMsgs(m => [...m, {
          id: data.id,
          role: 'user_attachment',
          filename: data.filename,
          file_type: data.file_type,
          file_size: data.file_size,
          content: `[Document joint : ${data.filename}]`,
          extracted_length: data.extracted_length,
          created_at: data.created_at,
        }]);
        uploadedCount += 1;
        totalChars += data.extracted_length || 0;
      }
      if (uploadedCount === 1) {
        toast.success(`Document analyse (${totalChars} car.)`, {
          description: "Posez votre question - je peux desormais l'analyser",
        });
      } else {
        toast.success(`${uploadedCount} documents analyses (${totalChars} car. au total)`, {
          description: 'Vous pouvez maintenant demander une comparaison entre eux',
        });
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur upload');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
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
        className="w-7 h-7 rounded-full bg-slate-100 hover:bg-blue-100 text-slate-500 hover:text-[#022D52] flex items-center justify-center transition-colors"
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
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 bg-gradient-to-r from-[#022D52] to-[#1D4ED8] text-white">
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
                {/* Tabs Chat / Tickets */}
                <div className="flex border-b border-slate-100 bg-slate-50">
                  <button
                    onClick={() => { setTab('chat'); setMode(null); }}
                    className={`flex-1 py-2 text-xs font-medium flex items-center justify-center gap-1 transition-colors ${tab === 'chat' ? 'bg-white text-[#022D52] border-b-2 border-[#022D52]' : 'text-slate-500 hover:text-slate-700'}`}
                    data-testid="support-tab-chat"
                  >
                    <MessageCircleQuestion size={13} /> Assistant IA
                  </button>
                  <button
                    onClick={() => { setTab('tickets'); setMode(null); }}
                    className={`flex-1 py-2 text-xs font-medium flex items-center justify-center gap-1 transition-colors ${tab === 'tickets' ? 'bg-white text-[#022D52] border-b-2 border-[#022D52]' : 'text-slate-500 hover:text-slate-700'}`}
                    data-testid="support-tab-tickets"
                  >
                    <Ticket size={13} /> Mes tickets
                  </button>
                </div>

                {mode === 'picker' ? (
                  <div className="p-4 flex-1 overflow-y-auto" data-testid="support-mode-picker">
                    <button
                      onClick={() => setMode(null)}
                      className="text-[11px] text-slate-500 hover:text-slate-700 flex items-center gap-1 mb-3"
                      data-testid="support-picker-back-btn"
                    >
                      <ArrowLeft size={11} /> Retour
                    </button>
                    <div className="text-sm font-semibold text-slate-800 mb-1">De quoi s&apos;agit-il ?</div>
                    <p className="text-[11px] text-slate-500 mb-4 leading-relaxed">
                      Aidez-nous a router votre demande correctement.
                    </p>
                    <button
                      onClick={chooseModeOperational}
                      className="w-full text-left p-3 border-2 border-slate-200 hover:border-[#022D52] rounded-lg mb-2 transition-colors group"
                      data-testid="support-choose-operational-btn"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <MessageCircleQuestion size={16} className="text-[#022D52]" />
                        <span className="text-sm font-semibold text-slate-800 group-hover:text-[#022D52]">Question operationnelle</span>
                      </div>
                      <p className="text-[11px] text-slate-500 leading-relaxed">
                        &laquo; Comment faire pour... ? &raquo; L&apos;assistant IA vous repond immediatement.
                      </p>
                    </button>
                    <button
                      onClick={chooseModeBug}
                      className="w-full text-left p-3 border-2 border-slate-200 hover:border-amber-500 rounded-lg transition-colors group"
                      data-testid="support-choose-bug-btn"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <Bug size={16} className="text-amber-600" />
                        <span className="text-sm font-semibold text-slate-800 group-hover:text-amber-700">Remontee de bug</span>
                      </div>
                      <p className="text-[11px] text-slate-500 leading-relaxed">
                        Un dysfonctionnement, une erreur, un comportement inattendu. Cree un ticket suivi par le support.
                      </p>
                    </button>
                  </div>
                ) : mode === 'bug' ? (
                  <BugReportForm
                    userEmail={user?.email || ''}
                    onCreated={onBugCreated}
                    onCancel={() => setMode(null)}
                  />
                ) : tab === 'tickets' ? (
                  <SupportTicketsList superadmin={false} dense />
                ) : (
                  <>
                    <div className="p-3 border-b border-slate-100">
                      <Button
                        onClick={startNew}
                        className="w-full bg-[#022D52] hover:bg-[#1D4ED8] text-white h-9 text-xs"
                        data-testid="support-new-conv-btn"
                      >
                        <Plus size={14} className="mr-1" /> Poser une nouvelle question
                      </Button>
                      <p className="text-[11px] text-slate-500 mt-2 leading-relaxed">
                        L&apos;assistant IA repond aux questions sur NextGe Copro.
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
                )}
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
                  {msgs.map(m => {
                    if (m.role === 'user_attachment') {
                      return (
                        <div key={m.id} className="flex justify-end" data-testid={`support-attachment-${m.id}`}>
                          <div className="flex items-center gap-2 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 max-w-[85%]">
                            <FileText size={14} className="text-blue-600 shrink-0" />
                            <div className="text-xs">
                              <div className="font-medium text-slate-800 truncate">{m.filename || 'document'}</div>
                              <div className="text-[10px] text-slate-500">
                                {(m.file_type || '').toUpperCase()}
                                {m.extracted_length && ` · ${m.extracted_length} car. extraits`}
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    }
                    return (
                      <div key={m.id} className={`flex gap-2 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
                        <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 ${m.role === 'user' ? 'bg-[#022D52] text-white' : 'bg-slate-200 text-slate-600'}`}>
                          {m.role === 'user' ? <UserIcon size={12} /> : <Bot size={12} />}
                        </div>
                        <div className={`rounded-lg p-2 max-w-[85%] text-xs leading-relaxed whitespace-pre-wrap ${m.role === 'user' ? 'bg-[#022D52] text-white' : 'bg-white border border-slate-200 text-slate-800'}`}>
                          {m.content}
                          {m.needs_escalation && (
                            <div className="mt-2 pt-2 border-t border-slate-200 flex items-center gap-1 text-[10px] text-amber-700">
                              <AlertCircle size={11} /> Transmise au support
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
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
                      className="text-[11px] text-slate-500 hover:text-[#022D52] flex items-center gap-1 disabled:opacity-50"
                      data-testid="support-escalate-btn"
                    >
                      <Mail size={11} />
                      {escalating ? 'Envoi...' : 'Envoyer cette conversation au support'}
                    </button>
                  )}
                </div>

                {/* Input */}
                <div className="p-3 border-t border-slate-100 bg-white">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.csv"
                    multiple
                    onChange={uploadDocument}
                    className="hidden"
                    data-testid="support-file-input"
                  />
                  <div className="flex items-end gap-2">
                    <Button
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading || sending}
                      variant="ghost"
                      className="h-9 w-9 p-0 shrink-0 text-slate-500 hover:text-[#022D52]"
                      title="Joindre des documents (PDF ou CSV, max 3 fichiers)"
                      data-testid="support-attach-btn"
                    >
                      {uploading ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
                    </Button>
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
                      className="bg-[#022D52] hover:bg-[#1D4ED8] text-white h-9 w-9 p-0 shrink-0"
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
