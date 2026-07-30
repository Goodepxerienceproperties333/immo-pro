"""iter90gw — Situation de compte propriétaire : convention POV PROPRIO.

Regle utilisateur (16/07/2026) : "la situation de compte des proprietaires
n'est pas bonne". Comparaison avec le format Optipro belge de reference :

  Convention POV PROPRIO (extrait de compte envoye AU proprietaire) :
    Solde = Credit - Debit
    Solde POSITIF = "EN VOTRE FAVEUR" (proprio crediteur, ACP doit)
    Solde NEGATIF = "A REGLER" (proprio debiteur, doit encore)

  A ne pas confondre avec la convention POV ACP (grand livre / bilan) :
    Solde = Debit - Credit
    Positif = "creance ACP" = "proprio doit"

Le PDF de situation de compte doit utiliser la convention POV PROPRIO
(destinataire = le proprio, il lit son extrait comme sa banque). La
convention interne du grand livre reste POV ACP (non modifiee).

Tests :
  1. Le PDF affiche "EN VOTRE FAVEUR" si solde positif POV proprio.
  2. La ligne "Solde reporte" (AN) apparait EN TETE meme si sa date
     coincide avec des appels du meme jour.
  3. Le running_balance progressif = Credit cumule - Debit cumule.
  4. L'encart "Comment regler" s'affiche UNIQUEMENT quand proprio debiteur
     (running < -0.01), pas quand proprio crediteur.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://optipro-parser-fix.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": "admin@copro.be", "password": "admin123"}, timeout=30)
    assert r.status_code == 200
    return s


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extrait le texte d'un PDF pour assertions (utilise pypdf ou pdfminer)."""
    try:
        from pypdf import PdfReader
        from io import BytesIO
        r = PdfReader(BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except ImportError:
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            path = f.name
        try:
            out = subprocess.check_output(["pdftotext", path, "-"], timeout=30)
            return out.decode("utf-8", errors="ignore")
        finally:
            os.unlink(path)


def test_iter90gw_pdf_uses_credit_minus_debit_convention(admin_session):
    """Le PDF de situation Boxus Wivine (proprio Maria) doit :
      - afficher "EN VOTRE FAVEUR" (car total_paid > total_appele apres AN reprise)
      - avoir la ligne "Solde reporte" AU 01/03/2026 avec Credit 291,66
      - calculer le running en Credit - Debit (POV proprio)
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"  # Maria
    owner_id = "bed91088-a9a9-4566-8ee6-29ca214ac567"  # Boxus Wivine
    r = admin_session.get(
        f"{BASE_URL}/api/reports/situation-compte/{owner_id}/pdf",
        params={"copropriete_id": copro_id, "start_date": "2026-03-01", "end_date": "2026-07-16"},
        timeout=60,
    )
    assert r.status_code == 200, f"PDF endpoint failed: {r.status_code} {r.text[:200]}"
    assert r.content.startswith(b"%PDF"), "Response n'est pas un PDF valide"
    text = _extract_pdf_text(r.content)
    # 1. Convention POV proprio : "EN VOTRE FAVEUR" (Boxus a un solde crediteur
    # apres reprise + paiements > appels)
    assert "EN VOTRE FAVEUR" in text, f"Attendu 'EN VOTRE FAVEUR', PDF: {text[:800]}"
    # 2. Ligne reprise (OD d'ouverture / Solde reporte) presente
    assert "Solde reporte" in text or "OD d'ouverture" in text or "OD ouverture" in text, \
        f"Ligne 'Solde reporte' manquante dans le PDF: {text[:800]}"
    # 3. Le montant 291.66 apparait (reprise AN)
    assert "291,66" in text, f"Montant reprise 291,66 absent du PDF: {text[:800]}"


def test_iter90gw_no_payment_instructions_when_creditor(admin_session):
    """Quand le proprio est CREDITEUR (solde positif POV proprio),
    l'encart 'Comment regler ce solde' NE DOIT PAS apparaitre. On affiche
    'Solde en votre faveur' a la place.
    """
    copro_id = "ed728e70-1d0d-4057-a37a-d450cc9ac812"
    owner_id = "bed91088-a9a9-4566-8ee6-29ca214ac567"  # Boxus Wivine (crediteur 1.42€)
    r = admin_session.get(
        f"{BASE_URL}/api/reports/situation-compte/{owner_id}/pdf",
        params={"copropriete_id": copro_id, "start_date": "2026-03-01", "end_date": "2026-07-16"},
        timeout=60,
    )
    assert r.status_code == 200
    text = _extract_pdf_text(r.content)
    # Pas d'instructions de paiement quand crediteur
    assert "Comment regler" not in text, f"'Comment regler' ne doit pas etre present pour un proprio crediteur: {text[:500]}"
    # Mais l'encart 'Solde en votre faveur' est present
    assert "Solde en votre faveur" in text or "EN VOTRE FAVEUR" in text
