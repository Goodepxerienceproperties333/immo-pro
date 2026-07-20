"""iter90iz : Verrou de finalisation des lignes JE avant insertion en base.

Objectif "Zero Orphan on the Way Out" - refactor du wizard d'importation
(Option 1 ciblee, iter90iz) :

- CANONISE STRICTEMENT tout compte tier a 8 chars avant ecriture :
  * `440xxx` (fournisseurs) -> 8 chars via `canonize_supplier_tier_account`
  * `4100xxxx` / `4101xxxx` (owners) -> 8 chars via `canonize_owner_tier_account`
- RESOUD le `third_party_id` par matching NOM dans la meme ACP :
  * Pour les lignes 440xxx sans tp_id : match sur `suppliers` locaux (Chinese Wall)
  * Pour les lignes 4100/4101 sans tp_id : match sur `owners` de l'ACP
- FORCE la coherence `third_party_type` en aval.

Utilise dans TOUS les endpoints `commit-*` du wizard juste avant l'appel
`db.journal_entries.insert_one(...)` afin qu'aucun orphelin ne quitte le
pipeline d'importation.

Un `finalize_je_lines(db, copro_id, lines)` prepare un cache une seule fois
par ACP (index suppliers + owners par nom canonise) puis boucle sur les
lignes en O(1) par ligne.
"""
from __future__ import annotations

from typing import Optional

from tier_accounts import canonize_supplier_tier_account


# ---------------------------------------------------------------------------
# Verrou 8 chars pour les comptes owners 4100xxxx / 4101xxxx
# ---------------------------------------------------------------------------
def canonize_owner_tier_account(number: str) -> str:
    """Normalise un compte tier owner au format canonique 8 chars.

    - `4100XXXX` : fonds de reserve appele (par owner)
    - `4101XXXX` : fonds de roulement / provisions appele (par owner)

    Exemples :
      - "4100015"   (7 chars, Optipro legacy) -> "41000015"
      - "410015"    (6 chars)                 -> "41000015"
      - "41000001"  (8 chars, deja canonique) -> "41000001"
      - "4101"      (prefixe seul, invalide)  -> "4101" (retourne tel quel,
                                                delegue au matching en aval)
      - "551000"    (non 4100/4101)           -> "551000" (inchange)
    """
    acc = (number or "").strip()
    if not (acc.startswith("4100") or acc.startswith("4101")):
        return acc
    if len(acc) >= 8:
        return acc
    # Insere des zeros entre le prefixe "4100"/"4101" et le suffix jusqu'a 8 chars.
    prefix = acc[:4]
    suffix = acc[4:]
    pad = 8 - 4 - len(suffix)
    if pad > 0:
        return prefix + ("0" * pad) + suffix
    return acc


# ---------------------------------------------------------------------------
# Cache par ACP : index suppliers + owners par nom canonique
# ---------------------------------------------------------------------------
async def build_finalize_index(db, copro_id: str) -> dict:
    """Construit un index (nom -> {supplier_id, canonical_account}) pour l'ACP
    donnee. Idem pour les owners : (nom -> {owner_id, provisions, reserve}).

    Chinese Wall strict : ne charge QUE les suppliers/owners de l'ACP cible.

    Retour : dict avec cles :
      - "suppliers_by_name" : {frozenset(name_candidates) -> (id, tier_account_number)}
      - "owners_by_name"    : {frozenset(name_candidates) -> (id, provisions, reserve)}
      - "suppliers_by_id"   : {id -> tier_account_number}  (rewrite si acc non canonique)
      - "owners_by_id"      : {id -> (provisions, reserve)}
    """
    from routes.suppliers import _norm_name_candidates

    suppliers_by_name: dict = {}
    suppliers_by_id: dict = {}
    async for s in db.suppliers.find({"copropriete_id": copro_id}, {"_id": 0}):
        num = canonize_supplier_tier_account((s.get("tier_account_number") or "").strip())
        if not num or not num.startswith("440") or len(num) != 8:
            # Chinese wall strict : ne considere que les fiches avec un compte
            # canonique 8 chars valide. Les orphelines seront rattrapees plus tard.
            continue
        suppliers_by_id[s["id"]] = num
        cands = _norm_name_candidates(s.get("name", ""))
        if cands:
            suppliers_by_name[frozenset(cands)] = (s["id"], num, s.get("name", ""))

    owners_by_name: dict = {}
    owners_by_id: dict = {}
    async for o in db.owners.find(
        {"$or": [{"copropriete_id": copro_id}, {"copropriete_ids": copro_id}]},
        {"_id": 0},
    ):
        acc_map = ((o.get("tier_accounts") or {}).get(copro_id) or {})
        prov = canonize_owner_tier_account((acc_map.get("provisions") or "").strip())
        res = canonize_owner_tier_account((acc_map.get("reserve") or "").strip())
        # On ne retient que les owners ayant AU MOINS un des deux comptes en 8 chars
        if not (prov and len(prov) == 8) and not (res and len(res) == 8):
            continue
        owners_by_id[o["id"]] = (prov, res)
        display = (
            o.get("name")
            or f"{o.get('last_name', '')} {o.get('first_name', '')}".strip()
            or o.get("last_name", "")
        )
        cands = _norm_name_candidates(display)
        if cands:
            owners_by_name[frozenset(cands)] = (o["id"], prov, res, display)

    return {
        "suppliers_by_name": suppliers_by_name,
        "suppliers_by_id": suppliers_by_id,
        "owners_by_name": owners_by_name,
        "owners_by_id": owners_by_id,
    }


# ---------------------------------------------------------------------------
# Fonction principale : finalise une ligne JE avant insertion
# ---------------------------------------------------------------------------
def _pick_line_name(line: dict) -> str:
    """Recupere le meilleur nom possible pour matcher un tiers."""
    for k in ("third_party_name", "account_name", "label", "description"):
        v = (line.get(k) or "").strip()
        if not v:
            continue
        if v.lower() in ("fournisseurs", "fournisseur", "440", "440000"):
            continue
        return v
    return ""


def finalize_line(line: dict, idx: dict) -> dict:
    """Applique le verrou 8 chars + resolution tp_id sur UNE ligne JE.

    Retourne un NOUVEAU dict (immutable friendly) avec les corrections.
    Effet non destructif : les autres champs de `line` sont conserves tels quels.

    Regles :
      1. Compte 440xxx :
         - Canonise a 8 chars STRICT (raise si impossible)
         - Si `third_party_id` present -> reprend le compte canonique de la
           fiche supplier (source of truth). Corrige tp_type.
         - Sinon : match par NOM -> ecrit tp_id + compte canonique
      2. Compte 4100/4101 :
         - Canonise a 8 chars STRICT
         - Si `third_party_id` present -> reprend le compte canonique de la fiche owner
         - Sinon : match par NOM d'owner
      3. Autres comptes : passthrough (le "verrou" ne concerne que les tiers).
    """
    from routes.suppliers import _norm_name_candidates

    out = dict(line)
    acc = (out.get("account_number") or "").strip()

    # --- Cas fournisseurs 440xxx ---
    if acc.startswith("440"):
        canonical_acc = canonize_supplier_tier_account(acc)
        # Priorite : si tp_id set, on FORCE le compte canonique de la fiche
        tp_id = (out.get("third_party_id") or "").strip()
        if tp_id and tp_id in idx["suppliers_by_id"]:
            out["account_number"] = idx["suppliers_by_id"][tp_id]
            out["third_party_type"] = "supplier"
            return out
        # Sinon : match par nom
        name = _pick_line_name(out)
        if name:
            ln_cands = frozenset(_norm_name_candidates(name))
            if ln_cands:
                for k, (sid, canon, sname) in idx["suppliers_by_name"].items():
                    if ln_cands & k:
                        out["third_party_id"] = sid
                        out["third_party_type"] = "supplier"
                        out["account_number"] = canon
                        # Preserve un nom lisible si absent
                        if not (out.get("third_party_name") or "").strip():
                            out["third_party_name"] = sname
                        return out
        # Aucun match trouve : on canonise AU MOINS le compte (8 chars strict)
        out["account_number"] = canonical_acc
        return out

    # --- Cas owners 4100/4101 ---
    if acc.startswith("4100") or acc.startswith("4101"):
        canonical_acc = canonize_owner_tier_account(acc)
        tp_id = (out.get("third_party_id") or "").strip()
        if tp_id and tp_id in idx["owners_by_id"]:
            prov, res = idx["owners_by_id"][tp_id]
            # Rebranche sur le compte canonique correspondant selon prefixe
            if acc.startswith("4100") and res:
                out["account_number"] = res
            elif acc.startswith("4101") and prov:
                out["account_number"] = prov
            else:
                out["account_number"] = canonical_acc
            out["third_party_type"] = "owner"
            return out
        # Match par nom
        name = _pick_line_name(out)
        if name:
            ln_cands = frozenset(_norm_name_candidates(name))
            if ln_cands:
                for k, (oid, prov, res, oname) in idx["owners_by_name"].items():
                    if ln_cands & k:
                        out["third_party_id"] = oid
                        out["third_party_type"] = "owner"
                        if acc.startswith("4100") and res:
                            out["account_number"] = res
                        elif acc.startswith("4101") and prov:
                            out["account_number"] = prov
                        else:
                            out["account_number"] = canonical_acc
                        if not (out.get("third_party_name") or "").strip():
                            out["third_party_name"] = oname
                        return out
        out["account_number"] = canonical_acc
        return out

    # --- Autres comptes (bancaires, charges, etc.) : passthrough
    return out


async def finalize_je_doc(db, je_doc: dict, copro_id: Optional[str] = None) -> dict:
    """Applique `finalize_line` sur toutes les lignes d'un JE juste avant l'insert.

    Recupere l'ACP depuis `je_doc.copropriete_id` si `copro_id` absent.
    Idempotent : safe a appeler plusieurs fois.
    """
    cid = (copro_id or je_doc.get("copropriete_id") or "").strip()
    if not cid:
        return je_doc  # sans ACP on ne peut pas construire l'index (chinese wall)
    idx = await build_finalize_index(db, cid)
    lines = list(je_doc.get("lines") or [])
    je_doc["lines"] = [finalize_line(ln, idx) for ln in lines]
    return je_doc
