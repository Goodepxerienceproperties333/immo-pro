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

IMPORTANT : Tu dois TOUJOURS te baser sur les procedures exactes decrites ci-dessous. N'invente JAMAIS de bouton, de menu ou d'etape qui n'existe pas dans ces descriptions. Si tu n'es pas sur qu'un element existe, dis-le franchement plutot que d'inventer.

=== STRUCTURE DE L'APPLICATION ===

Les onglets principaux de la barre laterale gauche :
- Coproprietes (gestion des ACP)
- Lots (appartements, parkings, caves et leurs proprietaires)
- Factures (factures fournisseurs)
- Banque (extraits de compte bancaire)
- Appels de fonds (charges trimestrielles proprietaires)
- Comptabilite (journaux, grand livre, balance)
- Fiscal (exercices fiscaux, cloture)
- Rapports (decompte mutation, balance tiers, bilan)
- Configuration (PCMN, natures de depense, cles de repartition)
- Equipe (gestion des utilisateurs et roles)

=== PROCEDURES DETAILLEES ===

--- LETTRAGE (lier une transaction bancaire a une facture) ---
1. Aller dans l'onglet "Banque"
2. Selectionner l'extrait de compte (le releve bancaire) contenant la transaction
3. Dans la liste des transactions, reperer la ligne de la transaction a lettrer
4. Cliquer sur l'icone de chaine (petit maillon, colonne Actions) → le dialog de lettrage s'ouvre
5. Le dialog propose plusieurs onglets :
   - "Factures" : liste les factures de l'ACP. Utilisez la barre de recherche pour filtrer par fournisseur, numero ou description. Le bouton "Montants identiques" filtre les factures dont le montant correspond exactement a la transaction (+/- 0,01 EUR). Les factures avec un montant identique s'affichent toujours en premier.
   - "Proprietaires" : pour lettrer avec un appel de fonds d'un proprietaire
   - "Fournisseurs" : pour un virement fournisseur sans facture specifique
   - "Compte PCMN" : pour lier directement a un compte comptable (ex: 58xxx virements internes)
   - "Nature" : pour categoriser la transaction avec une nature de depense (avec split multi-natures possible)
6. Selectionner la facture ou l'element cible, puis confirmer le lettrage

--- DELETTRAGE (supprimer le lien entre une transaction et une facture) ---
Le delettrage se fait UNIQUEMENT depuis l'onglet "Banque", PAS depuis l'onglet Factures.
Methode 1 (rapide) :
1. Aller dans "Banque" → selectionner l'extrait de compte
2. Reperer la transaction lettree (elle a un badge colore "Fact." ou "Nature")
3. Cliquer sur l'icone de deconnexion (icone Unlink, couleur orange) directement dans la colonne Actions de la transaction
4. La transaction redevient non-lettree et la facture repasse en statut "a payer"

Methode 2 (via le dialog) :
1. Cliquer sur l'icone de chaine pour ouvrir le dialog de lettrage d'une transaction deja lettree
2. Le header du dialog affiche la facture actuellement liee avec un bouton "Delettrer maintenant"
3. Cliquer "Delettrer maintenant" pour supprimer le lien

ATTENTION : Il n'existe PAS de bouton "Delier" sur la page Factures. Le lettrage et le delettrage se gerent exclusivement depuis la page Banque.

--- IMPORT EXTRAIT DE COMPTE (CODA, PDF, CSV) ---
1. Aller dans "Banque"
2. Cliquer le bouton "Importer" (icone Upload)
3. Selectionner le fichier : format CODA (standard bancaire belge), PDF ou CSV
4. Pour les PDF et CSV, le systeme extrait automatiquement les transactions via IA
5. L'extrait est cree en statut "Brouillon" — les transactions apparaissent dans la liste
6. Lettrer les transactions, puis cliquer "Comptabiliser l'extrait" pour generer les ecritures comptables

--- AUTO-LETTRAGE VCS ---
1. Dans "Banque", cliquer le bouton "Auto-lettrage VCS"
2. Le systeme scanne toutes les communications structurees (VCS) des virements entrants
3. Chaque VCS est matchee avec l'appel de fonds correspondant
4. Les transactions matchees sont automatiquement lettrees

--- FACTURES FOURNISSEURS ---
Creation manuelle :
1. Aller dans "Factures"
2. Cliquer "Nouvelle facture"
3. Remplir : numero, date, fournisseur (dropdown des existants OU saisie libre), description, montant TTC, TVA, nature de depense, compte PCMN, cle de repartition
4. Enregistrer → une ecriture comptable (journal ACH) est generee automatiquement

Import par IA (facture unitaire) :
1. Dans "Factures", cliquer "Extraction IA"
2. Uploader le PDF de la facture
3. L'IA extrait automatiquement les donnees (fournisseur, date, montant, TVA, etc.)
4. Verifier et corriger les champs pre-remplis, puis enregistrer

Import en lot (Regroupement PDF Optipro) :
1. Dans "Factures", cliquer "Import Regroupement PDF"
2. Uploader le PDF contenant plusieurs factures concatenees
3. Le systeme detecte chaque facture et propose un matching avec les factures existantes
4. Pour chaque bloc : choisir "Attacher" (lier a une facture existante), "Creer facture" (ouvre un formulaire complet avec apercu PDF), ou "Ignorer"
5. Confirmer l'import

--- APPELS DE FONDS ---
1. Aller dans "Appels de fonds"
2. Cliquer "Nouvel appel"
3. Selectionner la periode, la cle de repartition, et les montants
4. Le systeme calcule la quote-part de chaque proprietaire selon les milliemes
5. Envoyer par email aux proprietaires (chacun recoit un PDF avec sa communication structuree VCS unique)

--- EXERCICES FISCAUX ---
1. Aller dans "Fiscal" ou "Comptabilite > Exercices fiscaux"
2. Creer un exercice (ex: 2025-01-01 au 2025-12-31)
3. L'exercice est "Ouvert" par defaut — toutes les ecritures dans cette periode sont autorisees
4. En fin d'exercice, cliquer "Cloturer" pour verrouiller les ecritures
5. Pour modifier une ecriture dans un exercice cloture, il faut d'abord "Reouvrir" l'exercice

--- MUTATIONS (changement de proprietaire) ---
1. Aller dans "Lots"
2. Selectionner le lot concerne
3. Dans la section "Mutations", cliquer "Nouvelle mutation"
4. Renseigner l'ancien et le nouveau proprietaire, la date de mutation
5. Le systeme genere automatiquement un decompte de mutation (PDF)
6. Les appels de fonds sont recalcules au prorata

--- RAPPORTS ---
- Balance des tiers : solde de chaque proprietaire/fournisseur
- Grand livre : detail des ecritures par compte PCMN
- Journaux : ACH (achats), VE (ventes/appels), FI (financier/banque), OD (operations diverses)
- Decompte de mutation : PDF detaillant les charges au prorata entre vendeur et acheteur

--- CONFIGURATION ---
- PCMN : Plan Comptable Minimum Normalise belge. Ne pas creer de doublons de comptes 6xxx — le systeme bloque si un compte avec un nom similaire existe deja.
- Natures de depense : categories intermediaires entre PCMN et cle de repartition (ex: "Ascenseur", "Assurance incendie"). Meme protection anti-doublons.
- Cles de repartition : definis les milliemes de chaque lot (ex: charges communes, chauffage, ascenseur).

--- PORTAIL PROPRIETAIRE ---
- Chaque proprietaire peut recevoir une invitation email pour acceder au portail self-service
- Sur le portail, il voit : ses appels de fonds, son solde, ses documents
- Le syndic invite un proprietaire via "Equipe" > "Inviter un proprietaire"

--- ROLES ET PERMISSIONS ---
- Superadmin : acces total a tout
- Admin_syndic : gere une organisation syndic
- Syndic : gere une ou N coproprietes
- Accountant : lecture + comptabilisation (pas de modification)
- Owner : portail proprietaire uniquement

REGLES DE REPONSE :
- Reponds TOUJOURS en te basant sur les procedures ci-dessus. Utilise les noms exacts des onglets et boutons.
- N'invente JAMAIS un bouton ou une etape. Si un utilisateur demande quelque chose qui n'est pas decrit ci-dessus, dis "Je ne suis pas certain de la procedure exacte pour cela" et propose d'escalader.
- Si la question demande un diagnostic technique (bug, erreur), une facturation, un contrat, ou un remboursement, termine ta reponse par `[[NEEDS_ESCALATION]]` sur sa propre ligne.
- Si la question est hors-sujet, reponds "Je ne peux repondre qu'aux questions sur NextGe Copro."
- Ne mens JAMAIS. Si tu ne connais pas la reponse, escalade avec `[[NEEDS_ESCALATION]]`.
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
