"""iter90cb - Fix distribution des appels sur clé avec lots orphelins.

Bug utilisateur reel (ACP Acacia / Matexi) : quand la cle de distribution
contient des lots avec share > 0 mais sans owner_id (orphelins), le
denominateur total_shares les compte, mais le numerateur skip -> perte de
X% du montant total distribue.

Exemple observe :
- Reserve budget = 1500 EUR
- Distribue = 1446 EUR (3.6% perdu)
- Cause : 3.6% des shares dans la cle "Charges communes" sont sur lots orphelins

Fix : filtrer les shares des lots sans owner AVANT de calculer total_shares.
Effet : redistribution proportionnelle sur les proprietaires actuels.
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


async def _setup_matexi_scenario(orphan_share_pct: float):
    """Cree une ACP avec :
    - 1 owner "Matexi" possedant N-1 lots (donc N-1 * quotity = 96.4% des shares)
    - 1 lot orphelin (owner_id=None) avec le reste des shares
    - 1 cle de distribution incluant TOUS les lots (dont l'orphelin)
    - 1 budget avec 1 ligne + reserve_fund + roulement_fund
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90cb-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'iter90cb-Acacia-like', 'active': True,
    })

    # 1 Owner Matexi
    matexi_id = str(uuid.uuid4())
    await db.owners.insert_one({
        'id': matexi_id, 'name': 'Matexi', 'last_name': 'Matexi',
        'copropriete_ids': [acp_id],
        'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
    })

    # 10 lots. 9 lots Matexi (100/lot = 900), 1 lot orphelin (adjusted for pct)
    # target: orphan_share_pct = 3.6 => orphelin_share = 900 * 0.036/0.964 = 33.6
    matexi_share_each = 100.0
    n_matexi_lots = 9
    total_matexi = matexi_share_each * n_matexi_lots
    orphan_share = total_matexi * orphan_share_pct / (100.0 - orphan_share_pct)
    key_lots = []
    for i in range(n_matexi_lots):
        lid = str(uuid.uuid4())
        await db.lots.insert_one({
            'id': lid, 'copropriete_id': acp_id,
            'number': f'A{i+1}', 'lot_number': f'A{i+1}',
            'owner_id': matexi_id, 'quotity': matexi_share_each,
        })
        key_lots.append({'lot_id': lid, 'share': matexi_share_each})
    # Lot orphelin (sans owner)
    orphan_lid = str(uuid.uuid4())
    await db.lots.insert_one({
        'id': orphan_lid, 'copropriete_id': acp_id,
        'number': 'ORPHAN', 'lot_number': 'ORPHAN',
        'owner_id': None, 'quotity': orphan_share,
    })
    key_lots.append({'lot_id': orphan_lid, 'share': orphan_share})

    # Cle de distribution "Charges communes" incluant tous les lots
    key_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        'id': key_id, 'copropriete_id': acp_id, 'name': 'Charges communes',
        'lots': key_lots,
    })

    # Fiscal year
    fy_id = str(uuid.uuid4())
    await db.fiscal_years.insert_one({
        'id': fy_id, 'copropriete_id': acp_id, 'name': '2026',
        'start_date': '2026-01-01', 'end_date': '2026-12-31', 'status': 'open',
    })

    # Budget approuve : 1000 EUR/an sur charges communes (250/trimestre)
    budget_id = str(uuid.uuid4())
    await db.budgets.insert_one({
        'id': budget_id, 'copropriete_id': acp_id, 'fiscal_year_id': fy_id,
        'name': 'Budget 2026', 'status': 'approved',
        'lines': [{
            'account_number': '611000', 'account_name': 'Charges test',
            'amount': 1000.0, 'distribution_key_id': key_id,
        }],
    })

    client.close()
    return acp_id, matexi_id, budget_id, key_id


async def _teardown(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for coll in ('coproprietes', 'owners', 'lots', 'distribution_keys',
                 'budgets', 'fund_calls', 'journal_entries', 'fiscal_years'):
        await db[coll].delete_many({'copropriete_id': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    client.close()


def test_iter90cb_orphan_lot_shares_are_redistributed_to_matexi():
    """Scenario Matexi : 9 lots avec owner + 1 lot orphelin (~3.6% share).
    Reserve = 1500 EUR. Attendu apres fix : Matexi recoit 1500 EUR (pas 1446).
    """
    acp_id, matexi_id, budget_id, key_id = asyncio.run(_setup_matexi_scenario(3.6))
    try:
        s = _login()
        # Preview appels : 4 trimestres avec reserve 1500 + roulement 5200
        r = s.post(f'{BASE}/api/fund-calls/generate-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'due_offset_days': 30,
            'reserve_fund': {'enabled': True, 'amount': 1500.0,
                             'distribution_key_id': key_id, 'label': 'Fonds de reserve'},
            'roulement_fund': {'enabled': True, 'amount': 5200.0,
                               'distribution_key_id': key_id, 'label': 'Fonds de roulement',
                               'mode': 'create'},
            'persist': False,
        })
        assert r.status_code == 200, r.text
        calls = r.json()['calls']
        # Premier appel contient provisions Q1 (250) + reserve (1500) + roulement (5200)
        # Matexi doit avoir 100% de chacun car il est seul owner
        first_call = calls[0]
        dist = first_call['distribution']
        matexi_lines = [d for d in dist if d.get('owner_id') == matexi_id]

        # Somme totale pour Matexi sur cet appel : 250 + 1500 + 5200 = 6950
        matexi_total = round(sum(d.get('amount', 0) for d in matexi_lines), 2)
        expected = round(250.0 + 1500.0 + 5200.0, 2)
        assert matexi_total == expected, (
            f"Matexi doit recevoir 100% de l'appel = {expected} EUR, "
            f"obtenu {matexi_total}"
        )

        # AUCUN owner=None ne doit avoir de amount
        orphan_lines = [d for d in dist if d.get('owner_id') is None]
        assert all(d.get('amount', 0) == 0 for d in orphan_lines), \
            f"Lots orphelins ne doivent JAMAIS recevoir de montant : {orphan_lines}"

        # Le total du call doit egaler la somme des amounts distribues
        call_total = round(sum(d.get('amount', 0) for d in dist), 2)
        assert call_total == expected

    finally:
        asyncio.run(_teardown(acp_id))


def test_iter90cb_no_orphans_regression():
    """Sans lot orphelin, le comportement doit rester identique (100% distribue)."""
    acp_id, matexi_id, budget_id, key_id = asyncio.run(_setup_matexi_scenario(0.0))
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/generate-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'due_offset_days': 30,
            'reserve_fund': {'enabled': True, 'amount': 1500.0,
                             'distribution_key_id': key_id, 'label': 'Fonds de reserve'},
            'roulement_fund': {'enabled': True, 'amount': 5200.0,
                               'distribution_key_id': key_id, 'label': 'Fonds de roulement',
                               'mode': 'create'},
            'persist': False,
        })
        assert r.status_code == 200, r.text
        calls = r.json()['calls']
        first_call = calls[0]
        dist = first_call['distribution']
        matexi_lines = [d for d in dist if d.get('owner_id') == matexi_id]
        matexi_total = round(sum(d.get('amount', 0) for d in matexi_lines), 2)
        expected = round(250.0 + 1500.0 + 5200.0, 2)
        assert matexi_total == expected
    finally:
        asyncio.run(_teardown(acp_id))


def test_iter90cb_multi_owners_orphan_lot():
    """2 proprietaires (50/50) + 1 lot orphelin : chacun doit recevoir 50% du montant."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    acp_id = f'iter90cb-multi-{uuid.uuid4()}'

    async def _setup():
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90cb-multi', 'active': True})
        owner_a = str(uuid.uuid4())
        owner_b = str(uuid.uuid4())
        for oid, name in [(owner_a, 'Owner A'), (owner_b, 'Owner B')]:
            await db.owners.insert_one({
                'id': oid, 'name': name, 'last_name': name,
                'copropriete_ids': [acp_id],
                'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
            })
        lot_a = str(uuid.uuid4())
        lot_b = str(uuid.uuid4())
        lot_orphan = str(uuid.uuid4())
        await db.lots.insert_one({
            'id': lot_a, 'copropriete_id': acp_id, 'number': 'A',
            'owner_id': owner_a, 'quotity': 100,
        })
        await db.lots.insert_one({
            'id': lot_b, 'copropriete_id': acp_id, 'number': 'B',
            'owner_id': owner_b, 'quotity': 100,
        })
        await db.lots.insert_one({
            'id': lot_orphan, 'copropriete_id': acp_id, 'number': 'ORPHAN',
            'owner_id': None, 'quotity': 50,
        })
        key_id = str(uuid.uuid4())
        await db.distribution_keys.insert_one({
            'id': key_id, 'copropriete_id': acp_id, 'name': 'Charges',
            'lots': [
                {'lot_id': lot_a, 'share': 100},
                {'lot_id': lot_b, 'share': 100},
                {'lot_id': lot_orphan, 'share': 50},  # orphelin avec 20% des shares
            ],
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
        return owner_a, owner_b, budget_id, key_id

    async def _cleanup():
        for coll in ('coproprietes', 'owners', 'lots', 'distribution_keys',
                     'budgets', 'fund_calls', 'journal_entries', 'fiscal_years'):
            await db[coll].delete_many({'copropriete_id': acp_id})
        await db.coproprietes.delete_many({'id': acp_id})

    owner_a, owner_b, budget_id, _key = asyncio.run(_setup())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/generate-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 1,
            'due_offset_days': 30,
            'persist': False,
        })
        assert r.status_code == 200, r.text
        dist = r.json()['calls'][0]['distribution']
        total = round(sum(d['amount'] for d in dist), 2)
        assert total == 1000.0, f"Total distribue doit etre 1000, obtenu {total}"
        # Chaque owner doit avoir 500 (50/50)
        a_total = round(sum(d['amount'] for d in dist if d.get('owner_id') == owner_a), 2)
        b_total = round(sum(d['amount'] for d in dist if d.get('owner_id') == owner_b), 2)
        assert a_total == 500.0
        assert b_total == 500.0
    finally:
        asyncio.run(_cleanup())
        client.close()
