"""
iter90cs : REGRESSION LOCK - Endpoint rebuild distribution key.

Bug rapporte utilisateur (Feb 2026) sur PROD ACP Acacia :
> "bouton 'Reconstruire la cle' dans la page LotsPage / DistributionKeysPage
> qui detecte automatiquement les entrees phantom et propose de les remplacer
> par les vrais lots (par ordre alphabetique du numero, ou en preservant la
> quotite globale). Cela nettoierait la cle Acacia definitivement au lieu
> de dependre du fallback runtime."

Fix iter90cs : POST /api/distribution-keys/{key_id}/rebuild
Modes :
- quotity (defaut) : rebuild complet - remplace tous les lots de la cle
  par les lots actuels de l'ACP avec share=quotity.
- match_by_number : preserve les shares originales des phantoms qui ont
  un lot_number matchant un lot actuel. Ajoute les lots manquants
  avec share=quotity.
- clean_only : supprime uniquement les phantoms, garde le reste tel quel.

Tests couvrent : dry_run, commit, mode=quotity, mode=match_by_number,
mode=clean_only, key sans phantom (regression).
"""
import asyncio
import os
import sys
import uuid
import httpx
import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BACKEND_URL = "http://localhost:8001"
_TOKEN_CACHE = {"token": None}


async def _login(client):
    if _TOKEN_CACHE["token"]:
        return {"Authorization": f"Bearer {_TOKEN_CACHE['token']}"}
    resp = await client.post(f"{BACKEND_URL}/api/auth/login", json={
        "email": "admin@copro.be", "password": "admin123",
    })
    resp.raise_for_status()
    tok = resp.json().get("access_token") or resp.json().get("token")
    _TOKEN_CACHE["token"] = tok
    return {"Authorization": f"Bearer {tok}"}


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _setup(prefix: str, key_lots_config: list):
    """Cree ACP + 3 lots Matexi + cle configurable.

    key_lots_config : liste de dict {lot_ref, share, is_phantom, lot_number}.
    """
    db = await _mongo()
    cid = f"{prefix}-{uuid.uuid4()}"
    matexi_id = f"matexi-{uuid.uuid4()}"

    await db.coproprietes.insert_one({
        "id": cid, "name": prefix, "reference": prefix[:15], "status": "active",
    })
    await db.owners.insert_one({
        "id": matexi_id, "name": "Matexi", "last_name": "Matexi",
        "auxiliary_code": "M001", "copropriete_ids": [cid],
    })

    L1_id = f"lot-{uuid.uuid4()}"
    L2_id = f"lot-{uuid.uuid4()}"
    L3_id = f"lot-{uuid.uuid4()}"
    await db.lots.insert_many([
        {"id": L1_id, "number": "001", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3000.0},
        {"id": L2_id, "number": "002", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
        {"id": L3_id, "number": "003", "owner_id": matexi_id,
         "owner_ids": [matexi_id], "copropriete_id": cid, "quotity": 3500.0},
    ])

    key_id = str(uuid.uuid4())
    real_lots = {"L1": (L1_id, "001"), "L2": (L2_id, "002"), "L3": (L3_id, "003")}
    key_lots = []
    for cfg in key_lots_config:
        if cfg.get("is_phantom"):
            key_lots.append({
                "lot_id": f"phantom-{uuid.uuid4()}",
                "share": cfg["share"],
                "lot_number": cfg.get("lot_number", ""),
            })
        else:
            lid, num = real_lots[cfg["lot_ref"]]
            key_lots.append({
                "lot_id": lid, "share": cfg["share"], "lot_number": num,
            })
    await db.distribution_keys.insert_one({
        "id": key_id, "copropriete_id": cid, "name": "Test key",
        "is_default": True, "key_type": "quotity", "lots": key_lots,
    })

    return {
        "db": db, "cid": cid, "key_id": key_id,
        "matexi": matexi_id,
        "L1": L1_id, "L2": L2_id, "L3": L3_id,
    }


async def _cleanup(ctx):
    db = ctx["db"]
    cid = ctx["cid"]
    await db.coproprietes.delete_one({"id": cid})
    await db.lots.delete_many({"copropriete_id": cid})
    await db.distribution_keys.delete_many({"copropriete_id": cid})
    await db.owners.delete_one({"id": ctx["matexi"]})


# =========================================================================
# SCENARIO 1 : mode=quotity dry_run -> rapport correct
# =========================================================================
async def _scenario_quotity_dry_run():
    ctx = await _setup("iter90cs-q-dry", [
        {"lot_ref": "L1", "share": 3000.0, "is_phantom": False},
        {"share": 3500.0, "is_phantom": True, "lot_number": "002"},
        {"share": 3500.0, "is_phantom": True, "lot_number": "003"},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/{ctx['key_id']}/rebuild",
                headers=hdr, json={"mode": "quotity", "dry_run": True},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["dry_run"] is True
            assert data["before"]["phantom_count"] == 2
            assert data["before"]["valid_count"] == 1
            assert data["after"]["entries_total"] == 3  # 3 lots actuels
            assert data["after"]["total_share"] == 10000.0  # 3000+3500+3500

            # Verifie que la cle N'A PAS ete modifiee (dry_run)
            key = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["key_id"]}, {"_id": 0},
            )
            assert len(key["lots"]) == 3
            phantom_still = [kl for kl in key["lots"]
                             if kl["lot_id"].startswith("phantom-")]
            assert len(phantom_still) == 2, "dry_run devrait preserver la cle"
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 2 : mode=quotity commit -> rebuild complet
# =========================================================================
async def _scenario_quotity_commit():
    ctx = await _setup("iter90cs-q-com", [
        {"share": 3000.0, "is_phantom": True, "lot_number": "001"},
        {"share": 3500.0, "is_phantom": True, "lot_number": "002"},
        {"share": 3500.0, "is_phantom": True, "lot_number": "003"},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/{ctx['key_id']}/rebuild",
                headers=hdr, json={"mode": "quotity", "dry_run": False},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["applied"] is True
            assert data["stats"]["current_lots_added"] == 3
            assert data["stats"]["phantom_removed"] == 3

            # Verifie que la cle est reconstruite avec les vrais lot_ids
            key = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["key_id"]}, {"_id": 0},
            )
            actual_lot_ids = {kl["lot_id"] for kl in key["lots"]}
            expected_ids = {ctx["L1"], ctx["L2"], ctx["L3"]}
            assert actual_lot_ids == expected_ids, (
                f"Cle devrait contenir les 3 vrais lot_ids. "
                f"Obtenu : {actual_lot_ids}"
            )
            # Verifie que les quotites sont utilisees comme share
            shares_by_num = {kl["lot_number"]: kl["share"] for kl in key["lots"]}
            assert shares_by_num["001"] == 3000.0
            assert shares_by_num["002"] == 3500.0
            assert shares_by_num["003"] == 3500.0
            assert key.get("iter90cs_rebuilt_at") is not None
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 3 : mode=match_by_number -> preserve les shares originales
# =========================================================================
async def _scenario_match_by_number():
    ctx = await _setup("iter90cs-match", [
        {"share": 2500.0, "is_phantom": True, "lot_number": "001"},  # match L1
        {"share": 4000.0, "is_phantom": True, "lot_number": "002"},  # match L2
        {"share": 3500.0, "is_phantom": True, "lot_number": "999"},  # NO match
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/{ctx['key_id']}/rebuild",
                headers=hdr, json={"mode": "match_by_number", "dry_run": False},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["stats"]["phantom_matched_by_number"] == 2  # L1 + L2
            assert data["stats"]["phantom_removed"] == 1  # 999 non-match
            assert data["stats"]["current_lots_added"] == 1  # L3 ajoute par quotite

            key = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["key_id"]}, {"_id": 0},
            )
            shares_by_id = {kl["lot_id"]: kl["share"] for kl in key["lots"]}
            # L1 et L2 gardent les shares originales des phantoms
            assert shares_by_id.get(ctx["L1"]) == 2500.0, (
                f"L1 devrait avoir 2500 (share matchee). Obtenu : {shares_by_id.get(ctx['L1'])}"
            )
            assert shares_by_id.get(ctx["L2"]) == 4000.0
            # L3 recoit sa quotity (3500) car aucun phantom ne matchait
            assert shares_by_id.get(ctx["L3"]) == 3500.0
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 4 : mode=clean_only -> supprime phantoms sans rien ajouter
# =========================================================================
async def _scenario_clean_only():
    ctx = await _setup("iter90cs-clean", [
        {"lot_ref": "L1", "share": 3000.0, "is_phantom": False},
        {"share": 3500.0, "is_phantom": True, "lot_number": "002"},
        {"lot_ref": "L3", "share": 3500.0, "is_phantom": False},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/{ctx['key_id']}/rebuild",
                headers=hdr, json={"mode": "clean_only", "dry_run": False},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["stats"]["phantom_removed"] == 1
            assert data["stats"]["existing_preserved"] == 2
            assert data["stats"]["current_lots_added"] == 0

            key = await ctx["db"].distribution_keys.find_one(
                {"id": ctx["key_id"]}, {"_id": 0},
            )
            actual_lot_ids = {kl["lot_id"] for kl in key["lots"]}
            # L1 + L3 preserves, L2 phantom supprime, RIEN ajoute (L2 reste absent)
            assert actual_lot_ids == {ctx["L1"], ctx["L3"]}
    finally:
        await _cleanup(ctx)


# =========================================================================
# SCENARIO 5 : Cle deja propre - regression, mode quotity remplace quand meme
# =========================================================================
async def _scenario_no_phantom_regression():
    ctx = await _setup("iter90cs-clean-key", [
        {"lot_ref": "L1", "share": 3000.0, "is_phantom": False},
        {"lot_ref": "L2", "share": 3500.0, "is_phantom": False},
        {"lot_ref": "L3", "share": 3500.0, "is_phantom": False},
    ])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            hdr = await _login(client)
            r = await client.post(
                f"{BACKEND_URL}/api/distribution-keys/{ctx['key_id']}/rebuild",
                headers=hdr, json={"mode": "clean_only", "dry_run": True},
            )
            assert r.status_code == 200
            data = r.json()
            # Cle sans phantom + clean_only -> rien a faire
            assert data["stats"]["phantom_removed"] == 0
            assert data["stats"]["existing_preserved"] == 3
    finally:
        await _cleanup(ctx)


# ============================ Tests entry points ==========================
def test_rebuild_quotity_dry_run_preserves_key():
    asyncio.run(_scenario_quotity_dry_run())


def test_rebuild_quotity_commit_replaces_all():
    asyncio.run(_scenario_quotity_commit())


def test_rebuild_match_by_number_preserves_shares():
    asyncio.run(_scenario_match_by_number())


def test_rebuild_clean_only_removes_phantoms_only():
    asyncio.run(_scenario_clean_only())


def test_rebuild_on_clean_key_no_op():
    asyncio.run(_scenario_no_phantom_regression())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
