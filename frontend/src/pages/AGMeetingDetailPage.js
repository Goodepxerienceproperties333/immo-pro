import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import api, { extractApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Vote, ArrowLeft, Save, Plus, Trash2, Edit3, Send, FileDown,
  Calendar, Clock, MapPin, TriangleAlert, CheckCircle2, ChevronUp, ChevronDown,
  Scale, Info,
} from 'lucide-react';

const BACKEND = process.env.REACT_APP_BACKEND_URL;

const DECISION_LABELS = {
  info: { label: 'Information', color: 'bg-slate-100 text-slate-700', legal: '-' },
  simple: { label: 'Majorite simple', color: 'bg-blue-100 text-blue-700', legal: 'Art. 3.88 §1' },
  '2_3': { label: 'Majorite 2/3', color: 'bg-amber-100 text-amber-700', legal: 'Art. 3.88 §2' },
  '4_5': { label: 'Majorite 4/5', color: 'bg-orange-100 text-orange-700', legal: 'Art. 3.88 §3' },
  unanimite: { label: 'Unanimite', color: 'bg-red-100 text-red-700', legal: 'Art. 3.88 §4' },
};

// ============ Agenda item editor ============
function AgendaItemDialog({ open, onOpenChange, meetingId, item, onSaved }) {
  const [form, setForm] = useState({
    title: '', description: '', decision_type: 'simple', proposed_by: 'syndic',
    legal_notes: '',
  });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (item) {
      setForm({
        title: item.title || '',
        description: item.description || '',
        decision_type: item.decision_type || 'simple',
        proposed_by: item.proposed_by || 'syndic',
        legal_notes: item.legal_notes || '',
      });
    } else {
      setForm({ title: '', description: '', decision_type: 'simple', proposed_by: 'syndic', legal_notes: '' });
    }
  }, [item, open]);

  const save = async () => {
    if (!form.title.trim()) { toast.error('Titre requis'); return; }
    setSaving(true);
    try {
      if (item?.id) {
        await api.put(`/ag/meetings/${meetingId}/agenda-items/${item.id}`, form);
        toast.success('Point mis a jour');
      } else {
        await api.post(`/ag/meetings/${meetingId}/agenda-items`, form);
        toast.success('Point ajoute');
      }
      onSaved?.();
      onOpenChange(false);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="dialog-agenda-item">
        <DialogHeader>
          <DialogTitle>{item?.id ? 'Modifier le point' : 'Ajouter un point'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs">Titre *</Label>
            <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
                   placeholder="Ex : Approbation du budget previsionnel 2026"
                   data-testid="agenda-input-title" />
          </div>
          <div>
            <Label className="text-xs">Description / expose</Label>
            <Textarea rows={3} value={form.description}
                      onChange={(e) => setForm({ ...form, description: e.target.value })}
                      data-testid="agenda-textarea-desc" />
          </div>
          <div>
            <Label className="text-xs">Type de vote / decision *</Label>
            <Select value={form.decision_type} onValueChange={(v) => setForm({ ...form, decision_type: v })}>
              <SelectTrigger data-testid="agenda-select-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(DECISION_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v.label} ({v.legal})</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="text-[11px] text-slate-500 mt-1 flex items-start gap-1">
              <Scale className="h-3 w-3 mt-0.5 shrink-0" />
              {form.decision_type === 'info' && "Point d'information - pas de vote."}
              {form.decision_type === 'simple' && "Majorite absolue (>50%) des voix presentes ou representees."}
              {form.decision_type === '2_3' && "Travaux, appels de fonds exceptionnels, contrats > 3 ans."}
              {form.decision_type === '4_5' && "Modification statuts, actes de disposition."}
              {form.decision_type === 'unanimite' && "Modification des quotites ou destination immeuble."}
            </div>
          </div>
          <div>
            <Label className="text-xs">Propose par</Label>
            <Input value={form.proposed_by} onChange={(e) => setForm({ ...form, proposed_by: e.target.value })}
                   placeholder="syndic OU nom du coproprietaire" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Annuler</Button>
          <Button onClick={save} disabled={saving} className="bg-blue-600 hover:bg-blue-700"
                  data-testid="agenda-btn-save">
            <Save className="h-4 w-4 mr-1" /> Enregistrer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Send convocation dialog ============
function SendConvocationDialog({ open, onOpenChange, meetingId, onSent }) {
  const [mailboxes, setMailboxes] = useState([]);
  const [fromMailbox, setFromMailbox] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open) return;
    api.get('/communication/mailboxes').then(r => {
      const boxes = r.data.mailboxes || [];
      setMailboxes(boxes);
      const def = boxes.find(b => b.default) || boxes[0];
      if (def) setFromMailbox(def.address);
    }).catch(() => {});
  }, [open]);

  const send = async () => {
    if (!fromMailbox) { toast.error('Choisissez une boite'); return; }
    setSending(true);
    try {
      const r = await api.post(`/ag/meetings/${meetingId}/convocation/send?from_mailbox=${encodeURIComponent(fromMailbox)}`);
      toast.success(`Convocation envoyee : ${r.data.sent} destinataires${r.data.failed?.length ? `, ${r.data.failed.length} echecs` : ''}`);
      onSent?.();
      onOpenChange(false);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSending(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" data-testid="dialog-send-convocation">
        <DialogHeader>
          <DialogTitle>Envoyer la convocation par email</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p className="text-slate-600">
            La convocation PDF sera envoyee a tous les proprietaires actifs de la copropriete
            (avec adresse email renseignee). Un PDF personnalise (adresse fenetre C6) est genere
            pour chacun.
          </p>
          <div>
            <Label className="text-xs">Envoyer depuis</Label>
            <Select value={fromMailbox} onValueChange={setFromMailbox}>
              <SelectTrigger data-testid="convocation-select-from"><SelectValue placeholder="Choisir une boite" /></SelectTrigger>
              <SelectContent>
                {mailboxes.map(b => <SelectItem key={b.address} value={b.address}>{b.address}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="bg-amber-50 border-l-4 border-amber-400 p-2 text-xs">
            <b>Rappel legal :</b> la convocation doit etre envoyee au moins 15 jours
            avant l&apos;AG (art. 3.87 §2 CC).
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Annuler</Button>
          <Button onClick={send} disabled={sending} className="bg-blue-600 hover:bg-blue-700"
                  data-testid="convocation-btn-send-confirm">
            <Send className="h-4 w-4 mr-1" /> Envoyer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Main detail page ============
export default function AGMeetingDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [meeting, setMeeting] = useState(null);
  const [loading, setLoading] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [showItemDialog, setShowItemDialog] = useState(false);
  const [showSendDialog, setShowSendDialog] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get(`/ag/meetings/${id}`);
      setMeeting(r.data);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setLoading(false); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const deleteItem = async (iid) => {
    if (!window.confirm('Supprimer ce point ?')) return;
    try {
      await api.delete(`/ag/meetings/${id}/agenda-items/${iid}`);
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const moveItem = async (iid, dir) => {
    const items = [...(meeting?.agenda_items || [])].sort((a, b) => a.order - b.order);
    const idx = items.findIndex(x => x.id === iid);
    if (idx < 0) return;
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= items.length) return;
    [items[idx], items[newIdx]] = [items[newIdx], items[idx]];
    try {
      await api.post(`/ag/meetings/${id}/reorder-agenda`, items.map(x => x.id));
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };

  const downloadPdf = () => {
    // Use backend URL directly to trigger download
    const url = `${BACKEND}/api/ag/meetings/${id}/convocation/pdf`;
    window.open(url, '_blank');
  };

  if (loading || !meeting) {
    return <div className="p-6 text-slate-500">Chargement...</div>;
  }

  const legal = meeting.legal_check || {};
  const canEdit = !['held', 'archived', 'cancelled'].includes(meeting.status);
  const canSend = canEdit && (meeting.agenda_items || []).length > 0;

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto space-y-4" data-testid="ag-detail-page">
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => navigate('/ag')}
                data-testid="btn-back-ag-list">
          <ArrowLeft className="h-4 w-4 mr-1" /> Retour
        </Button>
      </div>

      <Card>
        <CardContent className="p-6">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-2xl font-semibold flex items-center gap-2">
                <Vote className="h-6 w-6 text-blue-600" />
                AG {meeting.type} - {meeting.scheduled_date}
              </h1>
              <div className="mt-2 space-y-1 text-sm text-slate-600">
                <div className="flex items-center gap-1">
                  <Calendar className="h-3 w-3" /> {meeting.scheduled_date}
                  <Clock className="h-3 w-3 ml-3" /> {meeting.scheduled_time}
                </div>
                <div className="flex items-center gap-1">
                  <MapPin className="h-3 w-3" /> {meeting.location}
                </div>
                <div className="text-xs">Copropriete : <b>{meeting.copropriete?.name}</b></div>
              </div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <Badge className="text-sm">{meeting.status}</Badge>
              {legal.days_until_ag !== undefined && legal.days_until_ag >= 0 && (
                <div className={`text-xs flex items-center gap-1 ${legal.on_time ? 'text-emerald-600' : 'text-red-600'}`}>
                  {legal.on_time ? <CheckCircle2 className="h-3 w-3" /> : <TriangleAlert className="h-3 w-3" />}
                  J-{legal.days_until_ag} / {legal.legal_min_days}j min
                </div>
              )}
            </div>
          </div>

          {legal.warning && (
            <div className="mt-4 bg-red-50 border-l-4 border-red-400 p-3 text-sm text-red-800 flex items-start gap-2">
              <TriangleAlert className="h-4 w-4 shrink-0 mt-0.5" />
              {legal.warning}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Action bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={downloadPdf} variant="outline" data-testid="ag-btn-download-pdf">
          <FileDown className="h-4 w-4 mr-1" /> Apercu PDF convocation
        </Button>
        <Button onClick={() => setShowSendDialog(true)}
                disabled={!canSend}
                className="bg-blue-600 hover:bg-blue-700"
                data-testid="ag-btn-open-send">
          <Send className="h-4 w-4 mr-1" /> Envoyer aux proprietaires
        </Button>
        {meeting.convocation_sent_at && (
          <Badge className="bg-emerald-100 text-emerald-700">
            Envoyee {meeting.convocation_sent_to_count} fois le {new Date(meeting.convocation_sent_at).toLocaleDateString('fr-BE')}
          </Badge>
        )}
      </div>

      {/* Agenda */}
      <Card>
        <CardHeader className="pb-2 flex flex-row items-center justify-between">
          <CardTitle className="text-base">Ordre du jour</CardTitle>
          {canEdit && (
            <Button size="sm" onClick={() => { setEditingItem(null); setShowItemDialog(true); }}
                    data-testid="ag-btn-add-agenda-item">
              <Plus className="h-4 w-4 mr-1" /> Ajouter un point
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {(meeting.agenda_items || []).length === 0 ? (
            <div className="text-center text-sm text-slate-500 p-6">
              <Info className="h-6 w-6 mx-auto mb-2 text-slate-400" />
              Aucun point a l&apos;ordre du jour. Ajoutez-en avant d&apos;envoyer la convocation.
            </div>
          ) : (
            <div className="space-y-2">
              {[...meeting.agenda_items].sort((a, b) => a.order - b.order).map((it, idx, arr) => {
                const dt = DECISION_LABELS[it.decision_type] || DECISION_LABELS.simple;
                return (
                  <div key={it.id} className="border rounded-md p-3 hover:bg-slate-50 group"
                       data-testid={`ag-item-${it.id}`}>
                    <div className="flex items-start gap-3">
                      <div className="text-slate-400 font-mono text-xs pt-1 w-6 text-center">
                        {it.order}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-medium">{it.title}</div>
                        {it.description && (
                          <div className="text-xs text-slate-600 mt-1 whitespace-pre-line">{it.description}</div>
                        )}
                        <div className="mt-2 flex items-center gap-2 flex-wrap">
                          <Badge className={dt.color}>{dt.label}</Badge>
                          <span className="text-[10px] text-slate-500 font-mono">{dt.legal}</span>
                          {it.proposed_by && it.proposed_by !== 'syndic' && (
                            <Badge variant="outline" className="text-[10px]">Propose par {it.proposed_by}</Badge>
                          )}
                        </div>
                      </div>
                      {canEdit && (
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                          <Button size="sm" variant="ghost" onClick={() => moveItem(it.id, -1)}
                                  disabled={idx === 0}>
                            <ChevronUp className="h-3 w-3" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => moveItem(it.id, +1)}
                                  disabled={idx === arr.length - 1}>
                            <ChevronDown className="h-3 w-3" />
                          </Button>
                          <Button size="sm" variant="ghost"
                                  onClick={() => { setEditingItem(it); setShowItemDialog(true); }}
                                  data-testid={`ag-btn-edit-item-${it.id}`}>
                            <Edit3 className="h-3 w-3" />
                          </Button>
                          <Button size="sm" variant="ghost" className="text-red-600"
                                  onClick={() => deleteItem(it.id)}
                                  data-testid={`ag-btn-delete-item-${it.id}`}>
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <AgendaItemDialog
        open={showItemDialog}
        onOpenChange={setShowItemDialog}
        meetingId={id}
        item={editingItem}
        onSaved={load}
      />
      <SendConvocationDialog
        open={showSendDialog}
        onOpenChange={setShowSendDialog}
        meetingId={id}
        onSent={load}
      />
    </div>
  );
}
