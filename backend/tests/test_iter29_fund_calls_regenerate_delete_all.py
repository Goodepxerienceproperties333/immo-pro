"""Tests iteration 29: endpoints regenerate-entries + delete-all pour fund_calls.

- POST /api/fund-calls/regenerate-entries?copropriete_id=X
- POST /api/fund-calls/delete-all?copropriete_id=X
- GET /api/accounting/entries?copropriete_id=X&journal_type=VE : descriptions correctes
"""
import os
import pytest
import requests
import uuid

_be = os.environ.get("REACT_APP_BACKEND_URL")
if not _be:
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    _be = line.split("=", 1)[1].strip()
                    break
    except Exception:
        pass
BASE_URL = (_be or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"

COPRO_DEMO = "6748ca1a-216d-4002-8417-799287238736"
COPRO_TEST = "252c2888-7c95-4a89-b19f-70de62f7bb4a"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- CHINESE WALL CHECKS ----------
class TestChineseWalls:
    def test_regenerate_requires_copropriete_id(self, session):
        r = session.post(f"{BASE_URL}/api/fund-calls/regenerate-entries")
        assert r.status_code == 400, f"Expected 400 without copro_id, got {r.status_code}: {r.text}"

    def test_regenerate_rejects_all(self, session):
        r = session.post(f"{BASE_URL}/api/fund-calls/regenerate-entries?copropriete_id=all")
        assert r.status_code == 400

    def test_delete_all_requires_copropriete_id(self, session):
        r = session.post(f"{BASE_URL}/api/fund-calls/delete-all")
        assert r.status_code == 400, f"Expected 400 without copro_id, got {r.status_code}: {r.text}"

    def test_delete_all_rejects_all(self, session):
        r = session.post(f"{BASE_URL}/api/fund-calls/delete-all?copropriete_id=all")
        assert r.status_code == 400


# ---------- REGENERATE ON DEMO ----------
class TestRegenerateEntriesDemo:
    def test_regenerate_demo_returns_ok(self, session):
        r = session.post(f"{BASE_URL}/api/fund-calls/regenerate-entries?copropriete_id={COPRO_DEMO}")
        assert r.status_code == 200, f"regenerate failed: {r.status_code} {r.text}"
        data = r.json()
        assert data.get("status") == "ok"
        assert "scanned" in data
        assert "regenerated" in data
        assert isinstance(data.get("errors"), list)
        # Au moins quelque chose dans Demo
        assert data["scanned"] >= 1, f"Expected at least 1 call in Demo, got {data}"
        print(f"DEMO regenerate result: scanned={data['scanned']} regenerated={data['regenerated']} errors={data['errors']}")

    def test_ve_descriptions_have_correct_prefix(self, session):
        """Verifie qu'apres regenerate, les ecritures VE auto-generees commencent
        par le prefixe correspondant au call_type de la fund_call source."""
        # Calls de l'ACP Demo
        rc = session.get(f"{BASE_URL}/api/fund-calls?copropriete_id={COPRO_DEMO}")
        assert rc.status_code == 200
        calls = rc.json()
        assert len(calls) > 0, "No fund calls in Demo to test"
        calls_by_id = {c["id"]: c for c in calls}

        # Ecritures VE de l'ACP Demo
        re_ = session.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={COPRO_DEMO}&journal_type=VE")
        assert re_.status_code == 200, f"Failed to get entries: {re_.status_code} {re_.text}"
        entries = re_.json()
        # Garder uniquement les ecritures auto-generees liees a un fund_call
        auto_entries = [e for e in entries if e.get("auto_generated") and e.get("source_type") == "fund_call"]
        assert len(auto_entries) > 0, "No auto-generated VE entries linked to fund_calls"

        type_prefix = {
            "provisions": "Appel de provisions",
            "reserve": "Appel fonds de reserve",
            "roulement": "Appel fonds de roulement",
            "special": "Appel special",
        }
        mismatches = []
        seen_types = set()
        for e in auto_entries:
            src_id = e.get("source_id")
            fc = calls_by_id.get(src_id)
            if not fc:
                continue
            ctype = fc.get("call_type", "provisions")
            seen_types.add(ctype)
            expected = type_prefix.get(ctype, "Appel de fonds")
            desc = e.get("description", "")
            if not desc.startswith(expected):
                mismatches.append({"call": fc.get("name"), "type": ctype, "expected_prefix": expected, "desc": desc})
        print(f"VE entries checked: {len(auto_entries)}, types seen: {seen_types}")
        assert not mismatches, f"Descriptions mismatch: {mismatches}"


# ---------- DELETE-ALL ON COPRO_TEST ----------
class TestDeleteAllOnCoproTest:
    @pytest.fixture(scope="class")
    def seeded_calls(self, session):
        """Cree quelques appels temporaires sur l'ACP Test pour le delete-all."""
        created = []
        # Recup un FY de l'ACP Test si dispo, sinon empty string accept
        try:
            fr = session.get(f"{BASE_URL}/api/fiscal/years?copropriete_id={COPRO_TEST}")
            fy_id = ""
            if fr.status_code == 200 and isinstance(fr.json(), list) and fr.json():
                fy_id = fr.json()[0]["id"]
        except Exception:
            fy_id = ""

        types = ["provisions", "reserve", "roulement", "special"]
        for i, ct in enumerate(types):
            payload = {
                "name": f"TEST_iter29_{ct}_{uuid.uuid4().hex[:6]}",
                "date": "2025-01-15",
                "due_date": "2025-02-15",
                "fiscal_year_id": fy_id,
                "description": "Test iteration 29",
                "total_amount": 1200.0 + i * 100,
                "call_type": ct,
                "copropriete_id": COPRO_TEST,
            }
            r = session.post(f"{BASE_URL}/api/fund-calls", json=payload)
            assert r.status_code in (200, 201), f"create {ct} failed: {r.status_code} {r.text}"
            created.append(r.json())
        yield created
        # cleanup (au cas ou delete-all n'aurait pas tourne)
        try:
            session.post(f"{BASE_URL}/api/fund-calls/delete-all?copropriete_id={COPRO_TEST}")
        except Exception:
            pass

    def test_calls_present_before_delete(self, session, seeded_calls):
        r = session.get(f"{BASE_URL}/api/fund-calls?copropriete_id={COPRO_TEST}")
        assert r.status_code == 200
        calls = r.json()
        names = {c["name"] for c in calls}
        for sc in seeded_calls:
            assert sc["name"] in names, f"Seeded call {sc['name']} missing"

    def test_regenerate_on_test_copro(self, session, seeded_calls):
        """Regenere les ecritures VE sur l'ACP Test, puis verifie les prefixes."""
        r = session.post(f"{BASE_URL}/api/fund-calls/regenerate-entries?copropriete_id={COPRO_TEST}")
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "ok"
        assert data["scanned"] >= len(seeded_calls)

        # Verifier prefixes
        re_ = session.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={COPRO_TEST}&journal_type=VE")
        assert re_.status_code == 200
        entries = re_.json()
        seeded_ids = {c["id"] for c in seeded_calls}
        seeded_entries = [e for e in entries
                          if e.get("auto_generated") and e.get("source_type") == "fund_call"
                          and e.get("source_id") in seeded_ids]
        assert len(seeded_entries) >= 1, "No auto entries found for seeded calls"
        type_prefix = {
            "provisions": "Appel de provisions",
            "reserve": "Appel fonds de reserve",
            "roulement": "Appel fonds de roulement",
            "special": "Appel special",
        }
        call_by_id = {c["id"]: c for c in seeded_calls}
        for e in seeded_entries:
            ct = call_by_id[e["source_id"]]["call_type"]
            expected = type_prefix[ct]
            assert e["description"].startswith(expected), \
                f"Expected prefix '{expected}' for call_type={ct}, got '{e['description']}'"

    def test_delete_all_removes_calls_and_entries(self, session, seeded_calls):
        r = session.post(f"{BASE_URL}/api/fund-calls/delete-all?copropriete_id={COPRO_TEST}")
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        assert data["status"] == "ok"
        assert data["deleted_calls"] >= len(seeded_calls)

        # Verifier que la liste est vide
        rg = session.get(f"{BASE_URL}/api/fund-calls?copropriete_id={COPRO_TEST}")
        assert rg.status_code == 200
        remaining = rg.json()
        assert remaining == [], f"Expected [] after delete-all, got {len(remaining)} calls"

        # Verifier que les ecritures auto-generees liees aux seeded calls n'existent plus
        re_ = session.get(f"{BASE_URL}/api/accounting/entries?copropriete_id={COPRO_TEST}&journal_type=VE")
        assert re_.status_code == 200
        entries = re_.json()
        seeded_ids = {c["id"] for c in seeded_calls}
        orphan = [e for e in entries
                  if e.get("source_type") == "fund_call" and e.get("source_id") in seeded_ids
                  and e.get("auto_generated")]
        assert orphan == [], f"Auto entries should be deleted, found: {orphan}"


# ---------- DEMO INTEGRITY (ne pas casser Demo) ----------
class TestDemoNotImpacted:
    def test_demo_still_has_calls(self, session):
        r = session.get(f"{BASE_URL}/api/fund-calls?copropriete_id={COPRO_DEMO}")
        assert r.status_code == 200
        calls = r.json()
        assert len(calls) >= 1, "Demo calls were deleted by mistake!"
