"""
iter90fo : REGRESSION LOCK - bug P1 "nature de depense grisee / modification
non enregistree" sur l'edition de factures.

Bugs rapportes utilisateur (Feb 2026) :
> "impossible de modifier une facture apres sauvegarde ca doit toujours etre
> possible -- Corrige"
> "nature de depense grisee en production donc impossible de changer"
> "en partant de la liste des depenses, il est possible d'editer une facture,
> cependant, la modification n'est pas enregistree"

Root cause :
1. Frontend (`InvoicesPage.js::openEditInvoice`) : toute facture stockee
   avec un tableau `lines` non-vide (meme UNE seule entree - cas frequent
   des imports CODA/Optipro et extractions IA) activait le mode "lignes
   multiples", qui applique `opacity-50 pointer-events-none` sur le bloc
   Nature de depense / Compte PCMN / Cle de repartition -> champ grise et
   non-cliquable pour l'utilisateur.
2. Backend (`routes/invoices.py::update_invoice`) : meme quand l'edition se
   fait via les champs top-level (quick-edit de la Liste des Depenses, qui
   n'envoie jamais `lines` dans le body), l'ancienne `lines[0]` figee en
   base n'etait JAMAIS resynchronisee. Or `expense_rows.py` donne PRIORITE
   a `lines` sur les champs top-level quand `lines` est non-vide -> la
   Liste des Depenses continuait d'afficher l'ANCIENNE nature/compte apres
   une modification "reussie" (200 OK), donnant l'impression que l'edition
   n'est pas enregistree.

Fix :
- Frontend : `openEditInvoice` "aplatit" une facture a 1 seule ligne dans
  les champs top-level (mode normal, editable) ; les VRAIES factures
  multi-lignes (>= 2 lignes) restent en mode "lignes multiples".
- Backend : quand `data.lines` n'est pas fourni (None) et que la facture
  existante n'a qu'UNE seule ligne, `update_invoice` resynchronise cette
  ligne unique avec les nouvelles valeurs top-level a chaque sauvegarde.

Ce test reproduit fidelement le bug (injection directe d'une facture
"legacy 1-ligne" comme dans les donnees de production), edite via
PUT /api/invoices/{id} (comme le fait le quick-edit de la Liste des
Depenses OU le dialog principal apres flatten), puis verifie que
GET /api/fiscal/expenses reflete bien la nouvelle nature/compte.
"""
import os
import uuid

import pytest
import pymongo
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
API = f"{BASE_URL}/api"
MONGO_URL = os.environ.get('MONGO_URL')
DB_NAME = os.environ.get('DB_NAME')


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    resp = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    if resp.status_code != 200:
        pytest.skip("Login failed - skipping iter90fo invoice edit tests")
    return s


@pytest.fixture(scope="module")
def copro_and_categories(session):
    resp = session.get(f"{API}/coproprietes", params={"show_archived": "true"})
    assert resp.status_code == 200
    for acp in resp.json():
        fy = session.get(f"{API}/fiscal/years", params={"copropriete_id": acp["id"]})
        if fy.status_code != 200:
            continue
        open_years = [y for y in fy.json() if y.get("status") == "open"]
        if not open_years:
            continue
        cats_resp = session.get(f"{API}/expense-categories", params={"copropriete_id": acp["id"]})
        if cats_resp.status_code != 200:
            continue
        cats = cats_resp.json()
        if len(cats) >= 2:
            return acp["id"], open_years[0], cats[0], cats[1]
    pytest.skip("Aucune ACP avec exercice ouvert + >=2 natures de depense trouvee")


class TestLegacySingleLineInvoiceEditSyncs:

    def test_editing_top_level_category_syncs_stale_legacy_line(self, session, copro_and_categories):
        """Reproduction fidele du bug production : facture stockee avec
        `lines` = UNE seule entree (comme un import CODA/Optipro), editee
        via un PUT qui ne fournit PAS `lines` (comme le quick-edit de la
        Liste des Depenses). La nouvelle nature/compte DOIT se refleter
        dans la Liste des Depenses (qui priorise `lines` sur les champs
        top-level)."""
        copro_id, fy, cat_a, cat_b = copro_and_categories
        mid_date = fy["start_date"]

        client = pymongo.MongoClient(MONGO_URL)
        db = client[DB_NAME]
        inv_id = str(uuid.uuid4())
        ref = f"TEST-iter90fo-{uuid.uuid4().hex[:8]}"
        legacy_doc = {
            "id": inv_id, "copropriete_id": copro_id, "number": ref,
            "internal_reference": f"TEST-iter90fo-INT-{uuid.uuid4().hex[:6]}",
            "date": mid_date, "due_date": mid_date,
            "supplier": f"TEST_iter90fo_Supplier_{uuid.uuid4().hex[:6]}",
            "description": "TEST iter90fo legacy 1-ligne", "total_amount": 88.0, "vat_amount": 0.0,
            "account_number": "", "expense_category_id": "", "distribution_key_id": "",
            "status": "unpaid", "is_private_fee": False, "private_fee_owner_id": "",
            "private_fee_allocations": [], "occupant_pct": 100.0, "proprietaire_pct": 0.0,
            "lines": [{
                "account_number": cat_a["account_number"], "expense_category_id": cat_a["id"],
                "distribution_key_id": "", "amount": 88.0, "description": "",
                "occupant_pct": 100.0, "proprietaire_pct": 0.0,
            }],
            "distribution_lines": [], "attachments": [], "created_at": "2026-01-01T00:00:00+00:00",
        }
        try:
            db.invoices.insert_one(legacy_doc)

            # Verifie l'etat AVANT edit (nature = cat_a, comme dans lines[0])
            before = session.get(f"{API}/fiscal/expenses", params={"copropriete_id": copro_id})
            assert before.status_code == 200
            rows_before = [r for r in before.json().get("expenses", []) if r.get("number") == ref]
            assert len(rows_before) == 1
            assert rows_before[0]["expense_category_id"] == cat_a["id"]

            # PUT sans `lines` dans le body (comme quick-edit ExpensesPage
            # OU dialog principal InvoicesPage apres flatten) : change la
            # nature top-level vers cat_b.
            put_resp = session.put(f"{API}/invoices/{inv_id}", json={
                "number": ref, "date": mid_date, "due_date": mid_date,
                "supplier": legacy_doc["supplier"], "description": legacy_doc["description"],
                "total_amount": 88.0, "vat_amount": 0.0, "account_number": "",
                "expense_category_id": cat_b["id"], "distribution_key_id": "",
                "status": "unpaid", "is_private_fee": False, "private_fee_owner_id": "",
                "occupant_pct": 100.0, "proprietaire_pct": 0.0, "copropriete_id": copro_id,
            })
            assert put_resp.status_code == 200, put_resp.text
            updated_inv = put_resp.json()
            assert updated_inv["expense_category_id"] == cat_b["id"]
            assert len(updated_inv["lines"]) == 1, (
                "REGRESSION iter90fo : la ligne unique legacy doit rester "
                "synchronisee (1 entree), jamais dupliquee ni supprimee"
            )
            assert updated_inv["lines"][0]["expense_category_id"] == cat_b["id"], (
                f"REGRESSION iter90fo : lines[0] doit etre resynchronisee avec "
                f"la nouvelle nature {cat_b['id']}, trouve {updated_inv['lines'][0]}"
            )
            assert updated_inv["lines"][0]["account_number"] == cat_b["account_number"]

            # Verifie que la Liste des Depenses reflete bien le changement
            after = session.get(f"{API}/fiscal/expenses", params={"copropriete_id": copro_id})
            assert after.status_code == 200
            rows_after = [r for r in after.json().get("expenses", []) if r.get("number") == ref]
            assert len(rows_after) == 1
            assert rows_after[0]["expense_category_id"] == cat_b["id"], (
                f"REGRESSION iter90fo NON CORRIGEE : la Liste des Depenses affiche "
                f"encore l'ancienne nature {rows_after[0]['expense_category_id']} "
                f"au lieu de {cat_b['id']} apres une modification 200 OK"
            )
            assert rows_after[0]["account_number"] == cat_b["account_number"]
        finally:
            db.invoices.delete_one({"id": inv_id})
            client.close()

    def test_genuine_multiline_invoice_lines_untouched_by_partial_put(self, session, copro_and_categories):
        """Garde-fou : une VRAIE facture multi-lignes (>= 2 lignes distinctes)
        ne doit JAMAIS voir ses lignes ecrasees/fusionnees par le mecanisme
        de sync mono-ligne (qui ne doit se declencher que si len(lines)==1)."""
        copro_id, fy, cat_a, cat_b = copro_and_categories
        mid_date = fy["start_date"]
        client = pymongo.MongoClient(MONGO_URL)
        db = client[DB_NAME]
        inv_id = str(uuid.uuid4())
        ref = f"TEST-iter90fo-multi-{uuid.uuid4().hex[:8]}"
        multi_doc = {
            "id": inv_id, "copropriete_id": copro_id, "number": ref,
            "internal_reference": f"TEST-iter90fo-INT-{uuid.uuid4().hex[:6]}",
            "date": mid_date, "due_date": mid_date,
            "supplier": f"TEST_iter90fo_Multi_{uuid.uuid4().hex[:6]}",
            "description": "TEST iter90fo multi-lignes", "total_amount": 100.0, "vat_amount": 0.0,
            "account_number": "", "expense_category_id": "", "distribution_key_id": "",
            "status": "unpaid", "is_private_fee": False, "private_fee_owner_id": "",
            "private_fee_allocations": [], "occupant_pct": 100.0, "proprietaire_pct": 0.0,
            "lines": [
                {"account_number": cat_a["account_number"], "expense_category_id": cat_a["id"],
                 "distribution_key_id": "", "amount": 60.0, "description": "Part A",
                 "occupant_pct": 100.0, "proprietaire_pct": 0.0},
                {"account_number": cat_b["account_number"], "expense_category_id": cat_b["id"],
                 "distribution_key_id": "", "amount": 40.0, "description": "Part B",
                 "occupant_pct": 100.0, "proprietaire_pct": 0.0},
            ],
            "distribution_lines": [], "attachments": [], "created_at": "2026-01-01T00:00:00+00:00",
        }
        try:
            db.invoices.insert_one(multi_doc)
            # PUT partiel (status only) sans `lines` dans le body -> ne doit
            # JAMAIS toucher aux 2 lignes existantes.
            put_resp = session.put(f"{API}/invoices/{inv_id}", json={
                "number": ref, "date": mid_date, "due_date": mid_date,
                "supplier": multi_doc["supplier"], "description": multi_doc["description"],
                "total_amount": 100.0, "vat_amount": 0.0, "account_number": "",
                "expense_category_id": "", "distribution_key_id": "",
                "status": "paid", "is_private_fee": False, "private_fee_owner_id": "",
                "occupant_pct": 100.0, "proprietaire_pct": 0.0, "copropriete_id": copro_id,
            })
            assert put_resp.status_code == 200, put_resp.text
            updated_inv = put_resp.json()
            assert len(updated_inv["lines"]) == 2, (
                f"REGRESSION iter90fo : une VRAIE facture multi-lignes (2 lignes) "
                f"ne doit jamais etre ecrasee par la sync mono-ligne, trouve "
                f"{len(updated_inv['lines'])} ligne(s)"
            )
            assert updated_inv["status"] == "paid"
        finally:
            db.invoices.delete_one({"id": inv_id})
            client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
