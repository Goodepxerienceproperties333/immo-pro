import { useEffect, useState, useCallback } from 'react';
import api from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import {
  KeyRound, MailCheck, Ban, RotateCcw, Loader2,
  AlertCircle, CheckCircle2, Clock, ShieldOff, Trash2,
  History, ChevronDown, ChevronUp,
} from 'lucide-react';
import { fmtDate } from '@/lib/dateFmt';

const ACTION_LABELS = {
  grant: { label: 'Acces active', cls: 'text-emerald-700 bg-emerald-50', Icon: KeyRound },
  resend: { label: 'Invitation renvoyee', cls: 'text-blue-700 bg-blue-50', Icon: MailCheck },
  revoke: { label: 'Acces suspendu', cls: 'text-red-700 bg-red-50', Icon: Ban },
  reactivate: { label: 'Acces reactive', cls: 'text-emerald-700 bg-emerald-50', Icon: RotateCcw },
  delete: { label: 'Acces supprime', cls: 'text-red-800 bg-red-100 font-bold', Icon: Trash2 },
};

function fmtDateTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('fr-BE', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return iso;
  }
}

/**
 * OwnerAccessSection — Manages platform access for an owner.
 *
 * States (from backend `/api/owners/{id}/access-status`):
 *   - none      : no user linked → show "Activer l'acces"
 *   - pending   : user created, must_change_password=true → show "Renvoyer" + "Desactiver"
 *   - active    : user exists, password set, not suspended → show "Desactiver"
 *   - suspended : user exists but is_suspended=true → show "Reactiver"
 */
export default function OwnerAccessSection({ ownerId, ownerEmail }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(null); // 'grant' | 'resend' | 'revoke' | 'reactivate'
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditEntries, setAuditEntries] = useState(null);
  const [auditLoading, setAuditLoading] = useState(false);

  const load = useCallback(async () => {
    if (!ownerId) return;
    setLoading(true);
    try {
      const { data } = await api.get(`/owners/${ownerId}/access-status`);
      setStatus(data);
    } catch (e) {
      // 404 means the owner is not in our scope -> hide the section gracefully
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [ownerId]);

  useEffect(() => { load(); }, [load]);

  const loadAudit = useCallback(async () => {
    if (!ownerId) return;
    setAuditLoading(true);
    try {
      const { data } = await api.get(`/owners/${ownerId}/access-audit`, { params: { limit: 50 } });
      setAuditEntries(data.entries || []);
    } catch (e) {
      setAuditEntries([]);
      toast.error(e.response?.data?.detail || 'Impossible de charger l\'historique');
    } finally {
      setAuditLoading(false);
    }
  }, [ownerId]);

  const toggleAudit = async () => {
    const next = !auditOpen;
    setAuditOpen(next);
    if (next && auditEntries === null) {
      await loadAudit();
    }
  };

  const callAction = async (path, label, busyKey) => {
    setBusy(busyKey);
    try {
      const { data } = await api.post(`/owners/${ownerId}/${path}`);
      toast.success(data.message || label, {
        description: data.invitation_sent === false
          ? "Le mail n'a pas pu etre envoye automatiquement (MSGRAPH non configure). Le proprietaire pourra utiliser 'mot de passe oublie' lors de sa premiere visite."
          : undefined,
        duration: 6000,
      });
      if (data.status) setStatus(data.status);
      else load();
      // Force-refresh audit list next time it's opened
      setAuditEntries(null);
      if (auditOpen) loadAudit();
    } catch (e) {
      toast.error(e.response?.data?.detail || `Echec : ${label}`);
    } finally {
      setBusy(null);
    }
  };

  // iter90g : suppression complete (DELETE) de l'acces. Idempotent cote
  // backend mais demande une confirmation visuelle.
  const deleteAccess = async () => {
    if (!window.confirm(
      'Supprimer DEFINITIVEMENT le compte d\'acces du proprietaire ?\n\n' +
      '- Son compte sera detruit\n' +
      '- Toutes ses sessions seront invalidees\n' +
      '- L\'historique d\'audit est conserve\n\n' +
      'Vous pourrez recreer l\'acces plus tard depuis "Activer l\'acces".'
    )) return;
    setBusy('delete');
    try {
      const { data } = await api.delete(`/owners/${ownerId}/access`);
      toast.success(data.message || 'Acces supprime', { duration: 6000 });
      if (data.status) setStatus(data.status);
      else load();
      setAuditEntries(null);
      if (auditOpen) loadAudit();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Echec de la suppression');
    } finally {
      setBusy(null);
    }
  };

  if (loading && !status) {
    return (
      <div className="border-t border-slate-200 pt-4 mt-4 text-xs text-slate-500 flex items-center gap-2" data-testid="owner-access-loading">
        <Loader2 size={14} className="animate-spin" /> Verification de l&apos;acces plateforme...
      </div>
    );
  }
  if (!status) return null;

  const { status: s, user_email, last_login_at } = status;

  // Status badge
  let badge;
  let icon;
  if (s === 'none') {
    badge = <Badge variant="outline" className="bg-slate-50 text-slate-700">Pas d&apos;acces</Badge>;
    icon = <ShieldOff size={18} className="text-slate-400" />;
  } else if (s === 'pending') {
    badge = <Badge variant="outline" className="bg-amber-50 text-amber-800 border-amber-300">Invitation en attente</Badge>;
    icon = <Clock size={18} className="text-amber-500" />;
  } else if (s === 'active') {
    badge = <Badge variant="outline" className="bg-emerald-50 text-emerald-800 border-emerald-300">Compte actif</Badge>;
    icon = <CheckCircle2 size={18} className="text-emerald-500" />;
  } else if (s === 'suspended') {
    badge = <Badge variant="outline" className="bg-red-50 text-red-700 border-red-300">Acces suspendu</Badge>;
    icon = <Ban size={18} className="text-red-500" />;
  }

  return (
    <div className="border border-slate-200 rounded-md p-4 bg-slate-50/50" data-testid="owner-access-section">
      <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
        <div className="flex items-center gap-2">
          {icon}
          <div>
            <div className="font-semibold text-sm text-slate-900 flex items-center gap-2">
              Acces a la plateforme {badge}
            </div>
            <p className="text-[11px] text-slate-500 mt-0.5">
              {s === 'none' && "Active l'acces et envoie une invitation par email au proprietaire."}
              {s === 'pending' && `Invitation envoyee a ${user_email}. En attente de la definition du mot de passe.`}
              {s === 'active' && `Connecte avec ${user_email}.${last_login_at ? ` Derniere connexion : ${fmtDate(last_login_at)}` : ''}`}
              {s === 'suspended' && `Compte ${user_email} actuellement suspendu. Le proprietaire ne peut plus se connecter.`}
            </p>
          </div>
        </div>
      </div>

      {/* Email mismatch warning */}
      {ownerEmail && user_email && ownerEmail.toLowerCase() !== user_email.toLowerCase() && (
        <div className="bg-blue-50 border border-blue-200 rounded-md p-2 mb-3 text-[11px] text-blue-800 flex items-start gap-2">
          <AlertCircle size={12} className="flex-shrink-0 mt-0.5" />
          <span>L&apos;email de connexion (<b>{user_email}</b>) est different de l&apos;email de la fiche (<b>{ownerEmail}</b>).</span>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {s === 'none' && (
          <Button
            size="sm"
            onClick={() => callAction('grant-access', 'Acces active', 'grant')}
            disabled={busy === 'grant' || !ownerEmail}
            className="bg-[#0055FF] hover:bg-[#0040CC] text-white"
            data-testid="owner-grant-access-btn"
          >
            {busy === 'grant' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <KeyRound size={14} className="mr-1" />}
            Activer l&apos;acces et envoyer l&apos;invitation
          </Button>
        )}
        {s === 'pending' && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => callAction('resend-invitation', 'Invitation renvoyee', 'resend')}
              disabled={busy === 'resend'}
              data-testid="owner-resend-invitation-btn"
            >
              {busy === 'resend' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <MailCheck size={14} className="mr-1" />}
              Renvoyer l&apos;invitation
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => callAction('revoke-access', 'Acces suspendu', 'revoke')}
              disabled={busy === 'revoke'}
              className="text-red-600 hover:bg-red-50 border-red-200"
              data-testid="owner-revoke-access-btn"
            >
              {busy === 'revoke' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <Ban size={14} className="mr-1" />}
              Desactiver
            </Button>
          </>
        )}
        {s === 'active' && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              if (!window.confirm('Suspendre l\'acces du proprietaire ? Il ne pourra plus se connecter tant que vous ne reactivez pas son compte.')) return;
              callAction('revoke-access', 'Acces suspendu', 'revoke');
            }}
            disabled={busy === 'revoke'}
            className="text-red-600 hover:bg-red-50 border-red-200"
            data-testid="owner-revoke-access-btn"
          >
            {busy === 'revoke' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <Ban size={14} className="mr-1" />}
            Desactiver l&apos;acces
          </Button>
        )}
        {s === 'suspended' && (
          <Button
            size="sm"
            onClick={() => callAction('reactivate-access', 'Acces reactive', 'reactivate')}
            disabled={busy === 'reactivate'}
            className="bg-emerald-600 hover:bg-emerald-700 text-white"
            data-testid="owner-reactivate-access-btn"
          >
            {busy === 'reactivate' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <RotateCcw size={14} className="mr-1" />}
            Reactiver l&apos;acces
          </Button>
        )}
        {/* iter90g : bouton "Supprimer l'acces" (destructif) - dispo des qu'un compte est lie */}
        {s !== 'none' && (
          <Button
            size="sm"
            variant="outline"
            onClick={deleteAccess}
            disabled={busy === 'delete'}
            className="text-red-700 hover:bg-red-100 border-red-300"
            data-testid="owner-delete-access-btn"
            title="Supprimer definitivement le compte d'acces du proprietaire"
          >
            {busy === 'delete' ? <Loader2 size={14} className="mr-1 animate-spin" /> : <Trash2 size={14} className="mr-1" />}
            Supprimer l&apos;acces
          </Button>
        )}
      </div>

      {s === 'none' && !ownerEmail && (
        <p className="text-[11px] text-amber-700 mt-2 flex items-center gap-1">
          <AlertCircle size={12} /> Saisissez d&apos;abord une adresse email valide pour le proprietaire.
        </p>
      )}

      {/* ---- Audit journal (collapsible) ---- */}
      <div className="mt-4 pt-3 border-t border-slate-200">
        <button
          type="button"
          onClick={toggleAudit}
          className="text-xs text-slate-600 hover:text-slate-900 flex items-center gap-1.5"
          data-testid="owner-access-audit-toggle"
        >
          <History size={13} />
          <span>Historique des actions d&apos;acces</span>
          {auditOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
        {auditOpen && (
          <div className="mt-3" data-testid="owner-access-audit-list">
            {auditLoading ? (
              <div className="text-xs text-slate-500 flex items-center gap-2 py-2">
                <Loader2 size={12} className="animate-spin" /> Chargement de l&apos;historique...
              </div>
            ) : !auditEntries || auditEntries.length === 0 ? (
              <p className="text-xs text-slate-400 italic py-2">
                Aucune action enregistree pour ce proprietaire.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {auditEntries.map((e) => {
                  const cfg = ACTION_LABELS[e.action] || { label: e.action, cls: 'text-slate-700 bg-slate-100', Icon: History };
                  const ActIcon = cfg.Icon;
                  return (
                    <li
                      key={e.id}
                      className="flex items-start gap-2 text-[11px] bg-white border border-slate-200 rounded-md px-2 py-1.5"
                      data-testid={`owner-access-audit-entry-${e.id}`}
                    >
                      <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${cfg.cls} flex-shrink-0`}>
                        <ActIcon size={10} /> {cfg.label}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="text-slate-700">
                          <b>{e.actor_name || e.actor_email || 'syndic'}</b>
                          <span className="text-slate-400 ml-1.5">({e.actor_role})</span>
                          {e.target_user_email && (
                            <>
                              <span className="text-slate-400"> &rarr; </span>
                              <span className="font-mono text-[10px]">{e.target_user_email}</span>
                            </>
                          )}
                        </div>
                        <div className="text-slate-400 text-[10px] mt-0.5">
                          {fmtDateTime(e.created_at)}
                          {e.ip && <span className="ml-2">IP : <span className="font-mono">{e.ip}</span></span>}
                          {e.details?.invitation_sent === false && (
                            <span className="ml-2 text-amber-600">(email non envoye)</span>
                          )}
                          {e.details?.linked_existing_user === true && (
                            <span className="ml-2 text-blue-600">(compte existant lie)</span>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
