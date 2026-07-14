"""
iter90fq : REGRESSION LOCK - bug "bilan apres repartition PDF incorrect,
le compte de regularisation (499) est toujours present".

Ticket utilisateur (Feb 2026, PRODUCTION, ACP Acacia TER) :
> "le bilan après répartition en PDF n'est pas le bon, le compte de
> régularisation est toujours présent et donc pas réparti sur les
> propriétaires"
> "le PDF du bilan après répartition n'est pas correct il affiche le bilan
> avant réparation"

Root cause : `GET /api/reports/bilan/pdf` (routes/reports.py::bilan_pdf)
n'acceptait PAS le parametre `view_mode` dans sa signature (seulement
`copropriete_id`, `fiscal_year_id`, `date_to`). Le frontend
(`ReportsPage.js::downloadBilanPdf`) envoie pourtant bien
`params.view_mode` (choix "Avant"/"Apres repartition" dans l'UI) - ce
parametre etait silencieusement ignore par FastAPI (query param non
declare), et l'appel interne `await bilan(...)` ne recevait donc JAMAIS
`view_mode`, retombant TOUJOURS sur le defaut `"before_distribution"`
(compte 499 non reparti). Le titre du PDF (`pdf_bilan.py`) etait en plus
hardcode "BILAN COMPTABLE APRES REPARTITION" quel que soit le mode reel
calcule, ce qui masquait completement le bug a l'oeil.

Fix :
- `bilan_pdf()` declare desormais `view_mode: Optional[str] =
  "before_distribution"` et le transmet a `bilan(...)`.
- `build_bilan_pdf()` accepte `view_mode` et affiche le titre correct
  ("AVANT" vs "APRES REPARTITION") en fonction du mode REELLEMENT utilise.

Ce test verifie, sur une ACP avec un boni d'exercice reel (compte 499
present en mode avant-repartition) :
A) Le PDF genere avec `view_mode=after_distribution` NE CONTIENT PLUS le
   compte 499 (redistribue aux proprietaires).
B) Le PDF genere avec `view_mode=before_distribution` CONTIENT bien le
   compte 499 (comportement volontairement different, garde-fou).
C) Le titre du PDF correspond au mode demande dans les 2 cas.
"""
import io
import os

import pytest
import requests
from pypdf import PdfReader

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL').rstrip('/')
API = f"{BASE_URL}/api"
# ACP de test preview avec un boni d'exercice reel (compte 499 present
# en mode avant-repartition, verifie manuellement avant d'ecrire ce test)
ACP_ID = "47e79d1c-d96a-4e8b-b80c-148295f9160f"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    resp = s.post(f"{API}/auth/login", json={"email": "admin@copro.be", "password": "admin123"})
    if resp.status_code != 200:
        pytest.skip("Login failed - skipping iter90fq bilan PDF tests")
    return s


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return reader.pages[0].extract_text()


class TestBilanPdfViewModeRespected:

    def test_after_distribution_pdf_has_no_499_and_correct_title(self, session):
        r = session.get(f"{API}/reports/bilan/pdf", params={
            "copropriete_id": ACP_ID, "view_mode": "after_distribution",
        })
        assert r.status_code == 200, r.text
        text = _pdf_text(r.content)
        assert "APRES REPARTITION" in text
        assert "499" not in text, (
            "REGRESSION iter90fq : le PDF 'apres repartition' contient "
            "encore le compte 499 non reparti"
        )

    def test_before_distribution_pdf_has_499_and_correct_title(self, session):
        r = session.get(f"{API}/reports/bilan/pdf", params={
            "copropriete_id": ACP_ID, "view_mode": "before_distribution",
        })
        assert r.status_code == 200, r.text
        text = _pdf_text(r.content)
        assert "AVANT REPARTITION" in text
        assert "499" in text, (
            "REGRESSION iter90fq : le mode 'avant repartition' doit "
            "toujours afficher le compte 499 (comportement volontaire)"
        )

    def test_default_view_mode_is_before_distribution(self, session):
        """Sans parametre explicite, le comportement historique (defaut
        before_distribution) doit etre preserve."""
        r = session.get(f"{API}/reports/bilan/pdf", params={"copropriete_id": ACP_ID})
        assert r.status_code == 200, r.text
        text = _pdf_text(r.content)
        assert "AVANT REPARTITION" in text
        assert "499" in text


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
