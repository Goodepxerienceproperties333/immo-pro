"""
Iter90z : Fallback OCR Tesseract pour PDFs bancaires scannes (sans couche texte).

Ce test valide que :
1. Un PDF image-only (typiquement un scan) declenche automatiquement l'OCR.
2. Le texte extrait contient les mots clefs bancaires attendus.
3. Un PDF avec couche texte n'invoque pas l'OCR (pas d'appel a pytesseract).

On genere un PDF image-only en :
- rendant du texte sur une image PIL,
- convertissant l'image en PDF (page unique image-only).
"""
import io
import os
import sys
import tempfile
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bank_import import _extract_pdf_text, _extract_pdf_text_ocr  # noqa: E402


def _build_scanned_pdf(text_lines: list[str]) -> bytes:
    """Genere un PDF image-only (pas de couche texte) avec les lignes donnees."""
    W, H = 1240, 1754  # A4 a ~150 DPI
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    # Utilise une police par defaut lisible
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
    except Exception:
        font = ImageFont.load_default()

    y = 60
    for line in text_lines:
        draw.text((60, y), line, fill="black", font=font)
        y += 50

    # Convertit en PDF (image-only, aucune couche texte)
    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=150.0)
    return buf.getvalue()


def _build_text_pdf(text: str) -> bytes:
    """Genere un PDF avec vraie couche texte (via reportlab)."""
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


def test_ocr_extracts_scanned_pdf():
    """Un PDF image-only doit etre lu via OCR Tesseract."""
    lines = [
        "Extrait de compte BNP Paribas Fortis",
        "IBAN: BE68 5390 0754 7034",
        "Periode: 01/01/2026 - 31/01/2026",
        "Solde initial: 1 234.56 EUR",
        "Virement recu 500.00 EUR",
        "Solde final: 1 734.56 EUR",
    ]
    pdf_bytes = _build_scanned_pdf(lines)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name
    try:
        text = _extract_pdf_text(tmp_path)
        # Tesseract est OCR : quelques mots suffisent
        assert len(text) > 40, f"Texte extrait trop court : {text!r}"
        # Doit reconnaitre au moins une donnee bancaire clef
        lower = text.lower()
        found = sum(1 for kw in ["extrait", "compte", "solde", "iban", "virement", "eur"] if kw in lower)
        assert found >= 3, f"Mots-clefs bancaires insuffisants ({found}/6) dans : {text!r}"
    finally:
        os.unlink(tmp_path)


def test_text_pdf_does_not_use_ocr():
    """Un PDF avec vraie couche texte ne doit PAS invoquer l'OCR."""
    txt = (
        "Extrait BNPP Fortis\n"
        "Date Libelle Debit Credit\n"
        "05/01/2026 Facture 32.50 0.00\n"
        "10/01/2026 Salaire 0.00 1500.00\n"
    )
    pdf_bytes = _build_text_pdf(txt)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name
    try:
        with patch("bank_import._extract_pdf_text_ocr") as mock_ocr:
            result = _extract_pdf_text(tmp_path)
            # OCR ne doit PAS avoir ete appele : texte natif suffisant
            assert not mock_ocr.called, "OCR appele alors que le PDF a une couche texte"
        assert "Extrait" in result
        assert "Salaire" in result
    finally:
        os.unlink(tmp_path)


def test_ocr_direct_returns_text_from_scan():
    """Verifie que _extract_pdf_text_ocr fonctionne isolement."""
    pdf_bytes = _build_scanned_pdf(["HELLO WORLD", "12345 EUR", "COMPTE BANCAIRE"])
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name
    try:
        text = _extract_pdf_text_ocr(tmp_path)
        assert text, "OCR n'a rien retourne"
        lower = text.lower()
        assert any(kw in lower for kw in ["hello", "world", "compte", "bancaire", "eur"])
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
