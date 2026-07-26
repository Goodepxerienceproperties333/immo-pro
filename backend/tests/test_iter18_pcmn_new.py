"""Iter18 - Tests pour nouveau PCMN belge (327 + 10 compat = 337) + CRUD custom + migration + PDF Liste des depenses + regression."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")
DEMO_ACP = "6748ca1a-216d-4002-8417-799287238736"
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASS = "admin123"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


# ------- PCMN listing --------------------------------------------------------
class TestPCMNList:
    def test_pcmn_count_337(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/accounting/pcmn", params={"copropriete_id": DEMO_ACP}, timeout=20)
        assert r.status_code == 200
        accounts = r.json()
        # Should have 337 (327 official + 10 compat) after migration applied on existing ACP
        assert len(accounts) >= 337, f"Expected >=337 accounts, got {len(accounts)}"
        nums = {a["number"] for a in accounts}
        # Spot check official accounts
        for expected in ("100", "410", "61000", "6130", "76001"):
            assert expected in nums, f"Missing official account {expected}"
        # Compat accounts must exist
        for expected in ("400000", "440000", "550000", "614000", "615000", "700000", "701000"):
            assert expected in nums, f"Missing compat account {expected}"

    def test_pcmn_filter_class_num(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/accounting/pcmn",
                              params={"copropriete_id": DEMO_ACP, "class_num": 6}, timeout=20)
        assert r.status_code == 200
        accounts = r.json()
        assert len(accounts) > 0
        assert all(a["class_num"] == 6 for a in accounts)
        # 614000 and 615000 are compat class 6
        nums = {a["number"] for a in accounts}
        assert "614000" in nums and "615000" in nums

    def test_pcmn_614_615_active_by_default(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/accounting/pcmn",
                              params={"copropriete_id": DEMO_ACP}, timeout=20)
        accounts = {a["number"]: a for a in r.json()}
        assert accounts["614000"]["active"] is True
        assert accounts["615000"]["active"] is True


# ------- Migration endpoint --------------------------------------------------
class TestMigration:
    def test_migrate_pcmn_import_idempotent(self, admin_session):
        # First call - may add 0 or more depending on prior state
        r1 = admin_session.post(f"{BASE_URL}/api/admin/migrate/pcmn-import",
                                params={"copropriete_id": DEMO_ACP}, timeout=30)
        assert r1.status_code == 200, f"{r1.status_code} {r1.text}"
        data1 = r1.json()
        assert "acps" in data1 and "total_added" in data1
        # Second call must add 0
        r2 = admin_session.post(f"{BASE_URL}/api/admin/migrate/pcmn-import",
                                params={"copropriete_id": DEMO_ACP}, timeout=30)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["total_added"] == 0, f"Migration not idempotent: added {data2['total_added']} on second call"
        # Total existing should be >=337
        assert data2["total_existing"] >= 337


# ------- CRUD Custom Accounts -----------------------------------------------
class TestCustomPCMN:
    @pytest.fixture(scope="class", autouse=True)
    def cleanup(self, admin_session):
        yield
        for num in self._created:
            try:
                admin_session.delete(f"{BASE_URL}/api/accounting/pcmn/{num}",
                                     params={"copropriete_id": DEMO_ACP}, timeout=10)
            except Exception:
                pass

    _created = []

    def test_create_custom_account_auto_class_type(self, admin_session):
        num = f"6999{int(time.time()) % 100000}"
        r = admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                               json={"number": num, "name": "TEST_iter18 custom 6", "copropriete_id": DEMO_ACP},
                               timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["class_num"] == 6
        assert d["type"] == "result"
        assert d["is_custom"] is True
        assert d["active"] is True
        self.__class__._created.append(num)

    def test_create_custom_class1_balance(self, admin_session):
        num = f"1999{int(time.time()) % 100000}"
        r = admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                               json={"number": num, "name": "TEST_iter18 custom 1", "copropriete_id": DEMO_ACP},
                               timeout=10)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["class_num"] == 1 and d["type"] == "balance"
        self.__class__._created.append(num)

    def test_duplicate_create_refused(self, admin_session):
        num = f"4999{int(time.time()) % 100000}"
        r1 = admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                                json={"number": num, "name": "TEST_iter18 dup", "copropriete_id": DEMO_ACP},
                                timeout=10)
        assert r1.status_code == 200
        self.__class__._created.append(num)
        r2 = admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                                json={"number": num, "name": "TEST_iter18 dup2", "copropriete_id": DEMO_ACP},
                                timeout=10)
        assert r2.status_code == 400

    def test_put_updates_account(self, admin_session):
        num = f"6998{int(time.time()) % 100000}"
        admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                           json={"number": num, "name": "Orig", "copropriete_id": DEMO_ACP}, timeout=10)
        self.__class__._created.append(num)
        r = admin_session.put(f"{BASE_URL}/api/accounting/pcmn/{num}",
                              json={"name": "Updated", "active": False, "copropriete_id": DEMO_ACP}, timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["name"] == "Updated"
        assert d["active"] is False

    def test_toggle_active(self, admin_session):
        num = f"6997{int(time.time()) % 100000}"
        admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                           json={"number": num, "name": "TEST_iter18 toggle", "copropriete_id": DEMO_ACP}, timeout=10)
        self.__class__._created.append(num)
        r = admin_session.patch(f"{BASE_URL}/api/accounting/pcmn/{num}/toggle-active",
                                params={"copropriete_id": DEMO_ACP},
                                json={"active": False}, timeout=10)
        assert r.status_code == 200
        assert r.json()["active"] is False

    def test_delete_custom_success(self, admin_session):
        num = f"6996{int(time.time()) % 100000}"
        admin_session.post(f"{BASE_URL}/api/accounting/pcmn",
                           json={"number": num, "name": "TEST_iter18 todel", "copropriete_id": DEMO_ACP}, timeout=10)
        r = admin_session.delete(f"{BASE_URL}/api/accounting/pcmn/{num}",
                                 params={"copropriete_id": DEMO_ACP}, timeout=10)
        assert r.status_code == 200

    def test_delete_official_refused(self, admin_session):
        # 100 is official, not is_custom
        r = admin_session.delete(f"{BASE_URL}/api/accounting/pcmn/100",
                                 params={"copropriete_id": DEMO_ACP}, timeout=10)
        assert r.status_code == 400
        assert "officiel" in r.text.lower() or "PCMN" in r.text

    def test_delete_official_with_force(self, admin_session):
        # Use a rarely-used official account like "693" (Valeurs a reporter)
        # Check it exists then force delete and re-add via migration
        r_check = admin_session.get(f"{BASE_URL}/api/accounting/pcmn",
                                    params={"copropriete_id": DEMO_ACP}, timeout=20)
        nums = {a["number"] for a in r_check.json()}
        if "693" not in nums:
            pytest.skip("693 not seeded")
        r = admin_session.delete(f"{BASE_URL}/api/accounting/pcmn/693",
                                 params={"copropriete_id": DEMO_ACP, "force": "true"}, timeout=10)
        assert r.status_code in (200, 409), r.text
        # Re-add via migration
        admin_session.post(f"{BASE_URL}/api/admin/migrate/pcmn-import",
                           params={"copropriete_id": DEMO_ACP}, timeout=30)


# ------- PDF Liste des depenses ----------------------------------------------
class TestPDFListeDepenses:
    def test_pdf_basic(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reports/depenses/pdf",
                              params={"copropriete_id": DEMO_ACP,
                                      "date_from": "2024-01-01", "date_to": "2025-12-31"},
                              timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 1000, f"PDF too small: {len(r.content)} bytes"
        assert r.content[:4] == b"%PDF", "Not a valid PDF"

    def test_pdf_with_account_filter(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/reports/depenses/pdf",
                              params={"copropriete_id": DEMO_ACP,
                                      "date_from": "2024-01-01", "date_to": "2025-12-31",
                                      "account_number": "614000"},
                              timeout=30)
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"


# ------- Regression ----------------------------------------------------------
class TestRegression:
    def test_decompte_pdf_still_works(self, admin_session):
        owners = admin_session.get(f"{BASE_URL}/api/owners", timeout=15).json()
        if not owners:
            pytest.skip("No owners")
        oid = owners[0]["id"]
        r = admin_session.get(f"{BASE_URL}/api/reports/decompte/pdf/{oid}",
                              params={"copropriete_id": DEMO_ACP,
                                      "date_from": "2024-01-01", "date_to": "2025-12-31"},
                              timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.content[:4] == b"%PDF"
        assert len(r.content) > 1000

    def test_compat_accounts_present(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/accounting/pcmn",
                              params={"copropriete_id": DEMO_ACP}, timeout=20)
        nums = {a["number"] for a in r.json()}
        for required in ("400000", "440000", "550000", "550100", "614000",
                         "615000", "700000", "701000"):
            assert required in nums, f"Compat account {required} missing"

    def test_create_acp_seeds_337(self, admin_session):
        name = f"TEST_iter18_ACP_{uuid.uuid4().hex[:6]}"
        r = admin_session.post(f"{BASE_URL}/api/coproprietes",
                               json={"name": name, "address": "Rue Test 1", "vat_number": "BE0123456789"},
                               timeout=30)
        assert r.status_code in (200, 201), r.text
        new_id = r.json()["id"]
        try:
            r2 = admin_session.get(f"{BASE_URL}/api/accounting/pcmn",
                                   params={"copropriete_id": new_id}, timeout=20)
            assert r2.status_code == 200
            count = len(r2.json())
            assert count >= 337, f"New ACP seeded {count} accounts, expected >=337"
            nums = {a["number"] for a in r2.json()}
            assert "614000" in nums and "440000" in nums and "76001" in nums
        finally:
            admin_session.delete(f"{BASE_URL}/api/coproprietes/{new_id}", timeout=15)
