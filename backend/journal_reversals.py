"""iter90bx - Systeme de contre-passation traceable (Belgian PCMN legal).

Regle metier belge : les ecritures comptables NE PEUVENT PAS etre supprimees.
Toute "suppression" doit generer une ecriture INVERSE (Dr<->Cr swap) qui
neutralise mathematiquement l'originale tout en gardant la trace audit.

Etats des ecritures :
- Ecriture "normale" : ni `reversed` ni `is_reversal`.
- Ecriture "extournee" : `reversed=True`, `reversed_by_entry_id=<rev_id>`.
  L'ecriture originale reste immuable, seuls des champs meta sont ajoutes.
- Ecriture "de contre-passation" : `is_reversal=True`, `reverses_entry_id=<orig_id>`.
  Cree lors de la suppression. Debit et Credit sont inverses ligne par ligne.

Visibilite :
- Dans les journaux comptables : toujours visible (audit trail).
- Dans les balances / bilan / grand livre : masquee (paires s'annulent).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _resolve_reversal_date(db, orig_entry: dict) -> str:
    """Detecte si la date originale est dans un exercice cloture. Si oui,
    utilise la date d'aujourd'hui (dans un exercice ouvert). Sinon, garde la
    date originale."""
    orig_date = orig_entry.get("date") or ""
    copro_id = orig_entry.get("copropriete_id") or ""
    if not orig_date or not copro_id:
        return datetime.now(timezone.utc).date().isoformat()
    # Check if closed fiscal year contains this date
    fy = await db.fiscal_years.find_one({
        "copropriete_id": copro_id,
        "status": "closed",
        "start_date": {"$lte": orig_date},
        "end_date": {"$gte": orig_date},
    })
    if fy:
        # Original is in a closed FY, reverse today (in open FY)
        return datetime.now(timezone.utc).date().isoformat()
    return orig_date


async def reverse_journal_entry(
    db, orig_entry: dict, reason: str = "", by_user_id: Optional[str] = None,
) -> Optional[dict]:
    """Cree une contre-passation d'une ecriture existante.

    Idempotent : ne fait rien si l'ecriture est deja `reversed=True` OU
    `is_reversal=True` (on ne contre-passe pas une contre-passation).

    Retourne l'ecriture de contre-passation creee (ou None si skip).
    """
    if not orig_entry:
        return None
    if orig_entry.get("reversed"):
        return None  # deja contre-passee
    if orig_entry.get("is_reversal"):
        return None  # c'est deja une contre-passation

    rev_lines = []
    for ln in orig_entry.get("lines", []) or []:
        rev_lines.append({
            **{k: v for k, v in ln.items() if k not in ("debit", "credit")},
            "debit": float(ln.get("credit", 0) or 0),
            "credit": float(ln.get("debit", 0) or 0),
        })

    rev_id = str(uuid.uuid4())
    rev_date = await _resolve_reversal_date(db, orig_entry)
    rev_doc = {
        "id": rev_id,
        "journal_type": orig_entry.get("journal_type", "OD"),
        "date": rev_date,
        "reference": f"EXT-{orig_entry.get('reference','')}".rstrip("-"),
        "description": f"Contre-passation : {orig_entry.get('description','')}".strip(),
        "lines": rev_lines,
        "total_debit": round(sum(ln["debit"] for ln in rev_lines), 2),
        "total_credit": round(sum(ln["credit"] for ln in rev_lines), 2),
        "copropriete_id": orig_entry.get("copropriete_id", ""),
        "fiscal_year_id": orig_entry.get("fiscal_year_id"),
        "is_reversal": True,
        "reverses_entry_id": orig_entry.get("id"),
        "reversal_reason": reason or "",
        "created_at": _iso_now(),
        "created_by": by_user_id or "",
    }
    # Pas source_type/source_id : la contre-passation est independante
    await db.journal_entries.insert_one(rev_doc)

    # Marque l'originale comme reversee
    await db.journal_entries.update_one(
        {"id": orig_entry["id"]},
        {"$set": {
            "reversed": True,
            "reversed_at": _iso_now(),
            "reversed_by_entry_id": rev_id,
            "reversal_reason": reason or "",
        }},
    )
    return rev_doc


async def reverse_auto_entries(
    db, source_type: str, source_id: str, reason: str = "",
    by_user_id: Optional[str] = None,
) -> int:
    """Remplace la suppression brute des ecritures auto-generees par une
    contre-passation. Preserve `manually_edited` (non touchee) et les
    ecritures deja `reversed`/`is_reversal`.

    Retourne le nombre d'ecritures contre-passees.
    """
    entries = await db.journal_entries.find({
        "auto_generated": True,
        "manually_edited": {"$ne": True},
        "source_type": source_type,
        "source_id": source_id,
        "reversed": {"$ne": True},
        "is_reversal": {"$ne": True},
    }, {"_id": 0}).to_list(10000)

    count = 0
    for e in entries:
        rev = await reverse_journal_entry(db, e, reason=reason, by_user_id=by_user_id)
        if rev:
            count += 1
    return count
