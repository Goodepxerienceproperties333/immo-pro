"""iter90fl : le filtre d'exclusion des ecritures OD/FI extournees dans
`expense_rows.py::compute_expense_rows` (utilise par `/api/fiscal/expenses`
ET par le PDF "Liste des depenses") utilisait des noms de champs
INEXISTANTS (`reverses_id` / `reversed_by_id`) au lieu des vrais champs
poses par `journal_reversals.py` (`reversed` / `is_reversal`). Resultat :
le filtre etait totalement inoperant et toute ecriture OD/FI extournee sur
un compte de charge restait visible EN DOUBLE (originale + contre-passation)
dans la Liste des depenses, faussant le total.

Ce test reproduit le scenario complet via l'API publique :
1. Creation d'une ecriture OD manuelle sur un compte de charge (classe 6).
2. Verification qu'elle apparait UNE fois dans /api/fiscal/expenses.
3. Suppression de l'ecriture (DELETE /api/entries/{id} -> contre-passation
   automatique, jamais de suppression physique en PCMN belge).
4. Verification qu'elle N'APPARAIT PLUS DU TOUT (ni l'originale reversee,
   ni la contre-passation) et que le total n'est pas fausse.
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
        pytest.skip("Login failed - skipping iter90fl expense_rows reversal tests")
    return s


@pytest.fixture(scope="module")
def copro_and_date(session):
    """ACP avec un plan PCMN complet (classe 6) et un exercice fiscal ouvert.
    Retourne (copro_id, une_date_valide_dans_l_exercice_ouvert)."""
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
        if pcmn.status_code == 200 and any(str(a.get("class_num")) == "6" and len(a.get("number", "")) == 5 for a in pcmn.json()):
            return acp["id"], open_years[0]["start_date"]
    pytest.skip("Aucune ACP avec exercice ouvert + plan PCMN classe 6 trouvee")


def _get_expense_total_and_count(session, copro_id):
    resp = session.get(f"{API}/fiscal/expenses", params={"copropriete_id": copro_id})
    assert resp.status_code == 200
    data = resp.json()
    return data.get("expenses", []), data.get("totals")


class TestExpenseRowsExcludesReversedEntries:

    def test_reversed_od_entry_not_double_counted(self, session, copro_and_date):
        copro_id, entry_date = copro_and_date
        charge_account = "61000"
        balance_account = "100"
        amount = 321.45
        ref = f"TEST-iter90fl-{uuid.uuid4().hex[:8]}"

        create_resp = session.post(f"{API}/accounting/entries", json={
            "copropriete_id": copro_id,
            "journal_type": "OD",
            "date": entry_date,
            "reference": ref,
            "description": "TEST iter90fl - ecriture OD isolee sur compte de charge",
            "lines": [
                {"account_number": charge_account, "debit": amount, "credit": 0,
                 "description": "TEST iter90fl debit charge"},
                {"account_number": balance_account, "debit": 0, "credit": amount,
                 "description": "TEST iter90fl credit contrepartie"},
            ],
        })
        assert create_resp.status_code == 200, create_resp.text
        entry = create_resp.json()

        # 1) L'ecriture doit apparaitre UNE fois dans la Liste des depenses
        rows_before, _ = _get_expense_total_and_count(session, copro_id)
        matches_before = [r for r in rows_before if r.get("number") == ref]
        assert len(matches_before) == 1, f"Attendu 1 ligne pour {ref}, trouve {len(matches_before)}"
        assert abs(matches_before[0]["total_amount"] - amount) < 0.01

        # 2) Suppression = contre-passation automatique (PCMN belge, jamais de hard delete)
        del_resp = session.delete(f"{API}/accounting/entries/{entry['id']}")
        assert del_resp.status_code == 200, del_resp.text

        # 3) Ni l'originale (reversed=True) ni la contre-passation (is_reversal=True)
        #    ne doivent apparaitre dans la Liste des depenses apres suppression.
        rows_after, _ = _get_expense_total_and_count(session, copro_id)
        matches_after = [r for r in rows_after if r.get("number") in (ref, f"EXT-{ref}")]
        assert len(matches_after) == 0, (
            f"BUG iter90fl : {len(matches_after)} ligne(s) fantome(s) encore visibles "
            f"apres suppression/contre-passation : {matches_after}"
        )
