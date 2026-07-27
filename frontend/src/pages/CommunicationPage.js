import { useState, useEffect, useCallback, useMemo } from 'react';
import api, { extractApiError } from '@/lib/api';
import { sanitizeHtml } from '@/lib/sanitizeHtml';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog';
import { toast } from 'sonner';
import {
  Mail, Send, Users, FileSignature, Inbox, TriangleAlert, Trash2, Plus,
  ArrowDownRight, ArrowUpRight, RefreshCw, Search, Eye,
} from 'lucide-react';

const currency = (v) => (v || 0).toLocaleString('fr-BE', { style: 'currency', currency: 'EUR' });

function BalanceBadge({ balance }) {
  if (balance > 0.01) {
    return (
      <Badge className="bg-red-100 text-red-700 border border-red-300" data-testid="badge-debtor">
        <ArrowUpRight className="h-3 w-3 mr-1" />{currency(balance)}
      </Badge>
    );
  }
  if (balance < -0.01) {
    return (
      <Badge className="bg-emerald-100 text-emerald-700 border border-emerald-300" data-testid="badge-creditor">
        <ArrowDownRight className="h-3 w-3 mr-1" />{currency(Math.abs(balance))} de trop
      </Badge>
    );
  }
  return (
    <Badge className="bg-slate-100 text-slate-700 border border-slate-300" data-testid="badge-neutral">
      Solde
    </Badge>
  );
}

// ============ Mailboxes (cabinet-scoped) ============
function MailboxesSection({ onChange }) {
  const [boxes, setBoxes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [newAddr, setNewAddr] = useState('');
  const [newName, setNewName] = useState('');
  const { user } = useAuth();
  const canManage = ['syndic', 'admin', 'superadmin'].includes(user?.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/communication/mailboxes');
      setBoxes(r.data.mailboxes || []);
      onChange?.(r.data.mailboxes || []);
    } catch (e) {
      toast.error(extractApiError(e, 'Impossible de charger les boites'));
    } finally { setLoading(false); }
  }, [onChange]);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!newAddr.trim()) return;
    try {
      await api.post('/communication/mailboxes', { address: newAddr, display_name: newName });
      setNewAddr(''); setNewName('');
      toast.success('Boite ajoutee');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };
  const remove = async (addr) => {
    if (!window.confirm(`Retirer ${addr} ?`)) return;
    try {
      await api.delete(`/communication/mailboxes?address=${encodeURIComponent(addr)}`);
      toast.success('Boite retiree');
      load();
    } catch (e) { toast.error(extractApiError(e)); }
  };

  return (
    <Card data-testid="mailboxes-section">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Inbox className="h-4 w-4 text-[#022D52]" />
          Boites mail autorisees {canManage ? '(cabinet)' : '(heritees du syndic)'}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <div className="text-sm text-slate-500">Chargement...</div>
        ) : boxes.length === 0 ? (
          <div className="text-sm text-slate-500 flex items-center gap-2">
            <TriangleAlert className="h-4 w-4 text-amber-500" />
            Aucune boite configuree. {canManage && 'Ajoutez une adresse ci-dessous.'}
          </div>
        ) : (
          <ul className="divide-y border rounded-md">
            {boxes.map((b) => (
              <li key={b.address} className="flex items-center justify-between p-2 text-sm">
                <div>
                  <div className="font-mono text-slate-800">{b.address}</div>
                  {b.display_name && <div className="text-xs text-slate-500">{b.display_name}</div>}
                </div>
                {canManage && !b.default && (
                  <Button variant="ghost" size="sm" onClick={() => remove(b.address)}
                          data-testid={`btn-remove-mailbox-${b.address}`}>
                    <Trash2 className="h-4 w-4 text-red-500" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {canManage && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2 pt-2 border-t">
            <Input placeholder="adresse@cabinet.be" value={newAddr}
                   onChange={(e) => setNewAddr(e.target.value)}
                   data-testid="input-new-mailbox-address" />
            <Input placeholder="Nom affiche (optionnel)" value={newName}
                   onChange={(e) => setNewName(e.target.value)}
                   data-testid="input-new-mailbox-name" />
            <Button onClick={add} data-testid="btn-add-mailbox">
              <Plus className="h-4 w-4 mr-1" /> Ajouter
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============ Signature editor (per-user) ============
function SignatureSection() {
  const [html, setHtml] = useState('');
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get('/communication/signature').then(r => {
      setHtml(r.data.signature_html || '');
      setName(r.data.name || '');
    }).catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await api.put('/communication/signature', { signature_html: html });
      toast.success('Signature enregistree');
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSaving(false); }
  };

  return (
    <Card data-testid="signature-section">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <FileSignature className="h-4 w-4 text-violet-600" />
          Ma signature ({name})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          rows={8}
          value={html}
          onChange={(e) => setHtml(e.target.value)}
          placeholder="<b>Prenom Nom</b><br>Gestionnaire<br>tel : +32 X XX XX XX"
          className="font-mono text-xs"
          data-testid="textarea-signature-html"
        />
        <div className="p-3 border rounded-md bg-slate-50">
          <div className="text-xs text-slate-500 mb-1">Apercu :</div>
          <div className="text-sm" dangerouslySetInnerHTML={{ __html: sanitizeHtml(html || '<em>Signature vide</em>') }} />
        </div>
        <Button onClick={save} disabled={saving} data-testid="btn-save-signature">
          <FileSignature className="h-4 w-4 mr-1" /> Enregistrer
        </Button>
      </CardContent>
    </Card>
  );
}

// ============ Send action dialogs ============
function SendActionDialog({
  action, // 'situation' | 'decompte'
  mailboxes,
  selectedOwners,
  copropriete_id,
  fiscalYears,
  onSent,
}) {
  const [open, setOpen] = useState(false);
  const [from_mailbox, setFrom] = useState('');
  const [subject, setSubject] = useState('');
  const [body_html, setBody] = useState('');
  const [include_signature, setIncSig] = useState(true);
  const [start_date, setStart] = useState('');
  const [end_date, setEnd] = useState('');
  const [fiscal_year_id, setFy] = useState('');
  const [sending, setSending] = useState(false);
  // iter90aw : template
  const [templates, setTemplates] = useState([]);
  const [template_id, setTemplateId] = useState('');
  // iter90fv : previsualisation email + PJ PDF
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewData, setPreviewData] = useState(null); // { owner_id, subject, body_html, attachment_pdf_base64, ... }
  const [previewIdx, setPreviewIdx] = useState(0);
  // iter90i8 : PJ additionnelles au decompte (choix syndic)
  const [attachableDocs, setAttachableDocs] = useState([]);
  const [selectedExtraDocIds, setSelectedExtraDocIds] = useState(new Set());
  const [includeExpensesList, setIncludeExpensesList] = useState(false);

  useEffect(() => {
    if (mailboxes.length && !from_mailbox) {
      const def = mailboxes.find(b => b.default) || mailboxes[0];
      setFrom(def.address);
    }
  }, [mailboxes, from_mailbox]);
  useEffect(() => {
    if (!open) return;
    api.get('/email-templates').then(r => setTemplates(r.data.templates || [])).catch(() => {});
  }, [open]);

  // iter90fx : auto-remplit la date de debut avec le 1er jour de l'exercice
  // comptable courant a chaque ouverture du modal (situation et decompte).
  // L'utilisateur peut toujours modifier la valeur.
  // Priorite : exercice deja selectionne (decompte) > FY "open" > FY le plus recent.
  useEffect(() => {
    if (!open || !fiscalYears || fiscalYears.length === 0) return;
    let target = null;
    if (fiscal_year_id) {
      target = fiscalYears.find(y => y.id === fiscal_year_id);
    }
    if (!target) target = fiscalYears.find(y => y.status === 'open');
    if (!target) target = [...fiscalYears].sort((a, b) => (b.start_date || '').localeCompare(a.start_date || ''))[0];
    if (target && target.start_date) {
      const fyStart = String(target.start_date).slice(0, 10);
      // Ne pas ecraser si l'utilisateur a deja saisi une date
      setStart(prev => prev || fyStart);
    }
  }, [open, fiscal_year_id, fiscalYears]);

  // iter90i8 : charge la liste des documents joignables quand l'exercice change
  useEffect(() => {
    if (!open || action !== 'decompte' || !fiscal_year_id || !copropriete_id) {
      setAttachableDocs([]);
      setSelectedExtraDocIds(new Set());
      setIncludeExpensesList(false);
      return;
    }
    api.get('/communication/attachable-documents', {
      params: { copropriete_id, fiscal_year_id }
    })
      .then(r => setAttachableDocs(r.data?.meter_attachments || []))
      .catch(() => setAttachableDocs([]));
  }, [open, action, fiscal_year_id, copropriete_id]);

  // Auto-remplit subject/body a la selection d'un template
  const applyTemplate = (tid) => {
    setTemplateId(tid);
    if (!tid) return;
    const tpl = templates.find(t => t.id === tid);
    if (tpl) {
      setSubject(tpl.subject || '');
      setBody(tpl.body_html || '');
    }
  };

  const defaultsFor = {
    situation: {
      title: 'Envoi situation de compte',
      subject: 'Situation de votre compte - Copropriete',
      body: 'Bonjour,<br><br>Veuillez trouver en piece jointe la situation actuelle de votre compte.<br><br>Cordialement,',
    },
    decompte: {
      title: 'Envoi decompte annuel',
      subject: 'Decompte annuel de charges - Copropriete',
      body: 'Bonjour,<br><br>Veuillez trouver en piece jointe votre decompte annuel de charges.<br><br>N\'hesitez pas a nous contacter en cas de question.<br><br>Cordialement,',
    },
  }[action];

  const send = async () => {
    if (!from_mailbox) return toast.error('Choisissez une boite expeditrice');
    if (selectedOwners.length === 0) return toast.error('Selectionnez au moins un proprietaire');
    if (action === 'decompte' && !fiscal_year_id) return toast.error('Choisissez un exercice');
    setSending(true);
    try {
      const payload = {
        from_mailbox,
        copropriete_id,
        owner_ids: selectedOwners.map(o => o.owner_id),
        subject: subject || defaultsFor.subject,
        body_html: body_html || defaultsFor.body,
        include_signature,
        template_id: template_id || '',
        ...(action === 'situation' ? { start_date, end_date } : {}),
        ...(action === 'decompte' ? {
          fiscal_year_id,
          // iter90i8 : PJ selectionnees par le syndic
          include_expenses_list: includeExpensesList,
          extra_document_ids: Array.from(selectedExtraDocIds),
        } : {}),
      };
      const r = await api.post(`/communication/send/${action}`, payload);
      const info = r.data.sent > 0
        ? `${r.data.sent} email(s) envoye(s)${r.data.dry_run ? ' (mode dry-run)' : ''}`
        : 'Aucun email envoye';
      if (r.data.failed?.length) {
        // iter90dc : affiche la raison detaillee du premier echec pour aider au debug
        const firstFail = r.data.failed[0] || {};
        const reason = firstFail.reason || 'raison inconnue';
        const suffix = r.data.failed.length > 1 ? ` (+${r.data.failed.length - 1} autre(s))` : '';
        toast.warning(`${info}. ${r.data.failed.length} echec(s) : ${reason}${suffix}`, {
          duration: 12000,
        });
      } else {
        toast.success(info);
      }
      setOpen(false);
      onSent?.(r.data);
    } catch (e) {
      toast.error(extractApiError(e, 'Envoi echoue'));
    } finally { setSending(false); }
  };

  // iter90fv : appelle l'endpoint preview pour rendre subject + body + PDF PJ
  // pour UN destinataire (celui de l'index courant). Utilise pour l'apercu
  // avant envoi.
  const loadPreview = async (idx) => {
    const owner = selectedOwners[idx];
    if (!owner) return;
    if (!from_mailbox) return toast.error('Choisissez une boite expeditrice');
    if (action === 'decompte' && !fiscal_year_id) return toast.error('Choisissez un exercice');
    setPreviewLoading(true);
    try {
      const payload = {
        from_mailbox,
        copropriete_id,
        owner_id: owner.owner_id,
        subject: subject || defaultsFor.subject,
        body_html: body_html || defaultsFor.body,
        include_signature,
        template_id: template_id || '',
        ...(action === 'situation' ? { start_date, end_date } : {}),
        ...(action === 'decompte' ? { fiscal_year_id } : {}),
      };
      const r = await api.post(`/communication/preview/${action}`, payload);
      setPreviewData(r.data);
    } catch (e) {
      toast.error(extractApiError(e, 'Erreur de generation de l apercu'));
    } finally { setPreviewLoading(false); }
  };

  const openPreview = () => {
    if (selectedOwners.length === 0) return toast.error('Selectionnez au moins un proprietaire');
    if (action === 'decompte' && !fiscal_year_id) return toast.error('Choisissez un exercice');
    setPreviewIdx(0);
    setPreviewData(null);
    setPreviewOpen(true);
    loadPreview(0);
  };
  const cyclePreview = (delta) => {
    const next = Math.min(Math.max(previewIdx + delta, 0), selectedOwners.length - 1);
    if (next === previewIdx) return;
    setPreviewIdx(next);
    setPreviewData(null);
    loadPreview(next);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          className="bg-[#022D52] hover:bg-[#01213e]"
          disabled={selectedOwners.length === 0}
          data-testid={`btn-open-send-${action}`}
        >
          <Send className="h-4 w-4 mr-1" />
          {defaultsFor.title} ({selectedOwners.length})
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl" data-testid={`dialog-send-${action}`}>
        <DialogHeader>
          <DialogTitle>{defaultsFor.title} - {selectedOwners.length} destinataire(s)</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <Label className="text-xs">Envoyer depuis</Label>
            <Select value={from_mailbox} onValueChange={setFrom}>
              <SelectTrigger data-testid={`select-from-${action}`}><SelectValue placeholder="Choisir une boite" /></SelectTrigger>
              <SelectContent>
                {mailboxes.map(b => (
                  <SelectItem key={b.address} value={b.address}>
                    {b.address}{b.default ? ' (defaut)' : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {/* iter90aw : Selecteur de template */}
          <div>
            <Label className="text-xs flex items-center justify-between">
              Modele email <span className="text-slate-400">(optionnel - remplit sujet + corps)</span>
            </Label>
            <Select value={template_id || 'none'} onValueChange={(v) => applyTemplate(v === 'none' ? '' : v)}>
              <SelectTrigger data-testid={`select-template-${action}`}>
                <SelectValue placeholder="Aucun modele - texte manuel" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Aucun modele (texte manuel)</SelectItem>
                {templates.map(t => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.name} {t.category ? `- ${t.category}` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {action === 'situation' && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label className="text-xs">Du (optionnel)</Label>
                <Input type="date" value={start_date} onChange={(e) => setStart(e.target.value)}
                       data-testid="input-start-date" />
              </div>
              <div>
                <Label className="text-xs">Au (optionnel)</Label>
                <Input type="date" value={end_date} onChange={(e) => setEnd(e.target.value)}
                       data-testid="input-end-date" />
              </div>
            </div>
          )}
          {action === 'decompte' && (
            <div>
              <Label className="text-xs">Exercice</Label>
              <Select value={fiscal_year_id} onValueChange={setFy}>
                <SelectTrigger data-testid="select-fiscal-year"><SelectValue placeholder="Choisir un exercice" /></SelectTrigger>
                <SelectContent>
                  {(fiscalYears || []).map(fy => (
                    <SelectItem key={fy.id} value={fy.id}>{fy.name} ({fy.status || 'ouvert'})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {/* iter90i8 : selection des PJ additionnelles au decompte annuel */}
          {action === 'decompte' && fiscal_year_id && (
            <div className="border border-slate-200 rounded p-3 bg-slate-50" data-testid="decompte-extra-attachments">
              <div className="text-xs font-semibold uppercase tracking-wider text-slate-600 mb-2">
                Pieces jointes additionnelles (facultatif)
              </div>
              <label className="flex items-center gap-2 text-sm cursor-pointer hover:bg-white rounded px-2 py-1">
                <input
                  type="checkbox"
                  checked={includeExpensesList}
                  onChange={e => setIncludeExpensesList(e.target.checked)}
                  data-testid="decompte-include-expenses"
                />
                <span>Liste des depenses de l&apos;exercice</span>
              </label>
              {attachableDocs.length === 0 ? (
                <div className="text-[11px] text-slate-500 italic mt-2 px-2">
                  Aucune piece jointe de releve de compteur disponible pour cet exercice.
                </div>
              ) : (
                <div className="space-y-1 mt-2">
                  <div className="text-[11px] text-slate-500 px-2">
                    Releves de compteur ({attachableDocs.length}) :
                  </div>
                  {attachableDocs.map(d => (
                    <label key={d.id} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-white rounded px-2 py-1">
                      <input
                        type="checkbox"
                        checked={selectedExtraDocIds.has(d.id)}
                        onChange={e => {
                          const s = new Set(selectedExtraDocIds);
                          if (e.target.checked) s.add(d.id); else s.delete(d.id);
                          setSelectedExtraDocIds(s);
                        }}
                        data-testid={`decompte-extra-doc-${d.id}`}
                      />
                      <span className="flex-1 truncate">{d.title}</span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {d.size ? `${(d.size / 1024).toFixed(1)} Ko` : ''}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          <div>
            <Label className="text-xs">Objet</Label>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)}
                   placeholder={defaultsFor.subject}
                   data-testid={`input-subject-${action}`} />
          </div>
          <div>
            <Label className="text-xs">Corps du message (HTML)</Label>
            <Textarea rows={6} value={body_html} onChange={(e) => setBody(e.target.value)}
                      placeholder={defaultsFor.body}
                      data-testid={`textarea-body-${action}`} />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id={`sig-${action}`} checked={include_signature}
                      onCheckedChange={setIncSig}
                      data-testid={`checkbox-include-signature-${action}`} />
            <Label htmlFor={`sig-${action}`} className="text-sm">Inclure ma signature</Label>
          </div>
          <div className="rounded-md bg-slate-50 border border-slate-200 p-2 text-xs text-slate-600">
            Destinataires : {selectedOwners.map(o => `${o.owner_name} <${o.email || '?'}>`).join(', ')}
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Annuler</Button>
          {/* iter90fv : bouton Previsualiser AVANT envoi */}
          <Button
            variant="outline"
            onClick={openPreview}
            disabled={selectedOwners.length === 0 || !from_mailbox}
            className="text-[#022D52] border-[#022D52]/40 hover:bg-[#022D52]/10"
            data-testid={`btn-preview-${action}`}
          >
            <Eye className="h-4 w-4 mr-1" /> Previsualiser
          </Button>
          <Button onClick={send} disabled={sending} className="bg-[#022D52] hover:bg-[#01213e]"
                  data-testid={`btn-confirm-send-${action}`}>
            <Send className="h-4 w-4 mr-1" /> Envoyer
          </Button>
        </DialogFooter>
      </DialogContent>

      {/* iter90fv : Sub-dialog d'apercu du mail rendu + PDF PJ inline */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-6xl w-[95vw] h-[90vh] p-0 overflow-hidden flex flex-col" data-testid={`dialog-preview-${action}`}>
          <DialogHeader className="px-6 py-3 border-b border-slate-200 bg-gradient-to-r from-[#022D52] to-[#1D4ED8] text-white shrink-0">
            <DialogTitle className="flex items-center justify-between text-white">
              <div className="flex items-center gap-2">
                <Eye className="h-5 w-5" />
                <span>Apercu du mail avant envoi</span>
                <span className="text-xs opacity-80 ml-2">
                  ({previewIdx + 1} / {selectedOwners.length})
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cyclePreview(-1)}
                  disabled={previewIdx === 0 || previewLoading}
                  className="bg-white text-[#022D52] hover:bg-slate-100 h-8"
                  data-testid="btn-preview-prev"
                >
                  Precedent
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => cyclePreview(1)}
                  disabled={previewIdx >= selectedOwners.length - 1 || previewLoading}
                  className="bg-white text-[#022D52] hover:bg-slate-100 h-8"
                  data-testid="btn-preview-next"
                >
                  Suivant
                </Button>
              </div>
            </DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-hidden bg-slate-100 grid grid-cols-2 gap-0">
            {/* Colonne gauche : mail rendu */}
            <div className="border-r border-slate-200 bg-white flex flex-col overflow-hidden">
              <div className="px-4 py-2 border-b border-slate-200 bg-slate-50 text-xs">
                {previewLoading ? (
                  <div className="text-slate-500">Chargement de l apercu...</div>
                ) : previewData ? (
                  <>
                    <div><span className="text-slate-500">De :</span> <span className="font-mono">{previewData.from_mailbox}</span></div>
                    <div><span className="text-slate-500">A :</span> <span className="font-mono">{previewData.owner_name} &lt;{previewData.owner_email || '(email manquant)'}&gt;</span></div>
                    <div><span className="text-slate-500">Objet :</span> <b>{previewData.subject}</b></div>
                    <div><span className="text-slate-500">Piece jointe :</span> <span className="font-mono text-[#022D52]">{previewData.attachment_filename}</span></div>
                  </>
                ) : (
                  <div className="text-slate-400">Aucune donnee</div>
                )}
              </div>
              <div className="flex-1 overflow-auto p-4">
                {previewLoading ? (
                  <div className="flex items-center justify-center h-full text-slate-400">
                    <div className="animate-spin h-8 w-8 border-4 border-[#022D52] border-t-transparent rounded-full" />
                  </div>
                ) : previewData ? (
                  <div
                    className="prose prose-sm max-w-none"
                    data-testid="preview-body-html"
                    dangerouslySetInnerHTML={{ __html: sanitizeHtml(previewData.body_html || '') }}
                  />
                ) : null}
              </div>
            </div>
            {/* Colonne droite : PDF PJ inline */}
            <div className="bg-slate-800 flex flex-col overflow-hidden">
              <div className="px-4 py-2 border-b border-slate-700 bg-slate-900 text-xs text-slate-300">
                Apercu de la piece jointe PDF
              </div>
              <div className="flex-1 overflow-hidden">
                {previewData?.attachment_pdf_base64 ? (
                  <iframe
                    src={`data:application/pdf;base64,${previewData.attachment_pdf_base64}`}
                    title="Apercu PDF piece jointe"
                    className="w-full h-full border-0"
                    data-testid="preview-attachment-iframe"
                  />
                ) : (
                  <div className="flex items-center justify-center h-full text-slate-400 text-sm">
                    {previewLoading ? 'Generation du PDF...' : 'Aucun PDF a afficher'}
                  </div>
                )}
              </div>
            </div>
          </div>
          <DialogFooter className="px-6 py-3 border-t border-slate-200 bg-white shrink-0">
            <Button variant="ghost" onClick={() => setPreviewOpen(false)}>Fermer</Button>
            <Button
              onClick={() => { setPreviewOpen(false); send(); }}
              disabled={sending}
              className="bg-[#022D52] hover:bg-[#01213e]"
              data-testid={`btn-preview-confirm-send-${action}`}
            >
              <Send className="h-4 w-4 mr-1" /> Confirmer l envoi ({selectedOwners.length} destinataires)
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}

// ============ Generic email composer ============
function GenericComposer({ mailboxes, copropriete_id }) {
  const [from_mailbox, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body_html, setBody] = useState('');
  const [include_signature, setIncSig] = useState(true);
  const [file, setFile] = useState(null);
  const [sending, setSending] = useState(false);
  // iter90fx : listes proprietaires/locataires + toggle CCI GDPR
  const [addressBook, setAddressBook] = useState({ owners: [], tenants: [] });
  const [abLoading, setAbLoading] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(null); // 'owners' | 'tenants' | null
  const [pickerSel, setPickerSel] = useState(new Set()); // emails cochees dans le picker actif
  const [pickerSearch, setPickerSearch] = useState('');
  // Manual override du toggle. `null` = auto (BCC si >1 destinataire).
  const [useBccOverride, setUseBccOverride] = useState(null);

  // Emails valides deduits du champ "to"
  const emails = to.split(/[,;\s]+/).map(s => s.trim()).filter(s => s.includes('@'));
  const auto_bcc = emails.length > 1;
  const use_bcc = useBccOverride !== null ? useBccOverride : auto_bcc;

  useEffect(() => {
    if (mailboxes.length && !from_mailbox) {
      const def = mailboxes.find(b => b.default) || mailboxes[0];
      setFrom(def.address);
    }
  }, [mailboxes, from_mailbox]);

  // iter90fx : charge l'address book quand la copropriete change
  useEffect(() => {
    if (!copropriete_id || copropriete_id === 'all') {
      setAddressBook({ owners: [], tenants: [] });
      return;
    }
    (async () => {
      setAbLoading(true);
      try {
        const r = await api.get('/communication/address-book');
        setAddressBook({ owners: r.data.owners || [], tenants: r.data.tenants || [] });
      } catch (e) {
        // Silencieux : le composant reste fonctionnel meme sans address book
      } finally { setAbLoading(false); }
    })();
  }, [copropriete_id]);

  const openPicker = (kind) => {
    const current = new Set(emails);
    setPickerSel(new Set([...current]));
    setPickerSearch('');
    setPickerOpen(kind);
  };
  const togglePickerEmail = (email) => {
    const next = new Set(pickerSel);
    if (next.has(email)) next.delete(email); else next.add(email);
    setPickerSel(next);
  };
  const applyPicker = () => {
    const merged = Array.from(new Set([...emails, ...pickerSel]));
    setTo(merged.join(', '));
    setPickerOpen(null);
  };
  const removeChip = (email) => {
    setTo(emails.filter(e => e !== email).join(', '));
  };

  const send = async () => {
    if (!from_mailbox) return toast.error('Choisissez une boite expeditrice');
    if (emails.length === 0) return toast.error('Aucun destinataire valide');
    if (!subject.trim()) return toast.error('Objet requis');
    setSending(true);
    try {
      const fd = new FormData();
      fd.append('from_mailbox', from_mailbox);
      fd.append('to_json', JSON.stringify(emails));
      fd.append('subject', subject);
      fd.append('body_html', body_html);
      fd.append('include_signature', String(include_signature));
      fd.append('use_bcc', String(use_bcc));
      if (file) fd.append('attachment', file);
      const r = await api.post('/communication/send/generic', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const bccInfo = use_bcc ? ` (${emails.length} destinataires en CCI)` : '';
      toast.success(`Email envoye${r.data.dry_run ? ' (mode dry-run)' : ''}${bccInfo}`);
      setTo(''); setSubject(''); setBody(''); setFile(null); setUseBccOverride(null);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSending(false); }
  };

  const pickerList = pickerOpen === 'owners' ? addressBook.owners : (pickerOpen === 'tenants' ? addressBook.tenants : []);
  const filteredPickerList = pickerSearch.trim()
    ? pickerList.filter(p =>
        (p.name || '').toLowerCase().includes(pickerSearch.toLowerCase()) ||
        (p.email || '').toLowerCase().includes(pickerSearch.toLowerCase()))
    : pickerList;

  return (
    <Card data-testid="generic-composer">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Mail className="h-4 w-4 text-[#022D52]" />
          Email libre avec piece jointe
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <Label className="text-xs">Envoyer depuis</Label>
            <Select value={from_mailbox} onValueChange={setFrom}>
              <SelectTrigger data-testid="select-from-generic"><SelectValue placeholder="Choisir une boite" /></SelectTrigger>
              <SelectContent>
                {mailboxes.map(b => (<SelectItem key={b.address} value={b.address}>{b.address}</SelectItem>))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <div className="flex items-center justify-between">
              <Label className="text-xs">Destinataires (separes par virgule)</Label>
              {/* iter90fx : pickers proprietaires + locataires de l'ACP */}
              <div className="flex items-center gap-1">
                <Button
                  type="button" variant="outline" size="sm"
                  className="h-6 px-2 text-[10px] text-[#022D52] border-[#022D52]/30"
                  onClick={() => openPicker('owners')}
                  disabled={abLoading || addressBook.owners.length === 0}
                  data-testid="btn-picker-owners"
                >
                  <Users className="h-3 w-3 mr-1" />
                  Proprietaires ({addressBook.owners.length})
                </Button>
                <Button
                  type="button" variant="outline" size="sm"
                  className="h-6 px-2 text-[10px] text-emerald-700 border-emerald-300"
                  onClick={() => openPicker('tenants')}
                  disabled={abLoading || addressBook.tenants.length === 0}
                  data-testid="btn-picker-tenants"
                >
                  <Users className="h-3 w-3 mr-1" />
                  Locataires ({addressBook.tenants.length})
                </Button>
              </div>
            </div>
            <Input value={to} onChange={(e) => setTo(e.target.value)}
                   placeholder="user1@ex.com, user2@ex.com"
                   data-testid="input-generic-to" />
            {/* Chips visuels des emails detectes */}
            {emails.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5" data-testid="chips-recipients">
                {emails.map(e => (
                  <span key={e}
                        className="inline-flex items-center gap-1 text-[10px] bg-slate-100 text-slate-700 border border-slate-200 rounded-full px-2 py-0.5"
                        data-testid={`chip-recipient-${e}`}>
                    {e}
                    <button type="button" onClick={() => removeChip(e)}
                            className="text-slate-400 hover:text-red-500"
                            aria-label={`Retirer ${e}`}>x</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        {/* iter90fx : Toggle CCI GDPR - active auto si >1 destinataire */}
        {emails.length > 1 && (
          <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded p-2">
            <Checkbox
              id="gen-bcc"
              checked={use_bcc}
              onCheckedChange={(v) => setUseBccOverride(!!v)}
              data-testid="checkbox-generic-bcc"
              className="mt-0.5"
            />
            <Label htmlFor="gen-bcc" className="text-xs leading-snug flex-1 cursor-pointer">
              <b>Envoyer en CCI (invisible entre destinataires)</b>
              <span className="block text-slate-600 mt-0.5">
                Recommande pour tout envoi groupe (RGPD). Les destinataires ne verront pas les adresses des autres.
                Vous recevez une copie dans &laquo;&nbsp;{from_mailbox || 'votre boite'}&nbsp;&raquo;.
              </span>
            </Label>
          </div>
        )}
        <div>
          <Label className="text-xs">Objet</Label>
          <Input value={subject} onChange={(e) => setSubject(e.target.value)}
                 data-testid="input-generic-subject" />
        </div>
        <div>
          <Label className="text-xs">Message (HTML)</Label>
          <Textarea rows={6} value={body_html} onChange={(e) => setBody(e.target.value)}
                    data-testid="textarea-generic-body" />
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Checkbox id="gen-sig" checked={include_signature}
                      onCheckedChange={setIncSig}
                      data-testid="checkbox-generic-signature" />
            <Label htmlFor="gen-sig" className="text-sm">Signature</Label>
          </div>
          <input type="file" accept="application/pdf"
                 onChange={(e) => setFile(e.target.files?.[0] || null)}
                 className="text-xs"
                 data-testid="input-generic-attachment" />
        </div>
        <Button onClick={send} disabled={sending} className="bg-[#022D52] hover:bg-[#01213e]"
                data-testid="btn-send-generic">
          <Send className="h-4 w-4 mr-1" /> Envoyer
        </Button>
      </CardContent>

      {/* iter90fx : Picker Proprietaires / Locataires */}
      <Dialog open={pickerOpen !== null} onOpenChange={(v) => !v && setPickerOpen(null)}>
        <DialogContent className="max-w-2xl" data-testid="dialog-address-picker">
          <DialogHeader>
            <DialogTitle>
              Selection {pickerOpen === 'owners' ? 'proprietaires' : 'locataires'}
              <span className="ml-2 text-xs text-slate-500 font-normal">
                ({pickerSel.size} coche(s))
              </span>
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-slate-400" />
              <Input
                placeholder="Rechercher par nom ou email..."
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                className="pl-7"
                data-testid="input-picker-search"
              />
            </div>
            <div className="flex items-center justify-between text-xs text-slate-500 px-1">
              <span>{filteredPickerList.length} disponibles</span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="text-[#022D52] hover:underline"
                  onClick={() => setPickerSel(new Set(filteredPickerList.map(p => p.email)))}
                >
                  Tout cocher
                </button>
                <span className="text-slate-300">|</span>
                <button
                  type="button"
                  className="text-slate-500 hover:underline"
                  onClick={() => setPickerSel(new Set())}
                >
                  Tout decocher
                </button>
              </div>
            </div>
            <div className="border rounded max-h-[400px] overflow-y-auto divide-y divide-slate-100">
              {filteredPickerList.map(p => {
                const checked = pickerSel.has(p.email);
                return (
                  <label
                    key={p.email + p.id}
                    className={`flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-slate-50 ${checked ? 'bg-blue-50/50' : ''}`}
                    data-testid={`picker-row-${p.email}`}
                  >
                    <Checkbox checked={checked} onCheckedChange={() => togglePickerEmail(p.email)} />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-slate-900 truncate">{p.name}</div>
                      <div className="text-xs text-slate-500 truncate">{p.email}</div>
                      {p.lot_number && (
                        <div className="text-[10px] text-slate-400">Lot {p.lot_number}</div>
                      )}
                    </div>
                    {p.vcs_code && (
                      <span className="text-[10px] text-[#022D52] font-mono">{p.vcs_code}</span>
                    )}
                  </label>
                );
              })}
              {filteredPickerList.length === 0 && (
                <div className="p-6 text-center text-sm text-slate-400">
                  Aucun {pickerOpen === 'owners' ? 'proprietaire' : 'locataire'} avec email
                </div>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPickerOpen(null)}>Annuler</Button>
            <Button
              onClick={applyPicker}
              className="bg-[#022D52] hover:bg-[#01213e]"
              data-testid="btn-picker-apply"
            >
              Ajouter ({pickerSel.size})
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ============ Main page ============
export default function CommunicationPage() {
  const { selectedCopro, fiscalYears } = useAuth();
  const [mailboxes, setMailboxes] = useState([]);
  const [owners, setOwners] = useState([]);
  const [loading, setLoading] = useState(false);
  const [totals, setTotals] = useState({ debiteurs: 0, crediteurs: 0 });
  const [selected, setSelected] = useState(new Set());
  const [filter, setFilter] = useState('all'); // all | debtors | creditors | with_email
  const [search, setSearch] = useState('');

  const loadOwners = useCallback(async () => {
    if (!selectedCopro || selectedCopro === 'all') {
      setOwners([]); setTotals({ debiteurs: 0, crediteurs: 0 });
      return;
    }
    setLoading(true);
    try {
      const r = await api.get('/communication/owners-balances');
      setOwners(r.data.owners || []);
      setTotals({ debiteurs: r.data.total_debiteurs || 0, crediteurs: r.data.total_crediteurs || 0 });
    } catch (e) {
      toast.error(extractApiError(e, 'Chargement des soldes echoue'));
    } finally { setLoading(false); }
  }, [selectedCopro]);

  useEffect(() => { loadOwners(); setSelected(new Set()); }, [loadOwners]);

  const filteredOwners = useMemo(() => {
    let list = owners;
    if (filter === 'debtors') list = list.filter(o => o.balance > 0.01);
    else if (filter === 'creditors') list = list.filter(o => o.balance < -0.01);
    else if (filter === 'with_email') list = list.filter(o => o.email);
    if (search.trim()) {
      const s = search.toLowerCase();
      list = list.filter(o =>
        (o.owner_name || '').toLowerCase().includes(s) ||
        (o.email || '').toLowerCase().includes(s)
      );
    }
    return list;
  }, [owners, filter, search]);

  const toggleAll = () => {
    if (selected.size === filteredOwners.length) setSelected(new Set());
    else setSelected(new Set(filteredOwners.filter(o => o.email).map(o => o.owner_id)));
  };
  const toggleOne = (oid) => {
    const next = new Set(selected);
    if (next.has(oid)) next.delete(oid); else next.add(oid);
    setSelected(next);
  };
  const selectedOwners = owners.filter(o => selected.has(o.owner_id));

  if (!selectedCopro || selectedCopro === 'all') {
    return (
      <div className="p-6 max-w-6xl mx-auto">
        <div className="flex items-center gap-2 text-amber-600 text-sm">
          <TriangleAlert className="h-4 w-4" />
          Selectionnez une copropriete pour utiliser le module communication.
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 md:p-6 max-w-7xl mx-auto space-y-6" data-testid="communication-page">
      <header>
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <Mail className="h-6 w-6 text-[#022D52]" />
          Communication proprietaires
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Envoyez situations de compte, decomptes annuels et emails libres aux proprietaires selectionnes.
        </p>
      </header>

      <Tabs defaultValue="send" className="space-y-4">
        <TabsList data-testid="tabs-communication">
          <TabsTrigger value="send" data-testid="tab-send">
            <Send className="h-4 w-4 mr-1" /> Envois
          </TabsTrigger>
          <TabsTrigger value="generic" data-testid="tab-generic">
            <Mail className="h-4 w-4 mr-1" /> Email libre
          </TabsTrigger>
          <TabsTrigger value="settings" data-testid="tab-settings">
            <FileSignature className="h-4 w-4 mr-1" /> Boites & signature
          </TabsTrigger>
        </TabsList>

        <TabsContent value="send" className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Card><CardContent className="p-4">
              <div className="text-xs text-slate-500">Total debiteurs</div>
              <div className="text-lg font-semibold text-red-600" data-testid="stat-total-debiteurs">
                {currency(totals.debiteurs)}
              </div>
            </CardContent></Card>
            <Card><CardContent className="p-4">
              <div className="text-xs text-slate-500">Total crediteurs</div>
              <div className="text-lg font-semibold text-emerald-600" data-testid="stat-total-crediteurs">
                {currency(totals.crediteurs)}
              </div>
            </CardContent></Card>
            <Card><CardContent className="p-4">
              <div className="text-xs text-slate-500">Proprietaires</div>
              <div className="text-lg font-semibold" data-testid="stat-total-owners">{owners.length}</div>
            </CardContent></Card>
            <Card><CardContent className="p-4">
              <div className="text-xs text-slate-500">Selectionnes</div>
              <div className="text-lg font-semibold text-[#022D52]" data-testid="stat-selected">
                {selected.size}
              </div>
            </CardContent></Card>
          </div>

          <Card>
            <CardContent className="p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex items-center gap-1 text-sm">
                  <Search className="h-4 w-4 text-slate-500" />
                  <Input
                    placeholder="Rechercher un proprietaire..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="w-64"
                    data-testid="input-search-owner"
                  />
                </div>
                <div className="flex items-center gap-1">
                  {[
                    { k: 'all', label: 'Tous' },
                    { k: 'debtors', label: 'Debiteurs' },
                    { k: 'creditors', label: 'Crediteurs' },
                    { k: 'with_email', label: 'Avec email' },
                  ].map(f => (
                    <Button
                      key={f.k}
                      variant={filter === f.k ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setFilter(f.k)}
                      data-testid={`filter-${f.k}`}
                    >{f.label}</Button>
                  ))}
                </div>
                <Button variant="ghost" size="sm" onClick={loadOwners}
                        data-testid="btn-refresh-owners">
                  <RefreshCw className="h-4 w-4" />
                </Button>
                <div className="flex-1" />
                <SendActionDialog
                  action="situation"
                  mailboxes={mailboxes}
                  selectedOwners={selectedOwners}
                  copropriete_id={selectedCopro}
                  fiscalYears={fiscalYears}
                  onSent={() => setSelected(new Set())}
                />
                <SendActionDialog
                  action="decompte"
                  mailboxes={mailboxes}
                  selectedOwners={selectedOwners}
                  copropriete_id={selectedCopro}
                  fiscalYears={fiscalYears}
                  onSent={() => setSelected(new Set())}
                />
              </div>

              <div className="border rounded-md overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-600 uppercase">
                    <tr>
                      <th className="px-3 py-2 text-left">
                        <Checkbox
                          checked={filteredOwners.length > 0 && selected.size === filteredOwners.filter(o => o.email).length}
                          onCheckedChange={toggleAll}
                          data-testid="checkbox-select-all"
                        />
                      </th>
                      <th className="px-3 py-2 text-left">Proprietaire</th>
                      <th className="px-3 py-2 text-left">Email</th>
                      <th className="px-3 py-2 text-right">Solde</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      <tr><td colSpan={4} className="p-6 text-center text-slate-500">Chargement...</td></tr>
                    ) : filteredOwners.length === 0 ? (
                      <tr><td colSpan={4} className="p-6 text-center text-slate-500">Aucun proprietaire</td></tr>
                    ) : filteredOwners.map(o => (
                      <tr key={o.owner_id} className={`border-t hover:bg-slate-50 ${o.is_former_owner ? 'opacity-70' : ''}`}
                          data-testid={`row-owner-${o.owner_id}`}>
                        <td className="px-3 py-2">
                          <Checkbox
                            checked={selected.has(o.owner_id)}
                            onCheckedChange={() => toggleOne(o.owner_id)}
                            disabled={!o.email}
                            data-testid={`checkbox-owner-${o.owner_id}`}
                          />
                        </td>
                        <td className="px-3 py-2">
                          <div className="font-medium">{o.owner_name}</div>
                          {o.is_former_owner && (
                            <div className="text-[10px] text-slate-400 uppercase">ancien</div>
                          )}
                        </td>
                        <td className="px-3 py-2">
                          {o.email ? (
                            <span className="font-mono text-xs">{o.email}</span>
                          ) : (
                            <span className="text-xs text-red-500">manquant</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right">
                          <BalanceBadge balance={o.balance} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="generic">
          <GenericComposer mailboxes={mailboxes} copropriete_id={selectedCopro} />
        </TabsContent>

        <TabsContent value="settings" className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <MailboxesSection onChange={setMailboxes} />
            <SignatureSection />
          </div>
        </TabsContent>
      </Tabs>

      {/* Hidden loader to sync mailboxes for other tabs on first mount */}
      {mailboxes.length === 0 && (
        <MailboxesLoader onLoaded={setMailboxes} />
      )}
    </div>
  );
}

function MailboxesLoader({ onLoaded }) {
  useEffect(() => {
    api.get('/communication/mailboxes').then(r => onLoaded(r.data.mailboxes || [])).catch(() => {});
  }, [onLoaded]);
  return null;
}
