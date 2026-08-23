"""iter94j : Sprint 2 - Sync des transactions Open Banking (Enable Banking).

- `enable_headers()` : signe un JWT RS256 court-lived.
- `eb_request()` : call HTTP a l'API Enable Banking.
- `sync_session(db, session_id, days_back=30)` : recupere accounts +
  transactions d'une session et upsert dans bank_transactions avec dedup.
- `sync_all_active(db)` : boucle sur toutes les sessions actives et
  synchronise chacune. Tolerant aux erreurs (une session KO ne bloque
  pas les autres).

Regle de dedup :
 - un bank_transaction Open Banking est dedupé sur `openbanking_txn_id`
 - si un CODA/PDF a deja importe la meme transaction (meme date+montant+
   communication+account_number), on la marque `openbanking_matched_to`
   plutot que de creer un doublon.
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
import jwt

_log = logging.getLogger("openbanking.sync")

APP_ID = os.environ.get("ENABLE_APP_ID", "")
KEY_PATH = os.environ.get("ENABLE_PRIVATE_KEY_PATH", "")
API_URL = os.environ.get(
    "ENABLE_API_URL", "https://api.enablebanking.com",
).rstrip("/")


def _private_key() -> bytes:
    if not KEY_PATH or not Path(KEY_PATH).exists():
        raise RuntimeError("ENABLE_PRIVATE_KEY_PATH manquant ou fichier absent")
    return Path(KEY_PATH).read_bytes()


def enable_headers() -> dict:
    """Genere un JWT RS256 valide 5min pour Enable Banking."""
    if not APP_ID:
        raise RuntimeError("ENABLE_APP_ID env variable manquante")
    now = int(datetime.now(timezone.utc).timestamp())
    token = jwt.encode(
        {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": now,
            "exp": now + 300,
        },
        _private_key(),
        algorithm="RS256",
        headers={"typ": "JWT", "kid": APP_ID},
    )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


async def eb_request(method: str, path: str, **kwargs) -> dict:
    """Appel HTTP a Enable Banking. Leve HTTPException-like sur erreur."""
    async with httpx.AsyncClient(base_url=API_URL, timeout=60.0) as c:
        r = await c.request(method, path, headers=enable_headers(), **kwargs)
    if r.is_error:
        raise RuntimeError(
            f"Enable Banking {r.status_code} on {method} {path} : {r.text[:400]}"
        )
    return r.json() if r.content else {}


def _tx_signature(txn: dict, account_iban: str) -> tuple:
    """Signature de dedup pour un transaction Open Banking vs un
    bank_transaction existant : (date, amount, communication_normalisee,
    account_number)."""
    date = str(txn.get("booking_date") or txn.get("transaction_date") or "")[:10]
    amount = float(txn.get("transaction_amount", {}).get("amount") or 0)
    if (txn.get("credit_debit_indicator") or "").upper() == "DBIT":
        amount = -abs(amount)
    else:
        amount = abs(amount)
    comm = (
        txn.get("remittance_information", [""])[0]
        if isinstance(txn.get("remittance_information"), list)
        else (txn.get("remittance_information") or "")
    )
    comm_norm = "".join(ch for ch in str(comm) if ch.isalnum()).lower()[:60]
    return (date, round(amount, 2), comm_norm, account_iban[-10:])


def _normalize_txn(
    raw: dict, session_doc: dict, account: dict, account_iban: str,
) -> dict:
    """Transforme un raw Enable Banking en bank_transaction."""
    amt_field = raw.get("transaction_amount", {}) or {}
    amount = float(amt_field.get("amount") or 0)
    is_debit = (raw.get("credit_debit_indicator") or "").upper() == "DBIT"
    signed = -abs(amount) if is_debit else abs(amount)
    # Communication
    remit = raw.get("remittance_information")
    if isinstance(remit, list):
        comm = " | ".join(str(r) for r in remit if r)
    else:
        comm = str(remit or "")
    # Counterparty
    counterparty_name = (
        (raw.get("creditor") or {}).get("name")
        if not is_debit
        else (raw.get("debtor") or {}).get("name")
    ) or (raw.get("counter_party_name") or "")
    counterparty_account = (
        ((raw.get("creditor_account") or {}).get("iban") if not is_debit
         else (raw.get("debtor_account") or {}).get("iban"))
        or ""
    )
    return {
        "id": str(uuid.uuid4()),
        "source": "openbanking",
        "openbanking_txn_id": raw.get("entry_reference")
        or raw.get("transaction_id") or "",
        "openbanking_session_id": session_doc["session_id"],
        "aspsp_name": session_doc.get("aspsp_name") or "",
        "copropriete_id": session_doc["copropriete_id"],
        "account_number": account_iban,
        "account_uid": account.get("uid") or "",
        "date": (
            raw.get("booking_date")
            or raw.get("transaction_date")
            or datetime.now(timezone.utc).date().isoformat()
        )[:10],
        "value_date": (raw.get("value_date") or "")[:10] or None,
        "amount": round(signed, 2),
        "counterparty_name": counterparty_name[:200],
        "counterparty_account": counterparty_account[:34],
        "communication": comm[:500],
        "transaction_type": "debit" if is_debit else "credit",
        "matched": False,
        "matched_to": None,
        "raw": raw,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _get_or_create_ob_statement(
    db, copropriete_id: str, account_iban: str, month_ym: str,
) -> str:
    """Cree (ou retrouve) un bank_statement virtuel mensuel pour
    representer les transactions Open Banking d'un compte donne."""
    number = f"OB-{month_ym}-{account_iban[-4:]}"
    existing = await db.bank_statements.find_one(
        {"copropriete_id": copropriete_id, "number": number},
    )
    if existing:
        return existing["id"]
    stmt_id = str(uuid.uuid4())
    await db.bank_statements.insert_one({
        "id": stmt_id,
        "copropriete_id": copropriete_id,
        "number": number,
        "date": f"{month_ym}-01",
        "account_number": account_iban,
        "opening_balance": 0.0,
        "closing_balance": 0.0,
        "source": "openbanking",
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return stmt_id


async def sync_session(
    db, session_id: str, days_back: int = 30,
) -> dict:
    """Sync une session : recupere accounts + transactions, upsert avec dedup."""
    session_doc = await db.openbanking_sessions.find_one(
        {"session_id": session_id},
    )
    if not session_doc:
        return {"session_id": session_id, "error": "session inconnue"}
    copropriete_id = session_doc["copropriete_id"]
    # Rafraichit la liste des comptes
    try:
        fresh = await eb_request("GET", f"/sessions/{session_id}")
    except RuntimeError as e:
        # Session expiree cote Enable Banking (401/403/410 => reauth requise)
        msg = str(e)
        if any(code in msg for code in ("401", "403", "410")):
            await db.openbanking_sessions.update_one(
                {"session_id": session_id},
                {"$set": {
                    "status": "reauthorization_required",
                    "last_error": msg[:200],
                    "last_sync_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
        return {"session_id": session_id, "error": msg[:200]}

    # iter94l : Enable Banking retourne accounts sous 2 formes :
    #  - `accounts` : liste de uids (strings)
    #  - `accounts_data` : liste enrichie avec uid + identification_hash + iban
    # Le Mock ASPSP sandbox n'expose pas /accounts/{uid} (404), donc on
    # ne fait PAS de call detail - on va directement chercher les
    # transactions.
    accounts_data = fresh.get("accounts_data") or []
    accounts_raw = fresh.get("accounts", [])
    stats = {"session_id": session_id, "accounts": 0, "inserted": 0,
             "matched_to_existing": 0, "skipped_duplicate": 0, "errors": []}
    accounts: list[dict] = []
    if accounts_data:
        for entry in accounts_data:
            if isinstance(entry, dict) and entry.get("uid"):
                accounts.append(entry)
    else:
        for entry in accounts_raw:
            if isinstance(entry, str):
                accounts.append({"uid": entry})
            elif isinstance(entry, dict) and entry.get("uid"):
                accounts.append(entry)
    # date range
    date_from = (datetime.now(timezone.utc).date()
                 - timedelta(days=days_back)).isoformat()
    date_to = datetime.now(timezone.utc).date().isoformat()

    for account in accounts:
        account_uid = account.get("uid")
        account_iban = (
            (account.get("account_id") or {}).get("iban")
            or account.get("iban")
            or ""
        )
        if not account_uid:
            continue
        # iter94o : skip comptes exclus manuellement par le syndic
        if account_uid in set(session_doc.get("excluded_account_uids") or []):
            continue
        if not account_iban:
            id_hash = account.get("identification_hash") or account_uid
            account_iban = f"OB-{id_hash[:16]}"
        stats["accounts"] += 1
        # Fetch transactions (paginated via continuation_key)
        continuation = None
        all_txns = []
        while True:
            params = {"date_from": date_from, "date_to": date_to}
            if continuation:
                params["continuation_key"] = continuation
            try:
                page = await eb_request(
                    "GET",
                    f"/accounts/{account_uid}/transactions",
                    params=params,
                )
            except RuntimeError as e:
                stats["errors"].append(f"{account_iban}: {str(e)[:150]}")
                break
            all_txns.extend(page.get("transactions", []))
            continuation = page.get("continuation_key")
            if not continuation:
                break
            if len(all_txns) > 5000:  # safety
                stats["errors"].append(
                    f"{account_iban}: >5000 tx, arret preventif"
                )
                break

        # Bucket par mois pour statements virtuels
        month_to_stmt: dict[str, str] = {}
        for raw in all_txns:
            date_str = (raw.get("booking_date")
                        or raw.get("transaction_date") or "")[:10]
            if not date_str:
                continue
            month_ym = date_str[:7]
            if month_ym not in month_to_stmt:
                month_to_stmt[month_ym] = await _get_or_create_ob_statement(
                    db, copropriete_id, account_iban, month_ym,
                )
            stmt_id = month_to_stmt[month_ym]
            normalized = _normalize_txn(raw, session_doc, account, account_iban)
            normalized["statement_id"] = stmt_id
            # Dedup 1 : openbanking_txn_id deja present
            if normalized["openbanking_txn_id"]:
                existing = await db.bank_transactions.find_one({
                    "copropriete_id": copropriete_id,
                    "openbanking_txn_id": normalized["openbanking_txn_id"],
                })
                if existing:
                    stats["skipped_duplicate"] += 1
                    continue
            # Dedup 2 : match CODA existant (memes date+amount+comm+account)
            sig = _tx_signature(raw, account_iban)
            coda_match = await db.bank_transactions.find_one({
                "copropriete_id": copropriete_id,
                "date": sig[0],
                "amount": sig[1],
                "account_number": {"$in": [account_iban, sig[3]]},
                "source": {"$ne": "openbanking"},
            })
            if coda_match:
                # Enrichit le CODA existant avec la reference OB (pas de doublon)
                await db.bank_transactions.update_one(
                    {"id": coda_match["id"]},
                    {"$set": {
                        "openbanking_txn_id": normalized["openbanking_txn_id"],
                        "openbanking_session_id": normalized["openbanking_session_id"],
                        "enriched_by_openbanking_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                    }},
                )
                stats["matched_to_existing"] += 1
                continue
            # Insert
            await db.bank_transactions.insert_one(normalized)
            stats["inserted"] += 1

    # Update session sync metadata
    await db.openbanking_sessions.update_one(
        {"session_id": session_id},
        {"$set": {
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_sync_stats": {k: v for k, v in stats.items() if k != "errors"},
            "status": "active",
        }},
    )
    return stats


async def sync_all_active(db) -> dict:
    """Boucle sur toutes les sessions non expirees + lance sync_session."""
    sessions = await db.openbanking_sessions.find(
        {"status": {"$ne": "reauthorization_required"}},
        {"_id": 0, "session_id": 1},
    ).to_list(500)
    summary = {"total": len(sessions), "success": 0, "failed": 0, "details": []}
    for s in sessions:
        try:
            stats = await sync_session(db, s["session_id"])
            summary["details"].append(stats)
            if stats.get("error"):
                summary["failed"] += 1
            else:
                summary["success"] += 1
        except Exception as e:  # noqa: BLE001
            summary["failed"] += 1
            summary["details"].append({
                "session_id": s["session_id"],
                "error": str(e)[:200],
            })
            _log.exception("sync_session failed for %s", s["session_id"])
    return summary
