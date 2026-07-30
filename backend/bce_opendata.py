"""iter90iq : Ingestion et recherche du dataset BCE Open Data (KBO/CBE).

Contexte : le module `bce_lookup` fait du scraping live sur KBO Public
Search. C'est gratuit mais fragile (dependance au HTML) et lent (~500ms
par requete). Le dataset officiel Open Data (CSV mensuel, gratuit apres
inscription simple sur kbopub.economie.fgov.be/kbo-open-data/signup)
contient ~2M entreprises actives et permet une recherche locale
instantanee et robuste.

Ce module :
- Ingere le ZIP officiel BCE Open Data (`ingest_bce_opendata_zip`).
- Extrait les champs necessaires (BCE, denominations, adresse siege
  social, statut actif) pour la recherche par nom.
- Persiste en collection Mongo `bce_opendata` avec indexes optimises.
- Expose `search_bce_opendata` qui retourne des candidats au meme
  format que `bce_lookup.search_kbo_by_name` (drop-in).

Structure du ZIP officiel :
- `enterprise.csv` : EnterpriseNumber, Status ("AC"=Actif), StartDate...
- `denomination.csv` : EntityNumber, Language, TypeOfDenomination
  (001=raison sociale, 002=abregee, 003=commerciale), Denomination
- `address.csv` : EntityNumber, TypeOfAddress (002=siege), Zipcode,
  MunicipalityFR/NL, StreetFR/NL, HouseNumber
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

# Reutilise les stopwords + normalisation depuis le module partage
# `bce_shared` (iter93bk) pour supprimer le cycle circulaire avec bce_lookup.
from bce_shared import _norm, _tokens, token_similarity  # noqa: F401 (utilise par search)

BATCH_SIZE = 2000  # taille des insert_many
COLLECTION = "bce_opendata"

# Code de dénomination pour raison sociale officielle.
_DENOM_OFFICIAL = "001"
# Code de type d'adresse pour siege social.
_ADDR_REGISTERED_OFFICE = "002"

_BCE_DOT_RE = re.compile(r"^\d{4}\.\d{3}\.\d{3}$")
_ONLY_DIGITS_RE = re.compile(r"[^0-9]")


def _format_bce(raw: str) -> str:
    """Convertit un numero BCE brut ("0403201185") en format canonique
    "0403.201.185". Passe inchange si deja au bon format. Retourne ""
    si moins de 10 chiffres."""
    if not raw:
        return ""
    if _BCE_DOT_RE.match(raw):
        return raw
    digits = _ONLY_DIGITS_RE.sub("", raw)
    if len(digits) != 10:
        return ""
    return f"{digits[0:4]}.{digits[4:7]}.{digits[7:10]}"


def _raw_bce(bce: str) -> str:
    """Extrait les 10 chiffres du BCE (sans points, sans espaces)."""
    return _ONLY_DIGITS_RE.sub("", bce or "")


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def _iter_csv_rows(zf: zipfile.ZipFile, filename: str) -> AsyncIterator[dict]:
    """Iterate CSV rows lazily from a ZIP entry (streaming, no full RAM load)."""
    try:
        with zf.open(filename, "r") as fh:
            reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
            for row in reader:
                yield row
    except KeyError:
        return  # fichier absent du ZIP - on laisse la logique appelante decider


def parse_bce_opendata_zip(zip_bytes: bytes) -> list[dict]:
    """Parse un ZIP BCE Open Data et retourne la liste des entreprises
    consolidees pour ingestion.

    Retourne des dicts {bce, bce_raw, name, all_names, all_names_norm,
    city, zipcode, address, active}.
    """
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    try:
        # 1. enterprise.csv : status actif ?
        active_set: set[str] = set()
        for row in _iter_csv_rows(zf, "enterprise.csv"):
            ent_num = (row.get("EnterpriseNumber") or "").strip()
            status = (row.get("Status") or "").strip().upper()
            if ent_num and status == "AC":
                active_set.add(ent_num)
        # 2. denomination.csv : consolide les noms par entite.
        # Un entite peut avoir plusieurs denominations (officielle,
        # commerciale, abregee) — on les collecte toutes.
        denoms_by_ent: dict[str, dict] = {}
        for row in _iter_csv_rows(zf, "denomination.csv"):
            ent_num = (row.get("EntityNumber") or "").strip()
            if not ent_num:
                continue
            type_denom = (row.get("TypeOfDenomination") or "").strip()
            denom = (row.get("Denomination") or "").strip()
            if not denom:
                continue
            entry = denoms_by_ent.setdefault(ent_num, {"official": "", "all": []})
            if type_denom == _DENOM_OFFICIAL and not entry["official"]:
                entry["official"] = denom
            if denom not in entry["all"]:
                entry["all"].append(denom)
        # 3. address.csv : siege social par entite.
        addr_by_ent: dict[str, dict] = {}
        for row in _iter_csv_rows(zf, "address.csv"):
            ent_num = (row.get("EntityNumber") or "").strip()
            if not ent_num or ent_num in addr_by_ent:
                continue
            type_addr = (row.get("TypeOfAddress") or "").strip()
            if type_addr and type_addr != _ADDR_REGISTERED_OFFICE:
                continue
            zipcode = (row.get("Zipcode") or "").strip()
            city = (row.get("MunicipalityFR") or row.get("MunicipalityNL") or "").strip()
            street = (row.get("StreetFR") or row.get("StreetNL") or "").strip()
            house = (row.get("HouseNumber") or "").strip()
            addr_by_ent[ent_num] = {
                "zipcode": zipcode,
                "city": city,
                "address": " ".join(x for x in [street, house, zipcode, city] if x),
            }
        # 4. Consolidation
        result: list[dict] = []
        for ent_num, denoms in denoms_by_ent.items():
            # BCE au format 0XXX.XXX.XXX (le CSV BCE utilise deja des .)
            bce_formatted = _format_bce(ent_num)
            if not bce_formatted:
                continue
            all_names = denoms["all"] or []
            official_name = denoms["official"] or (all_names[0] if all_names else "")
            if not official_name:
                continue
            addr = addr_by_ent.get(ent_num, {})
            result.append({
                "bce": bce_formatted,
                "bce_raw": _raw_bce(bce_formatted),
                "name": official_name,
                "all_names": all_names,
                "all_names_norm": list({_norm(n) for n in all_names if n}),
                "city": addr.get("city", ""),
                "zipcode": addr.get("zipcode", ""),
                "address": addr.get("address", ""),
                "active": ent_num in active_set,
            })
        return result
    finally:
        zf.close()


async def ensure_bce_opendata_indexes(db) -> None:
    """Cree les indexes MongoDB necessaires (idempotent)."""
    coll = db[COLLECTION]
    await coll.create_index("bce_raw", unique=True, name="uq_bce_opendata_bce")
    await coll.create_index("all_names_norm", name="idx_bce_opendata_names")
    # Text index sur name + all_names pour la recherche full-text.
    try:
        await coll.create_index(
            [("name", "text"), ("all_names", "text")],
            name="txt_bce_opendata_names",
            default_language="french",
        )
    except Exception:
        pass  # index text peut echouer si un autre text index existe deja


async def ingest_bce_opendata_zip(db, zip_bytes: bytes) -> dict:
    """Ingere un ZIP BCE Open Data en base. Retourne un rapport
    {parsed, inserted, updated, took_ms, snapshot_at}."""
    started_at = datetime.now(timezone.utc)
    entities = parse_bce_opendata_zip(zip_bytes)
    await ensure_bce_opendata_indexes(db)
    coll = db[COLLECTION]
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    updated = 0
    # Upsert par batch avec bulk_write.
    from pymongo import UpdateOne
    batch: list[UpdateOne] = []
    for ent in entities:
        ent["updated_at"] = now
        batch.append(UpdateOne(
            {"bce_raw": ent["bce_raw"]},
            {"$set": ent},
            upsert=True,
        ))
        if len(batch) >= BATCH_SIZE:
            res = await coll.bulk_write(batch, ordered=False)
            inserted += res.upserted_count
            updated += res.modified_count
            batch = []
    if batch:
        res = await coll.bulk_write(batch, ordered=False)
        inserted += res.upserted_count
        updated += res.modified_count
    took_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    # Enregistre une trace de la derniere ingestion.
    await db.bce_opendata_meta.update_one(
        {"_id": "last_ingest"},
        {"$set": {
            "at": now,
            "parsed": len(entities),
            "inserted": inserted,
            "updated": updated,
            "took_ms": took_ms,
        }},
        upsert=True,
    )
    return {
        "parsed": len(entities),
        "inserted": inserted,
        "updated": updated,
        "took_ms": took_ms,
        "snapshot_at": now,
    }


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------
async def search_bce_opendata(
    db,
    query: str,
    postal_code: str = "",
    top_n: int = 3,
) -> list[dict]:
    """Cherche des candidats dans le dataset local BCE Open Data.

    Retour compatible avec `bce_lookup.search_kbo_by_name` :
      [{bce, name, address, entity_type, similarity}, ...]
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    # 1. Full-text search (rapide, index MongoDB natif).
    q_tokens = _tokens(q)
    coll = db[COLLECTION]
    docs: list[dict] = []
    if q_tokens:
        try:
            cursor = coll.find(
                {"$text": {"$search": " ".join(q_tokens)}, "active": True},
                {"_id": 0, "score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"})]).limit(50)
            async for d in cursor:
                docs.append(d)
        except Exception:
            docs = []
    # 2. Fallback regex insensible casse/accents sur name_norm si $text ne
    #    renvoie rien (index text absent ou dictionnaire de langue different).
    if not docs and q_tokens:
        # Cherche les documents qui partagent au moins un token significatif.
        cursor = coll.find(
            {"all_names_norm": {"$in": list(q_tokens)}, "active": True},
            {"_id": 0},
        ).limit(50)
        async for d in cursor:
            docs.append(d)
    if not docs:
        return []
    # 3. Filtre postal_code (soft) si fourni.
    if postal_code:
        pc = postal_code.strip()
        filtered = [d for d in docs if d.get("zipcode", "") == pc]
        if filtered:
            docs = filtered
    # 4. Score Jaccard par tokens + sort desc.
    scored: list[dict] = []
    for d in docs:
        # Prend le meilleur score parmi tous les noms de l'entite.
        best_sim = 0.0
        best_name = d.get("name", "")
        for name in ([d.get("name", "")] + list(d.get("all_names") or [])):
            if not name:
                continue
            sim = token_similarity(q, name)
            if sim > best_sim:
                best_sim = sim
                best_name = name
        scored.append({
            "bce": d.get("bce", ""),
            "name": d.get("name", ""),
            "address": d.get("address", ""),
            "entity_type": "ENT Actif" if d.get("active") else "ENT Inactif",
            "similarity": round(best_sim, 3),
            "_best_name_len": len(_tokens(best_name)),
        })
    # Tri : similarite desc -> nb tokens du meilleur nom (proche du query
    # = mieux) -> longueur du nom officiel (le plus court gagne).
    q_len = len(_tokens(q)) or 1
    scored.sort(key=lambda x: (
        -x["similarity"],
        abs(x["_best_name_len"] - q_len),
        len(x["name"]),
    ))
    # Nettoie les champs internes avant retour.
    for s in scored:
        s.pop("_best_name_len", None)
    return scored[:top_n]


async def opendata_status(db) -> dict:
    """Retourne un resume de l'etat du dataset local (nb entites, date
    de derniere ingestion)."""
    total = await db[COLLECTION].estimated_document_count()
    last = await db.bce_opendata_meta.find_one({"_id": "last_ingest"})
    if last:
        last.pop("_id", None)
    return {
        "total_entities": total,
        "last_ingest": last or None,
        "enabled": total > 0,
    }
