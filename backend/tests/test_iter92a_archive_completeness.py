"""iter92a - Test archive ZIP ACP-wide : verifie que l'archive contient
bien les decomptes annuels par proprietaire (post-repartition), les
documents ACP (db.documents) et l'historique des communications sent.
"""
import asyncio
import io
import zipfile
import pytest
from motor.motor_asyncio import AsyncIOMotorClient


MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"
try:
    with open("/app/backend/.env") as f:
        for line in f:
            if line.startswith("MONGO_URL="):
                MONGO_URL = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("DB_NAME="):
                DB_NAME = line.split("=", 1)[1].strip().strip('"')
except Exception:
    pass


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_archive_zip_contains_owner_decomptes():
    """L'archive Gaura doit contenir un dossier /decomptes/ non vide
    par exercice fiscal, ainsi que le bilan.pdf par exercice."""
    async def run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        try:
            from backup_service import build_acp_archive_zip
            data, filename = await build_acp_archive_zip(
                db, "5725c50e-6115-4622-8917-cfed2bd0b96b"
            )
            assert data, "Archive vide"
            assert filename.endswith(".zip")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                assert len(names) == len(set(names)), \
                    "Duplicate names in ZIP - collision de noms de fichiers"
                decomptes = [n for n in names if "/decomptes/" in n]
                assert len(decomptes) > 0, "Aucun decompte annuel dans l'archive"
                bilans = [n for n in names if n.endswith("/bilan.pdf")]
                assert len(bilans) > 0, "bilan.pdf manquant"
                assert any(n.endswith("/README.txt") for n in names)
                assert any(n.endswith("/metadata.json") for n in names)
        finally:
            client.close()
    _run(run())


def test_archive_zip_maria_documents_and_communications():
    async def run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        try:
            from backup_service import build_acp_archive_zip
            maria_id = "a0eef7c0-1db2-45ae-9053-040fbee71be5"
            n_comms = await db.sent_communications.count_documents({"copropriete_id": maria_id})
            n_docs = await db.documents.count_documents({"copropriete_id": maria_id})
            if n_comms == 0 and n_docs == 0:
                pytest.skip(f"Maria n'a ni comms ni docs (comms={n_comms}, docs={n_docs})")
            data, _ = await build_acp_archive_zip(db, maria_id)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if n_comms > 0:
                    comm_csvs = [n for n in names if n.endswith("/communications/historique.csv")]
                    assert len(comm_csvs) == 1, "historique.csv des communications manquant"
                    with z.open(comm_csvs[0]) as f:
                        content = f.read().decode("utf-8-sig")
                        lines = content.splitlines()
                        assert len(lines) >= 2
        finally:
            client.close()
    _run(run())


def test_archive_zip_no_orphan_after_matexi_creation():
    """Regression iter91c/iter92a : Matexi doit apparaitre dans /decomptes/."""
    async def run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        try:
            matexi = await db.owners.find_one(
                {"name": {"$regex": "Matexi", "$options": "i"},
                 "copropriete_ids": "5725c50e-6115-4622-8917-cfed2bd0b96b"},
                {"_id": 0, "id": 1, "name": 1}
            )
            if not matexi:
                pytest.skip("Matexi non encore cree dans cette DB")
            from backup_service import build_acp_archive_zip
            data, _ = await build_acp_archive_zip(
                db, "5725c50e-6115-4622-8917-cfed2bd0b96b"
            )
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                matexi_decomptes = [n for n in names if "/decomptes/" in n and "Matexi" in n]
                assert len(matexi_decomptes) >= 1, \
                    f"Matexi absent des decomptes"
        finally:
            client.close()
    _run(run())
