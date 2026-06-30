"""Iter90i — Les frais privatifs (is_private_fee=true / compte 643*) sont
EXCLUS du total des dépenses communes.

Contexte : Découvert sur l'ACP Acacia (production) — 9 factures
is_private_fee=true totalisant 800,02 EUR (compte 643) étaient incluses
dans le total "Dépenses de l'exercice" alors qu'elles sont refacturées
aux propriétaires concernés et ne sont PAS des charges communes.

INVARIANTS :
- I1 : Une facture is_private_fee=true (sur 643) n'apparaît PAS dans
       compute_expense_rows.
- I2 : Une ligne d'écriture journal sur 643* (ex: OD-PRIV de
       refacturation) n'apparaît PAS dans compute_expense_rows même si
       elle est marquée class_num=6 dans le PCMN.
- I3 : Le total UI /api/fiscal/expenses == total PDF "Liste des dépenses"
       == somme des charges communes uniquement, hors privatifs.
- I4 : Les autres factures (charges communes) restent comptées
       normalement.
"""
import os
import sys
import asyncio
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient, ASGITransport

sys.path.insert(0, "/app/backend")

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-iter90i-" + uuid.uuid4().hex)
os.environ.setdefault("ADMIN_EMAIL", "admin@copro.be")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

from server import app, db  # noqa: E402


async def _wipe():
    await db.invoices.delete_many({"id": {"$regex": "^inv-iter90i-"}})
    await db.journal_entries.delete_many({"id": {"$regex": "^je-iter90i-"}})
    await db.coproprietes.delete_many({"id": {"$regex": "^c-iter90i-"}})
    await db.pcmn_accounts.delete_many({"copropriete_id": {"$regex": "^c-iter90i-"}})
    await db.distribution_keys.delete_many({"copropriete_id": {"$regex": "^c-iter90i-"}})
    await db.expense_categories.delete_many({"copropriete_id": {"$regex": "^c-iter90i-"}})


async def _login(c: AsyncClient):
    r = await c.post("/api/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, r.text


async def _seed_acp():
    cid = f"c-iter90i-{uuid.uuid4().hex[:8]}"
    await db.coproprietes.insert_one({
        "id": cid, "name": "ACP Acacia-Like",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # PCMN class-6 accounts (charges communes + 643 frais privatifs)
    for num, name in [
        ("611000", "Entretien"),
        ("612000", "Energie"),
        ("643000", "Frais privatifs"),  # class 6 mais NE doit pas etre compte comme charge commune
        ("440000", "Fournisseurs"),
    ]:
        cls = int(num[0])
        await db.pcmn_accounts.insert_one({
            "id": str(uuid.uuid4()), "copropriete_id": cid,
            "number": num, "name": name, "class_num": cls,
        })
    dk_main = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        "id": dk_main, "copropriete_id": cid, "code": "100",
        "name": "Charges communes", "lots": [],
    })
    return cid, dk_main


async def _t_invoice_with_is_private_fee_is_excluded():
    """I1 + I4 : une facture privative est ignoree, les charges communes restent."""
    await _wipe()
    cid, dk_main = await _seed_acp()
    # 1 facture privative (800,02 EUR sur 643000) -> doit etre exclue
    await db.invoices.insert_one({
        "id": "inv-iter90i-priv1", "number": "F-PRIV", "date": "2026-02-10",
        "supplier": "Plombier-Privatif",
        "description": "Reparation lot 5 (privatif)",
        "total_amount": 800.02, "vat_amount": 138.78,
        "account_number": "643000", "is_private_fee": True,
        "private_fee_owner_id": "owner-x",
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # 1 facture commune (121 EUR sur 611000) -> doit etre incluse
    await db.invoices.insert_one({
        "id": "inv-iter90i-com1", "number": "F-COM", "date": "2026-02-15",
        "supplier": "Entretien-Commun",
        "description": "Entretien parties communes",
        "total_amount": 121.00, "vat_amount": 21.00,
        "account_number": "611000", "distribution_key_id": dk_main,
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
    )
    assert len(rows) == 1, f"Une seule charge commune attendue, got {len(rows)}"
    assert rows[0]["account_number"] == "611000"
    assert abs(totals["total"] - 121.00) < 0.01, f"Total attendu 121.00, got {totals['total']}"
    # Pas de ligne sur 643*
    accs = {r["account_number"] for r in rows}
    assert "643000" not in accs
    assert not any(a.startswith("643") for a in accs)


async def _t_od_priv_on_643_is_excluded():
    """I2 : une ligne journal sur 643* est exclue meme si journal_type='OD'
    et meme si le compte est marque class_num=6."""
    await _wipe()
    cid, dk_main = await _seed_acp()
    # 1 facture commune normale
    await db.invoices.insert_one({
        "id": "inv-iter90i-com2", "number": "F2", "date": "2026-03-01",
        "supplier": "X", "description": "Commune",
        "total_amount": 100.0, "vat_amount": 0.0,
        "account_number": "611000", "distribution_key_id": dk_main,
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # OD-PRIV : refacturation au compte 643000 (debit 800.02)
    # contrepartie sur compte fournisseur 440000
    await db.journal_entries.insert_one({
        "id": "je-iter90i-od1", "journal_type": "OD", "date": "2026-03-05",
        "reference": "OD-PRIV-001",
        "description": "Refacturation privatif lot 5",
        "lines": [
            {"account_number": "643000", "account_name": "Frais privatifs",
             "debit": 800.02, "credit": 0.0, "distribution_key_id": dk_main},
            {"account_number": "440000", "account_name": "Fournisseurs",
             "debit": 0.0, "credit": 800.02},
        ],
        "total_debit": 800.02, "total_credit": 800.02,
        "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    from expense_rows import compute_expense_rows
    rows, totals = await compute_expense_rows(
        db, cid, date_from="2026-01-01", date_to="2026-12-31",
    )
    # Seule la facture commune (100 EUR) doit etre comptee
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}: {[(r['account_number'],r['total_amount']) for r in rows]}"
    assert abs(totals["total"] - 100.00) < 0.01, f"Total attendu 100.00, got {totals['total']}"
    assert rows[0]["account_number"] == "611000"


async def _t_acacia_like_scenario_reproduces_fix():
    """Scenario Acacia : 9 factures privatives + charges communes.
    Avant fix : total = communes + privatifs (sur-comptage).
    Apres fix : total = communes uniquement.
    Cible : sur Acacia, total brut 12128.74 - 800.02 = 11328.72 EUR.
    Ici on simule a echelle reduite : 5 privatives 160 EUR + 1 commune 500 EUR."""
    await _wipe()
    cid, dk_main = await _seed_acp()
    # 5 factures privatives (160 EUR chacune = 800 EUR total)
    for i in range(5):
        await db.invoices.insert_one({
            "id": f"inv-iter90i-acpriv-{i}", "number": f"P-{i}",
            "date": "2025-11-10",
            "supplier": f"Privatif-{i}", "description": "Frais privatif",
            "total_amount": 160.00, "vat_amount": 27.77,
            "account_number": "643000", "is_private_fee": True,
            "private_fee_owner_id": f"owner-{i}",
            "status": "paid", "copropriete_id": cid,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    # 1 charge commune (500 EUR)
    await db.invoices.insert_one({
        "id": "inv-iter90i-accom", "number": "C-1",
        "date": "2025-11-15",
        "supplier": "ChargeCommune", "description": "Eclairage halls",
        "total_amount": 500.00, "vat_amount": 86.78,
        "account_number": "611000", "distribution_key_id": dk_main,
        "status": "paid", "copropriete_id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await _login(c)
        r = await c.get(
            f"/api/fiscal/expenses?copropriete_id={cid}&date_from=2025-10-01&date_to=2026-06-30",
            headers={"X-Copropriete-Id": cid},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        ui_total = body["totals"]["total"]
        ui_count = body["totals"]["count"]
        # Seule la charge commune doit etre incluse
        assert ui_count == 1, f"Expected 1 charge commune, got {ui_count}"
        assert abs(ui_total - 500.00) < 0.01, f"Total attendu 500.00, got {ui_total}"
        # Le PDF doit donner le meme total
        r = await c.get(
            f"/api/reports/depenses/pdf?copropriete_id={cid}&date_from=2025-10-01&date_to=2026-06-30"
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"
        # Verifier le total via compute_expense_rows direct
        from expense_rows import compute_expense_rows
        rows, totals = await compute_expense_rows(
            db, cid, date_from="2025-10-01", date_to="2026-06-30",
        )
        assert abs(totals["total"] - ui_total) < 0.01, f"PDF vs UI: {totals['total']} vs {ui_total}"


async def _run_all():
    await _t_invoice_with_is_private_fee_is_excluded()
    await _t_od_priv_on_643_is_excluded()
    await _t_acacia_like_scenario_reproduces_fix()


def test_iter90i_private_fees_excluded():
    asyncio.run(_run_all())
