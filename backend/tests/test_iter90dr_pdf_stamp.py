"""iter90dr : tests unitaires pour l'estampille "Ref : XXX" en haut a droite
d'un PDF de facture.

Verifie :
1. `stamp_pdf_with_reference` ajoute le texte dans chaque page.
2. Retourne le PDF original inchange si reference vide.
3. Retourne le PDF original inchange si les bytes ne sont pas un PDF.
4. Robuste face a un PDF corrompu (fail-safe).
"""
import io
import sys

sys.path.insert(0, "/app/backend")

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def _make_test_pdf(pages: int = 2, page_size=A4) -> bytes:
    """Cree un PDF de test avec `pages` pages contenant chacune un titre."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    for i in range(pages):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 700, f"Facture test page {i+1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _extract_text(pdf_bytes: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(p.extract_text() or "") for p in reader.pages]


def test_stamp_adds_reference_to_all_pages():
    from pdf_stamp import stamp_pdf_with_reference
    original = _make_test_pdf(pages=3)
    stamped = stamp_pdf_with_reference(original, "ACACIA-2027-0001")
    assert stamped != original, "Bytes devraient etre modifies"
    texts = _extract_text(stamped)
    assert len(texts) == 3
    for i, t in enumerate(texts):
        assert "ACACIA-2027-0001" in t, f"Ref manquante page {i+1}: {t[:120]}"


def test_stamp_preserves_original_content():
    from pdf_stamp import stamp_pdf_with_reference
    original = _make_test_pdf(pages=1)
    stamped = stamp_pdf_with_reference(original, "REF-42")
    texts = _extract_text(stamped)
    # Le contenu original doit rester
    assert "Facture test page 1" in texts[0]
    # + la nouvelle reference
    assert "REF-42" in texts[0]


def test_stamp_empty_reference_returns_original():
    from pdf_stamp import stamp_pdf_with_reference
    original = _make_test_pdf(pages=1)
    result = stamp_pdf_with_reference(original, "")
    assert result == original


def test_stamp_none_reference_returns_original():
    from pdf_stamp import stamp_pdf_with_reference
    original = _make_test_pdf(pages=1)
    result = stamp_pdf_with_reference(original, None)
    assert result == original


def test_stamp_invalid_pdf_returns_original():
    from pdf_stamp import stamp_pdf_with_reference
    fake = b"not a pdf but should not crash"
    result = stamp_pdf_with_reference(fake, "REF-1")
    assert result == fake


def test_is_pdf_bytes_detection():
    from pdf_stamp import is_pdf_bytes
    assert is_pdf_bytes(b"%PDF-1.4\n...")
    assert is_pdf_bytes(_make_test_pdf(pages=1))
    assert not is_pdf_bytes(b"")
    assert not is_pdf_bytes(b"\x89PNG\r\n\x1a\n")  # PNG
    assert not is_pdf_bytes(b"\xff\xd8\xff\xe0")  # JPEG


def test_stamp_works_on_landscape_pdf():
    from pdf_stamp import stamp_pdf_with_reference
    from reportlab.lib.pagesizes import landscape
    original = _make_test_pdf(pages=1, page_size=landscape(A4))
    stamped = stamp_pdf_with_reference(original, "LAND-1")
    texts = _extract_text(stamped)
    assert "LAND-1" in texts[0]


def test_stamp_visual_bbox_placement():
    """Verifie que le rectangle bleu est place en top-right (Y < 30pt)."""
    import fitz
    from pdf_stamp import stamp_pdf_with_reference
    original = _make_test_pdf(pages=1)
    stamped = stamp_pdf_with_reference(original, "PLACE-TEST")
    doc = fitz.open(stream=stamped, filetype="pdf")
    page = doc[0]
    # Cherche le texte "Ref : PLACE-TEST"
    hits = page.search_for("Ref : PLACE-TEST")
    assert hits, "Estampille non trouvee visuellement"
    rect = hits[0]
    page_w = page.rect.width
    # Doit etre dans la moitie droite ET dans la partie haute (Y < 40pt)
    assert rect.x0 > page_w / 2, f"Pas en zone droite : x0={rect.x0}, page_w={page_w}"
    assert rect.y1 < 40, f"Pas en zone haute : y1={rect.y1}"
    doc.close()
