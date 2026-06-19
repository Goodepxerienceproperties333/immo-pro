"""Tier account management : auto-assign and persist PCMN sub-accounts for
owners (40000XXX + 40001XXX) and suppliers (44000XXX), scoped per ACP.

Belgian PCMN convention :
- 400000 (master) -> 40000001, 40000002, ... per owner (Provisions / charges)
- 400100 (master) -> 40010001, 40010002, ... per owner (Fonds de reserve)
   Note: we use 4000 / 4001 prefix (8-digit) for visual consistency: 40000XXX / 40010XXX
- 440000 (master) -> 44000001, 44000002, ... per supplier
"""
from typing import Optional


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


async def _ensure_account(db, copro_id: str, number: str, name: str, class_num: int):
    """Idempotent: create PCMN account if missing in this ACP."""
    q = {"number": number, "copropriete_id": copro_id}
    existing = await db.pcmn_accounts.find_one(q, {"_id": 0})
    if existing:
        return existing
    doc = {
        "number": number,
        "name": name,
        "class_num": class_num,
        "parent": number[:6],
        "type": "balance",
        "copropriete_id": copro_id,
        "active": True,
        "is_tier_account": True,
    }
    await db.pcmn_accounts.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}


async def assign_owner_accounts(db, owner: dict, copro_id: Optional[str] = None) -> dict:
    """Ensure the owner has provisions + reserve PCMN accounts in the given ACP.
    Updates the owner doc in DB. Returns the updated owner."""
    target_copro = copro_id or owner.get("copropriete_id", "")
    if not target_copro:
        return owner
    # Per-ACP per-owner mapping stored as dict: {copro_id: {provisions, reserve}}
    accounts_map = owner.get("tier_accounts", {}) or {}
    if target_copro in accounts_map:
        return owner
    seq_prov = await _next_seq(db, target_copro, "40000")
    prov_num = _format_seq("40000", seq_prov, width=3)
    seq_res = await _next_seq(db, target_copro, "40010")
    res_num = _format_seq("40010", seq_res, width=3)
    short_name = (owner.get("last_name") or owner.get("name") or "")[:30]
    await _ensure_account(db, target_copro, prov_num,
                          f"Prov. charges - {short_name}".strip(), 4)
    await _ensure_account(db, target_copro, res_num,
                          f"Fonds reserve - {short_name}".strip(), 4)
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
