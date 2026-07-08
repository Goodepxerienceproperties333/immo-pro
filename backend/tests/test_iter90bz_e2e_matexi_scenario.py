"""iter90bz E2E - Verification HTTP du regroupement des mutations lot.

Simule le scenario Matexi : propriétaire avec 5 lots, chaque mutation trimestrielle
cree 5 ecritures OD distinctes ; l'endpoint doit les fusionner en 1 ligne "Mutations".
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


async def _setup_matexi_like():
    """Cree un promoteur avec 5 lots + 1 appel VE Q1 + 5 mutations OD (une par lot)."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90bz-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'iter90bz-testacp', 'active': True,
    })

    owner_id = str(uuid.uuid4())
    tier_prov = '41010001'
    await db.owners.insert_one({
        'id': owner_id, 'name': 'Iter90bz Promoteur', 'last_name': 'Promoteur',
        'copropriete_ids': [acp_id],
        'tier_accounts': {acp_id: {'provisions': tier_prov, 'reserve': '41000001'}},
    })

    # 5 lots
    lot_ids = []
    for i in range(5):
        lid = str(uuid.uuid4())
        lot_ids.append(lid)
        await db.lots.insert_one({
            'id': lid, 'copropriete_id': acp_id,
            'lot_number': f'L{i+1:03d}', 'owner_id': owner_id, 'quotity': 100,
        })

    # 1 VE : Appel de provisions Q1 (une seule ecriture debit sur tier_prov)
    await db.journal_entries.insert_one({
        'id': str(uuid.uuid4()),
        'copropriete_id': acp_id,
        'date': '2026-01-01',
        'journal_type': 'VE',
        'reference': 'FC-Q1-2026',
        'description': 'Appel Q1 2026',
        'source_type': 'fund_call',
        'lines': [
            {'account_number': tier_prov, 'account_name': 'Prov charges',
             'debit': 1000.0, 'credit': 0.0,
             'third_party_id': owner_id,
             'line_description': 'Appel de provisions - Trimestriel 1/4 - Exercice 2026'},
            {'account_number': '700000', 'account_name': 'Provisions appelees',
             'debit': 0.0, 'credit': 1000.0},
        ],
    })

    # 5 mutations OD (une par lot) - meme date, meme label
    for i, lid in enumerate(lot_ids):
        amount = 50.0 + i * 10  # 50, 60, 70, 80, 90
        await db.journal_entries.insert_one({
            'id': str(uuid.uuid4()),
            'copropriete_id': acp_id,
            'date': '2026-01-01',
            'journal_type': 'OD',
            'reference': f'MUT-L{i+1:03d}-Q1',
            'description': (
                f'Mutation lot L{i+1:03d} - Prorata appel (Trimestriel 1/4 - Exercice 2026): '
                f'Promoteur -> Buyer{i} ({amount:.2f} EUR)'
            ),
            'source_type': 'lot_mutation',
            'source_id': lid,
            'source_subtype': 'prorata',
            'lines': [
                {'account_number': tier_prov, 'account_name': 'Prov charges',
                 'debit': 0.0, 'credit': amount,
                 'third_party_id': owner_id,
                 'line_description': f'Mutation lot L{i+1:03d} - Prorata appel (Trimestriel 1/4 - Exercice 2026)'},
                {'account_number': '41010099', 'account_name': f'Buyer{i}',
                 'debit': amount, 'credit': 0.0},
            ],
        })

    client.close()
    return acp_id, owner_id


async def _teardown(acp_id, owner_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    await db.coproprietes.delete_many({'id': acp_id})
    await db.owners.delete_many({'id': owner_id})
    await db.lots.delete_many({'copropriete_id': acp_id})
    await db.journal_entries.delete_many({'copropriete_id': acp_id})
    client.close()


def test_matexi_scenario_aggregates_mutations_in_grouped_view():
    """5 lots + 1 VE + 5 mutations => vue groupee doit avoir 2 mouvements (VE + Mutations agregees)."""
    acp_id, owner_id = asyncio.run(_setup_matexi_like())
    try:
        s = _login()
        r = s.get(
            f'{BASE}/api/reports/balance-tiers/owners/{owner_id}',
            params={'copropriete_id': acp_id, 'group_by_owner': 'true'},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        movements = data['movements']
        # 2 mouvements : 1 VE + 1 Mutations agregees
        assert len(movements) == 2, f"Attendu 2 mouvements, obtenu {len(movements)}: {[m['description'] for m in movements]}"

        ve = [m for m in movements if m['journal_type'] == 'VE'][0]
        muts = [m for m in movements if m['journal_type'] == 'OD'][0]

        assert ve['debit'] == 1000.0
        assert muts['credit'] == round(50 + 60 + 70 + 80 + 90, 2)  # 350
        assert 'Mutations (5 lots)' in muts['description']
        assert 'Prorata appel' in muts['description']
        assert muts['reference'] == 'MUT-AGG (5)'

        # Balance finale : 1000 - 350 = 650
        assert data['total_debit'] == 1000.0
        assert data['total_credit'] == 350.0
        assert data['balance'] == 650.0

    finally:
        asyncio.run(_teardown(acp_id, owner_id))


def test_matexi_detailed_view_shows_all_5_mutations():
    """Le mode detail par lot (group_by_owner=false) doit toujours afficher les 5 mutations."""
    acp_id, owner_id = asyncio.run(_setup_matexi_like())
    try:
        s = _login()
        r = s.get(
            f'{BASE}/api/reports/balance-tiers/owners/{owner_id}',
            params={'copropriete_id': acp_id, 'group_by_owner': 'false'},
        )
        assert r.status_code == 200
        movements = r.json()['movements']
        # 1 VE + 5 mutations = 6 lignes
        assert len(movements) == 6, f"Detail attendu 6 lignes, obtenu {len(movements)}"
        muts = [m for m in movements if m['journal_type'] == 'OD']
        assert len(muts) == 5
        # Chaque mutation garde le numero de lot dans sa description
        for i, m in enumerate(sorted(muts, key=lambda x: x['reference'])):
            assert f'Mutation lot L{i+1:03d}' in m['description']
    finally:
        asyncio.run(_teardown(acp_id, owner_id))
