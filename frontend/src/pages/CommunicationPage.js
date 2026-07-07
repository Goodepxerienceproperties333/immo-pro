import { useState, useEffect, useCallback, useMemo } from 'react';
import api, { extractApiError } from '@/lib/api';
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
  ArrowDownRight, ArrowUpRight, RefreshCw, Search,
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
          <Inbox className="h-4 w-4 text-blue-600" />
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
          <div className="text-sm" dangerouslySetInnerHTML={{ __html: html || '<em>Signature vide</em>' }} />
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
        ...(action === 'situation' ? { start_date, end_date } : {}),
        ...(action === 'decompte' ? { fiscal_year_id } : {}),
      };
      const r = await api.post(`/communication/send/${action}`, payload);
      const dryRun = r.data.dry_run || r.data.dry_run === undefined ? '' : '';
      const info = r.data.sent > 0
        ? `${r.data.sent} email(s) envoye(s)${r.data.dry_run ? ' (mode dry-run)' : ''}`
        : 'Aucun email envoye';
      if (r.data.failed?.length) {
        toast.warning(`${info}. ${r.data.failed.length} echec(s).`);
      } else {
        toast.success(info);
      }
      setOpen(false);
      onSent?.(r.data);
    } catch (e) {
      toast.error(extractApiError(e, 'Envoi echoue'));
    } finally { setSending(false); }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          className="bg-blue-600 hover:bg-blue-700"
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
                  <SelectItem key={b.address} value={b.address}>{b.address}</SelectItem>
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
          <Button onClick={send} disabled={sending} className="bg-blue-600 hover:bg-blue-700"
                  data-testid={`btn-confirm-send-${action}`}>
            <Send className="h-4 w-4 mr-1" /> Envoyer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============ Generic email composer ============
function GenericComposer({ mailboxes }) {
  const [from_mailbox, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body_html, setBody] = useState('');
  const [include_signature, setIncSig] = useState(true);
  const [file, setFile] = useState(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (mailboxes.length && !from_mailbox) {
      const def = mailboxes.find(b => b.default) || mailboxes[0];
      setFrom(def.address);
    }
  }, [mailboxes, from_mailbox]);

  const send = async () => {
    const emails = to.split(/[,;\s]+/).map(s => s.trim()).filter(s => s.includes('@'));
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
      if (file) fd.append('attachment', file);
      const r = await api.post('/communication/send/generic', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(`Email envoye${r.data.dry_run ? ' (mode dry-run)' : ''}`);
      setTo(''); setSubject(''); setBody(''); setFile(null);
    } catch (e) { toast.error(extractApiError(e)); }
    finally { setSending(false); }
  };

  return (
    <Card data-testid="generic-composer">
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Mail className="h-4 w-4 text-blue-600" />
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
            <Label className="text-xs">Destinataires (separes par virgule)</Label>
            <Input value={to} onChange={(e) => setTo(e.target.value)}
                   placeholder="user1@ex.com, user2@ex.com"
                   data-testid="input-generic-to" />
          </div>
        </div>
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
        <Button onClick={send} disabled={sending} className="bg-blue-600 hover:bg-blue-700"
                data-testid="btn-send-generic">
          <Send className="h-4 w-4 mr-1" /> Envoyer
        </Button>
      </CardContent>
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
          <Mail className="h-6 w-6 text-blue-600" />
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
              <div className="text-lg font-semibold text-blue-600" data-testid="stat-selected">
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
          <GenericComposer mailboxes={mailboxes} />
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
