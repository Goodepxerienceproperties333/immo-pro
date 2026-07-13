"""iter90fk : nettoyage automatique des fiches fournisseurs auto-creees
orphelines lors de la suppression d'une facture.

Couvre :
- Une fiche `auto_created=True` qui n'est plus referencee par AUCUNE
  facture apres suppression est supprimee automatiquement.
- Une fiche `auto_created=True` encore referencee par une AUTRE facture
  (meme nom) N'est PAS supprimee.
- Une fiche creee EXPLICITEMENT par le syndic (`auto_created` absent) N'est
  JAMAIS supprimee automatiquement, meme devenue inutilisee.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    resp = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    if resp.status_code != 200:
        pytest.skip("Login failed - skipping iter90fk cleanup tests")
    return s


@pytest.fixture(scope="module")
def copro_id(session):
    resp = session.get(f"{API}/coproprietes")
    assert resp.status_code == 200
    for acp in resp.json():
        fy = session.get(f"{API}/fiscal/years", params={"copropriete_id": acp["id"]})
        if fy.status_code == 200 and any(y.get("status") == "open" for y in fy.json()):
            return acp["id"]
    pytest.skip("Aucune ACP avec exercice fiscal ouvert trouvee")


def _find_supplier_by_name(session, copro_id, name):
    resp = session.get(f"{API}/suppliers", params={"copropriete_id": copro_id})
    assert resp.status_code == 200
    for s in resp.json():
        if s.get("name") == name:
            return s
    return None


class TestCleanupOrphanAutoSupplier:

    def test_orphan_auto_supplier_deleted_when_last_invoice_removed(self, session, copro_id):
        name = f"TEST_iter90fk_Orphan_{uuid.uuid4().hex[:8]} SPRL"
        create_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 10.0,
            "vat_amount": 0,
            "description": "TEST iter90fk orphan",
            "account_number": "61000",
        })
        assert create_resp.status_code == 200
        inv = create_resp.json()

        card = _find_supplier_by_name(session, copro_id, name)
        assert card is not None
        assert card.get("auto_created") is True

        del_resp = session.delete(f"{API}/invoices/{inv['id']}")
        assert del_resp.status_code == 200

        card_after = _find_supplier_by_name(session, copro_id, name)
        assert card_after is None

    def test_auto_supplier_kept_while_another_invoice_still_references_it(self, session, copro_id):
        name = f"TEST_iter90fk_Shared_{uuid.uuid4().hex[:8]} SPRL"
        payload = {
            "copropriete_id": copro_id,
            "supplier": name,
            "date": "2026-02-10",
            "vat_amount": 0,
            "account_number": "61000",
        }
        r1 = session.post(f"{API}/invoices", json={**payload, "number": f"TEST-{uuid.uuid4().hex[:6]}", "total_amount": 10.0, "description": "t1"})
        r2 = session.post(f"{API}/invoices", json={**payload, "number": f"TEST-{uuid.uuid4().hex[:6]}", "total_amount": 20.0, "description": "t2"})
        assert r1.status_code == 200 and r2.status_code == 200
        inv1, inv2 = r1.json(), r2.json()

        del1 = session.delete(f"{API}/invoices/{inv1['id']}")
        assert del1.status_code == 200

        # Toujours reference par inv2 -> la fiche doit persister
        card_mid = _find_supplier_by_name(session, copro_id, name)
        assert card_mid is not None

        del2 = session.delete(f"{API}/invoices/{inv2['id']}")
        assert del2.status_code == 200

        card_final = _find_supplier_by_name(session, copro_id, name)
        assert card_final is None

    def test_manually_created_supplier_never_auto_deleted(self, session, copro_id):
        name = f"TEST_iter90fk_Manual_{uuid.uuid4().hex[:8]} SPRL"
        sup_resp = session.post(f"{API}/suppliers", json={"name": name, "copropriete_id": copro_id})
        assert sup_resp.status_code == 200
        card = sup_resp.json()
        assert not card.get("auto_created")

        inv_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 10.0,
            "vat_amount": 0,
            "description": "TEST iter90fk manual",
            "account_number": "61000",
        })
        assert inv_resp.status_code == 200
        inv = inv_resp.json()

        del_resp = session.delete(f"{API}/invoices/{inv['id']}")
        assert del_resp.status_code == 200

        # Fiche cree explicitement -> ne doit JAMAIS etre supprimee automatiquement
        card_after = _find_supplier_by_name(session, copro_id, name)
        assert card_after is not None
        assert card_after["id"] == card["id"]

        # Cleanup manuel de la fiche de test
        session.delete(f"{API}/suppliers/{card['id']}")
