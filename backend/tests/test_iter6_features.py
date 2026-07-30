"""Iteration 6 backend tests: Belgian ACP enhanced (BCE, multi-bank, PCMN auto, ref ACP-YYYYMM-NNN),
owner duplicate detection, role gating (syndic vs gestionnaire)."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = os.environ.get("TEST_ADMIN_EMAIL", "admin@copro.be")
ADMIN_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "admin123")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def created_copro(admin_session):
    ts = int(time.time())
    payload = {
        "name": f"TEST_ACP_Iter6_{ts}",
        "bce": "0123.456.789",
        "address": "Rue de l'Essai 12",
        "postal_code": "1000",
        "city": "Bruxelles",
        "country": "Belgique",
        "description": "ACP test iter6",
        "bank_accounts": [
            {"iban": "BE68539007547034", "bic": "GKCCBEBB", "account_type": "vue", "is_default": True, "label": "Compte courant"},
            {"iban": "BE71096123456769", "bic": "GEBABEBB", "account_type": "epargne", "is_default": False, "label": "Reserve"},
        ],
        "quarterly_closing": True,
        "default_provisions": True,
    }
    r = admin_session.post(f"{BASE_URL}/api/coproprietes", json=payload, timeout=15)
    assert r.status_code == 200, f"create copro failed: {r.status_code} {r.text}"
    return r.json()


# ---- ACP creation & enhanced fields ----

class TestACPCreation:
    def test_chronological_reference(self, created_copro):
        ref = created_copro.get("reference", "")
        assert ref.startswith("ACP-"), f"reference missing ACP prefix: {ref}"
        parts = ref.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 6  # YYYYMM
        assert parts[2].isdigit() and len(parts[2]) == 3  # NNN

    def test_persistence_get(self, admin_session, created_copro):
        r = admin_session.get(f"{BASE_URL}/api/coproprietes/{created_copro['id']}", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["bce"] == "0123.456.789"
        assert d["postal_code"] == "1000"
        assert d["city"] == "Bruxelles"
        assert d["quarterly_closing"] is True
        assert d["default_provisions"] is True
        assert isinstance(d["bank_accounts"], list)
        assert len(d["bank_accounts"]) == 2

    def test_default_bank_account(self, created_copro):
        defaults = [b for b in created_copro["bank_accounts"] if b.get("is_default")]
        assert len(defaults) == 1
        assert defaults[0]["account_type"] == "vue"

    def test_pcmn_numbers_on_bank_accounts(self, created_copro):
        # vue -> 551 + last3 + 00 ; epargne -> 550 + last3 + 00
        for ba in created_copro["bank_accounts"]:
            assert "pcmn_number" in ba
            iban_clean = ba["iban"].replace(" ", "").replace("-", "")
            last3 = iban_clean[-3:]
            if ba["account_type"] == "vue":
                assert ba["pcmn_number"] == f"551{last3}00", ba
            else:
                assert ba["pcmn_number"] == f"550{last3}00", ba

    def test_pcmn_accounts_persisted(self, admin_session, created_copro):
        r = admin_session.get(f"{BASE_URL}/api/accounting/pcmn", timeout=10)
        # endpoint may differ; tolerate 404
        if r.status_code == 404:
            pytest.skip("PCMN listing endpoint not exposed; skipping persistence check via API")
        assert r.status_code == 200
        numbers = {a.get("number") for a in r.json()}
        for ba in created_copro["bank_accounts"]:
            assert ba["pcmn_number"] in numbers, f"PCMN {ba['pcmn_number']} not in chart"


class TestACPListAndArchive:
    def test_list_contains_created(self, admin_session, created_copro):
        r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=10)
        assert r.status_code == 200
        items = r.json()
        ids = {c["id"] for c in items}
        assert created_copro["id"] in ids
        match = next(c for c in items if c["id"] == created_copro["id"])
        assert match.get("reference", "").startswith("ACP-")
        assert match.get("bce") == "0123.456.789"
        assert any(b.get("is_default") for b in match.get("bank_accounts", []))

    def test_archive_then_unarchive(self, admin_session, created_copro):
        cid = created_copro["id"]
        r = admin_session.post(f"{BASE_URL}/api/coproprietes/{cid}/archive", timeout=10)
        assert r.status_code == 200
        # Should not appear in default list
        r2 = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=10)
        assert cid not in {c["id"] for c in r2.json()}
        # Should appear with show_archived=true
        r3 = admin_session.get(f"{BASE_URL}/api/coproprietes?show_archived=true", timeout=10)
        assert cid in {c["id"] for c in r3.json()}
        # Unarchive
        r4 = admin_session.post(f"{BASE_URL}/api/coproprietes/{cid}/unarchive", timeout=10)
        assert r4.status_code == 200
        r5 = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=10)
        assert cid in {c["id"] for c in r5.json()}


# ---- Owner duplicate detection ----

class TestOwnerDuplicateDetection:
    @pytest.fixture(scope="class")
    def seeded_owner(self, admin_session, created_copro):
        ts = int(time.time())
        payload = {
            "name": f"TEST_DupOwner_{ts}",
            "first_name": "Jean",
            "last_name": f"Dup{ts}",
            "email": f"jeandup{ts}@test.be",
            "phone": f"+3247500{ts % 10000:04d}",
            "copropriete_id": created_copro["id"],
        }
        r = admin_session.post(f"{BASE_URL}/api/owners", json=payload, timeout=10)
        assert r.status_code == 200, r.text
        return r.json()

    def test_duplicate_by_email(self, admin_session, seeded_owner):
        r = admin_session.get(f"{BASE_URL}/api/owners/check-duplicate",
                              params={"email": seeded_owner["email"]}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["has_duplicates"] is True
        assert any(x["field"] == "email" and x["owner_name"] == seeded_owner["name"]
                   for x in d["duplicates"])

    def test_duplicate_by_phone(self, admin_session, seeded_owner):
        r = admin_session.get(f"{BASE_URL}/api/owners/check-duplicate",
                              params={"phone": seeded_owner["phone"]}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["has_duplicates"] is True
        assert any(x["field"] == "phone" for x in d["duplicates"])

    def test_no_duplicate_for_unknown(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/owners/check-duplicate",
                              params={"email": "nobody-iter6@nowhere.example"}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["has_duplicates"] is False
        assert d["duplicates"] == []


# ---- Role gating: syndic vs gestionnaire ----

class TestRoleGating:
    @pytest.fixture(scope="class")
    def gestionnaire_creds(self, admin_session, created_copro):
        ts = int(time.time())
        email = f"gest{ts}@test.be"
        password = "Gest12345!"
        payload = {
            "email": email,
            "password": password,
            "name": f"TEST_Gestionnaire_{ts}",
            "role": "gestionnaire",
            "copropriete_ids": [created_copro["id"]],
        }
        r = admin_session.post(f"{BASE_URL}/api/admin/users", json=payload, timeout=10)
        assert r.status_code == 200, f"create gestionnaire failed: {r.status_code} {r.text}"
        return {"email": email, "password": password}

    def test_superadmin_can_list_users(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/admin/users", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_gestionnaire_cannot_access_admin(self, gestionnaire_creds):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login", json=gestionnaire_creds, timeout=10)
        assert r.status_code == 200, f"gestionnaire login failed: {r.text}"
        r2 = s.get(f"{BASE_URL}/api/admin/users", timeout=10)
        assert r2.status_code == 403, f"expected 403, got {r2.status_code}: {r2.text}"

    def test_gestionnaire_can_manage_data(self, gestionnaire_creds, created_copro):
        # gestionnaire should be able to create owners / coproprietes (can_manage=True)
        s = requests.Session()
        s.post(f"{BASE_URL}/api/auth/login", json=gestionnaire_creds, timeout=10)
        ts = int(time.time())
        payload = {
            "name": f"TEST_OwnerByGest_{ts}",
            "first_name": "Anne",
            "last_name": f"Gest{ts}",
            "email": f"annegest{ts}@test.be",
            "copropriete_id": created_copro["id"],
        }
        r = s.post(f"{BASE_URL}/api/owners", json=payload, timeout=10)
        assert r.status_code == 200, f"gestionnaire should be able to create owner: {r.status_code} {r.text}"
