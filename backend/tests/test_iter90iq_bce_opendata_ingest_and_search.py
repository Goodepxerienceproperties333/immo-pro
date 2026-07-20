"""iter90iq : Ingestion et recherche du dataset BCE Open Data (KBO/CBE).

Verrouille :
- Parser du ZIP officiel (denomination + address + enterprise).
- Ingestion idempotente en MongoDB (upsert par bce_raw).
- Recherche `search_bce_opendata` (full-text + Jaccard scoring).
- Endpoint superadmin `/api/admin/bce-opendata/status`.
- Priorite Open Data > KBO scrape dans `search_kbo_by_name`.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import sys
import uuid
import zipfile

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")


def _build_sample_zip() -> bytes:
    """Construit un ZIP BCE Open Data minimal pour les tests offline."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # enterprise.csv
        ent_csv = io.StringIO()
        w = csv.DictWriter(ent_csv, fieldnames=["EnterpriseNumber", "Status", "StartDate"])
        w.writeheader()
        w.writerow({"EnterpriseNumber": "0403.201.185", "Status": "AC", "StartDate": "1962-10-23"})
        w.writerow({"EnterpriseNumber": "0893.860.839", "Status": "AC", "StartDate": "2007-11-30"})
        w.writerow({"EnterpriseNumber": "0400.000.000", "Status": "ST", "StartDate": "1900-01-01"})  # stopped
        zf.writestr("enterprise.csv", ent_csv.getvalue())
        # denomination.csv
        denom_csv = io.StringIO()
        w = csv.DictWriter(denom_csv, fieldnames=["EntityNumber", "Language", "TypeOfDenomination", "Denomination"])
        w.writeheader()
        w.writerow({"EntityNumber": "0403.201.185", "Language": "FR", "TypeOfDenomination": "001", "Denomination": "BELFIUS BANQUE"})
        w.writerow({"EntityNumber": "0403.201.185", "Language": "NL", "TypeOfDenomination": "001", "Denomination": "BELFIUS BANK"})
        w.writerow({"EntityNumber": "0403.201.185", "Language": "FR", "TypeOfDenomination": "003", "Denomination": "Belfius"})
        w.writerow({"EntityNumber": "0893.860.839", "Language": "FR", "TypeOfDenomination": "001", "Denomination": "BELFIUS ASSET FINANCE HOLDING"})
        w.writerow({"EntityNumber": "0400.000.000", "Language": "FR", "TypeOfDenomination": "001", "Denomination": "Vieille SA Fermee"})
        zf.writestr("denomination.csv", denom_csv.getvalue())
        # address.csv
        addr_csv = io.StringIO()
        w = csv.DictWriter(addr_csv, fieldnames=["EntityNumber", "TypeOfAddress", "Zipcode", "MunicipalityFR", "MunicipalityNL", "StreetFR", "StreetNL", "HouseNumber"])
        w.writeheader()
        w.writerow({"EntityNumber": "0403.201.185", "TypeOfAddress": "002", "Zipcode": "1210", "MunicipalityFR": "SAINT-JOSSE-TEN-NOODE", "MunicipalityNL": "SINT-JOOST-TEN-NODE", "StreetFR": "Place Charles Rogier", "StreetNL": "Karel Rogierplein", "HouseNumber": "11"})
        w.writerow({"EntityNumber": "0893.860.839", "TypeOfAddress": "002", "Zipcode": "1210", "MunicipalityFR": "SAINT-JOSSE-TEN-NOODE", "MunicipalityNL": "", "StreetFR": "Place Charles Rogier", "StreetNL": "", "HouseNumber": "11"})
        zf.writestr("address.csv", addr_csv.getvalue())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def test_parse_bce_opendata_extracts_official_name():
    """iter90iq-1 : le parser distingue la denomination officielle
    (TypeOfDenomination=001) des variantes."""
    from bce_opendata import parse_bce_opendata_zip
    entities = parse_bce_opendata_zip(_build_sample_zip())
    by_bce = {e["bce"]: e for e in entities}
    assert "0403.201.185" in by_bce
    e = by_bce["0403.201.185"]
    assert e["name"] == "BELFIUS BANQUE"
    # Toutes les denominations doivent etre collectees
    assert "BELFIUS BANK" in e["all_names"]
    assert "Belfius" in e["all_names"]


def test_parse_bce_opendata_merges_address_registered_office():
    """iter90iq-2 : l'adresse doit provenir du siege social (TypeOfAddress=002)."""
    from bce_opendata import parse_bce_opendata_zip
    entities = parse_bce_opendata_zip(_build_sample_zip())
    by_bce = {e["bce"]: e for e in entities}
    e = by_bce["0403.201.185"]
    assert e["zipcode"] == "1210"
    assert "SAINT-JOSSE" in e["city"]
    assert "Place Charles Rogier" in e["address"]
    assert "11" in e["address"]


def test_parse_bce_opendata_marks_active_status():
    """iter90iq-3 : `active=True` pour Status=AC, `active=False` pour ST."""
    from bce_opendata import parse_bce_opendata_zip
    entities = parse_bce_opendata_zip(_build_sample_zip())
    by_bce = {e["bce"]: e for e in entities}
    assert by_bce["0403.201.185"]["active"] is True
    assert by_bce["0400.000.000"]["active"] is False


def test_parse_bce_opendata_normalizes_bce_format():
    """iter90iq-4 : `bce` en format 0XXX.XXX.XXX, `bce_raw` en 10 chiffres."""
    from bce_opendata import parse_bce_opendata_zip
    entities = parse_bce_opendata_zip(_build_sample_zip())
    for e in entities:
        assert len(e["bce"].replace(".", "")) == 10
        assert e["bce_raw"].isdigit() and len(e["bce_raw"]) == 10


# ---------------------------------------------------------------------------
# Ingestion + search
# ---------------------------------------------------------------------------
def test_ingest_and_search_end_to_end():
    """iter90iq-5 : ingere le ZIP fixture, recherche 'Belfius' -> retourne
    BELFIUS BANQUE avec similarity >= 1.0, active=True."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from bce_opendata import ingest_bce_opendata_zip, search_bce_opendata, opendata_status
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        # Namespace de test : utilise une DB dediee pour isoler.
        test_db_name = f"test_bce_opendata_{uuid.uuid4().hex[:8]}"
        tdb = client[test_db_name]
        try:
            zip_bytes = _build_sample_zip()
            report = await ingest_bce_opendata_zip(tdb, zip_bytes)
            assert report["parsed"] == 3, f"Attendu 3 entites, recu {report}"
            assert report["inserted"] + report["updated"] == 3

            # Search 'Belfius' -> BELFIUS BANQUE en premier
            hits = await search_bce_opendata(tdb, "Belfius", top_n=5)
            assert len(hits) >= 1, f"Doit trouver au moins 1 match pour Belfius, recu : {hits}"
            top = hits[0]
            assert top["bce"] == "0403.201.185"
            assert top["name"] == "BELFIUS BANQUE"
            assert top["similarity"] >= 0.99

            # Search 'Vieille' -> l'entite ST n'est pas retournee (active only)
            hits2 = await search_bce_opendata(tdb, "Vieille", top_n=5)
            bces2 = {h["bce"] for h in hits2}
            assert "0400.000.000" not in bces2, "Une entite STOPPED (Status!=AC) ne doit pas remonter"

            # Status endpoint
            status = await opendata_status(tdb)
            assert status["total_entities"] >= 2
            assert status["enabled"] is True
            assert status["last_ingest"] is not None
            assert status["last_ingest"]["parsed"] == 3

            # Idempotence : reingest doit updater sans dupliquer
            report2 = await ingest_bce_opendata_zip(tdb, zip_bytes)
            assert report2["parsed"] == 3
            assert report2["inserted"] == 0, "Reingest doit etre 100% update (upsert idempotent)"
            total_after = await tdb.bce_opendata.count_documents({})
            assert total_after == 3, f"Doit rester 3 docs apres reingest, recu {total_after}"
        finally:
            await client.drop_database(test_db_name)
            client.close()

    asyncio.run(_go())


def test_search_bce_opendata_filters_by_postal_code():
    """iter90iq-6 : filtre soft sur zipcode. Si aucun match sur le zipcode
    donne, on relaxe le filtre (mieux vaut candidat imparfait que rien)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from bce_opendata import ingest_bce_opendata_zip, search_bce_opendata
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        test_db_name = f"test_bce_pc_{uuid.uuid4().hex[:8]}"
        tdb = client[test_db_name]
        try:
            await ingest_bce_opendata_zip(tdb, _build_sample_zip())
            # zipcode 1210 -> filtre retenu
            hits = await search_bce_opendata(tdb, "Belfius", postal_code="1210", top_n=5)
            assert all(h["bce"] in ("0403.201.185", "0893.860.839") for h in hits)
            # zipcode 9999 -> aucun match sur ce zipcode, on relaxe
            hits2 = await search_bce_opendata(tdb, "Belfius", postal_code="9999", top_n=5)
            assert len(hits2) >= 1
        finally:
            await client.drop_database(test_db_name)
            client.close()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Priorite Open Data > KBO scrape
# ---------------------------------------------------------------------------
def test_search_kbo_prefers_local_open_data_over_scrape(monkeypatch):
    """iter90iq-7 : si des candidats locaux existent, `search_kbo_by_name`
    ne fait AUCUNE requete reseau (pas de call a _fetch_kbo)."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        import bce_lookup as bl
        from bce_opendata import ingest_bce_opendata_zip
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        test_db_name = f"test_bce_prio_{uuid.uuid4().hex[:8]}"
        tdb = client[test_db_name]
        try:
            await ingest_bce_opendata_zip(tdb, _build_sample_zip())

            call_counter = {"n": 0}

            async def fake_fetch(name, postal_code=""):
                call_counter["n"] += 1
                return ""
            monkeypatch.setattr(bl, "_fetch_kbo", fake_fetch)

            hits = await bl.search_kbo_by_name("Belfius Banque", top_n=3, db=tdb)
            assert len(hits) >= 1, "Doit retourner des candidats locaux"
            assert hits[0]["bce"] == "0403.201.185"
            assert call_counter["n"] == 0, (
                f"Aucun appel reseau attendu quand Open Data local matche. "
                f"call_counter={call_counter['n']}"
            )
        finally:
            await client.drop_database(test_db_name)
            client.close()

    asyncio.run(_go())


def test_search_kbo_falls_back_to_scrape_when_no_local_match(monkeypatch):
    """iter90iq-8 : si Open Data ne matche pas, fallback sur scrape KBO."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        import bce_lookup as bl
        from bce_opendata import ingest_bce_opendata_zip
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        test_db_name = f"test_bce_fb_{uuid.uuid4().hex[:8]}"
        tdb = client[test_db_name]
        try:
            await ingest_bce_opendata_zip(tdb, _build_sample_zip())

            call_counter = {"n": 0}
            FAKE_HTML = """
<table>
<tr>
  <td>1</td>
  <td>ENT PM Actif</td>
  <td><a href="toonondernemingps.html?ondernemingsnummer=999999999">0999.999.999</a> 2024-01-01</td>
  <td>-</td>
  <td>SCRAPED FALLBACK SA</td>
  <td>Rue Test 1 1000 Bruxelles</td>
</tr>
</table>
"""

            async def fake_fetch(name, postal_code=""):
                call_counter["n"] += 1
                return FAKE_HTML
            monkeypatch.setattr(bl, "_fetch_kbo", fake_fetch)

            # Nom qui n'existe pas dans le fixture Open Data
            hits = await bl.search_kbo_by_name(
                "UnknownCorp-" + uuid.uuid4().hex[:8],
                top_n=3, db=tdb,
            )
            assert call_counter["n"] == 1, (
                f"Un appel reseau attendu (fallback). call_counter={call_counter['n']}"
            )
            assert len(hits) == 1
            assert hits[0]["bce"] == "0999.999.999"
            assert hits[0]["name"] == "SCRAPED FALLBACK SA"
        finally:
            await client.drop_database(test_db_name)
            client.close()

    asyncio.run(_go())


# ---------------------------------------------------------------------------
# Endpoint status
# ---------------------------------------------------------------------------
def test_status_endpoint_reports_empty_when_no_ingest():
    """iter90iq-9 : status retourne enabled=False + total=0 sur une base
    fraiche."""
    async def _go():
        from motor.motor_asyncio import AsyncIOMotorClient
        from bce_opendata import opendata_status
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        test_db_name = f"test_bce_stat_{uuid.uuid4().hex[:8]}"
        tdb = client[test_db_name]
        try:
            status = await opendata_status(tdb)
            assert status["total_entities"] == 0
            assert status["enabled"] is False
            assert status["last_ingest"] is None
        finally:
            await client.drop_database(test_db_name)
            client.close()

    asyncio.run(_go())
