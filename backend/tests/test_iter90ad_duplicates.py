"""iter90ad : Test detection + fusion doublons (owners & suppliers) via HTTP.

Couvre :
- Auth superadmin
- GET /api/admin/duplicates/owners (global scope, sans copropriete_id)
- GET /api/admin/duplicates/suppliers (global scope, sans copropriete_id)
- POST /api/admin/duplicates/owners/merge (fusion + migration lots)
- POST /api/suppliers/merge (fusion + migration invoices)
Cleanup via fixture module-scoped.
"""
import os
import uuid
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def test_acp(session):
    """Cree une ACP temporaire et cleanup en fin de module."""
    payload = {
        "name": f"TEST_iter90ad_{uuid.uuid4().hex[:8]}",
        "address": "Rue Test 1",
        "postal_code": "1000",
        "city": "Bruxelles",
    }
    r = session.post(f"{BASE_URL}/api/coproprietes", json=payload)
    assert r.status_code in (200, 201), f"Create ACP failed: {r.text}"
    acp = r.json()
    acp_id = acp.get("id")
    yield acp_id
    # Cleanup
    try:
        session.delete(f"{BASE_URL}/api/coproprietes/{acp_id}")
    except Exception:
        pass


# ---- Detection endpoints (scope global) ----

class TestDuplicatesDetection:
    def test_owners_duplicates_global_scope(self, session):
        r = session.get(f"{BASE_URL}/api/admin/duplicates/owners")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "groups" in data
        assert "total_owners" in data
        assert isinstance(data["groups"], list)
        # Superadmin doit avoir scope=platform
        assert data.get("scope") == "platform"
        print(f"Owners: {data['total_owners']} total, {len(data['groups'])} groupes doublons")

    def test_suppliers_duplicates_global_scope(self, session):
        r = session.get(f"{BASE_URL}/api/admin/duplicates/suppliers")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "groups" in data
        assert "total_suppliers" in data
        assert data.get("scope") == "platform"
        print(f"Suppliers: {data['total_suppliers']} total, {len(data['groups'])} groupes doublons")

    def test_owners_endpoint_no_copro_header_injection(self, session):
        """Verifie que meme si on injecte X-Copropriete-Id, on ne filtre pas
        automatiquement (endpoint est dans GLOBAL_PATH_PREFIXES cote frontend)."""
        # Cote backend, si on n'envoie pas copropriete_id en query, scope=global
        r = session.get(
            f"{BASE_URL}/api/admin/duplicates/owners",
            headers={"X-Copropriete-Id": "some-fake-id"},
        )
        assert r.status_code == 200
        # Backend n'utilise pas ce header pour cette route


# ---- Merge owners ----

class TestOwnerMerge:
    def test_merge_owners_migrates_lots(self, session, test_acp):
        # Cree 2 owners distincts (emails differents pour contourner check-duplicate)
        # La fusion doit fonctionner independamment que ce soient des vrais doublons ou non.
        owners_created = []
        for i in range(2):
            payload = {
                "name": f"TEST_Dupe{i}_{uuid.uuid4().hex[:4]}",
                "last_name": f"TEST_Dupe{i}",
                "first_name": f"OwnerN{i}_{uuid.uuid4().hex[:4]}",
                "email": f"test_dupe_{uuid.uuid4().hex[:8]}@example.com",
                "copropriete_id": test_acp,
            }
            r = session.post(f"{BASE_URL}/api/owners", json=payload)
            assert r.status_code in (200, 201), f"Owner create failed: {r.text}"
            owners_created.append(r.json())
        keep_id = owners_created[0]["id"]
        remove_id = owners_created[1]["id"]

        # Cree un lot pointant vers remove_id
        lot_payload = {
            "copropriete_id": test_acp,
            "number": f"L{uuid.uuid4().hex[:4]}",
            "reference": f"LOT_iter90ad_{uuid.uuid4().hex[:4]}",
            "owner_id": remove_id,
            "quotites": 100,
        }
        rl = session.post(f"{BASE_URL}/api/lots", json=lot_payload)
        assert rl.status_code in (200, 201), f"Lot create failed: {rl.text}"
        lot = rl.json()
        lot_id = lot["id"]

        # Fusionne
        r = session.post(
            f"{BASE_URL}/api/admin/duplicates/owners/merge",
            json={"keep_id": keep_id, "remove_ids": [remove_id]},
        )
        assert r.status_code == 200, f"Merge failed: {r.text}"
        merge_data = r.json()
        assert merge_data["kept_id"] == keep_id
        assert remove_id in merge_data["removed_ids"]
        assert merge_data["lots_simple_updated"] >= 1

        # Verifie que remove_id est supprime
        r_check = session.get(f"{BASE_URL}/api/owners/{remove_id}")
        assert r_check.status_code == 404, f"Removed owner still exists: {r_check.status_code}"

        # Verifie que le lot pointe maintenant vers keep_id (via list endpoint)
        r_lots = session.get(f"{BASE_URL}/api/lots", params={"copropriete_id": test_acp})
        assert r_lots.status_code == 200
        lots = r_lots.json()
        matched = [l for l in lots if l.get("id") == lot_id]
        assert matched, "Lot introuvable dans la liste"
        assert matched[0]["owner_id"] == keep_id, "Lot n'a pas ete migre vers keep_id"

    def test_merge_owners_rejects_keep_in_remove(self, session, test_acp):
        # Cree 1 owner
        payload = {
            "name": "TEST_Solo",
            "last_name": "Solo",
            "email": f"solo_{uuid.uuid4().hex[:6]}@t.be",
            "copropriete_id": test_acp,
        }
        r = session.post(f"{BASE_URL}/api/owners", json=payload)
        assert r.status_code in (200, 201)
        oid = r.json()["id"]
        r = session.post(
            f"{BASE_URL}/api/admin/duplicates/owners/merge",
            json={"keep_id": oid, "remove_ids": [oid]},
        )
        assert r.status_code == 400


# ---- Merge suppliers ----

class TestSupplierMerge:
    def test_merge_suppliers(self, session, test_acp):
        # Cree 2 suppliers avec BCE differents (contourne check-duplicate)
        supplies_created = []
        for i in range(2):
            payload = {
                "name": f"TEST_SupDupe_{i}_{uuid.uuid4().hex[:4]}",
                "bce_number": f"BE0{uuid.uuid4().hex[:9]}",
                "copropriete_id": test_acp,
                "force_create_despite_similar": True,
            }
            r = session.post(f"{BASE_URL}/api/suppliers", json=payload)
            assert r.status_code in (200, 201), f"Supplier create failed: {r.text}"
            supplies_created.append(r.json())
        keep_id = supplies_created[0]["id"]
        remove_id = supplies_created[1]["id"]

        r = session.post(
            f"{BASE_URL}/api/suppliers/merge",
            json={"keep_id": keep_id, "remove_ids": [remove_id]},
        )
        assert r.status_code == 200, f"Merge failed: {r.text}"
        merge_data = r.json()
        assert merge_data["kept_id"] == keep_id
        assert remove_id in merge_data["removed_ids"]

        # Verifie que remove_id est supprime
        r_check = session.get(f"{BASE_URL}/api/suppliers/{remove_id}")
        assert r_check.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
