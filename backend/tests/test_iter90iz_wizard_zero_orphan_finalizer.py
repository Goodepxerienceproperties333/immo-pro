"""iter90iz : Verrou "Zero Orphan on the Way Out" - Refactor wizard.

Verrouille que le pipeline d'importation :
- Canonise STRICTEMENT tout compte tier a 8 chars avant persistence :
  * `440xxx`   -> 8 chars via `canonize_supplier_tier_account`
  * `4100xxxx` / `4101xxxx` -> 8 chars via `canonize_owner_tier_account`
- Resout le `third_party_id` par matching NOM dans la meme ACP (Chinese Wall)
- Ne peut pas laisser passer une ligne 440/4100/4101 non canonique en base.
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
# Verrou 8 chars pour owners 4100/4101
# ---------------------------------------------------------------------------
def test_canonize_owner_tier_account_pads_to_8_chars():
    """iter90iz-1 : verifier que le canonize owner pad les 7-char en 8-char."""
    from import_finalizer import canonize_owner_tier_account

    assert canonize_owner_tier_account("4100015") == "41000015", (
        "'4100015' (7 chars) doit devenir '41000015' (8 chars)"
    )
    assert canonize_owner_tier_account("4101015") == "41010015"
    assert canonize_owner_tier_account("410015") == "41000015"  # 6 chars
    assert canonize_owner_tier_account("41000001") == "41000001"  # deja 8
    assert canonize_owner_tier_account("41010001") == "41010001"
    # Non 4100/4101 = passthrough
    assert canonize_owner_tier_account("44000015") == "44000015"
    assert canonize_owner_tier_account("551000") == "551000"
    assert canonize_owner_tier_account("") == ""


# ---------------------------------------------------------------------------
# finalize_line : compte 440 sans tp_id -> resolution par nom + canonique 8
# ---------------------------------------------------------------------------
def test_finalize_line_resolves_supplier_by_name_and_canonizes_to_8chars():
    """iter90iz-2 : une ligne 440xxx en 7 chars sans tp_id est corrigee :
    - compte reecrit en 8 chars canonique de la fiche
    - third_party_id = supplier.id
    - third_party_type = 'supplier'
    """
    from import_finalizer import finalize_line

    idx = {
        "suppliers_by_id": {"sup-123": "44000042"},
        "suppliers_by_name": {
            frozenset({"engie"}): ("sup-123", "44000042", "Engie SA"),
        },
        "owners_by_id": {},
        "owners_by_name": {},
    }
    line_in = {
        "account_number": "4400015",  # 7 chars
        "account_name": "Engie",
        "debit": 100.0, "credit": 0.0,
    }
    out = finalize_line(line_in, idx)
    assert out["account_number"] == "44000042", (
        f"Le compte devait etre re-ecrit avec le canonique de la fiche (44000042), "
        f"vu {out['account_number']}"
    )
    assert out["third_party_id"] == "sup-123"
    assert out["third_party_type"] == "supplier"
    # L'input n'est pas mute
    assert line_in["account_number"] == "4400015"


# ---------------------------------------------------------------------------
# finalize_line : compte 440 SANS match ni tp_id -> canonise a 8 chars AU MOINS
# ---------------------------------------------------------------------------
def test_finalize_line_canonizes_even_without_supplier_match():
    """iter90iz-3 : une ligne 440xxx en 7 chars, sans nom exploitable et sans
    tp_id, doit AU MOINS etre canonisee a 8 chars (verrou non-negociable)."""
    from import_finalizer import finalize_line

    idx = {
        "suppliers_by_id": {},
        "suppliers_by_name": {},
        "owners_by_id": {},
        "owners_by_name": {},
    }
    line_in = {
        "account_number": "4400015",
        "account_name": "",  # pas de nom exploitable
        "debit": 100.0, "credit": 0.0,
    }
    out = finalize_line(line_in, idx)
    assert out["account_number"] == "44000015", (
        f"Meme sans match, le compte doit etre canonise 8 chars. "
        f"Vu {out['account_number']}"
    )
    assert "third_party_id" not in out or not out.get("third_party_id")


# ---------------------------------------------------------------------------
# finalize_line : owner 4100 sans tp_id -> match par nom + compte reserve 8 chars
# ---------------------------------------------------------------------------
def test_finalize_line_resolves_owner_by_name_reserve_account():
    """iter90iz-4 : une ligne 4100 (reserve) avec le nom d'un owner doit
    etre reecrite avec l'ID + le compte reserve canonique de l'owner."""
    from import_finalizer import finalize_line

    idx = {
        "suppliers_by_id": {},
        "suppliers_by_name": {},
        "owners_by_id": {"own-42": ("41010007", "41000007")},
        "owners_by_name": {
            frozenset({"dupont"}): ("own-42", "41010007", "41000007", "Dupont"),
        },
    }
    line_in = {
        "account_number": "4100015",  # 7 chars, prefix reserve
        "account_name": "Dupont",
        "debit": 200.0, "credit": 0.0,
    }
    out = finalize_line(line_in, idx)
    # Compte reserve = 4100XXXX -> reecrit avec res=41000007 de la fiche
    assert out["account_number"] == "41000007", out
    assert out["third_party_id"] == "own-42"
    assert out["third_party_type"] == "owner"


def test_finalize_line_resolves_owner_by_name_provisions_account():
    """iter90iz-5 : idem pour provisions (4101)."""
    from import_finalizer import finalize_line

    idx = {
        "suppliers_by_id": {},
        "suppliers_by_name": {},
        "owners_by_id": {"own-42": ("41010007", "41000007")},
        "owners_by_name": {
            frozenset({"dupont"}): ("own-42", "41010007", "41000007", "Dupont"),
        },
    }
    line_in = {
        "account_number": "4101015",  # 7 chars, prefix provisions
        "account_name": "Dupont",
        "debit": 0.0, "credit": 500.0,
    }
    out = finalize_line(line_in, idx)
    assert out["account_number"] == "41010007", out
    assert out["third_party_id"] == "own-42"


# ---------------------------------------------------------------------------
# Chinese Wall strict : ne matche PAS un supplier d'une autre ACP
# ---------------------------------------------------------------------------
def test_build_finalize_index_respects_chinese_wall():
    """iter90iz-6 : deux ACPs avec chacune un supplier 'Engie'. L'index
    construit pour ACP-A ne doit contenir QUE le supplier de ACP-A."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from import_finalizer import build_finalize_index

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp_a = f"acp-iz-a-{suffix}"
        acp_b = f"acp-iz-b-{suffix}"
        s_a = f"sup-a-{suffix}"
        s_b = f"sup-b-{suffix}"
        try:
            await db.suppliers.insert_many([
                {"id": s_a, "name": f"Engie-{suffix}", "copropriete_id": acp_a,
                 "tier_account_number": "44000001"},
                {"id": s_b, "name": f"Engie-{suffix}", "copropriete_id": acp_b,
                 "tier_account_number": "44000009"},
            ])
            idx_a = await build_finalize_index(db, acp_a)
            assert s_a in idx_a["suppliers_by_id"]
            assert s_b not in idx_a["suppliers_by_id"], (
                "Chinese wall FUITE : supplier de ACP-B present dans l'index ACP-A"
            )
            assert idx_a["suppliers_by_id"][s_a] == "44000001"
        finally:
            await db.suppliers.delete_many({"id": {"$in": [s_a, s_b]}})

    _run(_go())


# ---------------------------------------------------------------------------
# finalize_je_doc : un JE entier passe le verrou
# ---------------------------------------------------------------------------
def test_finalize_je_doc_end_to_end():
    """iter90iz-7 : un JE de type FA avec 2 lignes (charge + fournisseur 440)
    doit avoir sa ligne 440 corrigee : compte 8 chars + tp_id de la fiche."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from import_finalizer import finalize_je_doc

        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]

        suffix = uuid.uuid4().hex[:8]
        acp = f"acp-iz-e2e-{suffix}"
        sup_id = f"sup-{suffix}"
        try:
            await db.suppliers.insert_one({
                "id": sup_id, "name": f"TotalEnergies-{suffix}",
                "copropriete_id": acp, "tier_account_number": "44000077",
            })
            je_doc = {
                "id": f"je-{suffix}",
                "journal_type": "FA",
                "date": "2026-01-15",
                "reference": "F-001",
                "lines": [
                    {"account_number": "615000", "debit": 100.0, "credit": 0.0},
                    {"account_number": "4400077",  # 7 chars, sans tp_id
                     "account_name": f"TotalEnergies-{suffix}",
                     "debit": 0.0, "credit": 100.0},
                ],
                "total_debit": 100.0, "total_credit": 100.0,
                "copropriete_id": acp,
            }
            out = await finalize_je_doc(db, je_doc, acp)
            assert out["lines"][0]["account_number"] == "615000"  # inchange
            l440 = out["lines"][1]
            assert l440["account_number"] == "44000077", (
                f"Le compte 4400077 (7 chars) devait etre reecrit 44000077 (fiche). "
                f"Vu {l440['account_number']}"
            )
            assert l440.get("third_party_id") == sup_id
            assert l440.get("third_party_type") == "supplier"
        finally:
            await db.suppliers.delete_one({"id": sup_id})

    _run(_go())
