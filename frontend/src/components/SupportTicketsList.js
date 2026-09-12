import { useState, useEffect, useCallback, useMemo } from 'react';
import { ArrowLeft, MessageSquare, Paperclip, Send, Loader2, ChevronRight, RefreshCw, Megaphone } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import api from '../lib/api';

/**
 * Iter90fs - Panneau de suivi des tickets support.
 *
 * Utilise dans :
 *  - Chatbot (mode syndic) : n'affiche que les tickets du syndic connecte.
 *  - Page admin (superadmin) : affiche tous les tickets + actions statut.
 *
 * Props :
 *  - superadmin (bool) : active les actions superadmin (changer statut).
 *  - onBack (fn|null) : bouton retour (chatbot uniquement).
 *  - dense (bool) : rendu compact (chatbot side panel).
 */

const STATUS_COLORS = {
  open:        'bg-slate-100 text-slate-700 border-slate-200',
  assigned:    'bg-blue-100 text-blue-700 border-blue-200',
  in_progress: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  testing:    'bg-purple-100 text-purple-700 border-purple-200',
  deployment: 'bg-amber-100 text-amber-800 border-amber-200',
  closed:     'bg-emerald-100 text-emerald-800 border-emerald-200',
  rejected:   'bg-red-100 text-red-700 border-red-200',
};

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleString('fr-BE', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

export default function SupportTicketsList({ superadmin = false, onBack = null, dense = false }) {
  const [tickets, setTickets] = useState([]);
  const [statuses, setStatuses] = useState([]);
  const [loading, setLoading] = useState(false);
  const [activeTicket, setActiveTicket] = useState(null);
  const [events, setEvents] = useState([]);
  const [comment, setComment] = useState('');
  const [changingStatus, setChangingStatus] = useState(false);
  const [posting, setPosting] = useState(false);
  const [filterStatus, setFilterStatus] = useState('all');
  const [search, setSearch] = useState('');
  // iter95s : dialog annonce superadmin
  const [announceOpen, setAnnounceOpen] = useState(false);
  const [announceForm, setAnnounceForm] = useState({ title: '', description: '', target: 'all', target_syndic_ids: [] });
  const [syndicsList, setSyndicsList] = useState([]);
  const [posting_announce, setPostingAnnounce] = useState(false);

  useEffect(() => {
    if (!superadmin) return;
    api.get('/admin/syndics-overview').then(r => {
      setSyndicsList((r.data || []).map(s => ({
        id: s.syndic_id, name: s.name || s.email, email: s.email,
      })));
    }).catch(() => {});
  }, [superadmin]);

  const submitAnnounce = async () => {
    if (announceForm.title.trim().length < 5) { toast.error('Titre trop court (min 5)'); return; }
    if (announceForm.description.trim().length < 10) { toast.error('Description trop courte (min 10)'); return; }
    if (announceForm.target === 'specific' && announceForm.target_syndic_ids.length === 0) {
      toast.error('Selectionnez au moins un syndic'); return;
    }
    setPostingAnnounce(true);
    try {
      const { data } = await api.post('/tickets/admin/announce', {
        title: announceForm.title.trim(),
        description: announceForm.description.trim(),
        target: announceForm.target,
        target_syndic_ids: announceForm.target_syndic_ids,
        notify_email: true,
      });
      toast.success(
        `Ticket ${data.number} cree - ${data.notified_recipients}/${data.total_targets} email(s) envoye(s)`,
        { duration: 6000 },
      );
      setAnnounceOpen(false);
      setAnnounceForm({ title: '', description: '', target: 'all', target_syndic_ids: [] });
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Echec creation annonce');
    } finally {
      setPostingAnnounce(false);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      if (filterStatus && filterStatus !== 'all') params.status = filterStatus;
      if (search.trim()) params.search = search.trim();
      const { data } = await api.get('/tickets', { params });
      setTickets(data || []);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur chargement tickets');
    } finally {
      setLoading(false);
    }
  }, [filterStatus, search]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    api.get('/tickets/statuses').then(r => setStatuses(r.data || [])).catch(() => {});
  }, []);

  const openTicket = async (t) => {
    setActiveTicket(t);
    setEvents([]);
    setComment('');
    try {
      const { data } = await api.get(`/tickets/${t.id}/events`);
      setEvents(data || []);
    } catch (err) {
      toast.error('Erreur chargement historique');
    }
  };

  const refreshActive = async () => {
    if (!activeTicket) return;
    try {
      const [t, ev] = await Promise.all([
        api.get(`/tickets/${activeTicket.id}`),
        api.get(`/tickets/${activeTicket.id}/events`),
      ]);
      setActiveTicket(t.data);
      setEvents(ev.data || []);
    } catch { /* silent */ }
  };

  const postComment = async () => {
    if (!activeTicket || !comment.trim()) return;
    setPosting(true);
    try {
      await api.post(`/tickets/${activeTicket.id}/comments`, { comment: comment.trim() });
      setComment('');
      await refreshActive();
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur commentaire');
    } finally {
      setPosting(false);
    }
  };

  const changeStatus = async (newStatus) => {
    if (!activeTicket || !superadmin) return;
    setChangingStatus(true);
    try {
      const c = comment.trim();
      await api.post(`/tickets/${activeTicket.id}/status`, { new_status: newStatus, comment: c });
      setComment('');
      toast.success('Statut mis a jour, email envoye au demandeur');
      await refreshActive();
      load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur changement statut');
    } finally {
      setChangingStatus(false);
    }
  };

  const resendSupportEmail = async () => {
    if (!activeTicket || !superadmin) return;
    try {
      const { data } = await api.post(`/tickets/${activeTicket.id}/resend-support-email`);
      toast.success(`Email renvoye a ${data.sent_to}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur renvoi email support');
    }
  };

  const statusLabel = useMemo(() => {
    const m = {};
    statuses.forEach(s => { m[s.key] = s.label; });
    return m;
  }, [statuses]);

  // ---- Ticket detail view ----
  if (activeTicket) {
    return (
      <div className="flex flex-col h-full" data-testid="ticket-detail-panel">
        <div className={`px-4 py-3 border-b border-slate-100 flex items-center justify-between ${dense ? 'bg-slate-50' : ''}`}>
          <div className="flex items-center gap-2 min-w-0">
            <button
              onClick={() => setActiveTicket(null)}
              className="p-1 hover:bg-slate-100 rounded"
              title="Retour a la liste"
              data-testid="ticket-back-btn"
            ><ArrowLeft size={14} /></button>
            <div className="min-w-0">
              <div className="text-[11px] text-slate-500 font-mono">{activeTicket.number}</div>
              <div className="text-sm font-semibold truncate" title={activeTicket.title}>{activeTicket.title}</div>
            </div>
          </div>
          <Badge className={`${STATUS_COLORS[activeTicket.status] || 'bg-slate-100'} text-[10px]`} data-testid="ticket-status-badge">
            {statusLabel[activeTicket.status] || activeTicket.status}
          </Badge>
          {superadmin && (
            <button
              onClick={resendSupportEmail}
              className="ml-2 text-[10px] px-2 py-1 rounded border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
              title="Renvoyer la notification de creation vers support@nextgecopro.be"
              data-testid="ticket-resend-support-btn"
            >Renvoyer email support</button>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-3 bg-slate-50/40">
          {/* iter95t : bandeau annonce dans la vue detail */}
          {activeTicket.is_admin_announcement && (
            <section className="bg-amber-50 border border-amber-300 rounded-lg p-3 text-xs" data-testid="announcement-detail-banner">
              <div className="flex items-center gap-2 mb-1">
                <Megaphone size={14} className="text-amber-600" />
                <span className="font-semibold text-amber-900">Annonce support</span>
                <span className="text-[10px] bg-amber-200 text-amber-900 px-1.5 py-0.5 rounded">
                  {(activeTicket.target_syndic_ids || []).includes('*')
                    ? 'Diffusion generale'
                    : `${(activeTicket.target_syndic_ids || []).length} syndic(s) cible(s)`}
                </span>
              </div>
              <div className="text-[11px] text-amber-800">
                {superadmin
                  ? 'Vous etes l\'auteur de cette annonce. Les reponses des syndics vous seront notifiees par email.'
                  : 'Ceci est une communication du support NextGe Copro. Utilisez le champ de reponse en bas pour poser vos questions ou signaler un impact - le support sera notifie directement.'}
              </div>
            </section>
          )}
          {/* Ticket infos */}
          <section className="bg-white rounded-lg border border-slate-200 p-3 text-xs space-y-2">
            <div className="flex justify-between text-[10px] text-slate-500">
              <span>Demandeur : {activeTicket.requester_name} &lt;{activeTicket.requester_email}&gt;</span>
              <span>{fmtDate(activeTicket.created_at)}</span>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-0.5">Description</div>
              <div className="whitespace-pre-wrap text-slate-700">{activeTicket.description}</div>
            </div>
            {activeTicket.steps_to_reproduce && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-0.5">Etapes</div>
                <div className="whitespace-pre-wrap text-slate-700">{activeTicket.steps_to_reproduce}</div>
              </div>
            )}
            {activeTicket.expected_behavior && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-0.5">Attendu</div>
                <div className="whitespace-pre-wrap text-slate-700">{activeTicket.expected_behavior}</div>
              </div>
            )}
            {activeTicket.observed_behavior && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-0.5">Observe</div>
                <div className="whitespace-pre-wrap text-slate-700">{activeTicket.observed_behavior}</div>
              </div>
            )}
            {activeTicket.attachments && activeTicket.attachments.length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold mb-1">Pieces jointes</div>
                <div className="flex flex-wrap gap-1">
                  {activeTicket.attachments.map(a => (
                    <a
                      key={a.file_id}
                      href={`${process.env.REACT_APP_BACKEND_URL}/api/tickets/${activeTicket.id}/attachments/${a.file_id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[10px] bg-slate-100 hover:bg-slate-200 rounded px-2 py-1 border border-slate-200 text-slate-700"
                      data-testid={`ticket-att-${a.file_id}`}
                    >
                      <Paperclip size={10} /> {a.filename} <span className="text-slate-400">({Math.round(a.size / 1024)} KB)</span>
                    </a>
                  ))}
                </div>
              </div>
            )}
          </section>

          {/* Timeline */}
          <section className="bg-white rounded-lg border border-slate-200 p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">Historique</div>
              <button
                onClick={refreshActive}
                className="text-slate-400 hover:text-slate-700"
                title="Rafraichir"
                data-testid="ticket-refresh-btn"
              ><RefreshCw size={11} /></button>
            </div>
            <div className="space-y-2">
              {events.length === 0 ? (
                <div className="text-[11px] text-slate-400 italic">Aucun evenement</div>
              ) : events.map(ev => (
                <div key={ev.id} className="border-l-2 border-slate-200 pl-2 text-[11px]" data-testid={`ticket-event-${ev.id}`}>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-slate-700">{ev.actor_name}</span>
                    <span className="text-slate-400">{fmtDate(ev.created_at)}</span>
                    {ev.event_type === 'status_changed' && (
                      <Badge className={`${STATUS_COLORS[ev.new_status] || 'bg-slate-100'} text-[9px]`}>
                        {statusLabel[ev.old_status] || ev.old_status} &rarr; {statusLabel[ev.new_status] || ev.new_status}
                      </Badge>
                    )}
                    {ev.event_type === 'created' && <Badge className="bg-slate-100 text-slate-700 text-[9px]">Cree</Badge>}
                    {ev.event_type === 'comment' && <Badge className="bg-blue-50 text-blue-700 text-[9px]">Commentaire</Badge>}
                    {ev.event_type === 'assigned' && <Badge className="bg-amber-50 text-amber-700 text-[9px]">Assigne</Badge>}
                  </div>
                  {ev.comment && <div className="text-slate-600 whitespace-pre-wrap mt-0.5">{ev.comment}</div>}
                </div>
              ))}
            </div>
          </section>
        </div>

        {/* Actions bar */}
        <div className="p-3 border-t border-slate-100 bg-white space-y-2">
          {/* iter95t : bandeau contextuel pour le fil de discussion sur une annonce */}
          {activeTicket.is_admin_announcement && !superadmin && (
            <div className="bg-amber-50 border border-amber-200 rounded p-2 text-[11px] text-amber-900 flex items-start gap-2" data-testid="announcement-reply-hint">
              <Megaphone size={12} className="mt-0.5 shrink-0 text-amber-600" />
              <div>
                Ceci est une <b>annonce du support</b>. Vous pouvez repondre ci-dessous
                pour poser une question ou signaler un impact - l&apos;equipe support
                sera notifiee par email.
              </div>
            </div>
          )}
          <Textarea
            value={comment}
            onChange={e => setComment(e.target.value)}
            placeholder={
              activeTicket.is_admin_announcement && !superadmin
                ? 'Repondre a l\'annonce (question, impact sur votre ACP...)'
                : superadmin
                  ? 'Commentaire (optionnel avec changement de statut)'
                  : 'Ajouter un commentaire...'
            }
            rows={2}
            className="text-xs"
            data-testid="ticket-comment-input"
          />
          <div className="flex flex-wrap gap-2 justify-between">
            <Button
              variant="outline"
              size="sm"
              onClick={postComment}
              disabled={posting || !comment.trim()}
              className="h-8 text-xs"
              data-testid="ticket-post-comment-btn"
            >
              {posting ? <Loader2 size={11} className="animate-spin mr-1" /> : <MessageSquare size={11} className="mr-1" />}
              Commenter
            </Button>
            {superadmin && (
              <div className="flex items-center gap-1">
                <Select disabled={changingStatus} onValueChange={changeStatus}>
                  <SelectTrigger className="h-8 text-xs w-[180px]" data-testid="ticket-status-change-select">
                    <SelectValue placeholder="Changer le statut..." />
                  </SelectTrigger>
                  <SelectContent>
                    {statuses.map(s => (
                      <SelectItem
                        key={s.key}
                        value={s.key}
                        disabled={s.key === activeTicket.status}
                        data-testid={`ticket-status-option-${s.key}`}
                      >
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ---- List view ----
  return (
    <div className="flex flex-col h-full" data-testid="tickets-list-panel">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
        {onBack && (
          <button onClick={onBack} className="p-1 hover:bg-slate-100 rounded" title="Retour" data-testid="tickets-back-btn">
            <ArrowLeft size={14} />
          </button>
        )}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">Mes tickets{superadmin && ' (tous)'}</div>
          <div className="text-[11px] text-slate-500">{tickets.length} ticket(s)</div>
        </div>
        <button
          onClick={load}
          className="p-1 hover:bg-slate-100 rounded text-slate-400 hover:text-slate-700"
          title="Rafraichir"
          data-testid="tickets-refresh-btn"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
        </button>
        {/* iter95s : nouveau ticket-annonce (superadmin uniquement) */}
        {superadmin && (
          <Button
            size="sm"
            onClick={() => setAnnounceOpen(true)}
            className="h-7 text-[11px] bg-amber-500 hover:bg-amber-600 text-white gap-1"
            data-testid="tickets-new-announcement-btn"
          >
            <Megaphone size={12} /> Annoncer
          </Button>
        )}
      </div>

      {/* iter95s : Dialog creation annonce superadmin */}
      <Dialog open={announceOpen} onOpenChange={setAnnounceOpen}>
        <DialogContent className="max-w-lg" data-testid="admin-announce-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Megaphone size={16} className="text-amber-600" />
              Nouvelle annonce superadmin
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 pt-2">
            <div>
              <label className="text-[11px] font-semibold text-slate-600 uppercase">Titre *</label>
              <Input
                value={announceForm.title}
                onChange={e => setAnnounceForm({ ...announceForm, title: e.target.value })}
                placeholder="Ex : Maintenance planifiee 24/02 22h"
                data-testid="announce-title"
              />
            </div>
            <div>
              <label className="text-[11px] font-semibold text-slate-600 uppercase">Description *</label>
              <Textarea
                value={announceForm.description}
                onChange={e => setAnnounceForm({ ...announceForm, description: e.target.value })}
                rows={4}
                placeholder="Explication detaillee du sujet (interruption, bug en cours de resolution, information generale...)"
                data-testid="announce-description"
              />
            </div>
            <div>
              <label className="text-[11px] font-semibold text-slate-600 uppercase">Destinataires</label>
              <div className="flex gap-2 items-center mt-1">
                <Select value={announceForm.target} onValueChange={v => setAnnounceForm({ ...announceForm, target: v, target_syndic_ids: v === 'all' ? [] : announceForm.target_syndic_ids })}>
                  <SelectTrigger className="h-8 text-xs w-[180px]" data-testid="announce-target">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Tous les syndics ({syndicsList.length})</SelectItem>
                    <SelectItem value="specific">Syndics specifiques</SelectItem>
                  </SelectContent>
                </Select>
                <span className="text-[11px] text-slate-500">
                  {announceForm.target === 'all'
                    ? `Le ticket sera visible par les ${syndicsList.length} syndic(s) actifs`
                    : `${announceForm.target_syndic_ids.length} syndic(s) selectionne(s)`}
                </span>
              </div>
            </div>
            {announceForm.target === 'specific' && (
              <div className="border border-slate-200 rounded-md p-2 max-h-40 overflow-y-auto space-y-1" data-testid="announce-syndics-picker">
                {syndicsList.length === 0 ? (
                  <div className="text-[11px] italic text-slate-400 text-center py-2">Chargement des syndics...</div>
                ) : (
                  syndicsList.map(s => {
                    const checked = announceForm.target_syndic_ids.includes(s.id);
                    return (
                      <label key={s.id} className="flex items-center gap-2 p-1 hover:bg-slate-50 rounded cursor-pointer" data-testid={`announce-syndic-${s.id}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => setAnnounceForm(f => ({
                            ...f,
                            target_syndic_ids: checked
                              ? f.target_syndic_ids.filter(x => x !== s.id)
                              : [...f.target_syndic_ids, s.id],
                          }))}
                        />
                        <div className="flex-1">
                          <div className="text-xs font-medium">{s.name}</div>
                          <div className="text-[10px] text-slate-500">{s.email}</div>
                        </div>
                      </label>
                    );
                  })
                )}
              </div>
            )}
            <div className="bg-blue-50 border border-blue-200 rounded p-2 text-[11px] text-blue-900">
              Un email sera envoye automatiquement aux syndics cibles pour les informer.
              Le ticket sera cree avec le statut &quot;En cours de developpement&quot; et vous
              en serez le responsable.
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setAnnounceOpen(false)} disabled={posting_announce} data-testid="announce-cancel">
              Annuler
            </Button>
            <Button
              onClick={submitAnnounce}
              disabled={posting_announce}
              className="bg-amber-500 hover:bg-amber-600 text-white"
              data-testid="announce-submit"
            >
              {posting_announce ? <><Loader2 size={12} className="mr-1 animate-spin" /> Envoi...</> : <><Send size={12} className="mr-1" /> Publier l&apos;annonce</>}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-2">
        <Select value={filterStatus} onValueChange={setFilterStatus}>
          <SelectTrigger className="h-8 text-xs w-[140px]" data-testid="tickets-filter-status">
            <SelectValue placeholder="Statut" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Tous les statuts</SelectItem>
            {statuses.map(s => (
              <SelectItem key={s.key} value={s.key}>{s.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Rechercher (numero, titre, demandeur)"
          className="flex-1 h-8 text-xs px-2 border border-slate-200 rounded"
          data-testid="tickets-search-input"
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {tickets.length === 0 ? (
          <div className="p-6 text-center text-xs text-slate-400" data-testid="tickets-empty">
            {loading ? 'Chargement...' : 'Aucun ticket pour le moment.'}
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {tickets.map(t => (
              <button
                key={t.id}
                onClick={() => openTicket(t)}
                className="w-full text-left p-3 hover:bg-slate-50 flex items-start gap-2"
                data-testid={`ticket-row-${t.id}`}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-[10px] font-mono text-slate-500">{t.number}</span>
                    <Badge className={`${STATUS_COLORS[t.status] || 'bg-slate-100'} text-[9px]`}>
                      {statusLabel[t.status] || t.status_label || t.status}
                    </Badge>
                    {/* iter95s : badge annonce superadmin */}
                    {t.is_admin_announcement && (
                      <Badge className="bg-amber-100 text-amber-800 border border-amber-300 text-[9px]" data-testid={`ticket-announce-badge-${t.id}`}>
                        <Megaphone size={9} className="mr-0.5" /> Annonce
                      </Badge>
                    )}
                    {t.attachments && t.attachments.length > 0 && (
                      <span className="text-[9px] text-slate-400 inline-flex items-center gap-0.5">
                        <Paperclip size={9} /> {t.attachments.length}
                      </span>
                    )}
                  </div>
                  <div className="text-xs font-medium text-slate-800 truncate">{t.title}</div>
                  {superadmin && (
                    <div className="text-[10px] text-slate-500 truncate">
                      Par {t.requester_name} — {fmtDate(t.created_at)}
                    </div>
                  )}
                  {!superadmin && (
                    <div className="text-[10px] text-slate-400">{fmtDate(t.updated_at)}</div>
                  )}
                </div>
                <ChevronRight size={14} className="text-slate-300 mt-2" />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
