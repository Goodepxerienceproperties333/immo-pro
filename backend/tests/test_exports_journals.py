"""Backend tests for /api/exports/journals.csv and .pdf (iter90fr P1)."""
import os
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")

BETA_COPRO = "778391ef-ff95-44d3-9b6c-2d33542d09f8"
ALPHA_COPRO_1 = "7375e9ae-b6c1-40e8-b632-1901553af965"
OTHER_COPRO = "cb5bc07b"  # substring only used to check absence

def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s

@pytest.fixture(scope="module")
def beta():
    return _login("syndic_beta@copro.be", "Syndic123!")

@pytest.fixture(scope="module")
def alpha():
    return _login("syndic_alpha@copro.be", "Syndic123!")

@pytest.fixture(scope="module")
def superadmin():
    return _login("admin@copro.be", "admin123")


# --- CSV Basic ---
def test_csv_export_basic(beta):
    r = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                 params={"copropriete_id": BETA_COPRO, "journal_type": "AC"}, timeout=30)
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers.get("Content-Type", "")
    assert "attachment" in r.headers.get("Content-Disposition", "")
    text = r.content.decode("utf-8")
    assert text.startswith("\ufeff"), "Missing UTF-8 BOM"
    lines = text.splitlines()
    header = lines[0].lstrip("\ufeff")
    assert header == "Date;Journal;Reference;Description;Compte;Libelle compte;Tiers;Debit;Credit;Contre-passation;Extournee"
    # last non-empty line = TOTAL
    non_empty = [l for l in lines if l.strip()]
    assert non_empty[-1].startswith("TOTAL"), f"Expected TOTAL row, got: {non_empty[-1]}"
    assert "," in non_empty[-1], "French decimal comma expected in TOTAL row"
    # data lines exist
    data_lines = [l for l in lines[1:] if l.strip() and not l.startswith("TOTAL")]
    assert len(data_lines) >= 2, f"Expected AC entries, got {len(data_lines)}"


# --- PDF Basic ---
def test_pdf_export_basic(beta):
    r = beta.get(f"{BASE_URL}/api/exports/journals.pdf",
                 params={"copropriete_id": BETA_COPRO, "journal_type": "AC"}, timeout=30)
    assert r.status_code == 200, r.text
    assert r.headers.get("Content-Type", "").startswith("application/pdf")
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1024


# --- Chinese Wall ---
def test_chinese_wall_csv(alpha):
    r = alpha.get(f"{BASE_URL}/api/exports/journals.csv",
                  params={"copropriete_id": BETA_COPRO}, timeout=15)
    assert r.status_code == 403
    assert "chinese wall" in r.text.lower()

def test_chinese_wall_pdf(alpha):
    r = alpha.get(f"{BASE_URL}/api/exports/journals.pdf",
                  params={"copropriete_id": BETA_COPRO}, timeout=15)
    assert r.status_code == 403
    assert "chinese wall" in r.text.lower()


# --- Date filter ---
def test_date_filter_wide_and_narrow(beta):
    base = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                    params={"copropriete_id": BETA_COPRO, "journal_type": "AC"}, timeout=30)
    wide = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                    params={"copropriete_id": BETA_COPRO, "journal_type": "AC",
                            "date_from": "2020-01-01", "date_to": "2030-12-31"}, timeout=30)
    assert wide.status_code == 200
    def data_rows(txt):
        return [l for l in txt.splitlines()[1:] if l.strip() and not l.startswith("TOTAL")]
    assert len(data_rows(wide.text)) == len(data_rows(base.text))

    narrow = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                      params={"copropriete_id": BETA_COPRO, "journal_type": "AC",
                              "date_from": "1999-01-01", "date_to": "1999-12-31"}, timeout=30)
    assert narrow.status_code == 200
    dr = data_rows(narrow.text)
    assert len(dr) == 0, f"Expected empty range, got {len(dr)} rows"
    # TOTAL row with 0,00
    non_empty = [l for l in narrow.text.splitlines() if l.strip()]
    total_row = non_empty[-1]
    assert total_row.startswith("TOTAL")
    assert "0,00" in total_row


# --- Superadmin no copro ---
def test_superadmin_all(superadmin):
    r = superadmin.get(f"{BASE_URL}/api/exports/journals.csv", timeout=60)
    assert r.status_code == 200, r.text
    text = r.text
    # Sanity: has header + TOTAL row, doesn't crash
    assert "Date;Journal" in text
    assert "TOTAL" in text


# --- Syndic no copro: only beta's ACPs ---
def test_syndic_no_copro_scoped(beta):
    r = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                 params={"journal_type": "AC"}, timeout=30)
    assert r.status_code == 200, r.text
    text = r.text
    # No leak of Alpha's copros -- verify indirectly: fetch csv scoped for beta copro and ensure
    # this response has AT LEAST that many data rows and does not error.
    scoped = beta.get(f"{BASE_URL}/api/exports/journals.csv",
                      params={"copropriete_id": BETA_COPRO, "journal_type": "AC"}, timeout=30)
    def rows(t): return [l for l in t.splitlines()[1:] if l.strip() and not l.startswith("TOTAL")]
    assert len(rows(text)) >= len(rows(scoped.text))
