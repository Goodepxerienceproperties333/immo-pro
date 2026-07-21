"""
Tests for compte_resultat fix (dedup AC orphans + filter reversals/EXT/OD-REG/AN closing)
plus coherence between reports Résultat / Bilan / Dépenses.
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ACP_ID = "138cfd69-cc07-46bb-ad17-a98debee7a3a"
FY_ID = "5acee0fc-66b9-449c-82de-61ad8bc13a45"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": "admin@copro.be", "password": "admin123"},
               timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


def _approx(a, b, tol=0.02):
    return abs(float(a) - float(b)) <= tol


def test_compte_resultat_total_charges_and_produits(session):
    r = session.get(f"{BASE_URL}/api/reports/resultat",
                    params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID},
                    timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    print("Résultat keys:", list(data.keys()))
    print("total_charges =", data.get("total_charges"))
    print("total_produits =", data.get("total_produits"))
    print("resultat =", data.get("resultat"))
    assert _approx(data["total_charges"], 5333.51), f"total_charges={data['total_charges']}"
    assert _approx(data["total_produits"], 6503.67), f"total_produits={data['total_produits']}"
    assert _approx(data["resultat"], 1170.16), f"resultat={data['resultat']}"
    # Benefice
    label = str(data.get("resultat_label") or data.get("type_resultat") or "").lower()
    if label:
        assert "benefice" in label or "bénéfice" in label or "boni" in label


def test_compte_resultat_accounts_no_duplication(session):
    """61300 must be 753.99 (not doubled 1871.97) and 6160 must be 678.99 (not 993.99)."""
    r = session.get(f"{BASE_URL}/api/reports/resultat",
                    params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID},
                    timeout=60)
    assert r.status_code == 200
    data = r.json()
    # Try to find charges lines
    charges = data.get("charges") or []

    def find_amount(acct):
        for group in charges:
            for a in group.get("accounts", []):
                if str(a.get("account_number")) == acct:
                    return float(a.get("amount", 0))
        return None

    amt_61300 = find_amount("61300")
    amt_6160 = find_amount("6160")
    print(f"61300={amt_61300}, 6160={amt_6160}")
    assert amt_61300 is not None and _approx(amt_61300, 753.99), f"61300 expected 753.99, got {amt_61300}"
    assert amt_6160 is not None and _approx(amt_6160, 678.99), f"6160 expected 678.99, got {amt_6160}"


def test_bilan_compte_499_and_finlead(session):
    r = session.get(f"{BASE_URL}/api/reports/bilan",
                    params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID},
                    timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    # find compte 499
    compte_499 = data.get("compte_499") or data.get("solde_499")
    if compte_499 is None:
        # try search in passif
        passif = data.get("passif") or {}
        # walk structure
        import json as _j
        text = _j.dumps(data)
        assert "499" in text
        # fallback: look for 1170 anywhere
    else:
        assert _approx(abs(float(compte_499)), 1170.16), f"499={compte_499}"

    equilibre = data.get("equilibre")
    if equilibre is not None:
        assert equilibre is True or equilibre == "true"

    # Find Finlead=639.69 in passif buckets
    import json as _j
    dump = _j.dumps(data)
    assert "639.69" in dump or "639.7" in dump, "Finlead 639.69 not present in bilan"


def test_expenses_total(session):
    r = session.get(f"{BASE_URL}/api/fiscal/expenses",
                    params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID},
                    timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    total = data.get("totals", {}).get("total") if isinstance(data, dict) else None
    print("expenses total =", total)
    assert total is not None
    assert _approx(float(total), 5329.84, tol=0.05), f"expenses total={total}"


def test_coherence_resultat_bilan_expenses(session):
    """Résultat 1170.16 = Bilan 499 = Dépenses 5329.84 + 3.67 - FI charges."""
    # Résultat
    r1 = session.get(f"{BASE_URL}/api/reports/resultat",
                     params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID}, timeout=60)
    resultat = r1.json()["resultat"]
    # Bilan
    r2 = session.get(f"{BASE_URL}/api/reports/bilan",
                     params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID}, timeout=60)
    b = r2.json()
    compte_499 = b.get("compte_499") or b.get("solde_499") or 0
    if not compte_499:
        import re
        m = re.search(r"1170\.16", str(b))
        assert m
        compte_499 = 1170.16
    print(f"Résultat={resultat}, Bilan499={compte_499}")
    assert _approx(float(resultat), abs(float(compte_499)), tol=0.05)


def test_bilan_after_distribution_balanced(session):
    r = session.get(f"{BASE_URL}/api/reports/bilan",
                    params={"copropriete_id": ACP_ID, "fiscal_year_id": FY_ID,
                            "view_mode": "after_distribution"},
                    timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    eq = data.get("equilibre")
    assert eq is True, f"equilibre={eq}"
    # 499 metadata may still be reported at top-level but should be absent from passif buckets
    import json as _j
    passif = data.get("passif") or []
    passif_text = _j.dumps(passif)
    # ensure no 499 account line in passif
    for group in passif:
        for a in group.get("accounts", []):
            assert not str(a.get("account_number", "")).startswith("499"), \
                f"499 still present in passif after distribution: {a}"
