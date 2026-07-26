import { useState, useRef } from 'react';
import { X, Send, Upload, Bug, Paperclip, Loader2, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import api, { extractApiError } from '../lib/api';

/**
 * Iter90fs - Formulaire de remontee de bug integre au chatbot support.
 * Permet au syndic de :
 *  - decrire le probleme (titre + description obligatoires)
 *  - fournir etapes / attendu / observe
 *  - joindre jusqu'a 5 fichiers (max 10 MB chacun, tout type)
 *  - specifier l'email de contact (pre-rempli)
 * A la soumission -> POST /api/tickets (multipart) puis affiche le numero.
 */

const MAX_FILES = 5;
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB

export default function BugReportForm({ userEmail = '', onCreated, onCancel, linkedConversationId = '' }) {
  const [form, setForm] = useState({
    title: '',
    description: '',
    steps_to_reproduce: '',
    expected_behavior: '',
    observed_behavior: '',
    requester_email: userEmail,
  });
  const [files, setFiles] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const fileInputRef = useRef(null);

  const addFiles = (list) => {
    const arr = Array.from(list || []);
    const merged = [...files];
    for (const f of arr) {
      if (merged.length >= MAX_FILES) {
        toast.error(`Maximum ${MAX_FILES} pieces jointes`);
        break;
      }
      if (f.size > MAX_FILE_SIZE) {
        toast.error(`Fichier "${f.name}" trop gros (max 10 MB)`);
        continue;
      }
      if (merged.some(m => m.name === f.name && m.size === f.size)) continue;
      merged.push(f);
    }
    setFiles(merged);
  };

  const removeFile = (idx) => setFiles(fs => fs.filter((_, i) => i !== idx));

  const submit = async () => {
    if (form.title.trim().length < 5) {
      toast.error('Titre trop court (min 5 caracteres)');
      return;
    }
    if (form.description.trim().length < 10) {
      toast.error('Description trop courte (min 10 caracteres)');
      return;
    }
    if (!form.requester_email.trim()) {
      toast.error('Email de contact requis');
      return;
    }
    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('title', form.title.trim());
      fd.append('description', form.description.trim());
      fd.append('steps_to_reproduce', form.steps_to_reproduce.trim());
      fd.append('expected_behavior', form.expected_behavior.trim());
      fd.append('observed_behavior', form.observed_behavior.trim());
      fd.append('requester_email', form.requester_email.trim());
      if (linkedConversationId) fd.append('linked_conversation_id', linkedConversationId);
      files.forEach(f => fd.append('files', f, f.name));
      const { data } = await api.post('/tickets', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(`Ticket ${data.number} cree`, {
        description: 'Le support a ete notifie et vous suivrez l\'evolution ici.',
        duration: 6000,
      });
      onCreated && onCreated(data);
    } catch (err) {
      console.error('Bug report submit failed:', err, err?.response);
      const detail = extractApiError(err, 'Erreur creation ticket');
      toast.error(detail, { duration: 6000 });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col h-full" data-testid="bug-report-form">
      <div className="px-4 py-3 border-b border-slate-100 bg-amber-50/60">
        <div className="flex items-center gap-2 text-amber-800">
          <Bug size={16} />
          <span className="text-sm font-semibold">Remontee de bug</span>
        </div>
        <p className="text-[11px] text-amber-700 mt-1 leading-relaxed">
          Soyez le plus precis possible. Le support recevra un email et vous pourrez suivre l&apos;evolution dans l&apos;onglet &quot;Mes tickets&quot;.
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <div>
          <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Titre *</label>
          <Input
            value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="Resume court du probleme"
            maxLength={200}
            className="text-xs h-9"
            data-testid="bug-title-input"
          />
        </div>
        <div>
          <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Description detaillee *</label>
          <Textarea
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Que s'est-il passe ? Sur quel ecran ? Avec quelles donnees ?"
            rows={4}
            className="text-xs"
            data-testid="bug-description-input"
          />
        </div>
        <div>
          <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Etapes pour reproduire</label>
          <Textarea
            value={form.steps_to_reproduce}
            onChange={e => setForm(f => ({ ...f, steps_to_reproduce: e.target.value }))}
            placeholder={'1. Aller dans...\n2. Cliquer sur...\n3. Observer...'}
            rows={3}
            className="text-xs"
            data-testid="bug-steps-input"
          />
        </div>
        <div className="grid grid-cols-1 gap-3">
          <div>
            <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Comportement attendu</label>
            <Textarea
              value={form.expected_behavior}
              onChange={e => setForm(f => ({ ...f, expected_behavior: e.target.value }))}
              placeholder="Ce que le systeme aurait du faire"
              rows={2}
              className="text-xs"
              data-testid="bug-expected-input"
            />
          </div>
          <div>
            <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Comportement observe</label>
            <Textarea
              value={form.observed_behavior}
              onChange={e => setForm(f => ({ ...f, observed_behavior: e.target.value }))}
              placeholder="Ce que le systeme a reellement fait (message d'erreur exact si possible)"
              rows={2}
              className="text-xs"
              data-testid="bug-observed-input"
            />
          </div>
        </div>
        <div>
          <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">Email de contact *</label>
          <Input
            type="email"
            value={form.requester_email}
            onChange={e => setForm(f => ({ ...f, requester_email: e.target.value }))}
            placeholder="Vous recevrez les mises a jour du ticket ici"
            className="text-xs h-9"
            data-testid="bug-email-input"
          />
        </div>

        {/* Attachments */}
        <div>
          <label className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold block mb-1">
            Pieces jointes ({files.length}/{MAX_FILES}) — max 10 MB chacune
          </label>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={e => { addFiles(e.target.files); e.target.value = ''; }}
            data-testid="bug-file-input"
          />
          <div className="flex flex-wrap gap-2 items-center">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={files.length >= MAX_FILES}
              className="h-8 text-xs"
              data-testid="bug-add-file-btn"
            >
              <Upload size={12} className="mr-1" /> Ajouter un fichier
            </Button>
            {files.map((f, i) => (
              <div key={i} className="flex items-center gap-1 text-[11px] bg-slate-100 rounded px-2 py-1 border border-slate-200" data-testid={`bug-file-item-${i}`}>
                <Paperclip size={10} className="text-slate-500" />
                <span className="max-w-[120px] truncate" title={f.name}>{f.name}</span>
                <span className="text-slate-400">({Math.round(f.size / 1024)} KB)</span>
                <button
                  onClick={() => removeFile(i)}
                  className="text-slate-400 hover:text-red-500"
                  title="Retirer"
                  data-testid={`bug-file-remove-${i}`}
                >
                  <X size={11} />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded bg-slate-50 border border-slate-200 p-2 flex items-start gap-2">
          <Info size={12} className="text-slate-400 mt-0.5 shrink-0" />
          <p className="text-[10px] text-slate-500 leading-relaxed">
            Votre ticket sera envoye au support NextGe Copro. Statuts : Ouvert &rarr; Affecte &rarr; En cours &rarr; Testing &rarr; Deploiement &rarr; Cloture. Vous serez notifie par email a chaque changement de statut.
          </p>
        </div>
      </div>

      <div className="p-3 border-t border-slate-100 bg-white flex gap-2 justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={submitting}
          className="h-9 text-xs"
          data-testid="bug-cancel-btn"
        >
          Annuler
        </Button>
        <Button
          onClick={submit}
          disabled={submitting}
          className="bg-amber-600 hover:bg-amber-700 text-white h-9 text-xs"
          data-testid="bug-submit-btn"
        >
          {submitting ? <><Loader2 size={12} className="animate-spin mr-1" /> Envoi...</> : <><Send size={12} className="mr-1" /> Envoyer le ticket</>}
        </Button>
      </div>
    </div>
  );
}
