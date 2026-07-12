"""iter90dr : appose une reference interne (top-right) sur un PDF de facture.

Applique par le endpoint download attachment quand une facture a un
`internal_reference` et que le fichier est un PDF. Non-destructif :
le PDF stocke en GridFS reste intact, l'estampille est calculee a chaque
telechargement (garantit que la ref affichee = ref actuelle en base, meme si
edite apres).

Choix technique : PyMuPDF (fitz) - deja installe, rapide, supporte tous PDFs
(y compris ceux avec formulaires ou signatures).
"""
from __future__ import annotations

import io
import logging
from typing import Optional

_logger = logging.getLogger(__name__)


def stamp_pdf_with_reference(pdf_bytes: bytes, reference: str) -> bytes:
    """Retourne le PDF original avec une estampille "Ref : {reference}" en haut
    a droite de CHAQUE page.

    En cas d'echec (PDF corrompu, chiffre, protege...), retourne le PDF original
    sans modification (fail-safe).
    """
    if not reference or not pdf_bytes:
        return pdf_bytes
    try:
        import fitz  # PyMuPDF
    except ImportError:  # pragma: no cover
        _logger.warning("PyMuPDF non installe - pas d'estampille")
        return pdf_bytes

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("PDF non lisible pour estampille : %s", exc)
        return pdf_bytes

    stamp_text = f"Ref : {reference}"
    try:
        for page in doc:
            rect = page.rect
            # Position : top-right avec 10mm de marge (28pt)
            # Largeur estimee du texte : ~ 4pt/char * len + padding.
            char_w = 4.5
            text_w = len(stamp_text) * char_w + 20  # + padding
            x1 = float(rect.width) - 14
            y0 = 12
            x0 = max(20.0, x1 - text_w)
            y1 = y0 + 18
            # Cadre semi-transparent bleu marine avec bordure
            page.draw_rect(
                fitz.Rect(x0, y0, x1, y1),
                color=(0.008, 0.176, 0.322),  # #022D52
                fill=(0.008, 0.176, 0.322),
                width=0.4,
                overlay=True,
            )
            # Texte blanc au-dessus
            page.insert_textbox(
                fitz.Rect(x0, y0 + 2, x1, y1),
                stamp_text,
                fontname="helv",
                fontsize=8.5,
                color=(1, 1, 1),  # blanc
                align=1,  # centre
                overlay=True,
            )
        # Serialise avec compression pour taille minimale
        out = doc.tobytes(garbage=3, deflate=True)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Erreur estampille PDF : %s", exc)
        return pdf_bytes
    finally:
        doc.close()

    return out


def is_pdf_bytes(data: bytes) -> bool:
    """Detection robuste du magic number PDF."""
    return bool(data) and data[:4] == b"%PDF"
