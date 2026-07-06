"""Iter90aq : Template appris par fournisseur pour extraction rapide.

Objectif : quand l'utilisateur corrige (ou confirme) les champs extraits par
l'IA sur une facture, on memorise pour ce (supplier_id, copropriete_id) les
"anchors" - portion de texte immediatement avant chaque valeur - afin qu'a
la prochaine facture du meme fournisseur on extraie les champs sans passer
par Claude (regex directement sur le texte PDF).

Champs suivis : number, date, due_date, total_amount, vat_amount, net_amount,
vat_rate, iban, communication.

Structure de document dans MongoDB (collection `invoice_templates`) :
{
  "id": "<uuid>",
  "supplier_id": "<supplier id>",
  "copropriete_id": "<acp id>",
  "supplier_name": "<denormalized for reporting>",
  "patterns": {
    "number":     {"anchor": "N degres  facture", "example": "F-2026-001", "sample_count": 3, "last_used": "2026-06-15T..."},
    "date":       {"anchor": "Date facture",       "example": "15/06/2026", "sample_count": 2, "last_used": ...},
    "total_amount": {"anchor": "Total TTC",        "example": "1200.00",    "sample_count": 5, "last_used": ...},
    ...
  },
  "created_at": "...",
  "updated_at": "..."
}
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


# ---------- pattern helpers ----------

# Nombre max de chars avant la valeur qui constituent l'anchor.
_ANCHOR_LEN = 40
# Champs numeriques (comparaison float)
_NUM_FIELDS = {"total_amount", "vat_amount", "net_amount", "vat_rate"}
# Champs texte
_TEXT_FIELDS = {"number", "iban", "communication"}
# Champs date (format YYYY-MM-DD attendu apres normalisation)
_DATE_FIELDS = {"date", "due_date"}
_ALL_FIELDS = _NUM_FIELDS | _TEXT_FIELDS | _DATE_FIELDS


def _normalize_text(t: str) -> str:
    """Normalise le texte pour recherche insensible aux espaces multiples et casse."""
    return " ".join((t or "").split()).lower()


def _fmt_num_variants(v: float) -> list[str]:
    """Retourne les variantes probables d'un float pour recherche dans le texte
    (europeen '1200,00', anglais '1200.00', arrondi '1200', belge '1.234,56')."""
    variants = set()
    for f in (f"{v:.2f}", f"{v:.0f}"):
        variants.add(f)
        variants.add(f.replace(".", ","))
    if v >= 1000:
        # Europeen avec espace milliers : 1 234,56
        s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
        variants.add(s)
        # Anglais : 1,234.56
        s2 = f"{v:,.2f}"
        variants.add(s2)
        # Belge / europeen avec POINT milliers et VIRGULE decimale : 1.234,56
        # (format le plus courant sur les factures belges)
        s3 = f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        variants.add(s3)
    return sorted(variants, key=len, reverse=True)


def _fmt_date_variants(iso: str) -> list[str]:
    """iso YYYY-MM-DD -> variantes belges DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, DD/MM/YY, month name FR."""
    if not iso:
        return []
    try:
        y, m, d = iso.split("-")
        y_short = y[-2:]
        variants = [
            f"{d}/{m}/{y}", f"{d}-{m}-{y}", f"{d}.{m}.{y}",
            f"{d}/{m}/{y_short}", f"{d}-{m}-{y_short}", f"{d}.{m}.{y_short}",
            iso,  # ISO original
        ]
        # Sans zero-padding : 5/6/2026
        d_np = str(int(d)); m_np = str(int(m))
        variants += [f"{d_np}/{m_np}/{y}", f"{d_np}-{m_np}-{y}", f"{d_np}.{m_np}.{y}"]
        return list(dict.fromkeys(variants))  # dedupe stable
    except (ValueError, AttributeError):
        return []


def _find_value_position(text: str, value_str: str) -> int:
    """Retourne la position du premier match de value_str (case-insensitive), -1 si absent."""
    if not value_str or not text:
        return -1
    idx = text.lower().find(value_str.lower())
    return idx


def _capture_anchor(text: str, value_pos: int) -> str:
    """Retourne les `_ANCHOR_LEN` chars immediatement avant `value_pos`, nettoyes."""
    if value_pos <= 0:
        return ""
    start = max(0, value_pos - _ANCHOR_LEN)
    raw = text[start:value_pos]
    # Garder seulement les caracteres pertinents (alphanumerique + accents FR)
    cleaned = re.sub(r"[^\w\sA-Za-zeeaèêëaàâîïôöoûüuAEIOU]", " ", raw)
    cleaned = " ".join(cleaned.split())
    # Prend les 3-6 derniers mots comme anchor stable
    tokens = cleaned.split()
    return " ".join(tokens[-6:]) if tokens else ""


# ---------- learning ----------

def _extract_pattern_for_field(text: str, field: str, value) -> Optional[dict]:
    """Cherche `value` dans `text` avec ses variantes; si trouve, retourne
    l'anchor + example. Retourne None si valeur introuvable."""
    if value in (None, "", 0):
        return None

    if field in _NUM_FIELDS:
        try:
            fval = float(value)
        except (TypeError, ValueError):
            return None
        if fval <= 0:
            return None
        variants = _fmt_num_variants(fval)
        example = f"{fval:.2f}"
    elif field in _DATE_FIELDS:
        variants = _fmt_date_variants(str(value))
        example = str(value)
    else:
        variants = [str(value)]
        example = str(value)

    for v in variants:
        pos = _find_value_position(text, v)
        if pos > 0:
            anchor = _capture_anchor(text, pos)
            if anchor and len(anchor) >= 3:  # anchor doit avoir du contenu
                return {"anchor": anchor, "example": example, "matched_variant": v}
    return None


async def learn_template(
    db,
    supplier_id: str,
    supplier_name: str,
    copropriete_id: str,
    raw_text: str,
    user_values: dict,
) -> dict:
    """Apprend ou met a jour le template pour ce (supplier, ACP).

    `user_values` : les valeurs finales sauvegardees par l'utilisateur
    (numero, date, total_amount, etc.). On tente de retrouver chacune dans
    `raw_text` et memorise l'anchor. Les champs non retrouves sont ignores
    silencieusement.
    """
    if not supplier_id or not raw_text:
        return {"learned": 0, "skipped": "supplier_id or raw_text missing"}

    now = datetime.now(timezone.utc).isoformat()
    existing = await db.invoice_templates.find_one(
        {"supplier_id": supplier_id, "copropriete_id": copropriete_id or ""},
        {"_id": 0},
    )
    patterns = (existing or {}).get("patterns", {}) or {}

    learned_fields = []
    for field in _ALL_FIELDS:
        if field not in user_values:
            continue
        pat = _extract_pattern_for_field(raw_text, field, user_values.get(field))
        if pat is None:
            continue
        old = patterns.get(field) or {}
        count = int(old.get("sample_count", 0)) + 1
        patterns[field] = {
            "anchor": pat["anchor"],
            "example": pat["example"],
            "sample_count": count,
            "last_used": now,
        }
        learned_fields.append(field)

    if not learned_fields:
        return {"learned": 0, "skipped": "no field matched in raw_text"}

    doc = {
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "copropriete_id": copropriete_id or "",
        "patterns": patterns,
        "updated_at": now,
    }
    if existing:
        await db.invoice_templates.update_one(
            {"supplier_id": supplier_id, "copropriete_id": copropriete_id or ""},
            {"$set": doc},
        )
    else:
        doc["id"] = f"tpl-{uuid.uuid4()}"
        doc["created_at"] = now
        await db.invoice_templates.insert_one(doc)

    return {"learned": len(learned_fields), "fields": learned_fields}


# ---------- application (extraction depuis template) ----------

_VALUE_REGEX_BY_FIELD = {
    # Numero facture : alphanumerique + tirets/points/slashes
    "number":         re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_./]{2,30}"),
    # Date : DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY (2 ou 4 chiffres annee)
    "date":           re.compile(r"\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}"),
    "due_date":       re.compile(r"\d{1,2}[\/\.\-]\d{1,2}[\/\.\-]\d{2,4}"),
    # Montants : entier + separateur decimal + 2 chiffres (avec ou sans espace milliers)
    "total_amount":   re.compile(r"[\d\s]{1,10}[.,]\d{2}"),
    "vat_amount":     re.compile(r"[\d\s]{1,10}[.,]\d{2}"),
    "net_amount":     re.compile(r"[\d\s]{1,10}[.,]\d{2}"),
    "vat_rate":       re.compile(r"\d{1,2}[.,]?\d{0,2}\s*%?"),
    "iban":           re.compile(r"[A-Z]{2}\d{2}\s?(?:\d{4}\s?){3,4}\d{0,4}"),
    "communication":  re.compile(r"\+{3}?\d{3}[/\s]?\d{4}[/\s]?\d{5}\+{3}?|[\w\-\/]{5,30}"),
}


def _parse_date_be(s: str) -> str:
    """DD/MM/YYYY ou DD-MM-YYYY ou DD.MM.YY -> ISO YYYY-MM-DD ou ''."""
    if not s:
        return ""
    m = re.match(r"(\d{1,2})[\/\.\-](\d{1,2})[\/\.\-](\d{2,4})", s.strip())
    if not m:
        return ""
    d, mo, y = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    try:
        di, mi, yi = int(d), int(mo), int(y)
        if not (1 <= di <= 31 and 1 <= mi <= 12 and 2000 <= yi <= 2100):
            return ""
        return f"{yi:04d}-{mi:02d}-{di:02d}"
    except ValueError:
        return ""


def _parse_number(s: str) -> float:
    """1.234,56 ou 1234.56 ou 1 234,56 -> 1234.56, 0 si invalide."""
    if not s:
        return 0.0
    cleaned = re.sub(r"[^\d.,\-]", "", s.strip())
    # Supprime les espaces
    cleaned = cleaned.replace(" ", "")
    # Si 2 separateurs (ex : 1.234,56), le dernier est le decimal
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def apply_template(text: str, patterns: dict) -> dict:
    """Applique les anchors du template pour extraire les valeurs du texte.

    Retourne un dict {field: value} avec uniquement les champs matches.
    """
    if not text or not patterns:
        return {}

    text_low = text.lower()
    result: dict = {}
    for field, pat in patterns.items():
        anchor = (pat or {}).get("anchor", "")
        if not anchor:
            continue
        # Cherche l'anchor (insensible casse et espaces multiples)
        anchor_norm = _normalize_text(anchor)
        # Reduire les espaces multiples du texte pour matcher l'anchor normalise
        # Recherche dans le texte brut pour retrouver la position
        idx = text_low.find(anchor_norm.split()[-3:][0] if anchor_norm else "")
        if idx < 0:
            # Fallback : chercher les 3 derniers mots de l'anchor comme sequence relachee
            tokens = anchor_norm.split()
            if len(tokens) >= 2:
                sub = " ".join(tokens[-3:])
                idx = text_low.find(sub)
                if idx < 0:
                    continue
            else:
                continue
            search_from = idx + len(sub)
        else:
            search_from = idx + len(anchor_norm.split()[-3:][0])

        # Zone de recherche : les 120 chars apres l'anchor
        window = text[search_from:search_from + 120]

        regex = _VALUE_REGEX_BY_FIELD.get(field)
        if not regex:
            continue
        m = regex.search(window)
        if not m:
            continue
        raw = m.group(0).strip()

        if field in _DATE_FIELDS:
            iso = _parse_date_be(raw)
            if iso:
                # Sanity check annee
                y = int(iso[:4])
                if 2020 <= y <= 2035:
                    result[field] = iso
        elif field in _NUM_FIELDS:
            v = _parse_number(raw)
            if v > 0:
                result[field] = round(v, 2)
        else:
            # Text field : trimmed
            result[field] = raw.strip()

    return result


async def try_apply_supplier_template(db, supplier_id: str, copropriete_id: str, raw_text: str) -> dict:
    """Point d'entree utilise par invoice_ai.py.

    Retourne {"applied": True, "fields": {...}, "template_id": "..."} ou
    {"applied": False, "reason": "..."} si aucun template trouve.
    """
    if not supplier_id or not raw_text:
        return {"applied": False, "reason": "missing supplier_id or raw_text"}
    doc = await db.invoice_templates.find_one(
        {"supplier_id": supplier_id, "copropriete_id": copropriete_id or ""},
        {"_id": 0},
    )
    if not doc:
        return {"applied": False, "reason": "no template found for supplier"}
    fields = apply_template(raw_text, doc.get("patterns", {}) or {})
    return {"applied": True, "fields": fields, "template_id": doc.get("id", ""),
            "sample_counts": {k: v.get("sample_count", 0) for k, v in (doc.get("patterns") or {}).items()}}


# ---------- router (endpoint HTTP pour le frontend) ----------

class LearnPayload(BaseModel):
    supplier_id: str
    supplier_name: str = ""
    copropriete_id: str = ""
    raw_text: str = Field(..., min_length=1)
    user_values: dict = Field(default_factory=dict)


def create_invoice_templates_router(db):
    router = APIRouter(prefix="/api/invoice-templates")

    @router.post("/learn")
    async def learn(payload: LearnPayload, request: Request):
        """Enregistre / met a jour le template pour ce fournisseur a partir
        des valeurs finales corrigees par l'utilisateur."""
        if not payload.supplier_id:
            raise HTTPException(400, "supplier_id est requis pour apprendre un template")
        stats = await learn_template(
            db,
            supplier_id=payload.supplier_id,
            supplier_name=payload.supplier_name,
            copropriete_id=payload.copropriete_id,
            raw_text=payload.raw_text,
            user_values=payload.user_values,
        )
        return {"success": True, **stats}

    @router.get("/{supplier_id}")
    async def get_template(supplier_id: str, copropriete_id: str = ""):
        doc = await db.invoice_templates.find_one(
            {"supplier_id": supplier_id, "copropriete_id": copropriete_id or ""},
            {"_id": 0},
        )
        if not doc:
            return {"found": False}
        return {"found": True, **doc}

    return router
