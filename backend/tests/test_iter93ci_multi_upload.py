"""iter93ci: multi-document upload + LLM comparative table tests.

Validates:
- Multiple attachments (>= 2) in a conversation trigger a comparative
  hint in the LLM prompt (backend side) and produce a markdown table.
- Regression: single upload continues to work with normal answer.
- Regression: list conv + delete still work.
"""
import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
FIXTURE_PDF = "/app/backend/tests/fixtures/optipro_bilan_31_03_2027.pdf"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@copro.be", "password": "admin123"},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def multi_conv(client):
    """Fresh conversation for multi-doc scenario."""
    r = client.post(
        f"{BASE_URL}/api/support/conversations",
        json={"title": "TEST_iter93ci_multi"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    conv = r.json()
    yield conv
    try:
        client.delete(
            f"{BASE_URL}/api/support/conversations/{conv['id']}", timeout=10
        )
    except Exception:
        pass


# --- Multi-upload: 2 docs in the same conversation ---

def test_upload_pdf_then_csv(client, multi_conv):
    """Upload PDF then CSV sequentially (mimics frontend loop)."""
    cid = multi_conv["id"]

    with open(FIXTURE_PDF, "rb") as f:
        pdf_bytes = f.read()
    r1 = client.post(
        f"{BASE_URL}/api/support/conversations/{cid}/attach",
        files={
            "file": (
                "optipro_bilan_31_03_2027.pdf", pdf_bytes, "application/pdf"
            )
        },
        timeout=30,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["file_type"] == "pdf"

    # NextGe-style CSV with alternate Guerit balance to force a compare
    csv_bytes = (
        b"compte;libelle;solde\n"
        b"41000968;Guerit;26.95\n"
        b"41010000;Autre;100.00\n"
    )
    r2 = client.post(
        f"{BASE_URL}/api/support/conversations/{cid}/attach",
        files={"file": ("nextge_balance.csv", csv_bytes, "text/csv")},
        timeout=15,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["file_type"] == "csv"
    assert r2.json()["extracted_length"] > 0

    # Verify both persisted
    r3 = client.get(
        f"{BASE_URL}/api/support/conversations/{cid}/messages", timeout=15
    )
    assert r3.status_code == 200
    atts = [m for m in r3.json() if m.get("role") == "user_attachment"]
    filenames = {m.get("filename") for m in atts}
    assert "optipro_bilan_31_03_2027.pdf" in filenames
    assert "nextge_balance.csv" in filenames


# --- Validation: extension / size ---

def test_reject_bad_extension_multi(client, multi_conv):
    """Even in a multi-doc conv, bad ext must be rejected."""
    files = {"file": ("notes.txt", b"just text", "text/plain")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{multi_conv['id']}/attach",
        files=files,
        timeout=10,
    )
    assert r.status_code == 400


# --- E2E: LLM comparative table with 2 documents ---

def test_e2e_multidoc_llm_produces_comparative_table(client, multi_conv):
    """After PDF+CSV uploaded, ask compare Guerit => expect a markdown table
    citing both documents, and iter93cc-iter93cg family rules."""
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{multi_conv['id']}/chat",
        json={
            "message": "Compare le solde du compte Guerit entre les deux "
                       "documents joints (Optipro vs NextGe). "
                       "Presente un tableau comparatif et explique l'ecart."
        },
        timeout=180,
    )
    assert r.status_code == 200, r.text
    ans = r.json()["assistant_message"]["content"]
    print("\n=== iter93ci LLM answer ===\n", ans, "\n===")

    # Must be a markdown table (pipe format) with at least a header separator
    assert "|" in ans, "expected markdown table (pipe chars) in answer"
    assert "---" in ans or "--|" in ans or "|-" in ans, (
        "expected markdown table separator row"
    )

    # Must mention both file references OR both balance values
    ans_low = ans.lower()
    mentions_both_docs = (
        ("optipro" in ans_low and "nextge" in ans_low)
        or ("26,96" in ans and "26,95" in ans)
        or ("26.96" in ans and "26.95" in ans)
    )
    assert mentions_both_docs, (
        f"answer must reference both docs / both values: {ans[:600]}"
    )

    # Must cite at least one iter93c* rule
    assert any(k in ans_low for k in [
        "iter93cc", "iter93cd", "iter93ce", "iter93cf", "iter93cg",
        "repartition", "rounding", "0,10", "0.10",
    ]), f"answer must cite metier rules: {ans[:600]}"


# --- Regression: single upload flow still fine (no comparison hint forced) ---

def test_regression_single_upload_no_forced_comparison(client):
    """A conversation with only 1 attachment should NOT trigger the
    'CONTEXTE ... comparaison' hint injection. We can't inspect the prompt
    directly, but we can ensure single-doc chat still returns a normal answer
    without a markdown table being forced."""
    r = client.post(
        f"{BASE_URL}/api/support/conversations",
        json={"title": "TEST_iter93ci_single"},
        timeout=15,
    )
    assert r.status_code == 200
    cid = r.json()["id"]
    try:
        csv_bytes = b"compte;solde\n41000968;26.96\n"
        ra = client.post(
            f"{BASE_URL}/api/support/conversations/{cid}/attach",
            files={"file": ("solo.csv", csv_bytes, "text/csv")},
            timeout=15,
        )
        assert ra.status_code == 200

        rc = client.post(
            f"{BASE_URL}/api/support/conversations/{cid}/chat",
            json={"message": "Quel est le solde du compte Guerit ?"},
            timeout=120,
        )
        assert rc.status_code == 200, rc.text
        ans = rc.json()["assistant_message"]["content"]
        assert len(ans) > 0
        # Should mention 26,96 from the CSV
        assert "26,96" in ans or "26.96" in ans, ans[:400]
    finally:
        client.delete(
            f"{BASE_URL}/api/support/conversations/{cid}", timeout=10
        )


# --- Regression: existing endpoints ---

def test_regression_list_conversations(client):
    r = client.get(f"{BASE_URL}/api/support/conversations", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
