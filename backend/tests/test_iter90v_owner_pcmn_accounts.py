"""iter90v - Tests: 
1. Nouvelle assignation 4100/4101 sur creation d'un proprietaire
2. Delete guard (409 si journal entries existent)
3. Bilan : affiche le n° de compte (410x) + agrege provisions + reserve
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


async def _get_or_create_test_acp():
    """Return a fresh ACP id for testing (isolated from real data)."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    acp_id = f'iter90v-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'iter90v-testacp', 'address': 'Test',
        'active': True,
    })
    client.close()
    return acp_id


async def _cleanup_acp(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for col in ['coproprietes', 'owners', 'pcmn_accounts', 'journal_entries', 'lots']:
        await db[col].delete_many({'copropriete_id': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    await db.owners.delete_many({'copropriete_ids': acp_id})
    client.close()


def test_new_owner_gets_two_pcmn_accounts():
    """Un nouveau proprietaire a 2 comptes 4101XXXX (roulement) + 4100XXXX (reserve)."""
    acp_id = asyncio.run(_get_or_create_test_acp())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/owners',
                   headers={'X-Copropriete-Id': acp_id},
                   json={'first_name': 'iter90v', 'last_name': 'AccTest',
                         'copropriete_id': acp_id})
        assert r.status_code == 200, r.text
        body = r.json()
        accs = body.get('tier_accounts', {}).get(acp_id, {})
        assert 'provisions' in accs and 'reserve' in accs
        # Nouveau format PCMN
        assert accs['provisions'].startswith('4101'), f"Expected 4101 prefix, got {accs['provisions']}"
        assert accs['reserve'].startswith('4100'), f"Expected 4100 prefix, got {accs['reserve']}"
        # Longueur 8 chars (4 + 4)
        assert len(accs['provisions']) == 8
        assert len(accs['reserve']) == 8

        # Verifie les comptes maitres crees dans pcmn_accounts
        async def check_pcmn():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            names = {}
            for num in ['4100', '4101', accs['provisions'], accs['reserve']]:
                doc = await db.pcmn_accounts.find_one(
                    {'number': num, 'copropriete_id': acp_id}, {'_id': 0})
                names[num] = doc['name'] if doc else None
            client.close()
            return names

        names = asyncio.run(check_pcmn())
        assert names['4100'] == 'Acompte de fonds de reserve appele'
        assert names['4101'] == 'Acompte de fonds de roulement appele'
        assert 'Acompte de fonds de roulement appele' in names[accs['provisions']]
        assert 'Acompte de fonds de reserve appele' in names[accs['reserve']]
    finally:
        asyncio.run(_cleanup_acp(acp_id))


def test_owner_delete_blocked_by_journal_entries():
    """La suppression d'un proprietaire est refusee (409) s'il a des ecritures comptables."""
    acp_id = asyncio.run(_get_or_create_test_acp())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/owners',
                   headers={'X-Copropriete-Id': acp_id},
                   json={'first_name': 'iter90v', 'last_name': 'DeleteBlockTest',
                         'copropriete_id': acp_id})
        assert r.status_code == 200
        owner_id = r.json()['id']
        prov_acc = r.json()['tier_accounts'][acp_id]['provisions']

        # Insere une ecriture comptable directement dans la DB
        async def insert_je():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.journal_entries.insert_one({
                'id': str(uuid.uuid4()),
                'copropriete_id': acp_id,
                'journal_type': 'VE',
                'date': '2026-01-01',
                'reference': 'iter90v-test-je',
                'lines': [
                    {'account_number': prov_acc, 'account_name': 'test',
                     'debit': 100, 'credit': 0, 'third_party_id': owner_id},
                    {'account_number': '700000', 'account_name': 'Provisions',
                     'debit': 0, 'credit': 100},
                ],
            })
            client.close()
        asyncio.run(insert_je())

        # Delete should be REFUSED
        r_del = s.delete(f'{BASE}/api/owners/{owner_id}',
                         headers={'X-Copropriete-Id': acp_id})
        assert r_del.status_code == 409, f'Expected 409, got {r_del.status_code}: {r_del.text}'
        assert 'ecriture' in r_del.json()['detail'].lower()
        assert '1 ecriture' in r_del.json()['detail']

        # Cleanup: remove journal entry then delete succeeds
        async def clean_je():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.journal_entries.delete_many({'reference': 'iter90v-test-je'})
            client.close()
        asyncio.run(clean_je())

        r_del2 = s.delete(f'{BASE}/api/owners/{owner_id}',
                          headers={'X-Copropriete-Id': acp_id})
        assert r_del2.status_code == 200, r_del2.text
    finally:
        asyncio.run(_cleanup_acp(acp_id))


def test_owner_delete_blocked_by_lot_assignment():
    """La suppression est refusee (409) si un lot est encore assigne."""
    acp_id = asyncio.run(_get_or_create_test_acp())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/owners', headers={'X-Copropriete-Id': acp_id},
                   json={'first_name': 'iter90v', 'last_name': 'LotBlockTest',
                         'copropriete_id': acp_id})
        assert r.status_code == 200
        owner_id = r.json()['id']

        # Attach a lot
        async def insert_lot():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.lots.insert_one({
                'id': str(uuid.uuid4()), 'number': 'LT01', 'quotity': 100,
                'owner_id': owner_id, 'copropriete_id': acp_id,
            })
            client.close()
        asyncio.run(insert_lot())

        r_del = s.delete(f'{BASE}/api/owners/{owner_id}',
                         headers={'X-Copropriete-Id': acp_id})
        assert r_del.status_code == 409
        assert 'lot' in r_del.json()['detail'].lower()
    finally:
        asyncio.run(_cleanup_acp(acp_id))


def test_bilan_shows_account_number_and_sums_provisions_reserve():
    """Le bilan affiche le n° de compte du proprietaire + agrege les 2 tier accounts."""
    acp_id = asyncio.run(_get_or_create_test_acp())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/owners', headers={'X-Copropriete-Id': acp_id},
                   json={'first_name': 'Alice', 'last_name': 'BilanTest',
                         'copropriete_id': acp_id})
        assert r.status_code == 200
        owner_id = r.json()['id']
        accs = r.json()['tier_accounts'][acp_id]
        prov_acc = accs['provisions']  # 4101xxxx
        res_acc = accs['reserve']       # 4100xxxx

        # Insert 2 journal entries: 100 EUR debit on provisions, 50 EUR debit on reserve
        async def insert_jes():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.journal_entries.insert_many([
                {'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
                 'journal_type': 'VE', 'date': '2026-01-01', 'reference': 'iter90v-bilan-1',
                 'lines': [
                     {'account_number': prov_acc, 'account_name': 'Roul',
                      'debit': 100, 'credit': 0, 'third_party_id': owner_id},
                     {'account_number': '700000', 'account_name': 'x', 'debit': 0, 'credit': 100},
                 ]},
                {'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
                 'journal_type': 'VE', 'date': '2026-01-02', 'reference': 'iter90v-bilan-2',
                 'lines': [
                     {'account_number': res_acc, 'account_name': 'Res',
                      'debit': 50, 'credit': 0, 'third_party_id': owner_id},
                     {'account_number': '160', 'account_name': 'x', 'debit': 0, 'credit': 50},
                 ]},
            ])
            client.close()
        asyncio.run(insert_jes())

        r_bil = s.get(f'{BASE}/api/reports/bilan?copropriete_id={acp_id}',
                      headers={'X-Copropriete-Id': acp_id})
        assert r_bil.status_code == 200
        va = next((rub for rub in r_bil.json()['actif']
                   if rub['label'].startswith('V.A')), None)
        assert va is not None
        assert va['total'] > 0.01
        # Alice doit apparaitre avec un compte commencant par 4101 (prov) et total = 100+50=150
        alice = next((a for a in va['accounts']
                      if 'Alice' in a['account_name'] or 'BilanTest' in a['account_name']),
                     None)
        assert alice is not None, f"Alice not in {va['accounts']}"
        # Compte affiche : provisions (4101...)
        assert alice['account_number'].startswith('4101'), \
            f"Expected 4101 prefix, got {alice['account_number']}"
        # Total = sum(prov 100 + res 50) = 150
        assert abs(alice['amount'] - 150.0) < 0.01, \
            f"Expected 150.00, got {alice['amount']}"
    finally:
        asyncio.run(_cleanup_acp(acp_id))


if __name__ == '__main__':
    test_new_owner_gets_two_pcmn_accounts(); print('.', end='', flush=True)
    test_owner_delete_blocked_by_journal_entries(); print('.', end='', flush=True)
    test_owner_delete_blocked_by_lot_assignment(); print('.', end='', flush=True)
    test_bilan_shows_account_number_and_sums_provisions_reserve(); print('.', end='', flush=True)
    print(' 4/4 OK')
