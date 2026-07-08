"""iter90cc - Endpoint preflight orphan check.

Verifie que les endpoints /fund-calls/preview-from-budget et
/fund-calls/preflight-orphan-check detectent correctement les lots orphelins
et retournent un warning structure pour l'UI.
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


async def _setup_acp_with_orphans(orphan_share_pct: float):
    """Cree une ACP avec 1 owner + 1 lot orphelin dans la meme cle."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90cc-{uuid.uuid4()}'
    await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90cc-preflight', 'active': True})

    owner_id = str(uuid.uuid4())
    await db.owners.insert_one({
        'id': owner_id, 'name': 'Matexi', 'copropriete_ids': [acp_id],
        'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
    })

    # 9 lots owner + 1 lot orphelin
    total_owner_share = 900.0
    orphan_share = total_owner_share * orphan_share_pct / (100 - orphan_share_pct) if orphan_share_pct > 0 else 0
    key_lots = []
    for i in range(9):
        lid = str(uuid.uuid4())
        await db.lots.insert_one({
            'id': lid, 'copropriete_id': acp_id,
            'number': f'A{i+1}', 'owner_id': owner_id, 'quotity': 100,
        })
        key_lots.append({'lot_id': lid, 'share': 100})
    if orphan_share > 0:
        orphan_lid = str(uuid.uuid4())
        await db.lots.insert_one({
            'id': orphan_lid, 'copropriete_id': acp_id,
            'number': 'ORPHAN-42', 'owner_id': None, 'quotity': orphan_share,
        })
        key_lots.append({'lot_id': orphan_lid, 'share': orphan_share})

    key_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        'id': key_id, 'copropriete_id': acp_id, 'name': 'Charges communes',
        'lots': key_lots,
    })

    fy_id = str(uuid.uuid4())
    await db.fiscal_years.insert_one({
        'id': fy_id, 'copropriete_id': acp_id, 'name': '2026',
        'start_date': '2026-01-01', 'end_date': '2026-12-31',
    })

    budget_id = str(uuid.uuid4())
    await db.budgets.insert_one({
        'id': budget_id, 'copropriete_id': acp_id, 'fiscal_year_id': fy_id,
        'status': 'approved',
        'lines': [{
            'account_number': '611000', 'account_name': 'Charges',
            'amount': 1000.0, 'distribution_key_id': key_id,
        }],
    })

    client.close()
    return acp_id, budget_id, key_id


async def _cleanup(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for coll in ('coproprietes', 'owners', 'lots', 'distribution_keys',
                 'budgets', 'fund_calls', 'fiscal_years'):
        await db[coll].delete_many({'copropriete_id': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    client.close()


def test_preflight_detects_orphan_lots_with_share():
    """Preflight doit detecter le lot ORPHAN-42 et calculer le % correctement."""
    acp_id, budget_id, key_id = asyncio.run(_setup_acp_with_orphans(3.6))
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preflight-orphan-check', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'reserve_fund': {'enabled': True, 'amount': 1500.0,
                             'distribution_key_id': key_id, 'label': 'Reserve'},
            'roulement_fund': {'enabled': True, 'amount': 5200.0,
                               'distribution_key_id': key_id, 'label': 'Roulement', 'mode': 'create'},
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        warning = r.json()['orphan_lots_warning']
        assert warning['orphan_count'] == 1
        assert 3.5 <= warning['orphan_share_percentage'] <= 3.7
        assert len(warning['keys_affected']) == 1
        assert warning['keys_affected'][0]['key_name'] == 'Charges communes'
        assert len(warning['lots']) == 1
        assert warning['lots'][0]['lot_number'] == 'ORPHAN-42'
        # La cle est referencee 3 fois (budget line + reserve + roulement) mais
        # doit apparaitre une seule fois dans keys_affected
        assert len(warning['keys_affected']) == 1
    finally:
        asyncio.run(_cleanup(acp_id))


def test_preflight_no_orphans_returns_empty_warning():
    """Sans lot orphelin, preflight retourne 0 orphelin et 0%."""
    acp_id, budget_id, key_id = asyncio.run(_setup_acp_with_orphans(0.0))
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preflight-orphan-check', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        warning = r.json()['orphan_lots_warning']
        assert warning['orphan_count'] == 0
        assert warning['orphan_share_percentage'] == 0.0
        assert warning['keys_affected'] == []
        assert warning['lots'] == []
    finally:
        asyncio.run(_cleanup(acp_id))


def test_preview_endpoint_also_includes_warning():
    """L'endpoint preview-from-budget doit inclure le warning dans sa reponse
    (enrichissement iter90cc)."""
    acp_id, budget_id, key_id = asyncio.run(_setup_acp_with_orphans(3.6))
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preview-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'reserve_fund': {'enabled': True, 'amount': 1500.0,
                             'distribution_key_id': key_id, 'label': 'Reserve'},
            'roulement_fund': {'enabled': True, 'amount': 5200.0,
                               'distribution_key_id': key_id, 'label': 'Roulement', 'mode': 'create'},
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert 'orphan_lots_warning' in body
        assert body['orphan_lots_warning']['orphan_count'] == 1
        # La reponse contient AUSSI les calls (preview normal)
        assert 'calls' in body
        assert len(body['calls']) == 4
    finally:
        asyncio.run(_cleanup(acp_id))
