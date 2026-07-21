"""iter90k: (1) invoice update must not duplicate AC journal entries,
(2) CSV import with multi-account invoice must produce N debit lines.
"""
import os
import uuid
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ACP_ID = "138cfd69-cc07-46bb-ad17-a98debee7a3a"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    # Set active copropriete
    r2 = s.post(f"{BASE}/api/auth/switch-copropriete/{ACP_ID}", timeout=15)
    # not all setups require this; ignore result
    return s


def _count_ac_for_invoice(client, invoice_id):
    """Count non-reversed AC journal entries for this invoice."""
    r = client.get(f"{BASE}/api/accounting/entries",
                   params={"copropriete_id": ACP_ID, "journal_type": "AC"},
                   timeout=30)
    assert r.status_code == 200, f"list JE failed: {r.status_code} {r.text}"
    data = r.json()
    entries = data if isinstance(data, list) else data.get("entries", data.get("items", []))
    matches = []
    for e in entries:
        if e.get("source_type") == "invoice" and e.get("source_id") == invoice_id:
            if e.get("reversed") or e.get("is_reversal"):
                continue
            matches.append(e)
    return matches


class TestInvoiceUpdateNoDuplicate:
    """Bug #1: PUT /api/invoices/{id} must not create duplicate AC entries."""

    def test_invoice_update_produces_single_ac_entry(self, client):
        unique_num = f"TEST-UPD-{uuid.uuid4().hex[:8]}"
        payload = {
            "number": unique_num,
            "date": "2026-04-15",
            "due_date": "2026-05-15",
            "supplier": "TEST_UpdateSupplier_ZZ",
            "description": "Test invoice update no duplicate",
            "total_amount": 100.0,
            "vat_amount": 21.0,
            "account_number": "61300",
            "status": "unpaid",
            "copropriete_id": ACP_ID,
        }
        r = client.post(f"{BASE}/api/invoices", json=payload, timeout=30)
        assert r.status_code in (200, 201), f"create failed: {r.status_code} {r.text}"
        inv = r.json()
        invoice_id = inv["id"]

        # Verify exactly 1 AC entry after create
        matches = _count_ac_for_invoice(client, invoice_id)
        assert len(matches) == 1, f"Expected 1 AC after create, got {len(matches)}"
        original_je_id = matches[0]["id"]

        # Update the invoice (change amount)
        update_payload = dict(payload)
        update_payload["total_amount"] = 150.0
        update_payload["description"] = "Updated description"
        r2 = client.put(f"{BASE}/api/invoices/{invoice_id}",
                        json=update_payload, timeout=30)
        assert r2.status_code == 200, f"update failed: {r2.status_code} {r2.text}"

        # Verify still exactly 1 AC entry (no duplicate)
        matches2 = _count_ac_for_invoice(client, invoice_id)
        assert len(matches2) == 1, (
            f"Expected 1 AC after update, got {len(matches2)}. "
            f"IDs: {[m['id'] for m in matches2]}. "
            f"Original JE {original_je_id} was {'replaced' if matches2 and matches2[0]['id'] != original_je_id else 'kept'}"
        )
        # Verify it's the NEW entry (hard-delete + recreate)
        new_je = matches2[0]
        assert new_je["id"] != original_je_id, "New AC should be a different doc (hard delete + recreate)"
        assert abs(new_je["total_debit"] - 150.0) < 0.01, (
            f"Expected total_debit=150.0, got {new_je['total_debit']}"
        )

        # Second update - amount 200
        update_payload["total_amount"] = 200.0
        r3 = client.put(f"{BASE}/api/invoices/{invoice_id}",
                        json=update_payload, timeout=30)
        assert r3.status_code == 200

        matches3 = _count_ac_for_invoice(client, invoice_id)
        assert len(matches3) == 1, (
            f"Expected 1 AC after second update, got {len(matches3)}"
        )
        assert abs(matches3[0]["total_debit"] - 200.0) < 0.01

        # Cleanup - delete invoice + all traces via direct DB
        try:
            import asyncio as _asyncio
            from motor.motor_asyncio import AsyncIOMotorClient as _AMC
            from dotenv import load_dotenv as _ld
            _ld()
            _c = _AMC(os.environ["MONGO_URL"])
            _db = _c[os.environ["DB_NAME"]]

            async def _cleanup():
                await _db.journal_entries.delete_many({
                    "source_type": "invoice", "source_id": invoice_id
                })
                await _db.invoices.delete_one({"id": invoice_id})
                await _db.suppliers.delete_many({
                    "copropriete_id": ACP_ID,
                    "name": "TEST_UpdateSupplier_ZZ",
                })

            _asyncio.get_event_loop().run_until_complete(_cleanup())
        except Exception as _e:
            print(f"[cleanup] {_e}")


class TestImportMultiAccountVentilation:
    """Bug #2: CSV import with multi-account invoice must have N debit lines
    (one per distinct account_number), not lump sum.
    Uses ACP 138 existing session data if available.
    """

    def test_commit_multi_account_invoice_creates_ventilated_je(self, client):
        """Create a fresh import session, commit a 2-line invoice (61300 +
        6160 = same external_ref), verify the resulting AC JE has 2 debit
        lines (one per distinct account) + 1 credit supplier line."""
        # Create session
        r = client.post(f"{BASE}/api/import-wizard/sessions",
                        json={"copropriete_id": ACP_ID, "source_system": "Optipro"},
                        timeout=30)
        assert r.status_code == 200, f"session create: {r.status_code} {r.text}"
        session_id = r.json()["id"]

        # 2 lines with SAME external_ref => grouped as 1 invoice with 2 _split_lines
        external_ref = f"TEST-MULTI-{uuid.uuid4().hex[:8]}"
        supplier_name = f"TEST_MultiAccSupplier_{uuid.uuid4().hex[:6]}"
        payload = {
            "invoices": [
                {
                    "supplier_aux_code": "",
                    "supplier_name": supplier_name,
                    "account_number": "61300",
                    "account_label": "Eau",
                    "montant_ht": 558.99,
                    "montant_tvac": 558.99,
                    "date": "2026-04-01",
                    "external_ref": external_ref,
                    "libelle": "Eau",
                },
                {
                    "supplier_aux_code": "",
                    "supplier_name": supplier_name,
                    "account_number": "6160",
                    "account_label": "Fournitures",
                    "montant_ht": 195.00,
                    "montant_tvac": 195.00,
                    "date": "2026-04-01",
                    "external_ref": external_ref,
                    "libelle": "Fournitures",
                },
            ]
        }
        r2 = client.post(
            f"{BASE}/api/import-wizard/sessions/{session_id}/commit-invoices",
            json=payload, timeout=60,
        )
        assert r2.status_code == 200, f"commit failed: {r2.status_code} {r2.text}"
        body = r2.json()
        assert body.get("inserted") == 1, (
            f"Expected 1 invoice merged/inserted, got {body}"
        )
        assert body.get("journal_entries") == 1, (
            f"Expected 1 AC JE inserted, got {body}"
        )

        # Fetch the JE for this session and verify ventilation
        r3 = client.get(f"{BASE}/api/accounting/entries",
                        params={"copropriete_id": ACP_ID, "journal_type": "AC"},
                        timeout=30)
        entries = r3.json() if isinstance(r3.json(), list) else \
            r3.json().get("entries", r3.json().get("items", []))
        target_jes = [e for e in entries
                      if e.get("import_session_id") == session_id
                      and e.get("journal_type") == "AC"]
        assert len(target_jes) == 1, (
            f"Expected 1 JE for session {session_id}, got {len(target_jes)}"
        )
        je = target_jes[0]
        lines = je.get("lines", [])
        debit_lines = [l for l in lines if float(l.get("debit", 0) or 0) > 0.01]
        credit_lines = [l for l in lines if float(l.get("credit", 0) or 0) > 0.01]
        debit_accs = {(l.get("account_number") or "").strip() for l in debit_lines}

        assert len(debit_lines) == 2, (
            f"Expected 2 debit lines (multi-account ventilation), "
            f"got {len(debit_lines)}: {debit_lines}"
        )
        assert "61300" in debit_accs, f"Missing debit on 61300: {debit_accs}"
        assert "6160" in debit_accs, f"Missing debit on 6160: {debit_accs}"

        # Verify amounts on each debit
        by_acc = {(l.get("account_number") or "").strip(): float(l.get("debit", 0))
                  for l in debit_lines}
        assert abs(by_acc["61300"] - 558.99) < 0.01, (
            f"61300 debit should be 558.99, got {by_acc['61300']}"
        )
        assert abs(by_acc["6160"] - 195.00) < 0.01, (
            f"6160 debit should be 195.00, got {by_acc['6160']}"
        )

        # Exactly 1 credit line (supplier)
        assert len(credit_lines) == 1, (
            f"Expected 1 credit supplier line, got {len(credit_lines)}: {credit_lines}"
        )
        cl = credit_lines[0]
        assert (cl.get("account_number") or "").startswith("44"), (
            f"Credit line should be supplier 44xxxxxx, got {cl.get('account_number')}"
        )
        assert abs(float(cl.get("credit", 0)) - 753.99) < 0.01, (
            f"Supplier credit should be 753.99, got {cl.get('credit')}"
        )

        # Cleanup: delete the invoice + JE + supplier + session via direct DB
        # (import wizard JEs don't have auto_generated=True so
        # /api/invoices/{id} DELETE doesn't remove them).
        inv_id = je.get("source_invoice_id")
        try:
            import asyncio as _asyncio
            from motor.motor_asyncio import AsyncIOMotorClient as _AMC
            from dotenv import load_dotenv as _ld
            _ld()
            _c = _AMC(os.environ["MONGO_URL"])
            _db = _c[os.environ["DB_NAME"]]

            async def _cleanup():
                await _db.journal_entries.delete_many(
                    {"import_session_id": session_id}
                )
                if inv_id:
                    await _db.invoices.delete_one({"id": inv_id})
                await _db.suppliers.delete_many({
                    "copropriete_id": ACP_ID, "name": supplier_name
                })
                await _db.import_sessions.delete_one({"id": session_id})

            _asyncio.get_event_loop().run_until_complete(_cleanup())
        except Exception as _e:
            print(f"[cleanup] {_e}")

    def test_bilan_regression_499_and_totals(self, client):
        """Confirm ACP 138 bilan totals remain: compte_499=1170.16."""
        r = client.get(f"{BASE}/api/reports/bilan",
                       params={"copropriete_id": ACP_ID}, timeout=30)
        assert r.status_code == 200, f"bilan failed: {r.status_code} {r.text}"
        b = r.json()
        # Direct top-level key from the API
        c499 = b.get("compte_499")
        assert c499 is not None, f"compte_499 not found in bilan: keys={list(b.keys())}"
        val = float(c499 if isinstance(c499, (int, float)) else c499.get("balance", 0))
        assert abs(abs(val) - 1170.16) < 0.05, (
            f"compte_499 expected 1170.16, got {val}"
        )
        # Bilan must remain balanced
        assert b.get("equilibre") is True, (
            f"Bilan not balanced: total_actif={b.get('total_actif')} "
            f"total_passif={b.get('total_passif')} ecart={b.get('ecart')}"
        )
