"""iter90fn : la repartition Occupant/Proprietaire (occupant_pct /
proprietaire_pct) doit TOUJOURS sommer a 100, jamais 2 valeurs
independantes (bug constate en production : une facture JAG SPRL
affichait "100% / 100%" dans la Liste des Depenses au lieu de "100%/0%").

Root cause : plusieurs anciens documents (factures, lignes de facture,
lignes d'ecriture OD/FI) ont ete enregistres avec les 2 champs stockes
independamment (donnees historiques / import). `expense_rows.py` et
`routes/accounting.py::update_entry_line_quick` faisaient confiance au
champ `proprietaire_pct` stocke tel quel au lieu de le deriver de
`occupant_pct` (seul champ de reference).

Ce test simule une facture "corrompue" directement en base (comme les
donnees historiques) et verifie que la Liste des Depenses "auto-guerit"
l'affichage (proprietaire_pct = 100 - occupant_pct), meme si occupant_pct
et proprietaire_pct sont stockes a 100/100 en base.
"""
import os
import uuid
import pytest
import requests
import pymongo

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME')


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    resp = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    if resp.status_code != 200:
        pytest.skip("Login failed - skipping iter90fn repartition tests")
    return s


@pytest.fixture(scope="module")
def copro_and_date(session):
    resp = session.get(f"{API}/coproprietes", params={"show_archived": "true"})
    assert resp.status_code == 200
    for acp in resp.json():
        fy = session.get(f"{API}/fiscal/years", params={"copropriete_id": acp["id"]})
        if fy.status_code != 200:
            continue
        open_years = [y for y in fy.json() if y.get("status") == "open"]
        if not open_years:
            continue
        pcmn = session.get(f"{API}/accounting/pcmn", params={"copropriete_id": acp["id"]})
        if pcmn.status_code == 200:
            numbers = {a.get("number") for a in pcmn.json()}
            if "61000" in numbers and "100" in numbers:
                return acp["id"], open_years[0]["start_date"]
    pytest.skip("Aucune ACP avec exercice ouvert + comptes 61000/100 trouvee")


class TestExpenseRowsRepartitionSelfHeals:

    def test_corrupted_100_100_invoice_self_heals_in_expenses_list(self, session, copro_and_date):
        """Simule une facture historique corrompue (occupant_pct=100 ET
        proprietaire_pct=100 stockes independamment) en passant directement
        par l'API de creation SANS le garde-fou (le garde-fou serveur
        recalcule toujours proprietaire_pct a la creation - donc ce test
        cree la facture normalement puis PATCH directement le champ en base
        via une ecriture Mongo n'est pas possible depuis les tests HTTP;
        on verifie donc plutot que le calcul reste coherent meme pour une
        facture normalement creee, ET que expense_rows derive toujours
        proprietaire_pct = 100 - occupant_pct quel que soit ce qui est
        renvoye)."""
        copro_id, entry_date = copro_and_date
        ref = f"TEST-iter90fn-{uuid.uuid4().hex[:8]}"
        create_resp = session.post(f"{API}/invoices", json={
            "copropriete_id": copro_id,
            "supplier": f"TEST_iter90fn_Supplier_{uuid.uuid4().hex[:6]}",
            "number": ref,
            "date": entry_date,
            "total_amount": 150.0,
            "vat_amount": 0,
            "description": "TEST iter90fn repartition",
            "account_number": "61000",
            "occupant_pct": 100,
        })
        assert create_resp.status_code == 200, create_resp.text
        inv = create_resp.json()
        assert inv["occupant_pct"] == 100
        assert inv["proprietaire_pct"] == 0, "Le serveur doit toujours deriver proprietaire_pct = 100 - occupant_pct"

        exp_resp = session.get(f"{API}/fiscal/expenses", params={"copropriete_id": copro_id})
        assert exp_resp.status_code == 200
        rows = [r for r in exp_resp.json().get("expenses", []) if r.get("number") == ref]
        assert len(rows) == 1
        row = rows[0]
        assert row["occupant_pct"] == 100
        assert row["proprietaire_pct"] == 0, (
            f"BUG iter90fn : proprietaire_pct devrait etre 0 (100-100), trouve {row['proprietaire_pct']}"
        )
        assert abs(row["occupant_pct"] + row["proprietaire_pct"] - 100) < 0.01

        session.delete(f"{API}/invoices/{inv['id']}")

    def test_legacy_corrupted_100_100_invoice_self_heals_via_direct_db_injection(self, session, copro_and_date):
        """Reproduction FIDELE du bug historique de production : injecte
        directement en base (comme des donnees importees/anciennes, sans
        passer par l'API qui recalcule toujours) une facture avec
        occupant_pct=100 ET proprietaire_pct=100 stockes INDEPENDAMMENT,
        puis verifie que `/api/fiscal/expenses` corrige l'affichage a la
        lecture (100/0), sans jamais modifier le document source."""
        copro_id, entry_date = copro_and_date
        client = pymongo.MongoClient(MONGO_URL)
        db = client[DB_NAME]
        ref = f"TEST-iter90fn-legacy-{uuid.uuid4().hex[:8]}"
        inv_id = str(uuid.uuid4())
        corrupted_doc = {
            "id": inv_id,
            "copropriete_id": copro_id,
            "supplier": f"TEST_iter90fn_Legacy_{uuid.uuid4().hex[:6]}",
            "number": ref,
            "date": entry_date,
            "total_amount": 195.0,
            "vat_amount": 0.0,
            "description": "TEST iter90fn legacy corrompu",
            "account_number": "61000",
            "status": "unpaid",
            "occupant_pct": 100.0,
            "proprietaire_pct": 100.0,  # BUG historique : jamais derive, stocke tel quel
            "attachments": [],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        try:
            db.invoices.insert_one(corrupted_doc)

            exp_resp = session.get(f"{API}/fiscal/expenses", params={"copropriete_id": copro_id})
            assert exp_resp.status_code == 200
            rows = [r for r in exp_resp.json().get("expenses", []) if r.get("number") == ref]
            assert len(rows) == 1, f"Facture legacy corrompue non trouvee dans la Liste des Depenses"
            row = rows[0]
            assert row["occupant_pct"] == 100, "occupant_pct doit rester la valeur de reference"
            assert row["proprietaire_pct"] == 0, (
                f"BUG iter90fn NON CORRIGE : la facture legacy 100%/100% en base "
                f"apparait encore comme proprietaire_pct={row['proprietaire_pct']} "
                f"(attendu 0, derive de 100 - occupant_pct)"
            )
            assert abs(row["occupant_pct"] + row["proprietaire_pct"] - 100) < 0.01
        finally:
            db.invoices.delete_one({"id": inv_id})
            client.close()

    def test_line_quick_edit_always_enforces_complementary_pct(self, session, copro_and_date):
        """`PUT /accounting/entries/{id}/line-quick` doit TOUJOURS deriver
        automatiquement proprietaire_pct depuis occupant_pct (jamais 2
        valeurs independantes), meme si le body envoie les 2 champs."""
        copro_id, entry_date = copro_and_date
        ref = f"TEST-iter90fn-line-{uuid.uuid4().hex[:8]}"
        create_resp = session.post(f"{API}/accounting/entries", json={
            "copropriete_id": copro_id,
            "journal_type": "OD",
            "date": entry_date,
            "reference": ref,
            "description": "TEST iter90fn line-quick",
            "lines": [
                {"account_number": "61000", "debit": 80.0, "credit": 0,
                 "description": "TEST charge"},
                {"account_number": "100", "debit": 0, "credit": 80.0,
                 "description": "TEST contrepartie"},
            ],
        })
        assert create_resp.status_code == 200, create_resp.text
        entry = create_resp.json()

        # Tente d'enregistrer occupant_pct=100 ET proprietaire_pct=100 (bug historique)
        patch_resp = session.put(
            f"{API}/accounting/entries/{entry['id']}/line-quick",
            json={"line_account": "61000", "occupant_pct": 100, "proprietaire_pct": 100},
        )
        assert patch_resp.status_code == 200, patch_resp.text

        check_resp = session.get(f"{API}/accounting/entries", params={"copropriete_id": copro_id})
        assert check_resp.status_code == 200
        matches = [e for e in check_resp.json() if e.get("id") == entry["id"]]
        assert len(matches) == 1
        line = next(ln for ln in matches[0]["lines"] if ln["account_number"] == "61000")
        assert line["occupant_pct"] == 100
        assert line["proprietaire_pct"] == 0, (
            f"BUG iter90fn : le serveur doit forcer proprietaire_pct=0 (100-100), trouve {line['proprietaire_pct']}"
        )

        session.delete(f"{API}/accounting/entries/{entry['id']}")
