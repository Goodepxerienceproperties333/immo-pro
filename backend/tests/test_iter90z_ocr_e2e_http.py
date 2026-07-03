"""
Iter90z E2E : Verifie que le fallback OCR Tesseract fonctionne via
l'endpoint HTTP POST /api/banking/statements/import-files.

Notes :
- L'endpoint reel est `/statements/import-files` (pas `preview-import`).
- `copropriete_id` est un champ Form, pas un header.
- L'appel LLM peut prendre 5-15s ; timeout etendu.
"""
import io
import os
import sys
import time

import pytest
import requests
from PIL import Image, ImageDraw, ImageFont

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback: read frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

API = f"{BASE_URL}/api"


def _build_scanned_pdf(text_lines: list[str]) -> bytes:
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32
        )
    except Exception:
        font = ImageFont.load_default()
    y = 60
    for line in text_lines:
        draw.text((60, y), line, fill="black", font=font)
        y += 50
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150.0)
    return buf.getvalue()


def _build_text_pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.split("\n"):
        c.drawString(60, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    # Login superadmin
    r = s.post(
        f"{API}/auth/login",
        json={"email": "admin@copro.be", "password": "admin123"},
        timeout=30,
    )
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def copro_id(session):
    r = session.get(f"{API}/coproprietes", timeout=30)
    assert r.status_code == 200, f"list coproprietes: {r.status_code} {r.text[:200]}"
    items = r.json()
    if isinstance(items, dict):
        items = items.get("items") or items.get("data") or []
    assert items, "Aucune copropriete presente en base pour tester"
    cid = items[0].get("id")
    assert cid
    return cid


def _post_import(session, copro_id, filename, pdf_bytes):
    files = {"files": (filename, pdf_bytes, "application/pdf")}
    data = {"copropriete_id": copro_id}
    return session.post(
        f"{API}/banking/statements/import-files",
        files=files,
        data=data,
        timeout=90,
    )


def test_e2e_scanned_pdf_triggers_ocr(session, copro_id):
    """PDF scanne -> extraction_method='llm_text_ocr' + warning OCR Tesseract."""
    pdf = _build_scanned_pdf([
        "Extrait de compte BNP Paribas Fortis",
        "IBAN: BE68 5390 0754 7034",
        "Periode: 01/01/2026 - 31/01/2026",
        "Date 05/01/2026 Facture EDF -32.50",
        "Date 10/01/2026 Salaire +1500.00",
        "Solde final: 1 467.50 EUR",
    ])
    t0 = time.time()
    r = _post_import(session, copro_id, "iter90z_scan.pdf", pdf)
    dt = time.time() - t0
    print(f"[E2E scan] status={r.status_code} elapsed={dt:.1f}s")
    assert r.status_code == 200, f"HTTP {r.status_code} : {r.text[:500]}"
    body = r.json()
    print(f"[E2E scan] body={body}")
    assert body.get("results"), "results vide"
    res = body["results"][0]

    # L'ancien message 'OCR pas supporte' ne doit PLUS apparaitre
    err = (res.get("error") or "").lower()
    assert "l'ocr n'est pas encore supporte" not in err, (
        f"Ancien message d'erreur toujours present : {err}"
    )
    assert "n'est pas encore supporte" not in err

    method = res.get("extraction_method") or ""
    warnings = res.get("warnings") or []
    warnings_joined = " | ".join(warnings).lower()

    # Cas heureux : LLM a repondu, on doit voir llm_text_ocr
    if res.get("status") == "ok":
        assert method == "llm_text_ocr", (
            f"extraction_method attendu llm_text_ocr, recu {method!r}"
        )
        assert "ocr tesseract" in warnings_joined, (
            f"warning OCR Tesseract manquant : {warnings}"
        )
    else:
        # Cas ou LLM a echoue (quota/net) OU aucune transaction extraite :
        # on tolere mais il faut au moins voir que l'OCR a tourne
        # (soit via method llm_text_ocr, soit via warning OCR Tesseract).
        assert (
            method == "llm_text_ocr" or "ocr" in warnings_joined
        ), (
            f"OCR ne semble pas avoir tourne. method={method} warnings={warnings} "
            f"error={res.get('error')}"
        )


def test_e2e_native_text_pdf_does_not_trigger_ocr(session, copro_id):
    """PDF natif -> extraction_method='llm_text' (pas llm_text_ocr)."""
    pdf = _build_text_pdf(
        "Extrait BNPP Fortis\n"
        "Date Libelle Debit Credit\n"
        "05/01/2026 Facture EDF 32.50 0.00\n"
        "10/01/2026 Salaire 0.00 1500.00\n"
        "Solde final 1467.50\n"
    )
    r = _post_import(session, copro_id, "iter90z_native.pdf", pdf)
    print(f"[E2E native] status={r.status_code}")
    assert r.status_code == 200, f"HTTP {r.status_code} : {r.text[:500]}"
    body = r.json()
    print(f"[E2E native] body={body}")
    assert body.get("results")
    res = body["results"][0]

    method = res.get("extraction_method") or ""
    warnings = res.get("warnings") or []
    warnings_joined = " | ".join(warnings).lower()

    # Pas d'OCR : method != llm_text_ocr
    assert method != "llm_text_ocr", (
        f"OCR declenche a tort sur PDF natif : method={method}"
    )
    assert "ocr tesseract" not in warnings_joined, (
        f"Warning OCR present alors que PDF natif : {warnings}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
