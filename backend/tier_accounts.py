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
    existing = accounts_map.get(target_copro) or {}
    # iter90gx : verrouille la creation MANQUANTE d'un compte (provisions ou
    # reserve) meme si un mapping partiel existe deja pour cette ACP.
    # Ancien bug : sur les owners issus d'imports legacy (Optipro), seul
    # `provisions` etait renseigne -> tous les appels de fonds de reserve
    # generaient un VE vide (return None) car `accs.get("reserve") is None`
    # bloquait la boucle. Consequence : le journal Ventes ne contenait
    # aucune ligne de fonds de reserve alors que le fund_call etait cree.
    if existing.get("provisions") and existing.get("reserve"):
        return owner  # already fully assigned - fast path
    # Sinon on va creer les cles manquantes (idempotent)

    display_name = (
        owner.get("name")
        or f"{owner.get('last_name', '')} {owner.get('first_name', '')}".strip()
        or owner.get("last_name", "")
    ).strip()[:50] or f"Proprietaire {owner.get('id', '')[:8]}"

    # Comptes maitres PCMN (idempotent)
    await _ensure_account(db, target_copro, PROVISIONS_PREFIX,
                          "Acompte de fonds de roulement appele", 4, parent="410")
    await _ensure_account(db, target_copro, RESERVE_PREFIX,
                          "Acompte de fonds de reserve appele", 4, parent="410")

    # Provisions : ne cree que si absent
    if not existing.get("provisions"):
        seq_prov = await _next_seq(db, target_copro, PROVISIONS_PREFIX)
        prov_num = _format_seq(PROVISIONS_PREFIX, seq_prov, width=SEQ_WIDTH_NEW)
        await _ensure_account(db, target_copro, prov_num,
                              f"Acompte de fonds de roulement appele - {display_name}", 4,
                              parent=PROVISIONS_PREFIX)
        existing["provisions"] = prov_num

    # Reserve : ne cree que si absent (cas critique du legacy Optipro)
    if not existing.get("reserve"):
        seq_res = await _next_seq(db, target_copro, RESERVE_PREFIX)
        res_num = _format_seq(RESERVE_PREFIX, seq_res, width=SEQ_WIDTH_NEW)
        await _ensure_account(db, target_copro, res_num,
                              f"Acompte de fonds de reserve appele - {display_name}", 4,
                              parent=RESERVE_PREFIX)
        existing["reserve"] = res_num

    accounts_map[target_copro] = existing
    await db.owners.update_one({"id": owner["id"]}, {"$set": {"tier_accounts": accounts_map}})
    owner["tier_accounts"] = accounts_map
    return owner


async def assign_supplier_account(db, supplier: dict, copro_id: Optional[str] = None) -> dict:
    """iter90is (Chinese Wall strict) : chaque fournisseur est LOCAL a UNE
    seule ACP. Le compte tier PCMN 44000XXX est stocke directement dans
    `supplier.tier_account_number` (plus de dict `tier_accounts`).

    Idempotent : si le supplier a deja un `tier_account_number`, on ne
    reassigne pas.
    """
    target_copro = copro_id or supplier.get("copropriete_id", "")
    if not target_copro:
        return supplier
    # iter90is : chinese wall - refuse d'assigner un compte a une ACP
    # differente de celle de la fiche (isolation totale).
    fiche_copro = supplier.get("copropriete_id", "")
    if fiche_copro and fiche_copro != target_copro:
        return supplier  # ne pas polluer d'autres ACPs avec cette fiche
    # Deja assigne ?
    existing_num = (supplier.get("tier_account_number") or "").strip()
    if existing_num:
        return supplier
    seq = await _next_seq(db, target_copro, "44000")
    num = _format_seq("44000", seq, width=3)
    name = (supplier.get("name") or "Fournisseur")[:40]
    await _ensure_account(db, target_copro, num, name.strip(), 4)
    await db.suppliers.update_one(
        {"id": supplier["id"]},
        {"$set": {"tier_account_number": num, "copropriete_id": target_copro}},
    )
    supplier["tier_account_number"] = num
    supplier["copropriete_id"] = target_copro
    return supplier


def get_owner_accounts(owner: dict, copro_id: str) -> dict:
    """Return {provisions, reserve} for the given owner+ACP, or empty dict."""
    return (owner.get("tier_accounts") or {}).get(copro_id, {})


def get_supplier_account(supplier: dict, copro_id: str) -> str:
    """iter90is (Chinese Wall strict) : retourne le compte tier
    fournisseur SI ET SEULEMENT SI le supplier appartient a `copro_id`
    (isolation totale entre ACPs).

    Lecture backward-compatible :
    1. Fiches modernes (`copropriete_id` renseigne) : refuse si != copro_id.
    2. Fiches legacy (`copropriete_id` vide, dict `tier_accounts` populate) :
       autorisees pour compat. Le nouveau code n'en creera plus.
    3. Privilegie `tier_account_number` (nouveau champ), fallback
       `tier_accounts[copro_id].main` pour data legacy non migree.
    """
    if not copro_id or not supplier:
        return ""
    fiche_copro = supplier.get("copropriete_id", "")
    if fiche_copro and fiche_copro != copro_id:
        return ""  # chinese wall - refuse l'acces cross-ACP (fiche moderne)
    # Priorite : nouveau champ (post-iter90is)
    num = (supplier.get("tier_account_number") or "").strip()
    if num:
        return num
    # Fallback legacy (dict tier_accounts) - progressif retrait
    return ((supplier.get("tier_accounts") or {}).get(copro_id, {}) or {}).get("main", "")


# --- iter90io : normalisation des comptes tier fournisseurs ---------------
# Format canonique PCMN : "44000XXX" (5 chars prefixe + 3 chiffres = 8).
# Le pipeline Optipro/CODA historique produisait parfois des comptes en
# 7 chars ("4400" + zfill(3) = "4400015") qui creaient des DOUBLONS dans
# le Bilan / Balance des Tiers avec le canonique 8 chars ("44000015").
# On normalise a l'entree du pipeline pour ne jamais persister de compte
# non-canonique.
def canonize_supplier_tier_account(number: str) -> str:
    """Normalise un compte tier fournisseur au format canonique 8 chars.

    Exemples :
      - "4400015"  (7 chars, legacy Optipro) -> "44000015"
      - "440015"   (6 chars)                 -> "44000015"
      - "44000015" (8 chars, deja canonique) -> "44000015"
      - "44001115" (8 chars mais non canonique) -> "44001115"
        (retourne tel quel, resolution deleguee au matching par fiche
        via `_resolve_third_party` : la fiche fournisseur portera le
        canonique reel et la ligne AN sera reecrite en aval)
      - "551000"   (non 440, bancaire ou autre) -> "551000" (inchange)
      - ""                                       -> ""
    """
    acc = (number or "").strip()
    if not acc.startswith("440"):
        return acc
    if len(acc) >= 8:
        return acc  # deja canonique OU non canonique (delegue au matching)
    # Insere des zeros entre "440" et le suffix jusqu'a atteindre 8 chars.
    # Cas typique : "4400015" (7) -> "44000015" (8).
    suffix = acc[3:]
    pad = 8 - 3 - len(suffix)
    if pad > 0:
        return "440" + ("0" * pad) + suffix
    return acc
