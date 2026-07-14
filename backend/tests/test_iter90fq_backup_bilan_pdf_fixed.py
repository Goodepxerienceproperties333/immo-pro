"""
iter90fq (partie 2) : REGRESSION LOCK - appel casse a `build_bilan_pdf()`
dans `backup_service.py` (signature incorrecte + import d'une fonction
inexistante `_compute_bilan`), avale silencieusement par un try/except.

Contexte : repere pendant l'investigation du bug "PDF bilan apres
repartition incorrect" (voir `test_iter90fq_bilan_pdf_view_mode.py`).
L'appel `build_bilan_pdf(bilan_data)` dans `backup_service.py` passait un
SEUL argument positionnel a une fonction 100% keyword-only (`*,`) qui
requiert `copropriete` et `bilan_data`, et importait
`from routes.reports import _compute_bilan` - une fonction qui n'a JAMAIS
existe sous ce nom (la fonction s'appelle `bilan`, definie comme closure
NON exportable dans `create_reports_router(db)`). Le `bilan.pdf` etait
donc ABSENT de tous les backups legaux depuis la creation de cette
fonctionnalite, sans qu'aucune erreur ne soit visible (try/except large).

Fix :
- Extraction de la logique de calcul du bilan dans une fonction
  module-level importable `compute_bilan_data(db, copropriete_id,
  date_to=None, fiscal_year_id=None, view_mode="before_distribution")`
  dans `routes/reports.py` (reutilisee par l'endpoint FastAPI ET par
  `backup_service.py` - source unique de verite, evite toute divergence).
- `backup_service.py` appelle desormais `compute_bilan_data(...)` puis
  `build_bilan_pdf(copropriete=copro, fiscal_year=fy, bilan_data=...,
  date_to=...)` avec tous les kwargs requis.

Ce test verifie que le ZIP de backup contient bien un `bilan.pdf` VALIDE
(header PDF + donnees reelles) pour chaque exercice fiscal, alors qu'avant
le fix il etait systematiquement absent (echec silencieux).
"""
import asyncio
import io
import os
import sys
import zipfile

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv("/app/frontend/.env")
load_dotenv("/app/backend/.env")

from backup_service import build_acp_archive_zip  # noqa: E402

# ACP de test preview avec au moins 1 exercice fiscal + des ecritures
ACP_ID = "47e79d1c-d96a-4e8b-b80c-148295f9160f"


async def _mongo():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


async def _scenario_backup_zip_contains_valid_bilan_pdf():
    db = await _mongo()
    data, _fname = await build_acp_archive_zip(db, ACP_ID, include_pdfs=True)
    z = zipfile.ZipFile(io.BytesIO(data))
    bilan_entries = [n for n in z.namelist() if n.endswith("/bilan.pdf")]
    assert bilan_entries, (
        "REGRESSION iter90fq : aucun bilan.pdf trouve dans le ZIP de backup "
        "(l'appel a build_bilan_pdf() est de nouveau casse ou avale par un "
        "try/except silencieux)"
    )
    for entry in bilan_entries:
        content = z.read(entry)
        assert content.startswith(b"%PDF-"), f"{entry} n'est pas un PDF valide"
        assert len(content) > 500, f"{entry} semble vide/tronque ({len(content)} bytes)"


def test_backup_zip_contains_valid_bilan_pdf():
    asyncio.run(_scenario_backup_zip_contains_valid_bilan_pdf())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
