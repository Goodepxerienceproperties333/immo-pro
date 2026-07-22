"""
Regression test for iter90k: owner merge on ACP 138cfd69 + VE regeneration.

Validates:
- No duplicate owner records per auxiliary_code within the ACP
- 5 VE entries exist (Trim 1/4..4/4 + Fonds de reserve) all with distribution lines
- Bilan compte_499 == 1170.16 and equilibre == True
- Resultat: charges=5333.51, produits=6503.67, resultat=1170.16
- GET /api/accounting/entries with X-Copropriete-Id filter returns the 5 VE entries
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ACP_ID = "138cfd69"  # short prefix; will resolve full id via list
ADMIN_EMAIL = "admin@copro.be"
ADMIN_PASSWORD = "admin123"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def acp_id(session):
    # ACP is archived so not in default list; try list first, then include_archived, then full id
    for params in ({}, {"include_archived": "true"}, {"status": "all"}):
        r = session.get(f"{BASE_URL}/api/coproprietes", params=params)
        if r.status_code == 200:
            for c in r.json():
                cid = c.get("id") or ""
                if str(cid).startswith(ACP_ID):
                    return cid
    # Fallback: known full uuid
    full = "138cfd69-cc07-46bb-ad17-a98debee7a3a"
    r = session.get(f"{BASE_URL}/api/coproprietes/{full}")
    if r.status_code == 200:
        return full
    pytest.skip(f"ACP starting with {ACP_ID} not found (status={r.status_code})")


def test_no_duplicate_owners(session, acp_id):
    r = session.get(f"{BASE_URL}/api/owners", headers={"X-Copropriete-Id": acp_id})
    assert r.status_code == 200, r.text
    owners = r.json()
    # Group by auxiliary_code, ensure no duplicates within this ACP context
    by_aux = {}
    for o in owners:
        aux = o.get("auxiliary_code") or o.get("aux_code")
        copro_ids = o.get("copropriete_ids") or []
        if acp_id in copro_ids or not copro_ids:
            by_aux.setdefault(aux, []).append(o)
    dups = {k: [x.get("id") for x in v] for k, v in by_aux.items() if len(v) > 1 and k}
    assert not dups, f"Duplicate owners still present: {dups}"


def test_ve_entries_count_and_distribution(session, acp_id):
    r = session.get(
        f"{BASE_URL}/api/accounting/entries",
        params={"journal_type": "VE"},
        headers={"X-Copropriete-Id": acp_id},
    )
    assert r.status_code == 200, r.text
    entries = r.json()
    # Should be 5 VE entries
    assert len(entries) == 5, f"Expected 5 VE entries, got {len(entries)}: {[e.get('reference') or e.get('label') for e in entries]}"

    # Each entry must have distribution lines (owner_id set on at least some lines)
    for e in entries:
        lines = e.get("lines") or e.get("entry_lines") or []
        owner_lines = [l for l in lines if l.get("owner_id") or l.get("auxiliary_code") or l.get("third_party_id")]
        assert owner_lines, f"VE entry {e.get('reference')} has no distribution lines with owner_id"


def test_bilan_equilibre(session, acp_id):
    r = session.get(
        f"{BASE_URL}/api/reports/bilan",
        headers={"X-Copropriete-Id": acp_id},
    )
    assert r.status_code == 200, r.text
    b = r.json()
    compte_499 = b.get("compte_499") or b.get("resultat_exercice") or b.get("compte499")
    assert compte_499 is not None, f"bilan payload missing compte_499: keys={list(b.keys())}"
    assert abs(float(compte_499) - 1170.16) < 0.05, f"compte_499={compte_499}, expected 1170.16"
    equilibre = b.get("equilibre")
    assert equilibre is True, f"equilibre={equilibre}"


def test_resultat(session, acp_id):
    r = session.get(
        f"{BASE_URL}/api/reports/resultat",
        headers={"X-Copropriete-Id": acp_id},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    tc = float(d.get("total_charges", 0))
    tp = float(d.get("total_produits", 0))
    res = float(d.get("resultat", 0))
    assert abs(tc - 5333.51) < 0.05, f"total_charges={tc}, expected 5333.51"
    assert abs(tp - 6503.67) < 0.05, f"total_produits={tp}, expected 6503.67"
    assert abs(res - 1170.16) < 0.05, f"resultat={res}, expected 1170.16"
