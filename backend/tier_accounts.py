"""Tier account management : auto-assign and persist PCMN sub-accounts for
owners and suppliers, scoped per ACP.

Convention PCMN belge (arrete royal 12/07/2012 modifie 2018) :
- 4100XXXX : Acompte de fonds de reserve appele (par proprietaire)
- 4101XXXX : Acompte de fonds de roulement appele (par proprietaire)
- 44000XXX : Fournisseurs (par fournisseur)

Legacy (avant iter90v) : 40000XXX (provisions) + 40010XXX (fonds reserve).
Les proprietaires existants gardent leur ancien schema, les NOUVEAUX
proprietaires recoivent le nouveau schema. Les helpers ci-dessous acceptent
les 2 formats.
"""
from typing import Optional


# --- Prefixes ---------------------------------------------------------------
# Legacy prefixes still supported for existing owners (backward compat)
LEGACY_PROVISIONS_PREFIX = "40000"   # 40000XXX
LEGACY_RESERVE_PREFIX = "40010"      # 40010XXX
# New PCMN-compliant prefixes for new owners (iter90v)
PROVISIONS_PREFIX = "4101"           # 4101XXXX - fonds de roulement appele
RESERVE_PREFIX = "4100"              # 4100XXXX - fonds de reserve appele
SEQ_WIDTH_NEW = 4  # 4-digit suffix : 4101XXXX (max 9999 owners per ACP)


def is_provisions_account(number: str) -> bool:
    """True if an account number is a per-owner 'fonds de roulement / provisions'
    tier account (new PCMN 4101XXXX or legacy 40000XXX). Longueur >= 8 pour
    eviter les faux positifs (comptes maitres, comptes Optipro courts)."""
    if not number or len(number) < 8:
        return False
    return number.startswith(PROVISIONS_PREFIX) or number.startswith(LEGACY_PROVISIONS_PREFIX)


def is_reserve_account(number: str) -> bool:
    """True if an account number is a per-owner 'fonds de reserve' tier account
    (new PCMN 4100XXXX ou legacy 40010XXX). Longueur >= 8."""
    if not number or len(number) < 8:
        return False
    return number.startswith(RESERVE_PREFIX) or number.startswith(LEGACY_RESERVE_PREFIX)


def is_owner_tier_account(number: str) -> bool:
    """True if the account is a per-owner tier account (either kind, any format)."""
    return is_provisions_account(number) or is_reserve_account(number)


def _format_seq(prefix: str, seq: int, width: int = 3) -> str:
    return f"{prefix}{seq:0{width}d}"


async def _next_seq(db, copro_id: str, prefix: str) -> int:
    """Return next available sequence number for tier accounts starting with prefix."""
    q = {"number": {"$regex": f"^{prefix}\\d+$"}}
    if copro_id:
        q["copropriete_id"] = copro_id
    cursor = db.pcmn_accounts.find(q, {"_id": 0, "number": 1})
    used = set()
    async for d in cursor:
        try:
            used.add(int(d["number"][len(prefix):]))
        except (ValueError, KeyError):
            pass
    n = 1
    while n in used:
        n += 1
    return n


async def _ensure_account(db, copro_id: str, number: str, name: str, class_num: int,
                          parent: Optional[str] = None):
    """Idempotent: create PCMN account if missing in this ACP."""
    q = {"number": number, "copropriete_id": copro_id}
    existing = await db.pcmn_accounts.find_one(q, {"_id": 0})
    if existing:
        return existing
    doc = {
        "number": number,
        "name": name,
        "class_num": class_num,
        "parent": parent if parent is not None else number[:4],
        "type": "balance",
        "copropriete_id": copro_id,
        "active": True,
        "is_tier_account": True,
    }
    await db.pcmn_accounts.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def assign_owner_accounts(db, owner: dict, copro_id: Optional[str] = None) -> dict:
    """Ensure the owner has provisions + reserve PCMN accounts in the given ACP.
    Updates the owner doc in DB. Returns the updated owner.

    - Nouveaux comptes (iter90v) : `4101XXXX` (fonds de roulement / provisions)
      + `4100XXXX` (fonds de reserve appele), width 4 chiffres.
    - Idempotent : ne recree pas les comptes si deja assignes pour cette ACP
      (garde le format existant, y compris legacy `40000XXX / 40010XXX`).
    - Ajoute idempotemment l'ACP a `owner.copropriete_ids[]` (chinese wall).
    """
    target_copro = copro_id or owner.get("copropriete_id", "")
    if not target_copro:
        return owner
    # Toujours rattacher owner -> ACP (safe multiple calls, $addToSet).
    current_ids = list(owner.get("copropriete_ids") or [])
    if target_copro not in current_ids:
        await db.owners.update_one(
            {"id": owner["id"]},
            {"$addToSet": {"copropriete_ids": target_copro}}
        )
        current_ids.append(target_copro)
        owner["copropriete_ids"] = current_ids
    accounts_map = owner.get("tier_accounts", {}) or {}
    if target_copro in accounts_map:
        return owner

    # Generation des 2 comptes tiers dans le format PCMN standard
    seq_prov = await _next_seq(db, target_copro, PROVISIONS_PREFIX)
    prov_num = _format_seq(PROVISIONS_PREFIX, seq_prov, width=SEQ_WIDTH_NEW)
    seq_res = await _next_seq(db, target_copro, RESERVE_PREFIX)
    res_num = _format_seq(RESERVE_PREFIX, seq_res, width=SEQ_WIDTH_NEW)
    # Nom du proprietaire pour libelle
    display_name = (
        owner.get("name")
        or f"{owner.get('last_name', '')} {owner.get('first_name', '')}".strip()
        or owner.get("last_name", "")
    ).strip()[:50] or f"Proprietaire {owner.get('id', '')[:8]}"

    # Comptes maitres PCMN
    await _ensure_account(db, target_copro, PROVISIONS_PREFIX,
                          "Acompte de fonds de roulement appele", 4, parent="410")
    await _ensure_account(db, target_copro, RESERVE_PREFIX,
                          "Acompte de fonds de reserve appele", 4, parent="410")

    # Comptes auxiliaires du proprietaire
    await _ensure_account(db, target_copro, prov_num,
                          f"Acompte de fonds de roulement appele - {display_name}", 4,
                          parent=PROVISIONS_PREFIX)
    await _ensure_account(db, target_copro, res_num,
                          f"Acompte de fonds de reserve appele - {display_name}", 4,
                          parent=RESERVE_PREFIX)

    # Cles internes conservees pour compat : provisions/reserve
    accounts_map[target_copro] = {"provisions": prov_num, "reserve": res_num}
    await db.owners.update_one({"id": owner["id"]}, {"$set": {"tier_accounts": accounts_map}})
    owner["tier_accounts"] = accounts_map
    return owner


async def assign_supplier_account(db, supplier: dict, copro_id: Optional[str] = None) -> dict:
    """Ensure the supplier has a unique 44000XXX account in the given ACP.
    Suppliers are global; we store one account per ACP they appear in."""
    target_copro = copro_id or supplier.get("copropriete_id", "")
    if not target_copro:
        return supplier
    accounts_map = supplier.get("tier_accounts", {}) or {}
    if target_copro in accounts_map:
        return supplier
    seq = await _next_seq(db, target_copro, "44000")
    num = _format_seq("44000", seq, width=3)
    name = (supplier.get("name") or "Fournisseur")[:40]
    await _ensure_account(db, target_copro, num, name.strip(), 4)
    accounts_map[target_copro] = {"main": num}
    await db.suppliers.update_one({"id": supplier["id"]}, {"$set": {"tier_accounts": accounts_map}})
    supplier["tier_accounts"] = accounts_map
    return supplier


def get_owner_accounts(owner: dict, copro_id: str) -> dict:
    """Return {provisions, reserve} for the given owner+ACP, or empty dict."""
    return (owner.get("tier_accounts") or {}).get(copro_id, {})


def get_supplier_account(supplier: dict, copro_id: str) -> str:
    """Return supplier main account in given ACP, or '' if absent."""
    return ((supplier.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")
