"""iter90gp — Verrouille le contrat backend de sniff-csv?kind=invoices/journals.

Bug : le frontend `CsvMappingView` crashait ("Cannot read properties of
undefined (reading 'map')") quand l'endpoint retournait `{invoices: [...]}`
sans `headers`/`rows`. Le fix montage-conditionnel exclut invoices/journals
du CsvMappingView. Ce test garantit que le backend continue de renvoyer
la structure attendue (invoices/transactions) pour ces kinds sans casser
les autres imports.

On teste seulement les invariants de structure de reponse, pas la logique
de parsing (couverte ailleurs).
"""
import os
import io
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://teuwen-reports.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200
    return s


@pytest.fixture(scope="module")
def import_session(admin_session):
    # Recupere une ACP existante
    r = admin_session.get(f"{BASE_URL}/api/coproprietes", timeout=30)
    copros = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    assert copros, "Aucune copro pour tester"
    copro_id = copros[0]["id"]
    # Cree une session d'import
    r = admin_session.post(
        f"{BASE_URL}/api/import-wizard/sessions",
        json={"copropriete_id": copro_id, "name": "iter90gp smoke test"},
        timeout=30,
    )
    assert r.status_code in (200, 201), f"Failed: {r.status_code} {r.text[:200]}"
    return r.json()


def test_iter90gp_sniff_csv_invoices_returns_invoices_key(admin_session, import_session):
    """Contrat : sniff-csv?kind=invoices doit retourner un dict avec 'invoices'
    (liste), meme sur un CSV minimal. Ne doit PAS lever sur un CSV valide.
    Le frontend s'appuie sur cette structure pour peupler `invoicesParsed`.
    """
    # CSV Optipro minimal (headers puis 1 ligne)
    csv_content = (
        "Date;Ref;Fournisseur;Compte;Libelle;Montant TTC\n"
        "01/01/2025;F001;ELEC SPRL;61010000;Facture test;123.45\n"
    ).encode("latin-1")
    files = {"file": ("test.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"kind": "invoices"}
    r = admin_session.post(
        f"{BASE_URL}/api/import-wizard/sessions/{import_session['id']}/sniff-csv",
        files=files,
        data=data,
        timeout=30,
    )
    assert r.status_code == 200, f"Status {r.status_code}: {r.text[:300]}"
    body = r.json()
    # Contrat frontend : la reponse structure DOIT avoir la cle `invoices`
    # (liste, potentiellement vide) - JAMAIS `headers`/`rows` pour ce kind,
    # sinon le frontend legacy tenterait de monter CsvMappingView et crasherait.
    assert "invoices" in body, f"Cle 'invoices' manquante : {list(body.keys())}"
    assert isinstance(body["invoices"], list)


def test_iter90gp_sniff_csv_generic_returns_headers_and_rows(admin_session, import_session):
    """Contrat inverse : sniff-csv SANS kind (generic) DOIT renvoyer headers+rows
    (utilises par CsvMappingView). Verrouille que le mode 'mapping manuel'
    marche toujours pour les etapes owners/lots/keys/etc.
    """
    csv_content = (
        "Nom;Prenom;Email\n"
        "Dupont;Jean;jean@ex.be\n"
    ).encode("utf-8")
    files = {"file": ("owners.csv", io.BytesIO(csv_content), "text/csv")}
    r = admin_session.post(
        f"{BASE_URL}/api/import-wizard/sessions/{import_session['id']}/sniff-csv",
        files=files,
        timeout=30,
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert isinstance(body.get("headers"), list) and len(body["headers"]) > 0, \
        f"headers manquants ou vides : {body}"
    assert isinstance(body.get("rows"), list)


def test_iter90gp_sniff_csv_journals_returns_transactions_key(admin_session, import_session):
    """Meme contrat que invoices, pour journals (branch parallele du frontend)."""
    csv_content = (
        "Date;Ref;Compte;Libelle;Debit;Credit\n"
        "01/01/2025;J001;550000;Virement;100;0\n"
    ).encode("latin-1")
    files = {"file": ("j.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"kind": "journals"}
    r = admin_session.post(
        f"{BASE_URL}/api/import-wizard/sessions/{import_session['id']}/sniff-csv",
        files=files,
        data=data,
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    assert "transactions" in body, f"Cle 'transactions' manquante : {list(body.keys())}"
    assert isinstance(body["transactions"], list)
