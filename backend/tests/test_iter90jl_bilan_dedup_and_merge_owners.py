"""iter90jl : 3 fixes bilan + fusion proprietaires.

Tests :
1. `_dedup_duplicate_auto_fi_entries` (read-time filter dans compute_bilan_data) :
   exclut les FI auto-generees qui doublonnent une FI importee ou manuelle.
2. `_fi_signature` : signature stable pour detection de doublon.
3. `compute_bilan_data` avec doublon FI : le compte 55XXXX affiche le solde
   REEL (une seule FI comptee, meme si 2 sont en DB).
4. `merge_owners` : dry-run + execution reelle fusionne 2 fiches en 1 et
   reecrit les lignes JE, les lots, les mutations.
5. Regression : la cible garde ses tier_accounts + union avec ceux des sources.
6. Verifie que la classification Actif/Passif place un owner sur UN SEUL cote
   selon son solde net (jamais des deux cotes).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test 1 : _fi_signature stable + tier accounts frozenset
# ---------------------------------------------------------------------------
def test_fi_signature_uses_copro_date_amount_and_tier_accounts():
    from routes.reports import _fi_signature
    je = {
        "copropriete_id": "ACP-1",
        "date": "2026-01-15",
        "total_debit": 500.00,
        "lines": [
            {"account_number": "55133100", "debit": 500, "credit": 0},
            {"account_number": "44000015", "debit": 0, "credit": 500},
        ],
    }
    sig = _fi_signature(je)
    assert sig[0] == "ACP-1"
    assert sig[1] == "2026-01-15"
    assert sig[2] == 500.00
    assert sig[3] == frozenset({"44000015"})  # seul le tier 440XXX compte


# ---------------------------------------------------------------------------
# Test 2 : _dedup filter exclut les auto en doublon d'un importe
# ---------------------------------------------------------------------------
def test_dedup_excludes_auto_duplicate_of_imported_fi():
    from routes.reports import _dedup_duplicate_auto_fi_entries
    imported = {
        "id": "je-imp-1", "journal_type": "FI",
        "copropriete_id": "ACP-1", "date": "2026-01-15", "total_debit": 500,
        "import_session_id": "sess-1", "auto_generated": False,
        "lines": [
            {"account_number": "55133100", "debit": 500, "credit": 0},
            {"account_number": "44000015", "debit": 0, "credit": 500},
        ],
    }
    auto_dup = {
        "id": "je-auto-1", "journal_type": "FI",
        "copropriete_id": "ACP-1", "date": "2026-01-15", "total_debit": 500,
        "auto_generated": True, "source_type": "bank_txn", "source_id": "txn-1",
        "lines": [
            {"account_number": "55133100", "debit": 500, "credit": 0},
            {"account_number": "44000015", "debit": 0, "credit": 500},
        ],
    }
    ac_invoice = {
        "id": "je-ac-1", "journal_type": "AC",  # non-FI : ne bouge pas
        "copropriete_id": "ACP-1", "date": "2026-01-10", "total_debit": 500,
        "lines": [
            {"account_number": "61500000", "debit": 500, "credit": 0},
            {"account_number": "44000015", "debit": 0, "credit": 500},
        ],
    }
    filtered = _dedup_duplicate_auto_fi_entries([imported, auto_dup, ac_invoice])
    kept_ids = {e["id"] for e in filtered}
    assert "je-imp-1" in kept_ids, "L'importe doit etre conserve (source of truth)"
    assert "je-ac-1" in kept_ids, "L'AC (facture) reste toujours"
    assert "je-auto-1" not in kept_ids, "L'auto en doublon doit etre exclu"


# ---------------------------------------------------------------------------
# Test 3 : _dedup laisse un FI auto SEUL (pas de doublon) tel quel
# ---------------------------------------------------------------------------
def test_dedup_keeps_auto_fi_when_no_imported_sibling():
    from routes.reports import _dedup_duplicate_auto_fi_entries
    auto_solo = {
        "id": "je-auto-solo", "journal_type": "FI",
        "copropriete_id": "ACP-1", "date": "2026-02-01", "total_debit": 300,
        "auto_generated": True,
        "lines": [
            {"account_number": "55133100", "debit": 300, "credit": 0},
            {"account_number": "44000022", "debit": 0, "credit": 300},
        ],
    }
    filtered = _dedup_duplicate_auto_fi_entries([auto_solo])
    assert len(filtered) == 1, "Un FI auto SANS sibling importe reste en place"
    assert filtered[0]["id"] == "je-auto-solo"


# ---------------------------------------------------------------------------
# Test 4 : compute_bilan_data - le solde bancaire ignore les FI auto doublons
# ---------------------------------------------------------------------------
def test_bilan_bank_balance_ignores_duplicate_auto_fi():
    """Cas end-to-end : Bilan doit voir 500€ debit sur bank (1 seule FI comptee)
    et non 1000€ (les 2 FI comptees en doublon)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import compute_bilan_data

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jl4-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jl-{suffix}",
                "bank_accounts": [{"iban": "BE04001952089331", "pcmn_number": "55133100", "is_default": True}],
            })
            # 1 seul paiement reel, mais 2 JE FI en DB (importe + auto en doublon)
            await db.journal_entries.insert_many([
                {
                    "id": f"je-imp-{suffix}", "journal_type": "FI",
                    "copropriete_id": acp, "date": "2026-03-01",
                    "reference": f"FI-IMP-{suffix}",
                    "total_debit": 500, "total_credit": 500,
                    "import_session_id": f"sess-{suffix}",
                    "auto_generated": False,
                    "lines": [
                        {"account_number": "44000015", "debit": 500, "credit": 0},
                        {"account_number": "55133100", "debit": 0, "credit": 500},
                    ],
                },
                {
                    "id": f"je-auto-{suffix}", "journal_type": "FI",
                    "copropriete_id": acp, "date": "2026-03-01",
                    "reference": f"FI-AUTO-{suffix[:8]}",
                    "total_debit": 500, "total_credit": 500,
                    "auto_generated": True, "source_type": "bank_txn",
                    "source_id": f"txn-{suffix}",
                    "lines": [
                        {"account_number": "44000015", "debit": 500, "credit": 0},
                        {"account_number": "55133100", "debit": 0, "credit": 500},
                    ],
                },
                # AN : compte 55133100 = 6598.53 (opening bank balance)
                {
                    "id": f"je-an-{suffix}", "journal_type": "AN",
                    "copropriete_id": acp, "date": "2026-01-01",
                    "is_opening_balance": True,
                    "total_debit": 6598.53, "total_credit": 6598.53,
                    "lines": [
                        {"account_number": "55133100", "debit": 6598.53, "credit": 0},
                        {"account_number": "13000000", "debit": 0, "credit": 6598.53},
                    ],
                },
            ])
            data = await compute_bilan_data(db, acp, date_to="2026-12-31")
            # Solde attendu bank : 6598.53 - 500 (une seule FI) = 6098.53
            # Sans le fix : 6598.53 - 1000 (deux FI) = 5598.53 -> mauvais
            actif_rubs = data.get("actif") or []
            bank_line = None
            for r in actif_rubs:
                for a in (r.get("accounts") or []):
                    if a.get("account_number") == "55133100":
                        bank_line = a
                        break
                if bank_line:
                    break
            assert bank_line is not None, f"Compte 55133100 doit apparaitre dans l'actif. actif={actif_rubs}"
            # Le solde reel = 6098.53 (pas 5598.53)
            assert abs(bank_line["amount"] - 6098.53) < 0.02, (
                f"Solde attendu 6098.53, vu {bank_line['amount']}. "
                f"Le filtre dedup FI auto en doublon n'a pas ete applique."
            )
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 5 : merge_owners fusionne 2 fiches en 1 (dry-run + execute)
# ---------------------------------------------------------------------------
def test_merge_owners_rewrites_je_and_lots_and_deletes_sources():
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from scripts.merge_owners import _apply_merge

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jl5-{suffix}"
        src_id = f"owner-src-{suffix}"
        tgt_id = f"owner-tgt-{suffix}"
        lot_id = f"lot-{suffix}"
        je_id = f"je-{suffix}"
        try:
            await db.owners.insert_many([
                {
                    "id": src_id, "name": "KASH - GOOVAERTS Paul & Maite",
                    "auxiliary_code": f"C{suffix[:4]}A", "copropriete_id": acp,
                    "tier_accounts": {acp: {"provisions": "41010002", "reserve": "41000002"}},
                    "lot_ids": [lot_id],
                },
                {
                    "id": tgt_id, "name": "KASH - GOOVAERTS Jean-Paul & Maite",
                    "auxiliary_code": f"C{suffix[:4]}B", "copropriete_id": acp,
                    "tier_accounts": {},
                    "lot_ids": [],
                },
            ])
            await db.lots.insert_one({
                "id": lot_id, "copropriete_id": acp, "code": "LOT-A",
                "owner_id": src_id, "owner_ids": [src_id],
            })
            await db.journal_entries.insert_one({
                "id": je_id, "copropriete_id": acp, "journal_type": "AC",
                "date": "2026-03-01", "total_debit": 100, "total_credit": 100,
                "lines": [
                    {"account_number": "41010002", "debit": 100, "credit": 0,
                     "third_party_id": src_id, "third_party_type": "owner"},
                    {"account_number": "70000000", "debit": 0, "credit": 100},
                ],
            })

            # DRY-RUN
            r_dry = await _apply_merge(
                db, [src_id], tgt_id,
                new_name="KASH - GOOVAERTS Jean-Paul & Maite",
                execute=False,
            )
            assert not r_dry["executed"]
            # Verif : rien change en DB
            src_still = await db.owners.find_one({"id": src_id})
            assert src_still is not None, "Dry-run ne doit RIEN modifier"

            # EXECUTE
            r_exec = await _apply_merge(
                db, [src_id], tgt_id,
                new_name="KASH - GOOVAERTS Jean-Paul & Maite",
                execute=True,
            )
            assert r_exec["executed"]
            assert r_exec["counts"]["journal_lines_rewritten"] == 1
            assert r_exec["counts"]["lots_owner_id_rewritten"] == 1
            assert r_exec["counts"]["sources_deleted"] == 1

            # Verifications post-execution
            src_gone = await db.owners.find_one({"id": src_id})
            assert src_gone is None, "Source doit etre supprimee"
            tgt = await db.owners.find_one({"id": tgt_id}, {"_id": 0})
            assert tgt["name"] == "KASH - GOOVAERTS Jean-Paul & Maite"
            # tier_accounts fusionnes
            assert tgt.get("tier_accounts", {}).get(acp, {}).get("provisions") == "41010002"
            # lot_ids fusionnes
            assert lot_id in (tgt.get("lot_ids") or [])
            # JE reecrit
            je_fresh = await db.journal_entries.find_one({"id": je_id}, {"_id": 0})
            tpids = {ln.get("third_party_id") for ln in je_fresh.get("lines", [])}
            assert tgt_id in tpids
            assert src_id not in tpids
            # Lot reecrit
            lot_fresh = await db.lots.find_one({"id": lot_id}, {"_id": 0})
            assert lot_fresh["owner_id"] == tgt_id
            assert tgt_id in (lot_fresh.get("owner_ids") or [])
            assert src_id not in (lot_fresh.get("owner_ids") or [])
        finally:
            await db.owners.delete_many({"id": {"$in": [src_id, tgt_id]}})
            await db.lots.delete_one({"id": lot_id})
            await db.journal_entries.delete_one({"id": je_id})

    _run(_go())


# ---------------------------------------------------------------------------
# Test 6 : Actif/Passif - un owner apparait sur UN SEUL cote (regression check)
# ---------------------------------------------------------------------------
def test_bilan_owner_appears_on_single_side_based_on_net_balance():
    """Verifie que quand le meme owner a debit sur 4101 (provisions) et
    credit sur 4100 (reserve), le net solde decide de son cote (Actif OU
    Passif), jamais des deux."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from routes.reports import compute_bilan_data

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-jl6-{suffix}"
        owner_id = f"owner-{suffix}"
        try:
            await db.coproprietes.insert_one({
                "id": acp, "name": f"iter90jl6-{suffix}",
                "bank_accounts": [{"iban": "BE1", "pcmn_number": "55000000", "is_default": True}],
            })
            await db.owners.insert_one({
                "id": owner_id,
                "name": "Test Owner Split",
                "copropriete_id": acp,
                "tier_accounts": {acp: {"provisions": "41010009", "reserve": "41000009"}},
            })
            # Provisions : 800 debit (owner doit 800)
            # Reserve : 500 credit (owner a un excedent de 500)
            # Solde net owner : +300 (debiteur) -> ACTIF
            await db.journal_entries.insert_many([
                {
                    "id": f"je-1-{suffix}", "journal_type": "AC",
                    "copropriete_id": acp, "date": "2026-02-01",
                    "total_debit": 800, "total_credit": 800,
                    "lines": [
                        {"account_number": "41010009", "debit": 800, "credit": 0,
                         "third_party_id": owner_id, "third_party_type": "owner"},
                        {"account_number": "70000000", "debit": 0, "credit": 800},
                    ],
                },
                {
                    "id": f"je-2-{suffix}", "journal_type": "AC",
                    "copropriete_id": acp, "date": "2026-02-01",
                    "total_debit": 500, "total_credit": 500,
                    "lines": [
                        {"account_number": "41000009", "debit": 0, "credit": 500,
                         "third_party_id": owner_id, "third_party_type": "owner"},
                        {"account_number": "70000000", "debit": 500, "credit": 0},
                    ],
                },
            ])
            data = await compute_bilan_data(db, acp, date_to="2026-12-31")
            # Cherche l'owner dans les 2 buckets
            actif_names = []
            for r in (data.get("actif") or []):
                for a in (r.get("accounts") or []):
                    actif_names.append(a.get("account_name"))
            passif_names = []
            for r in (data.get("passif") or []):
                for a in (r.get("accounts") or []):
                    passif_names.append(a.get("account_name"))
            in_actif = "Test Owner Split" in actif_names
            in_passif = "Test Owner Split" in passif_names
            assert not (in_actif and in_passif), (
                f"L'owner ne doit pas apparaitre des 2 cotes ! "
                f"actif={in_actif} passif={in_passif}"
            )
            # Solde net = 800 - 500 = 300 (debiteur) -> Actif
            assert in_actif, f"Solde net +300 doit etre en Actif. actif_names={actif_names}, passif_names={passif_names}"
            assert not in_passif
        finally:
            await db.coproprietes.delete_one({"id": acp})
            await db.owners.delete_one({"id": owner_id})
            await db.journal_entries.delete_many({"copropriete_id": acp})

    _run(_go())
