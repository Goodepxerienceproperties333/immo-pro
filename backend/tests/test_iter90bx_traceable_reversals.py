"""iter90bx - Contre-passation traceable des ecritures comptables.

Regle metier belge (art. III.86 CDE + PCMN) : les ecritures comptables NE
PEUVENT PAS etre supprimees. Toute "suppression" doit generer une ecriture
INVERSE qui neutralise mathematiquement l'originale tout en gardant la
trace audit.

Tests :
- reverse_journal_entry cree une entree avec Dr<->Cr inverses
- L'originale est marquee `reversed=True` + `reversed_by_entry_id` pointant vers la contre-passation
- La contre-passation porte `is_reversal=True` + `reverses_entry_id` pointant vers l'originale
- Idempotence : re-appeler ne double pas
- _delete_auto_entries redirige vers la contre-passation
- L'endpoint DELETE /accounting/entries/{id} cree une contre-passation
"""
import asyncio
import os
import sys
import pathlib
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')

import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE = None
env_path = pathlib.Path(__file__).parent.parent.parent / 'frontend' / '.env'
for line in env_path.read_text().splitlines():
    if line.startswith('REACT_APP_BACKEND_URL='):
        BASE = line.split('=', 1)[1].strip()
        break
assert BASE

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


def _login():
    s = requests.Session()
    r = s.post(f'{BASE}/api/auth/login',
               json={'email': 'admin@copro.be', 'password': 'admin123'})
    assert r.status_code == 200
    return s


async def _create_test_entry(db, acp_id: str) -> str:
    """Insere une ecriture de test (OD manuelle) et retourne son id."""
    entry_id = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        'id': entry_id,
        'copropriete_id': acp_id,
        'date': '2026-01-15',
        'journal_type': 'OD',
        'reference': 'TEST-OD-001',
        'description': 'Ecriture test iter90bx',
        'lines': [
            {'account_number': '611000', 'account_name': 'Charge test',
             'debit': 500.0, 'credit': 0.0},
            {'account_number': '440000', 'account_name': 'Fournisseur',
             'debit': 0.0, 'credit': 500.0},
        ],
        'total_debit': 500.0, 'total_credit': 500.0,
    })
    return entry_id


async def _cleanup_acp(db, acp_id: str):
    await db.coproprietes.delete_many({'id': acp_id})
    await db.journal_entries.delete_many({'copropriete_id': acp_id})


def test_reverse_journal_entry_creates_inverse_and_marks_original():
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        try:
            entry_id = await _create_test_entry(db, acp_id)
            orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            from journal_reversals import reverse_journal_entry
            rev = await reverse_journal_entry(db, orig, reason='Test suppression')

            # La contre-passation
            assert rev is not None
            assert rev['is_reversal'] is True
            assert rev['reverses_entry_id'] == entry_id
            assert rev['reference'].startswith('EXT-')
            assert 'Contre-passation' in rev['description']
            assert rev['reversal_reason'] == 'Test suppression'
            # Dr<->Cr inverses
            assert rev['lines'][0]['debit'] == 0.0
            assert rev['lines'][0]['credit'] == 500.0
            assert rev['lines'][1]['debit'] == 500.0
            assert rev['lines'][1]['credit'] == 0.0
            assert rev['total_debit'] == 500.0
            assert rev['total_credit'] == 500.0
            assert rev['copropriete_id'] == acp_id

            # L'originale mise a jour
            updated = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            assert updated['reversed'] is True
            assert updated['reversed_by_entry_id'] == rev['id']
            assert updated['reversal_reason'] == 'Test suppression'
            # Les lignes de l'originale INCHANGEES (immuables)
            assert updated['lines'][0]['debit'] == 500.0
            assert updated['lines'][1]['credit'] == 500.0
        finally:
            await _cleanup_acp(db, acp_id)
            client.close()
    asyncio.run(_run())


def test_reverse_is_idempotent():
    """Contre-passer 2x la meme ecriture => ne fait rien la 2eme fois."""
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        try:
            entry_id = await _create_test_entry(db, acp_id)
            orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            from journal_reversals import reverse_journal_entry
            rev1 = await reverse_journal_entry(db, orig)
            # Re-fetch (originale maintenant marquee reversed=True)
            orig2 = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            rev2 = await reverse_journal_entry(db, orig2)
            assert rev1 is not None
            assert rev2 is None  # skip
            # Une seule contre-passation existe
            count = await db.journal_entries.count_documents(
                {'copropriete_id': acp_id, 'is_reversal': True}
            )
            assert count == 1
        finally:
            await _cleanup_acp(db, acp_id)
            client.close()
    asyncio.run(_run())


def test_cannot_reverse_a_reversal():
    """On ne contre-passe pas une contre-passation."""
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        try:
            entry_id = await _create_test_entry(db, acp_id)
            orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            from journal_reversals import reverse_journal_entry
            rev = await reverse_journal_entry(db, orig)
            # Essai de contre-passer la contre-passation
            rev_full = await db.journal_entries.find_one({'id': rev['id']}, {'_id': 0})
            result = await reverse_journal_entry(db, rev_full)
            assert result is None
        finally:
            await _cleanup_acp(db, acp_id)
            client.close()
    asyncio.run(_run())


def test_reverse_auto_entries_replaces_hard_delete():
    """_delete_auto_entries doit maintenant contre-passer, plus supprimer."""
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        try:
            src_id = str(uuid.uuid4())
            # 2 ecritures auto : une editable, une manuellement editee
            e1 = str(uuid.uuid4())
            e2 = str(uuid.uuid4())
            await db.journal_entries.insert_one({
                'id': e1, 'copropriete_id': acp_id, 'date': '2026-01-15',
                'journal_type': 'AC', 'auto_generated': True,
                'source_type': 'invoice', 'source_id': src_id,
                'lines': [{'account_number': '611', 'debit': 100.0, 'credit': 0.0},
                          {'account_number': '440', 'debit': 0.0, 'credit': 100.0}],
            })
            await db.journal_entries.insert_one({
                'id': e2, 'copropriete_id': acp_id, 'date': '2026-01-15',
                'journal_type': 'AC', 'auto_generated': True, 'manually_edited': True,
                'source_type': 'invoice', 'source_id': src_id,
                'lines': [{'account_number': '611', 'debit': 50.0, 'credit': 0.0},
                          {'account_number': '440', 'debit': 0.0, 'credit': 50.0}],
            })
            from auto_entries import _delete_auto_entries
            count = await _delete_auto_entries(db, 'invoice', src_id)
            # 1 contre-passation cree (e2 est manually_edited, preservee)
            assert count == 1
            # L'originale e1 EXISTE toujours en base mais reversed=True
            e1_updated = await db.journal_entries.find_one({'id': e1}, {'_id': 0})
            assert e1_updated is not None
            assert e1_updated['reversed'] is True
            # e2 non touchee (manually_edited)
            e2_updated = await db.journal_entries.find_one({'id': e2}, {'_id': 0})
            assert e2_updated is not None
            assert e2_updated.get('reversed') is not True
            # 1 contre-passation
            rev_count = await db.journal_entries.count_documents(
                {'copropriete_id': acp_id, 'is_reversal': True}
            )
            assert rev_count == 1
        finally:
            await _cleanup_acp(db, acp_id)
            client.close()
    asyncio.run(_run())


def test_delete_endpoint_creates_reversal_not_hard_delete():
    """DELETE /api/accounting/entries/{id} => 200 + contre-passation cree."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        entry_id = await _create_test_entry(db, acp_id)
        client.close()
        return acp_id, entry_id

    async def _cleanup(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id, entry_id = asyncio.run(_setup())
    try:
        s = _login()
        r = s.delete(
            f'{BASE}/api/accounting/entries/{entry_id}',
            params={'reason': 'test iter90bx via HTTP', 'copropriete_id': acp_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['original_id'] == entry_id
        assert 'reversal_id' in body
        assert body['reversal_reference'].startswith('EXT-')

        # Verification directe en base
        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            rev = await db.journal_entries.find_one({'id': body['reversal_id']}, {'_id': 0})
            client.close()
            return orig, rev

        orig, rev = asyncio.run(_check())
        assert orig['reversed'] is True
        assert orig['reversed_by_entry_id'] == body['reversal_id']
        assert rev['is_reversal'] is True
        assert rev['reverses_entry_id'] == entry_id

        # 2nd delete = 400 (deja contre-passee)
        r2 = s.delete(
            f'{BASE}/api/accounting/entries/{entry_id}',
            params={'copropriete_id': acp_id},
        )
        assert r2.status_code == 400
        assert 'deja' in r2.json()['detail'].lower()
    finally:
        asyncio.run(_cleanup(acp_id))


def test_list_entries_includes_reversals_by_default():
    """GET /api/accounting/entries retourne maintenant les contre-passations
    par defaut (audit trail visible)."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        entry_id = await _create_test_entry(db, acp_id)
        orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
        from journal_reversals import reverse_journal_entry
        rev = await reverse_journal_entry(db, orig)
        client.close()
        return acp_id, entry_id, rev['id']

    async def _cleanup(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id, entry_id, rev_id = asyncio.run(_setup())
    try:
        s = _login()
        # Sans include_reversals param -> defaut = true (iter90bx)
        r = s.get(f'{BASE}/api/accounting/entries', params={
            'copropriete_id': acp_id, 'journal_type': 'OD',
        })
        assert r.status_code == 200
        entries = r.json()
        ids = {e['id'] for e in entries}
        assert entry_id in ids, "Originale doit etre visible (audit)"
        assert rev_id in ids, "Contre-passation doit etre visible (audit)"

        # Avec include_reversals=false explicite -> cache les 2
        r2 = s.get(f'{BASE}/api/accounting/entries', params={
            'copropriete_id': acp_id, 'journal_type': 'OD',
            'include_reversals': 'false',
        })
        entries2 = r2.json()
        ids2 = {e['id'] for e in entries2}
        assert entry_id not in ids2
        assert rev_id not in ids2
    finally:
        asyncio.run(_cleanup(acp_id))


def test_reversal_date_uses_today_when_original_in_closed_fy():
    """Si l'ecriture originale est dans un exercice cloture, la contre-passation
    doit utiliser la date d'aujourd'hui (dans l'exercice ouvert)."""
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90bx-{uuid.uuid4()}'
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90bx', 'active': True})
        try:
            # FY 2025 cloture
            await db.fiscal_years.insert_one({
                'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
                'name': '2025', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
                'status': 'closed',
            })
            # Ecriture en 2025 (dans exercice cloture)
            entry_id = str(uuid.uuid4())
            await db.journal_entries.insert_one({
                'id': entry_id, 'copropriete_id': acp_id, 'date': '2025-06-15',
                'journal_type': 'OD',
                'lines': [{'account_number': '611', 'debit': 100.0, 'credit': 0.0},
                          {'account_number': '440', 'debit': 0.0, 'credit': 100.0}],
            })
            orig = await db.journal_entries.find_one({'id': entry_id}, {'_id': 0})
            from journal_reversals import reverse_journal_entry
            from datetime import datetime, timezone
            rev = await reverse_journal_entry(db, orig)
            today = datetime.now(timezone.utc).date().isoformat()
            assert rev['date'] == today, f"Contre-passation doit etre datee aujourd'hui, obtenu {rev['date']}"
        finally:
            await db.fiscal_years.delete_many({'copropriete_id': acp_id})
            await _cleanup_acp(db, acp_id)
            client.close()
    asyncio.run(_run())
