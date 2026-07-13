"""iter90fj : Gate homonyme fournisseur sur la sauvegarde de facture.

Couvre :
- create_invoice / update_invoice bloquent (409 SUPPLIER_HOMONYM) quand le
  fournisseur tape est un homonyme proche (>=0.80) d'une fiche existante et
  que `supplier_confirmed` n'est pas fourni.
- Un nom EXACTEMENT identique (ou identique apres normalisation / particules
  juridiques) snap silencieusement sur la fiche canonique (pas de 409).
- Un nom completement different (pas d'homonyme) s'enregistre directement.
- `supplier_confirmed=true` permet de passer outre le gate (creation
  explicite d'un nouveau fournisseur malgre l'homonyme).
- update_invoice ne re-verifie le gate QUE si le fournisseur soumis differe
  reellement de la valeur deja enregistree (pas de faux blocage sur un
  simple PUT qui touche juste le montant/description).
- GET /invoices/supplier-suggestion propage `occupant_pct` dans la reponse.
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
        pytest.skip("Login failed - skipping iter90fj gate tests")
    return s


@pytest.fixture(scope="module")
def copro_id(session):
    """ACP avec un exercice fiscal ouvert couvrant la date courante."""
    resp = session.get(f"{API}/coproprietes")
    assert resp.status_code == 200
    for acp in resp.json():
        fy = session.get(f"{API}/fiscal/years", params={"copropriete_id": acp["id"]})
        if fy.status_code == 200 and any(y.get("status") == "open" for y in fy.json()):
            return acp["id"]
    pytest.skip("Aucune ACP avec exercice fiscal ouvert trouvee")


@pytest.fixture(scope="module")
def base_supplier(session, copro_id):
    name = f"TEST_iter90fj_Gate_{uuid.uuid4().hex[:8]} SRL"
    resp = session.post(f"{API}/suppliers", json={"name": name, "copropriete_id": copro_id})
    assert resp.status_code == 200
    created = resp.json()
    yield created
    session.delete(f"{API}/suppliers/{created['id']}")


@pytest.fixture
def created_invoice_ids(session):
    ids = []
    yield ids
    for iid in ids:
        session.delete(f"{API}/invoices/{iid}")


class TestSupplierHomonymGateOnCreate:

    def test_homonym_typo_blocks_with_409(self, session, copro_id, base_supplier):
        typo_name = base_supplier["name"].replace(" SRL", "e SRL")  # petite variation proche
        resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": typo_name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 42.0,
            "vat_amount": 0,
            "description": "TEST iter90fj homonym typo",
        })
        assert resp.status_code == 409
        data = resp.json()
        detail = data.get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "SUPPLIER_HOMONYM"
        assert detail.get("typed_name") == typo_name
        assert isinstance(detail.get("similar"), list)
        assert len(detail["similar"]) >= 1
        assert detail["similar"][0]["name"] == base_supplier["name"]
        assert detail["similar"][0]["score"] >= 0.80

    def test_exact_match_snaps_silently_no_409(self, session, copro_id, base_supplier, created_invoice_ids):
        # Nom identique apres normalisation (sans particule "SRL")
        exact_name = base_supplier["name"].replace(" SRL", "")
        resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": exact_name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 10.0,
            "vat_amount": 0,
            "description": "TEST iter90fj exact match",
        })
        assert resp.status_code == 200
        created = resp.json()
        created_invoice_ids.append(created["id"])
        # Snap : le nom canonique de la fiche est utilise, pas le texte tape
        assert created["supplier"] == base_supplier["name"]

    def test_completely_new_name_saves_directly(self, session, copro_id, created_invoice_ids):
        unique_name = f"TEST_iter90fj_BrandNew_{uuid.uuid4().hex[:10]}"
        resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": unique_name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 15.0,
            "vat_amount": 0,
            "description": "TEST iter90fj brand new",
        })
        assert resp.status_code == 200
        created = resp.json()
        created_invoice_ids.append(created["id"])
        assert created["supplier"] == unique_name

    def test_supplier_confirmed_bypasses_gate(self, session, copro_id, base_supplier, created_invoice_ids):
        typo_name = base_supplier["name"].replace(" SRL", "y SRL")
        resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": typo_name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 20.0,
            "vat_amount": 0,
            "description": "TEST iter90fj confirmed bypass",
            "supplier_confirmed": True,
        })
        assert resp.status_code == 200
        created = resp.json()
        created_invoice_ids.append(created["id"])
        # supplier_confirmed=true : la facture garde le nom TAPE tel quel (nouveau fournisseur)
        assert created["supplier"] == typo_name


class TestSupplierHomonymGateOnUpdate:

    def test_update_supplier_to_homonym_blocks_with_409(self, session, copro_id, base_supplier, created_invoice_ids):
        create_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": f"TEST_iter90fj_EditBase_{uuid.uuid4().hex[:8]}",
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 30.0,
            "vat_amount": 0,
            "description": "TEST iter90fj edit base",
        })
        assert create_resp.status_code == 200
        inv = create_resp.json()
        created_invoice_ids.append(inv["id"])

        typo_name = base_supplier["name"].replace(" SRL", "z SRL")
        update_resp = session.put(f"{API}/invoices/{inv['id']}", json={
            "copropriete_id": copro_id,
            "supplier": typo_name,
            "number": inv["number"],
            "date": inv["date"],
            "total_amount": 30.0,
            "vat_amount": 0,
            "description": "TEST iter90fj edit base",
        })
        assert update_resp.status_code == 409
        detail = update_resp.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "SUPPLIER_HOMONYM"

    def test_update_without_touching_supplier_never_blocks(self, session, copro_id, created_invoice_ids):
        create_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": f"TEST_iter90fj_NoTouch_{uuid.uuid4().hex[:8]}",
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 50.0,
            "vat_amount": 0,
            "description": "TEST iter90fj no touch",
        })
        assert create_resp.status_code == 200
        inv = create_resp.json()
        created_invoice_ids.append(inv["id"])

        # Meme fournisseur, juste le montant change -> ne doit JAMAIS declencher le gate
        update_resp = session.put(f"{API}/invoices/{inv['id']}", json={
            "copropriete_id": copro_id,
            "supplier": inv["supplier"],
            "number": inv["number"],
            "date": inv["date"],
            "total_amount": 99.0,
            "vat_amount": 0,
            "description": "TEST iter90fj no touch",
        })
        assert update_resp.status_code == 200
        assert update_resp.json()["total_amount"] == 99.0


class TestSupplierSuggestionOccupantPct:

    def test_suggestion_includes_occupant_pct(self, session, copro_id, created_invoice_ids):
        supplier_name = f"TEST_iter90fj_Suggest_{uuid.uuid4().hex[:8]}"
        cat_resp = session.get(f"{API}/expense-categories", params={"copropriete_id": copro_id})
        assert cat_resp.status_code == 200
        cats = cat_resp.json()
        if not cats:
            pytest.skip("Aucune nature de depense disponible pour ce test")
        cat = cats[0]

        create_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": supplier_name,
            "number": f"TEST-{uuid.uuid4().hex[:6]}",
            "date": "2026-02-10",
            "total_amount": 40.0,
            "vat_amount": 0,
            "description": "TEST iter90fj suggestion occupant_pct",
            "expense_category_id": cat["id"],
            "occupant_pct": 42.0,
        })
        assert create_resp.status_code == 200
        inv = create_resp.json()
        created_invoice_ids.append(inv["id"])
        assert inv["occupant_pct"] == 42.0

        sug_resp = session.get(f"{API}/invoices/supplier-suggestion", params={
            "supplier": supplier_name, "copropriete_id": copro_id,
        })
        assert sug_resp.status_code == 200
        suggestion = sug_resp.json().get("suggestion")
        assert suggestion is not None
        assert suggestion["expense_category_id"] == cat["id"]
        # iter90fj : occupant_pct appris (35%) doit etre propage dans la suggestion
        assert suggestion.get("occupant_pct") == 42.0

        # Revert the learned default on the category to avoid polluting other tests
        session.put(f"{API}/expense-categories/{cat['id']}", json={
            **{k: v for k, v in cat.items() if k not in ("invoice_count", "invoice_total")},
            "default_occupant_pct": cat.get("default_occupant_pct"),
            "default_proprietaire_pct": cat.get("default_proprietaire_pct"),
        })
