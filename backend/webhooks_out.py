"""iter95g - Webhooks sortants vers la plateforme AG (Assemblee Generale).

Evenements pris en charge :
- `new-acp`       : creation d'une copropriete (POST /api/coproprietes)
- `acp-updated`   : modification d'une copropriete (PUT /api/coproprietes/{id})
- `acp-archived`  : archivage d'une copropriete (POST /api/coproprietes/{id}/archive)

Chaque webhook envoie un POST JSON `{copropriete_id, name, address}` avec
le header `X-Sync-Token` = `EXPORT_SYNC_TOKEN`.

Configuration :
- `AG_WEBHOOK_BASE_URL` (recommande) : URL de base, ex
  `https://ag.example.com/api/webhooks`. Chaque event est appendu comme
  segment (`/new-acp`, `/acp-updated`, `/acp-archived`).
- `AG_WEBHOOK_URL` (legacy iter95f) : URL du seul endpoint `new-acp`.
  Retro-compat : si `AG_WEBHOOK_BASE_URL` est absent, on tente d'en deriver
  la base en supprimant le suffixe `/new-acp`.

Best-effort :
- Fire-and-forget (`asyncio.create_task`) : ne bloque jamais la reponse HTTP.
- Timeout 5s, echec reseau logge en warning mais silencieux pour l'appelant.
- Si aucune URL configuree : webhook desactive (log info).
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_S = 5.0
SUPPORTED_EVENTS = ("new-acp", "acp-updated", "acp-archived")


def _base_url() -> str:
    """URL de base pour tous les webhooks AG. Vide = webhook desactive."""
    base = (os.environ.get("AG_WEBHOOK_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    # Retro-compat iter95f : `AG_WEBHOOK_URL` pointait sur `.../new-acp`.
    legacy = (os.environ.get("AG_WEBHOOK_URL") or "").strip().rstrip("/")
    if legacy:
        # Retire `/new-acp` si present pour reconstruire la base.
        if legacy.endswith("/new-acp"):
            legacy = legacy[: -len("/new-acp")]
        return legacy
    return ""


def _compose_address(doc: dict) -> str:
    """Adresse postale complete = rue, CP + ville, pays."""
    parts = [
        (doc.get("address") or "").strip(),
        " ".join(
            p for p in [
                (doc.get("postal_code") or "").strip(),
                (doc.get("city") or "").strip(),
            ] if p
        ).strip(),
        (doc.get("country") or "").strip(),
    ]
    return ", ".join(p for p in parts if p)


async def _send(event: str, payload: dict) -> None:
    """Emission bas-niveau d'un webhook. Silencieux sur erreur."""
    base = _base_url()
    if not base:
        logger.info(
            "AG webhook (%s) non emis : ni AG_WEBHOOK_BASE_URL ni AG_WEBHOOK_URL defini (copro=%s)",
            event, payload.get("copropriete_id"),
        )
        return
    token = (os.environ.get("EXPORT_SYNC_TOKEN") or "").strip()
    if not token:
        logger.warning(
            "EXPORT_SYNC_TOKEN absent : AG webhook (%s) abandonne (copro=%s)",
            event, payload.get("copropriete_id"),
        )
        return

    url = f"{base}/{event}"
    headers = {
        "X-Sync-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "NextGeCopro-Webhook/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        level = logger.warning if resp.status_code >= 400 else logger.info
        level(
            "AG webhook %s -> %s : HTTP %s (copro=%s)",
            event, url, resp.status_code, payload.get("copropriete_id"),
        )
    except Exception as exc:
        logger.warning(
            "AG webhook %s -> %s : echec (%s) copro=%s",
            event, url, exc, payload.get("copropriete_id"),
        )


def fire_acp_webhook(event: str, copro_doc: dict) -> None:
    """Fire-and-forget d'un webhook ACP (event = new-acp / acp-updated / acp-archived).

    Ne leve JAMAIS d'exception : les erreurs sont loggees. A appeler juste
    apres l'ecriture reussie en base.
    """
    if event not in SUPPORTED_EVENTS:
        logger.warning("AG webhook : event inconnu '%s' (ignore)", event)
        return
    payload = {
        "copropriete_id": copro_doc.get("id", ""),
        "name": copro_doc.get("name", ""),
        "address": _compose_address(copro_doc),
    }
    try:
        asyncio.create_task(_send(event, payload))
    except RuntimeError:
        # Pas de loop asyncio actif (script CLI) : execution synchrone.
        try:
            asyncio.run(_send(event, payload))
        except Exception as exc:
            logger.warning("AG webhook %s : impossible d'emettre : %s", event, exc)


# --- Retro-compat iter95f -------------------------------------------------
# Alias historique conservant l'ancienne signature `fire_new_acp_webhook(doc)`.

def fire_new_acp_webhook(copro_doc: dict) -> None:
    fire_acp_webhook("new-acp", copro_doc)
