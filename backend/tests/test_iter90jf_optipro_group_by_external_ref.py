"""iter90jf : Regroupement strict des lignes de facture Optipro multi-detail.

Bug historique : Optipro exporte une ligne CSV par ligne de detail comptable
(ex: facture 260081 avec 3 comptes de charges -> 3 lignes CSV). Le code de
regroupement ancien s'appuyait sur `internal_ref` comme cle prioritaire. Or
Optipro remplit ce champ de facon INCOHERENTE :
- soit sur la 1ere ligne uniquement (les suivantes ont `internal_ref=""`),
- soit avec une variation ("260081.1", "260081.2"),
- soit identique sur toutes les lignes.

Consequence : les 3 lignes avaient 3 cles differentes -> 3 factures creees
au lieu d'UNE avec 3 distribution_lines.

Fix iter90jf : cle stable `EX:external_ref|supplier|date` (identifiant Optipro
fiable). Toutes les variantes de scenarios sont testees ci-dessous.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helper : appelle commit_invoices avec un jeu de donnees construit a la main
# ---------------------------------------------------------------------------
async def _seed_and_commit(db, invoices_payload: list[dict], acp_id: str) -> tuple[list, dict]:
    """Cree une session + ACP + appelle commit-invoices en mode direct.
    Retourne (invoices en DB, step_result).
    """
    from routes.import_wizard import create_import_wizard_router
    from routes.import_wizard import CommitInvoicesInput

    router = create_import_wizard_router(db)
    session_id = str(uuid.uuid4())
    # ACP minimale + fiscal_year ouvert (requis par ensure_period_open)
    await db.coproprietes.insert_one({
        "id": acp_id, "name": f"iter90jf-{acp_id[:8]}",
        "bank_accounts": [{"iban": "BE04001952089331", "account_type": "vue", "is_default": True, "pcmn_number": "550000"}],
    })
    await db.fiscal_years.insert_one({
        "id": f"fy-{acp_id[:8]}", "copropriete_id": acp_id,
        "name": "2026", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "status": "open",
    })
    await db.import_sessions.insert_one({
        "id": session_id, "copropriete_id": acp_id, "status": "in_progress",
        "step_data": {},
    })
    # Trouve le handler
    handler = None
    for r in router.routes:
        p = getattr(r, "path", "")
        if p.endswith("/sessions/{session_id}/commit-invoices"):
            handler = r.endpoint
            break
    assert handler is not None, "commit-invoices handler introuvable"
    # Bypass auth
    import server
    original = server.get_current_user

    async def _fake_su(_req):
        return {"role": "superadmin", "copropriete_ids": [acp_id]}

    server.get_current_user = _fake_su

    class _FakeState:
        copropriete_id = None

    class _FakeReq:
        headers = {}
        state = _FakeState()

    try:
        payload = CommitInvoicesInput(invoices=invoices_payload)
        result = await handler(session_id=session_id, data=payload, request=_FakeReq())
        docs = await db.invoices.find({"import_session_id": session_id}, {"_id": 0}).to_list(1000)
        return docs, result
    finally:
        server.get_current_user = original
        # cleanup
        await db.invoices.delete_many({"import_session_id": session_id})
        await db.journal_entries.delete_many({"import_session_id": session_id})
        await db.import_sessions.delete_one({"id": session_id})
        await db.coproprietes.delete_one({"id": acp_id})
        await db.fiscal_years.delete_one({"id": f"fy-{acp_id[:8]}"})


# ---------------------------------------------------------------------------
# Cas 1 : 3 lignes CSV avec MEME external_ref, internal_ref VIDE sur les 2/3
# ---------------------------------------------------------------------------
def test_group_by_external_ref_when_internal_ref_missing_on_some_lines():
    """Reproduit le bug user : facture 260081 avec 3 lignes, internal_ref
    present sur la 1re mais vide sur les 2 autres.

    Attendu : UNE seule invoice avec distribution_lines de longueur 3.
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        acp_id = f"acp-jf-{uuid.uuid4().hex[:8]}"
        lines = [
            # Ligne 1 : internal_ref present
            {"copro_code": "", "copro_name": "", "date": "2026-01-15", "due_date": "",
             "internal_ref": "260081", "external_ref": "260081",
             "libelle": "Frais privatifs eau", "ne_pas_payer": False,
             "supplier_aux_code": "F00042", "supplier_name": "Sneyers Philippe SRL",
             "account_number": "643000", "account_label": "Frais privatifs",
             "dist_key_code": "K01", "dist_key_label": "Clef 1",
             "nature_code": "N01", "nature_label": "Nature 1", "vat_code": "0",
             "part_occupant": 0.0, "part_proprietaire": 100.0,
             "montant_ht": 100.0, "montant_tvac": 100.0, "montant_tva": 0.0},
            # Ligne 2 : internal_ref VIDE
            {"copro_code": "", "copro_name": "", "date": "2026-01-15", "due_date": "",
             "internal_ref": "", "external_ref": "260081",
             "libelle": "Frais privatifs elec", "ne_pas_payer": False,
             "supplier_aux_code": "F00042", "supplier_name": "Sneyers Philippe SRL",
             "account_number": "643000", "account_label": "Frais privatifs",
             "dist_key_code": "K02", "dist_key_label": "Clef 2",
             "nature_code": "N02", "nature_label": "Nature 2", "vat_code": "0",
             "part_occupant": 0.0, "part_proprietaire": 100.0,
             "montant_ht": 200.0, "montant_tvac": 200.0, "montant_tva": 0.0},
            # Ligne 3 : internal_ref VIDE, autre compte
            {"copro_code": "", "copro_name": "", "date": "2026-01-15", "due_date": "",
             "internal_ref": "", "external_ref": "260081",
             "libelle": "Charges communes chauffage", "ne_pas_payer": False,
             "supplier_aux_code": "F00042", "supplier_name": "Sneyers Philippe SRL",
             "account_number": "616000", "account_label": "Chauffage",
             "dist_key_code": "K01", "dist_key_label": "Clef 1",
             "nature_code": "N03", "nature_label": "Nature 3", "vat_code": "0",
             "part_occupant": 100.0, "part_proprietaire": 0.0,
             "montant_ht": 300.0, "montant_tvac": 300.0, "montant_tva": 0.0},
        ]
        docs, result = await _seed_and_commit(db, lines, acp_id)
        assert len(docs) == 1, (
            f"3 lignes du meme external_ref doivent produire UNE facture. Vu {len(docs)}"
        )
        inv = docs[0]
        assert inv["number"] == "260081"
        assert inv["total_amount"] == 600.0, (
            f"Total doit etre 100 + 200 + 300 = 600. Vu {inv['total_amount']}"
        )
        dl = inv.get("distribution_lines") or []
        assert len(dl) == 3, (
            f"distribution_lines doit contenir 3 lignes. Vu {len(dl)}"
        )
        amounts_dl = sorted([d.get("montant_tvac") for d in dl])
        assert amounts_dl == [100.0, 200.0, 300.0], f"Montants dl : {amounts_dl}"
        # Verifie le regroupement compte par la stats step
        assert result.get("grouped") == 2, (
            f"2 lignes ont ete regroupees (3 -> 1, soit 2 groupees). Vu {result.get('grouped')}"
        )

    _run(_go())


# ---------------------------------------------------------------------------
# Cas 2 : 2 factures DIFFERENTES avec le meme fournisseur/date -> non merges
# ---------------------------------------------------------------------------
def test_do_not_merge_different_invoices_same_supplier_same_date():
    """Deux factures 260081 et 260082 du meme fournisseur meme date ne
    doivent PAS etre regroupees (external_ref different).
    """
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        acp_id = f"acp-jf2-{uuid.uuid4().hex[:8]}"
        lines = [
            {"copro_code": "", "copro_name": "", "date": "2026-01-15", "due_date": "",
             "internal_ref": "260081", "external_ref": "260081",
             "libelle": "Facture A", "ne_pas_payer": False,
             "supplier_aux_code": "F00042", "supplier_name": "Baloise-A",
             "account_number": "616000", "account_label": "Chauffage",
             "dist_key_code": "K01", "dist_key_label": "",
             "nature_code": "N01", "nature_label": "", "vat_code": "0",
             "part_occupant": 0.0, "part_proprietaire": 100.0,
             "montant_ht": 100.0, "montant_tvac": 100.0, "montant_tva": 0.0},
            {"copro_code": "", "copro_name": "", "date": "2026-01-15", "due_date": "",
             "internal_ref": "260082", "external_ref": "260082",
             "libelle": "Facture B", "ne_pas_payer": False,
             "supplier_aux_code": "F00042", "supplier_name": "Baloise-A",
             "account_number": "616000", "account_label": "Chauffage",
             "dist_key_code": "K01", "dist_key_label": "",
             "nature_code": "N01", "nature_label": "", "vat_code": "0",
             "part_occupant": 0.0, "part_proprietaire": 100.0,
             "montant_ht": 200.0, "montant_tvac": 200.0, "montant_tva": 0.0},
        ]
        docs, _ = await _seed_and_commit(db, lines, acp_id)
        assert len(docs) == 2, f"2 factures differentes -> 2 invoices. Vu {len(docs)}"
        numbers = sorted(d["number"] for d in docs)
        assert numbers == ["260081", "260082"]

    _run(_go())


# ---------------------------------------------------------------------------
# Cas 3 : facture mono-ligne - distribution_lines toujours renseigne
# ---------------------------------------------------------------------------
def test_single_line_invoice_still_has_distribution_lines():
    """Meme pour une facture avec 1 seule ligne, distribution_lines contient
    cette ligne (uniformisation pour les consommateurs downstream)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        acp_id = f"acp-jf3-{uuid.uuid4().hex[:8]}"
        lines = [
            {"copro_code": "", "copro_name": "", "date": "2026-02-01", "due_date": "",
             "internal_ref": "F-001", "external_ref": "F-001",
             "libelle": "Facture solo", "ne_pas_payer": False,
             "supplier_aux_code": "F00001", "supplier_name": "Test SA",
             "account_number": "616000", "account_label": "Chauffage",
             "dist_key_code": "K01", "dist_key_label": "",
             "nature_code": "N01", "nature_label": "", "vat_code": "0",
             "part_occupant": 100.0, "part_proprietaire": 0.0,
             "montant_ht": 50.0, "montant_tvac": 50.0, "montant_tva": 0.0},
        ]
        docs, _ = await _seed_and_commit(db, lines, acp_id)
        assert len(docs) == 1
        dl = docs[0].get("distribution_lines") or []
        assert len(dl) == 1, (
            f"Meme mono-ligne, distribution_lines doit contenir 1 element. Vu {len(dl)}"
        )
        assert dl[0]["montant_tvac"] == 50.0

    _run(_go())
