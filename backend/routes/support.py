"""Iter90r - Support chatbot pour syndics.

- Chat IA via Claude Sonnet 4.5 (Emergent LLM Key)
- Base de connaissances des fonctionnalites NextGe Copro dans le system prompt
- Historique des conversations en base
- Escalade automatique par email si l'IA marque sa reponse [[NEEDS_ESCALATION]]
- Bouton manuel "Envoyer au support" toujours disponible

Routes :
- GET  /api/support/conversations               -> liste des conversations de l'user
- POST /api/support/conversations               -> creer une nouvelle conversation
- GET  /api/support/conversations/{id}/messages -> historique messages
- POST /api/support/conversations/{id}/chat     -> envoyer message + reponse IA
- POST /api/support/conversations/{id}/escalate -> envoyer conversation au support
- DELETE /api/support/conversations/{id}        -> supprimer une conversation
"""
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)


_SUPPORT_SYSTEM_PROMPT = """Tu es "Assistant NextGe Copro", un assistant support pour les syndics utilisant l'application NextGe Copro (gestion de copropriete belge conforme au PCMN).

Ton role : repondre aux questions fonctionnelles des syndics sur l'application, en francais, de facon claire et concise (max 4 paragraphes courts). Tu ne parles JAMAIS d'autres sujets que NextGe Copro.

FONCTIONNALITES NextGe Copro que tu connais :

1. **Coproprietes (ACP)** — Chaque syndic gere une ou plusieurs Associations de Coproprietaires. Onglet "Coproprietes" pour creer/editer. Chaque copro a son propre PCMN, journal, extraits bancaires.

2. **Lots et proprietaires** — Onglet "Lots" liste les lots (appartements, parkings, caves). Chaque lot a un ou plusieurs proprietaires actuels + historique via les "Mutations". Cle de repartition par lot pour les charges.

3. **PCMN et Natures de depenses** — Configuration > PCMN pour editer le plan comptable belge. Configuration > Natures pour creer des categories de depenses/produits/virements (classes 6, 7, ou 58 Virements internes).

4. **Factures fournisseurs** — Onglet "Factures". Import PDF/CSV possible avec extraction IA. Chaque facture est ventilee sur une nature + cle de repartition. Une facture marquee "frais privatif" (compte 643) est refacturee aux proprietaires concernes via OD-PRIV et N'APPARAIT PAS dans la liste des depenses communes.

5. **Extraits de compte** — Onglet "Banque". Import CODA, PDF ou CSV (IA extrait automatiquement les transactions). Chaque transaction se lettre a une facture / proprietaire / fournisseur, OU se categorise avec une nature (avec split multi-natures possible), OU se lie a un compte PCMN direct (utile pour compte 58 Virements internes). Extraits en Brouillon puis Comptabilise.

6. **Appels de fonds** — Onglet "Appels de fonds". Generation des appels par periode + cle de repartition. Envoi par email aux proprietaires. Lettrage automatique via VCS (Virement Communication Structuree).

7. **Rapports fiscaux** — Onglet "Fiscal". Liste des depenses de l'exercice, Grand livre, Journaux ACH/OD/FI/VE, Balance, Bilan, Compte de resultat, PDF Decompte de mutation.

8. **Portail proprietaire** — Chaque proprietaire peut avoir un acces au portail self-service (invitation email + reset password). Il voit ses appels de fonds, son solde, ses documents.

9. **Roles** — Superadmin (voit tout), Admin_syndic (gere une organisation syndic), Syndic (gere une ou N copros), Accountant (lecture + comptabilisation), Owner (portail proprietaire).

10. **Cloisonnement (Chinese wall)** — Chaque utilisateur (hors superadmin/admin) ne voit QUE les coproprietes auxquelles il est rattache.

REGLES DE REPONSE :
- Si la question porte sur une fonctionnalite existante ci-dessus, reponds precisement en indiquant les etapes (ex: "Allez dans Onglet X > bouton Y").
- Si la question demande un diagnostic technique (bug, erreur, comportement anormal), une facturation, un contrat, un remboursement, ou tout ce qui necessite intervention humaine, termine ta reponse par la balise EXACTE `[[NEEDS_ESCALATION]]` sur sa propre ligne, precede d'une phrase du type "Je transmets votre demande au service support qui vous repondra rapidement."
- Si la question est hors-sujet (autre app, question personnelle), reponds poliment "Je ne peux repondre qu'aux questions sur NextGe Copro. Pour toute autre demande, contactez le support directement." SANS ajouter la balise.
- Ne mens JAMAIS. Si tu ne connais pas la reponse precise a une question sur NextGe Copro, escalade avec `[[NEEDS_ESCALATION]]`.
"""


class NewConversationInput(BaseModel):
    title: Optional[str] = ""


class ChatMessageInput(BaseModel):
    message: str


class EscalateInput(BaseModel):
    reason: Optional[str] = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_user(request: Request):
    user_id = getattr(request.state, "user_id", "")
    if not user_id:
        raise HTTPException(401, "Authentification requise")
    return user_id


async def _load_conversation(db, conv_id: str, user_id: str) -> dict:
    conv = await db.support_conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation introuvable")
    if conv.get("user_id") != user_id:
        raise HTTPException(403, "Acces refuse a cette conversation")
    return conv


async def _send_support_escalation_email(
    *, support_email: str, requester_email: str, requester_name: str,
    conv_title: str, messages: List[dict], reason: str = "",
) -> None:
    """Envoie l'historique complet au service support avec Reply-To = requester."""
    from graph_email import send_html_email

    def _fmt_msg(m):
        role = m.get("role", "user")
        bg = "#EEF6FF" if role == "user" else "#F4F4F5"
        label = "Syndic" if role == "user" else "Assistant IA"
        content = (m.get("content") or "").replace("\n", "<br/>")
        return (
            f'<div style="background:{bg};padding:10px 12px;border-radius:8px;margin:6px 0;">'
            f'<div style="font-size:11px;color:#666;margin-bottom:4px;">{label}</div>'
            f'<div style="font-size:13px;color:#111;">{content}</div>'
            '</div>'
        )

    body = f"""
<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;">
  <h2 style="color:#0055FF;margin-bottom:4px;">Nouvelle demande support NextGe Copro</h2>
  <p style="color:#555;margin-top:0;font-size:13px;">
    Un syndic a besoin d'assistance humaine. L'IA n'a pas pu resoudre la question.
  </p>
  <div style="background:#FAFAFA;border:1px solid #EEE;border-radius:8px;padding:12px;margin:12px 0;">
    <b>Demandeur :</b> {requester_name} &lt;{requester_email}&gt;<br/>
    <b>Conversation :</b> {conv_title or 'Sans titre'}<br/>
    <b>Date :</b> {_now()}<br/>
    {'<b>Raison :</b> ' + reason + '<br/>' if reason else ''}
  </div>
  <h3 style="color:#333;margin-top:20px;">Historique complet</h3>
  {''.join(_fmt_msg(m) for m in messages)}
  <hr style="margin:24px 0;border:none;border-top:1px solid #EEE;"/>
  <p style="color:#888;font-size:11px;">
    Repondez directement a cet email : votre reponse partira automatiquement a {requester_email}.
  </p>
</div>
""".strip()

    await send_html_email(
        recipients=[support_email],
        subject=f"[NextGe Copro Support] {conv_title or 'Nouvelle demande'} — {requester_name}",
        html_body=body,
        reply_to=requester_email,
    )


def create_support_router(db):
    router = APIRouter(prefix="/api/support", tags=["support"])

    @router.get("/conversations")
    async def list_conversations(request: Request):
        user_id = await _get_user(request)
        convs = await db.support_conversations.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("updated_at", -1).to_list(200)
        return convs

    @router.post("/conversations")
    async def create_conversation(data: NewConversationInput, request: Request):
        user_id = await _get_user(request)
        conv = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "user_email": getattr(request.state, "user_email", ""),
            "user_name": getattr(request.state, "user_name", ""),
            "title": (data.title or "Nouvelle question").strip()[:120],
            "messages_count": 0,
            "escalated": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.support_conversations.insert_one(conv)
        conv.pop("_id", None)
        return conv

    @router.get("/conversations/{conv_id}/messages")
    async def get_messages(conv_id: str, request: Request):
        user_id = await _get_user(request)
        await _load_conversation(db, conv_id, user_id)
        msgs = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        return msgs

    @router.post("/conversations/{conv_id}/chat")
    async def chat(conv_id: str, data: ChatMessageInput, request: Request,
                   background: BackgroundTasks):
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        user_id = await _get_user(request)
        conv = await _load_conversation(db, conv_id, user_id)
        user_msg = (data.message or "").strip()
        if not user_msg:
            raise HTTPException(400, "Message vide")
        if len(user_msg) > 2000:
            raise HTTPException(400, "Message trop long (max 2000 caracteres)")

        # Store user message
        um = {
            "id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "role": "user",
            "content": user_msg,
            "created_at": _now(),
        }
        await db.support_messages.insert_one(um)

        # Fetch conversation history (last 20 messages) for context
        history = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(50)

        api_key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not api_key:
            raise HTTPException(500, "EMERGENT_LLM_KEY absent - IA indisponible")

        chat_obj = LlmChat(
            api_key=api_key,
            session_id=f"support-{conv_id}",
            system_message=_SUPPORT_SYSTEM_PROMPT,
        ).with_model("anthropic", "claude-sonnet-4-5-20250929")

        # Build the prompt with prior context (simple : concatenation of past turns)
        past_context = ""
        for m in history[:-1]:  # exclude the just-inserted user msg
            role_label = "Syndic" if m["role"] == "user" else "Assistant"
            past_context += f"\n{role_label} : {m['content']}\n"
        full_prompt = user_msg
        if past_context.strip():
            full_prompt = (
                "Historique de conversation :"
                f"{past_context}\n\n"
                f"Nouvelle question du syndic : {user_msg}"
            )
        try:
            ai_resp = await chat_obj.send_message(UserMessage(text=full_prompt))
        except Exception as e:
            logger.exception("Support LLM call failed")
            raise HTTPException(502, f"IA indisponible : {str(e)[:150]}")

        ai_text = (ai_resp or "").strip()
        needs_escalation = "[[NEEDS_ESCALATION]]" in ai_text
        # Clean the marker from the visible answer
        clean_text = ai_text.replace("[[NEEDS_ESCALATION]]", "").strip()

        am = {
            "id": str(uuid.uuid4()),
            "conversation_id": conv_id,
            "role": "assistant",
            "content": clean_text,
            "needs_escalation": needs_escalation,
            "created_at": _now(),
        }
        await db.support_messages.insert_one(am)
        am.pop("_id", None)

        # Update conversation meta
        title_update = conv.get("title") or ""
        if conv.get("messages_count", 0) == 0 or title_update == "Nouvelle question":
            title_update = user_msg[:80]
        await db.support_conversations.update_one(
            {"id": conv_id},
            {"$set": {
                "title": title_update,
                "updated_at": _now(),
                "last_message_preview": clean_text[:200],
            }, "$inc": {"messages_count": 2}}
        )

        # Auto-escalation if needed and not already escalated
        auto_escalated = False
        if needs_escalation and not conv.get("escalated"):
            support_email = os.environ.get("SUPPORT_EMAIL", "").strip()
            if support_email:
                # Refresh full history including new answer
                full_history = await db.support_messages.find(
                    {"conversation_id": conv_id}, {"_id": 0},
                ).sort("created_at", 1).to_list(500)
                requester_email = conv.get("user_email") or getattr(request.state, "user_email", "") or ""
                requester_name = conv.get("user_name") or getattr(request.state, "user_name", "") or "Syndic"
                background.add_task(
                    _send_support_escalation_email,
                    support_email=support_email,
                    requester_email=requester_email,
                    requester_name=requester_name,
                    conv_title=title_update,
                    messages=full_history,
                    reason="Escalade automatique (IA n'a pas pu resoudre)",
                )
                await db.support_conversations.update_one(
                    {"id": conv_id},
                    {"$set": {"escalated": True,
                              "escalated_at": _now(),
                              "escalated_kind": "auto"}}
                )
                auto_escalated = True

        return {
            "assistant_message": am,
            "needs_escalation": needs_escalation,
            "auto_escalated": auto_escalated,
        }

    @router.post("/conversations/{conv_id}/escalate")
    async def escalate(conv_id: str, data: EscalateInput, request: Request,
                       background: BackgroundTasks):
        user_id = await _get_user(request)
        conv = await _load_conversation(db, conv_id, user_id)
        support_email = os.environ.get("SUPPORT_EMAIL", "").strip()
        if not support_email:
            raise HTTPException(500, "SUPPORT_EMAIL non configure - contactez l'administrateur")
        history = await db.support_messages.find(
            {"conversation_id": conv_id}, {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        if not history:
            raise HTTPException(400, "Aucun message dans la conversation")
        requester_email = conv.get("user_email") or getattr(request.state, "user_email", "") or ""
        requester_name = conv.get("user_name") or getattr(request.state, "user_name", "") or "Syndic"
        background.add_task(
            _send_support_escalation_email,
            support_email=support_email,
            requester_email=requester_email,
            requester_name=requester_name,
            conv_title=conv.get("title", ""),
            messages=history,
            reason=(data.reason or "Escalade manuelle par le syndic"),
        )
        await db.support_conversations.update_one(
            {"id": conv_id},
            {"$set": {"escalated": True, "escalated_at": _now(),
                      "escalated_kind": "manual", "updated_at": _now()}}
        )
        return {"message": "Votre demande a ete transmise au support. Vous recevrez une reponse par email."}

    @router.delete("/conversations/{conv_id}")
    async def delete_conversation(conv_id: str, request: Request):
        user_id = await _get_user(request)
        await _load_conversation(db, conv_id, user_id)
        await db.support_messages.delete_many({"conversation_id": conv_id})
        await db.support_conversations.delete_one({"id": conv_id})
        return {"message": "Conversation supprimee"}

    return router
