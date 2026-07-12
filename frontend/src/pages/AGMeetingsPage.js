import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { extractApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import { toast } from 'sonner';
import {
  Vote, Plus, Calendar, MapPin, Clock, TriangleAlert, CheckCircle2,
  FileText, Send, ArrowRight, Trash2, Edit3, RefreshCw,
} from 'lucide-react';

const STATUS_BADGES = {
  draft: { label: 'Brouillon', className: 'bg-slate-100 text-slate-700' },
  convocation_sent: { label: 'Convocation envoyee', className: 'bg-blue-100 text-[#01213e]' },
  held: { label: 'Tenue', className: 'bg-emerald-100 text-emerald-700' },
  archived: { label: 'Archivee', className: 'bg-slate-200 text-slate-500' },
  cancelled: { label: 'Annulee', className: 'bg-red-100 text-red-700' },
};

const TYPE_LABELS = {
  ordinaire: 'Ordinaire',
  extraordinaire: 'Extraordinaire',
  second_call: '2e convocation',
};

// ============ Create AG dialog ============
function CreateAGDialog({ open, onOpenChange, copropriete_id, onCreated }) {
  const [form, setForm] = useState({
    type: 'ordinaire',
    scheduled_date: '',
    scheduled_time: '18:30',
    location: '',
    notes: '',
    quorum_required: true,
  });
  const [saving, setSaving] = useState(false);
  const [legalCheck, setLegalCheck] = useState(null);

  useEffect(() => {
    if (!form.scheduled_date) { setLegalCheck(null); return; }
    const today = new Date();
    const sd = new Date(form.scheduled_date);
    const days = Math.floor((sd - today) / (1000 * 60 * 60 * 24));
    setLegalCheck({
      days,
      on_time: days >= 15,
      warning: days < 15 ? `Delai legal non respecte : ${days}j < 15j min (art. 3.87 §2 CC)` : null,
    });
  }, [form.scheduled_date]);

  const create = async () => {
    if (!form.scheduled_date || !form.location) {
      toast.error('Date et lieu sont obligatoires');
      return;
    }
    setSaving(true);
    try {
      const r = await api.post('/ag/meetings', {
        copropriete_id, ...form, agenda_items: [],
      });
      toast.success('AG creee');
      onCreated?.(r.data);
      onOpenChange(false);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl" data-testid="dialog-create-ag">
        <DialogHeader>
          <DialogTitle>Planifier une Assemblee Generale</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs">Type</Label>
            <Select value={form.type} onValueChange={(v) => setForm({
              ...form, type: v,
              quorum_required: v !== 'second_call',
            })}>
              <SelectTrigger data-testid="ag-select-type"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ordinaire">AG Ordinaire (annuelle)</SelectItem>
                <SelectItem value="extraordinaire">AG Extraordinaire</SelectItem>
                <SelectItem value="second_call">2e convocation (quorum echoue)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label className="text-xs">Date *</Label>
              <Input type="date" value={form.scheduled_date}
                     onChange={(e) => setForm({ ...form, scheduled_date: e.target.value })}
                     data-testid="ag-input-date" />
            </div>
            <div>
              <Label className="text-xs">Heure</Label>
              <Input type="time" value={form.scheduled_time}
                     onChange={(e) => setForm({ ...form, scheduled_time: e.target.value })}
                     data-testid="ag-input-time" />
            </div>
          </div>
          <div>
            <Label className="text-xs">Lieu *</Label>
            <Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })}
                   placeholder="Salle communale, Rue X 12, 1000 Bruxelles"
                   data-testid="ag-input-location" />
          </div>
          <div>
            <Label className="text-xs">Notes internes</Label>
            <Textarea rows={2} value={form.notes}
                      onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          {legalCheck && (
            <div className={`p-2 rounded text-sm flex items-start gap-2 ${legalCheck.on_time ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'}`}
                 data-testid="ag-legal-check">
              {legalCheck.on_time ? <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" /> : <TriangleAlert className="h-4 w-4 shrink-0 mt-0.5" />}
              <div>
                <b>{legalCheck.on_time ? 'Delai legal respecte' : 'Attention'}</b> —
                {' '}J-{legalCheck.days} avant l&apos;AG.
                {legalCheck.warning && <div className="text-xs mt-1">{legalCheck.warning}</div>}
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Annuler</Button>
          <Button onClick={create} disabled={saving} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="ag-btn-create-confirm">
            <Plus className="h-4 w-4 mr-1" /> Creer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Main list page ============
export default function AGMeetingsPage() {
  const { selectedCopro } = useAuth();
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    if (!selectedCopro || selectedCopro === 'all') { setMeetings([]); return; }
    setLoading(true);
    try {
      const r = await api.get(`/ag/meetings?copropriete_id=${selectedCopro}`);
      setMeetings(r.data.meetings || []);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setLoading(false); }
  }, [selectedCopro]);

  useEffect(() => { load(); }, [load]);

  if (!selectedCopro || selectedCopro === 'all') {
    return (
      <div className="p-6 max-w-6xl mx-auto text-amber-600 text-sm flex items-center gap-2">
        <TriangleAlert className="h-4 w-4" />
        Selectionnez une copropriete pour gerer les Assemblees Generales.
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-4" data-testid="ag-meetings-page">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Vote className="h-6 w-6 text-[#022D52]" /> Assemblees Generales
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            Convocations conformes au Code civil belge (Livre 3, art. 3.87 et 3.88).
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={load} data-testid="ag-btn-refresh">
            <RefreshCw className="h-4 w-4" />
          </Button>
          <Button onClick={() => setCreating(true)} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid="ag-btn-new-meeting">
            <Plus className="h-4 w-4 mr-1" /> Nouvelle AG
          </Button>
        </div>
      </header>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Type</TableHead>
                <TableHead>Date & heure</TableHead>
                <TableHead>Lieu</TableHead>
                <TableHead>Points ordre du jour</TableHead>
                <TableHead className="text-center">Statut</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={6} className="p-8 text-center text-slate-500">Chargement...</TableCell></TableRow>
              ) : meetings.length === 0 ? (
                <TableRow><TableCell colSpan={6} className="p-8 text-center text-slate-500">
                  Aucune AG planifiee pour cette copropriete.
                </TableCell></TableRow>
              ) : meetings.map(m => {
                const sb = STATUS_BADGES[m.status] || STATUS_BADGES.draft;
                const legal = m.legal_check || {};
                return (
                  <TableRow key={m.id} data-testid={`ag-row-${m.id}`}>
                    <TableCell>
                      <div className="font-medium">{TYPE_LABELS[m.type] || m.type}</div>
                      {legal.days_until_ag !== undefined && legal.days_until_ag >= 0 && m.status === 'draft' && (
                        <div className={`text-[10px] ${legal.on_time ? 'text-emerald-600' : 'text-red-600'}`}>
                          J-{legal.days_until_ag}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1 text-sm">
                        <Calendar className="h-3 w-3 text-slate-400" />
                        {m.scheduled_date}
                      </div>
                      <div className="flex items-center gap-1 text-xs text-slate-500">
                        <Clock className="h-3 w-3" /> {m.scheduled_time}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1 text-xs text-slate-600 max-w-xs truncate">
                        <MapPin className="h-3 w-3 shrink-0 text-slate-400" />
                        {m.location}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{(m.agenda_items || []).length}</Badge>
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge className={sb.className}>{sb.label}</Badge>
                      {m.convocation_sent_at && (
                        <div className="text-[10px] text-slate-500 mt-1">
                          {m.convocation_sent_to_count} envoi(s)
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline"
                              onClick={() => navigate(`/ag/${m.id}`)}
                              data-testid={`ag-btn-open-${m.id}`}>
                        Ouvrir <ArrowRight className="h-3 w-3 ml-1" />
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <CreateAGDialog
        open={creating}
        onOpenChange={setCreating}
        copropriete_id={selectedCopro}
        onCreated={(m) => { load(); navigate(`/ag/${m.id}`); }}
      />
    </div>
  );
}
