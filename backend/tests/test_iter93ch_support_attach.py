"""iter93ch: tests for POST /api/support/conversations/{id}/attach
and integration with LLM chat context.
"""
import io
import os
import time

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
def conv(client):
    r = client.post(
        f"{BASE_URL}/api/support/conversations",
        json={"title": "TEST_iter93ch"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    conv = r.json()
    yield conv
    # teardown
    try:
        client.delete(f"{BASE_URL}/api/support/conversations/{conv['id']}", timeout=10)
    except Exception:
        pass


# --- Attach endpoint validation ---

def test_reject_unsupported_extension(client, conv):
    files = {"file": ("bad.txt", b"hello world", "text/plain")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/attach",
        files=files,
        timeout=15,
    )
    assert r.status_code == 400
    assert "non supporte" in r.text.lower() or "acceptes" in r.text.lower()


def test_reject_file_too_large(client, conv):
    big = b"x" * (11 * 1024 * 1024)  # 11 MB
    files = {"file": ("big.pdf", big, "application/pdf")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/attach",
        files=files,
        timeout=30,
    )
    assert r.status_code == 400
    assert "volumin" in r.text.lower() or "10" in r.text


def test_attach_csv_ok(client, conv):
    csv_bytes = b"col_a;col_b;solde\nGuerit;X;26.96\nAutre;Y;100.00\n"
    files = {"file": ("test.csv", csv_bytes, "text/csv")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/attach",
        files=files,
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["file_type"] == "csv"
    assert data["filename"] == "test.csv"
    assert data["extracted_length"] > 0
    assert "Guerit" in data["preview"]

    # Verify persisted in messages
    r2 = client.get(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/messages",
        timeout=15,
    )
    assert r2.status_code == 200
    msgs = r2.json()
    atts = [m for m in msgs if m.get("role") == "user_attachment"]
    assert any(m.get("filename") == "test.csv" for m in atts)


def test_attach_pdf_ok(client, conv):
    with open(FIXTURE_PDF, "rb") as f:
        content = f.read()
    assert len(content) > 1000
    files = {"file": ("optipro_bilan_31_03_2027.pdf", content, "application/pdf")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/attach",
        files=files,
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["file_type"] == "pdf"
    assert data["extracted_length"] > 500
    # PDF fixture should contain Guerit and 26.96
    r2 = client.get(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/messages",
        timeout=15,
    )
    msgs = r2.json()
    att_pdf = next(
        m for m in msgs
        if m.get("role") == "user_attachment"
        and m.get("filename") == "optipro_bilan_31_03_2027.pdf"
    )
    extracted = att_pdf.get("extracted_text", "")
    assert ("Guerit" in extracted) or ("Guérit" in extracted), (
        "Guerit/Guérit not found in extracted PDF text"
    )
    assert "26,96" in extracted or "26.96" in extracted


def test_empty_file_rejected(client, conv):
    files = {"file": ("empty.csv", b"", "text/csv")}
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/attach",
        files=files,
        timeout=10,
    )
    assert r.status_code == 400


# --- Regression: existing support endpoints still work ---

def test_regression_list_conversations(client):
    r = client.get(f"{BASE_URL}/api/support/conversations", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_regression_chat_without_document(client):
    # Fresh conv to avoid confusion with attached-pdf conv
    r = client.post(
        f"{BASE_URL}/api/support/conversations",
        json={"title": "TEST_iter93ch_no_doc"},
        timeout=15,
    )
    assert r.status_code == 200
    cid = r.json()["id"]
    try:
        r2 = client.post(
            f"{BASE_URL}/api/support/conversations/{cid}/chat",
            json={"message": "Bonjour, comment ca va ?"},
            timeout=90,
        )
        assert r2.status_code == 200, r2.text
        data = r2.json()
        assert "assistant_message" in data
        assert len(data["assistant_message"].get("content", "")) > 0
    finally:
        client.delete(f"{BASE_URL}/api/support/conversations/{cid}", timeout=10)


# --- E2E: PDF upload + chat about Guerit / iter93cf rules ---

def test_e2e_pdf_chat_about_guerit(client, conv):
    """conv already has the fixture PDF attached from test_attach_pdf_ok."""
    r = client.post(
        f"{BASE_URL}/api/support/conversations/{conv['id']}/chat",
        json={
            "message": "Quel est le solde de Guerit dans ce bilan ? "
                       "Compare aux regles iter93cf"
        },
        timeout=120,
    )
    assert r.status_code == 200, r.text
    answer = r.json()["assistant_message"]["content"]
    print("\n=== LLM answer ===\n", answer, "\n===")
    # The response must mention 26.96 (Guerit balance from fixture)
    assert "26" in answer and ("96" in answer or "95" in answer), (
        f"Expected Guerit balance 26.96 in answer: {answer[:500]}"
    )
    # Must reference the metier rules
    ans_low = answer.lower()
    assert any(k in ans_low for k in ["iter93cf", "iter93cc", "repartition", "bilan"])
