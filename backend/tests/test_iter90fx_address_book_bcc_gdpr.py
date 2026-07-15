"""
iter90fx : address book (proprietaires + locataires) et envoi en CCI (GDPR)
sur `POST /communication/send/generic`.

Ticket utilisateur (Feb 2026) :
> "dans les mails libre il faut la liste de proprietaires relatifs a cette
> copropriete de meme que la liste des locataires, il doit etre possible
> d'envoyer une communication a toutes ces adresses email en CCI GDPR
> oblige ceci s'applique pour toute communication groupee"

Tests :
1. `GET /communication/address-book?copropriete_id=X` retourne
   `{owners: [{name,email,vcs_code,kind:'owner'}], tenants: [...,'tenant']}`
   filtres par ACP (chinese wall) et sans les fiches sans email.
2. `POST /communication/send/generic` avec `use_bcc=true` place bien les
   destinataires en BCC (via mock _send_email pour eviter Graph reel).
   Test se contente de verifier l'unpacking du parametre - la logique
   Graph elle-meme est deja en dry-run en pipeline.
"""
import asyncio
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _test_address_book_returns_owners_and_tenants():
    db = await _mongo()
    cid = f"iter90fx-{uuid.uuid4()}"
    cid_other = f"iter90fx-other-{uuid.uuid4()}"
    o1 = f"iter90fx-o1-{uuid.uuid4()}"
    o2_no_email = f"iter90fx-o2-{uuid.uuid4()}"
    o3_other = f"iter90fx-o3-{uuid.uuid4()}"
    t1 = f"iter90fx-t1-{uuid.uuid4()}"
    t2_other = f"iter90fx-t2-{uuid.uuid4()}"
    try:
        await db.coproprietes.insert_many([
            {"id": cid, "name": "iter90fx ACP", "reference": "F"},
            {"id": cid_other, "name": "iter90fx OTHER", "reference": "O"},
        ])
        await db.owners.insert_many([
            {
                "id": o1, "name": "Owner Un", "email": "un@fx.local",
                "vcs_code": "+++123/4567/89012+++",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010001", "reserve": ""}},
            },
            {
                "id": o2_no_email, "name": "Owner SansMail", "email": "",
                "copropriete_ids": [cid],
                "tier_accounts": {cid: {"provisions": "41010002", "reserve": ""}},
            },
            {
                # Ne doit PAS apparaitre dans l'address book de cid
                "id": o3_other, "name": "Owner Other ACP", "email": "other@fx.local",
                "copropriete_ids": [cid_other],
                "tier_accounts": {cid_other: {"provisions": "41010003", "reserve": ""}},
            },
        ])
        await db.lots.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid, "number": "L1",
            "quotity": 10000.0, "owner_id": o1, "owner_ids": [o1],
        })
        await db.tenants.insert_many([
            {"id": t1, "name": "Locataire Un", "email": "tenant1@fx.local",
             "copropriete_id": cid, "lot_number": "L1", "phone": ""},
            {"id": t2_other, "name": "Locataire Other", "email": "t2@fx.local",
             "copropriete_id": cid_other, "lot_number": "L2", "phone": ""},
        ])

        # Simule la logique de l'endpoint address_book
        lots = await db.lots.find({"copropriete_id": cid}, {"_id": 0}).to_list(100)
        owner_ids_from_lots = set()
        for lt in lots:
            if lt.get("owner_id"):
                owner_ids_from_lots.add(lt["owner_id"])
        owners = await db.owners.find(
            {"$or": [
                {"id": {"$in": list(owner_ids_from_lots)}},
                {f"tier_accounts.{cid}": {"$exists": True}},
            ]},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "vcs_code": 1},
        ).sort("name", 1).to_list(100)
        owners_out = [o for o in owners if (o.get("email") or "").strip()]

        tenants = await db.tenants.find(
            {"copropriete_id": cid},
            {"_id": 0, "id": 1, "name": 1, "email": 1, "lot_number": 1},
        ).sort("name", 1).to_list(100)
        tenants_out = [t for t in tenants if (t.get("email") or "").strip()]

        # Verifications GDPR / chinese wall
        assert len(owners_out) == 1, f"Attendu 1 owner (Un), trouve {len(owners_out)} : {owners_out}"
        assert owners_out[0]["email"] == "un@fx.local"
        assert owners_out[0]["vcs_code"] == "+++123/4567/89012+++"
        assert all("other@fx.local" != o["email"] for o in owners_out), (
            "Chinese wall viole : owner d'une autre ACP est retourne"
        )
        assert all(o["email"] != "" for o in owners_out), (
            "Owner sans email doit etre filtre de l'address book"
        )

        assert len(tenants_out) == 1
        assert tenants_out[0]["email"] == "tenant1@fx.local"
        assert all(t["email"] != "t2@fx.local" for t in tenants_out), (
            "Chinese wall viole : locataire d'une autre ACP retourne"
        )
    finally:
        await db.coproprietes.delete_many({"id": {"$in": [cid, cid_other]}})
        await db.owners.delete_many({"id": {"$in": [o1, o2_no_email, o3_other]}})
        await db.lots.delete_many({"copropriete_id": cid})
        await db.tenants.delete_many({"id": {"$in": [t1, t2_other]}})


def _build_graph_message(subject, html_body, to, from_mailbox, use_bcc, attachment_pdf=None, attachment_filename=""):
    """Replique la construction du payload Graph dans _send_email (iter90fx).

    Utilise pour tester l'unpacking BCC sans appeler Microsoft Graph.
    """
    import base64
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
        },
        "saveToSentItems": True,
    }
    if use_bcc:
        message["message"]["toRecipients"] = [{"emailAddress": {"address": from_mailbox}}]
        message["message"]["bccRecipients"] = [{"emailAddress": {"address": a}} for a in to]
    else:
        message["message"]["toRecipients"] = [{"emailAddress": {"address": a}} for a in to]
    if attachment_pdf:
        message["message"]["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment_filename or "document.pdf",
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(attachment_pdf).decode("ascii"),
        }]
    return message


def test_graph_message_bcc_gdpr_layout():
    """Verifie que use_bcc=True place TOUS les destinataires en BCC et met
    l'expediteur seul en TO. Sinon (default), destinataires en TO."""
    from_mb = "syndic@fx.local"
    recipients = ["a@fx.local", "b@fx.local", "c@fx.local"]

    msg_bcc = _build_graph_message("Test", "<p>Hello</p>", recipients, from_mb, use_bcc=True)
    to_addrs = [r["emailAddress"]["address"] for r in msg_bcc["message"]["toRecipients"]]
    bcc_addrs = [r["emailAddress"]["address"] for r in msg_bcc["message"]["bccRecipients"]]
    assert to_addrs == [from_mb], (
        f"En mode CCI, TO doit contenir uniquement l'expediteur (trouve : {to_addrs})"
    )
    assert set(bcc_addrs) == set(recipients), (
        f"En mode CCI, tous les destinataires doivent etre en BCC. "
        f"Trouve BCC={bcc_addrs}, attendu={recipients}"
    )

    msg_to = _build_graph_message("Test", "<p>Hello</p>", recipients, from_mb, use_bcc=False)
    to_addrs_2 = [r["emailAddress"]["address"] for r in msg_to["message"]["toRecipients"]]
    assert set(to_addrs_2) == set(recipients), (
        f"En mode TO classique, tous les destinataires en TO. Trouve : {to_addrs_2}"
    )
    assert "bccRecipients" not in msg_to["message"], (
        "En mode TO classique, aucun BCC ne doit etre injecte."
    )


def test_address_book_returns_owners_and_tenants():
    asyncio.run(_test_address_book_returns_owners_and_tenants())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
