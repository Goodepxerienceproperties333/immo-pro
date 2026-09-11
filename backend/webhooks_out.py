"""iter95f - Webhook sortant vers une plateforme AG (Assemblee Generale)
lors de la creation d'une copropriete (ACP).

Se declenche a la fin de `POST /api/coproprietes` avec un payload minimal
`{copropriete_id, name, address}` protege par le meme `X-Sync-Token` que
l'endpoint `GET /api/export/owners/...` (partage `EXPORT_SYNC_TOKEN`).

Le webhook est :
- Optionnel : desactive si `AG_WEBHOOK_URL` n'est pas defini (log info).
- Non-bloquant : lance en `asyncio.create_task` pour ne pas retarder la
  reponse HTTP au syndic.
- Idempotent cote destinataire : le `copropriete_id` (UUID) doit servir
  de cle d'unicite.
- Robuste : en cas de timeout / erreur reseau, on log un warning mais on
  ne casse jamais la creation de l'ACP.
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_S = 5.0


def _compose_address(doc: dict) -> str:
    """Construit une adresse postale complete a partir des champs de l'ACP."""
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


async def _send_new_acp_webhook(copro_doc: dict) -> None:
    """Emet le webhook `new-acp` en tache de fond (best-effort)."""
    url = (os.environ.get("AG_WEBHOOK_URL") or "").strip()
    if not url:
        logger.info(
            "AG_WEBHOOK_URL non defini : webhook new-acp non emis pour %s",
            copro_doc.get("id"),
        )
        return
    token = (os.environ.get("EXPORT_SYNC_TOKEN") or "").strip()
    if not token:
        logger.warning(
            "EXPORT_SYNC_TOKEN absent : webhook new-acp abandonne (secret manquant)",
        )
        return

    payload = {
        "copropriete_id": copro_doc.get("id", ""),
        "name": copro_doc.get("name", ""),
        "address": _compose_address(copro_doc),
    }
    headers = {
        "X-Sync-Token": token,
        "Content-Type": "application/json",
        "User-Agent": "NextGeCopro-Webhook/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.warning(
                "Webhook new-acp -> %s : HTTP %s (copro=%s)",
                url, resp.status_code, payload["copropriete_id"],
            )
        else:
            logger.info(
                "Webhook new-acp -> %s : HTTP %s (copro=%s)",
                url, resp.status_code, payload["copropriete_id"],
            )
    except Exception as exc:
        logger.warning(
            "Webhook new-acp -> %s : echec (%s) copro=%s",
            url, exc, payload["copropriete_id"],
        )


def fire_new_acp_webhook(copro_doc: dict) -> None:
    """Fire-and-forget : ne bloque pas la reponse HTTP.

    A appeler apres l'insertion reussie en base. Toute erreur cote reseau
    est loggee mais silencieuse pour l'appelant.
    """
    try:
        asyncio.create_task(_send_new_acp_webhook(copro_doc))
    except RuntimeError:
        # Pas de loop actif (context script/CLI) : execution synchrone.
        try:
            asyncio.run(_send_new_acp_webhook(copro_doc))
        except Exception as exc:
            logger.warning("Impossible d'emettre le webhook new-acp: %s", exc)
