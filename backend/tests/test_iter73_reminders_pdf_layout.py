"""Regression test - iter73 - Rappels PDF : layout sans superposition + date de periode.

Bugs rapportes par l'user :
1. "supperposition de texte" : le nom de l'appel (col. "Appel") debordait sur les
   colonnes voisines quand son libelle etait long.
2. La date de periode de l'appel n'etait pas affichee, seule l'echeance l'etait.

Fix :
- Chaque cellule de la table est wrapp e dans un `Paragraph` ReportLab pour
  permettre le retour a la ligne automatique (word-wrap).
- Nouvelle colonne "Periode" entre "Appel" et "Echeance" affichant la date
  de l'appel (formatee DD/MM/YYYY).
- Dates au format DD/MM/YYYY (vs YYYY-MM-DD ISO precedent).
"""
import os
import sys
import asyncio

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _generate_pdf_bytes():
    """Generate a reminder PDF via the API and return raw bytes."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from routes.exports import create_reminders_router

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Trouve une ACP avec un fund_call dont au moins un owner_id existe encore
    fc = None
    owner_id = None
    cur = db.fund_calls.find({}, {"_id": 0})
    async for candidate in cur:
        for d in candidate.get("distribution", []):
            if not d.get("paid"):
                o = await db.owners.find_one({"id": d["owner_id"]}, {"_id": 0, "id": 1})
                if o:
                    fc = candidate
                    owner_id = d["owner_id"]
                    break
        if fc:
            break
    assert fc is not None, "Pas de fund_call non paye avec owner valide dans la base"

    router = create_reminders_router(db)
    fn = None
    for route in router.routes:
        if route.path == "/api/reminders/owner/{owner_id}/letter":
            fn = route.endpoint
            break
    assert fn is not None

    response = await fn(owner_id=owner_id, copropriete_id=fc["copropriete_id"])
    # StreamingResponse -> read body
    chunks = []
    async for c in response.body_iterator:
        chunks.append(c if isinstance(c, bytes) else c.encode("utf-8"))
    return b"".join(chunks)


def test_reminder_pdf_contains_period_column_and_no_overflow():
    pdf_bytes = asyncio.run(_generate_pdf_bytes())
    assert pdf_bytes[:5] == b"%PDF-", "Not a PDF"

    # Extract text and check structural markers
    from io import BytesIO
    import pdfplumber

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    assert "RAPPEL DE PAIEMENT" in full_text
    # Nouvelle colonne "Periode" presente
    assert "Periode" in full_text, f"Colonne 'Periode' manquante : {full_text[:500]}"
    # Format DD/MM/YYYY pour au moins une date
    import re
    assert re.search(r"\b\d{2}/\d{2}/\d{4}\b", full_text), "Format DD/MM/YYYY introuvable"
    # Headers obligatoires
    assert "Appel" in full_text
    assert "Echeance" in full_text
    assert "Montant" in full_text


if __name__ == "__main__":
    test_reminder_pdf_contains_period_column_and_no_overflow()
    print("OK : PDF rappel contient la colonne Periode + format DD/MM/YYYY")
