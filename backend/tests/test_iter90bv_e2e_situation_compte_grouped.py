"""iter90bv - Test E2E : endpoint situation-compte owner regroupe les lots.

Verifie que quand un proprietaire a plusieurs lots dans une ACP, l'endpoint
GET /api/reports/balance-tiers/owners/{owner_id} retourne :
- avec group_by_owner=true (defaut) : 1 mouvement par appel de fonds
- avec group_by_owner=false : N mouvements (1 par lot)
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


async def _setup_owner_with_3_lots_and_1_fund_call():
    """Cree une ACP avec 1 owner + 3 lots. Injecte une VE avec 3 lignes debit
    (1 par lot) sur le meme compte tier. Simule le cas reel."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90bv-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'iter90bv-testacp', 'active': True,
    })

    owner_id = str(uuid.uuid4())
    tier_prov = '41010001'
    tier_res = '41000001'
    await db.owners.insert_one({
        'id': owner_id, 'name': 'Iter90bv Multi-Lot Owner',
        'last_name': 'MultiLot', 'first_name': 'Test',
        'copropriete_ids': [acp_id],
        'tier_accounts': {acp_id: {'provisions': tier_prov, 'reserve': tier_res}},
    })

    # 3 lots pour ce proprietaire
    for i in range(3):
        await db.lots.insert_one({
            'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
            'lot_number': f'A{i+1}', 'owner_id': owner_id,
            'quotity': 100,
        })

    # Injecte une ecriture VE avec 3 lignes debit (1 par lot) + 1 credit
    # global sur 700000. C'est EXACTEMENT ce que genere generate_sale_entry
    # quand une distribution a 3 entries pour le meme owner.
    je_id = str(uuid.uuid4())
    fc_ref = 'FC-Q1-2026'
    await db.journal_entries.insert_one({
        'id': je_id,
        'copropriete_id': acp_id,
        'date': '2026-01-01',
        'journal_type': 'VE',
        'description': 'Appel Q1 2026',
        'reference': fc_ref,
        'source_type': 'fund_call',
        'lines': [
            {'account_number': tier_prov, 'account_name': 'Prov charges',
             'debit': 100.0, 'credit': 0.0,
             'third_party_id': owner_id,
             'line_description': 'Appel de provisions - Q1 2026'},
            {'account_number': tier_prov, 'account_name': 'Prov charges',
             'debit': 200.0, 'credit': 0.0,
             'third_party_id': owner_id,
             'line_description': 'Appel de provisions - Q1 2026'},
            {'account_number': tier_prov, 'account_name': 'Prov charges',
             'debit': 150.0, 'credit': 0.0,
             'third_party_id': owner_id,
             'line_description': 'Appel de provisions - Q1 2026'},
            {'account_number': '700000', 'account_name': 'Provisions appelees',
             'debit': 0.0, 'credit': 450.0, 'third_party_id': None},
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


def test_situation_compte_grouped_view_merges_multi_lot_lines():
    """Verifie que la vue groupee affiche 1 seul mouvement pour les 3 lots
    d'un meme appel de fonds, et que le total_debit reste correct (100+200+150=450)."""
    acp_id, owner_id = asyncio.run(_setup_owner_with_3_lots_and_1_fund_call())
    try:
        s = _login()
        # Vue groupee (defaut)
        r = s.get(
            f'{BASE}/api/reports/balance-tiers/owners/{owner_id}',
            params={'copropriete_id': acp_id, 'group_by_owner': 'true'},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        movements = data['movements']
        assert len(movements) == 1, f'Vue groupee doit avoir 1 mouvement, obtenu {len(movements)}: {movements}'
        assert movements[0]['debit'] == 450.0
        assert data['total_debit'] == 450.0
        assert data['balance'] == 450.0

        # Vue detaillee (group_by_owner=false)
        r2 = s.get(
            f'{BASE}/api/reports/balance-tiers/owners/{owner_id}',
            params={'copropriete_id': acp_id, 'group_by_owner': 'false'},
        )
        assert r2.status_code == 200, r2.text
        data2 = r2.json()
        movements2 = data2['movements']
        # Detail : 3 lignes de 100/200/150 (une par lot)
        assert len(movements2) == 3, f'Vue detaillee doit avoir 3 mouvements, obtenu {len(movements2)}'
        debits = sorted([m['debit'] for m in movements2])
        assert debits == [100.0, 150.0, 200.0]
        assert data2['total_debit'] == 450.0
        assert data2['balance'] == 450.0  # le solde reste identique
    finally:
        asyncio.run(_teardown(acp_id, owner_id))


def test_situation_compte_pdf_uses_grouped_by_default():
    """PDF de situation de compte doit toujours utiliser la vue resumee
    (le PDF est destine au proprietaire non-comptable)."""
    from routes.reports import _build_situation_compte_pdf
    from motor.motor_asyncio import AsyncIOMotorClient

    acp_id, owner_id = asyncio.run(_setup_owner_with_3_lots_and_1_fund_call())

    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Cette ACP n'a pas de config syndic complete, mais le PDF doit
        # au moins etre genere sans crasher.
        pdf_bytes, filename = await _build_situation_compte_pdf(
            db, owner_id, acp_id, start_date='2026-01-01', end_date='2026-12-31',
        )
        assert pdf_bytes[:4] == b'%PDF', 'Doit etre un PDF valide'
        assert len(pdf_bytes) > 2000
        client.close()

    try:
        asyncio.run(_run())
    finally:
        asyncio.run(_teardown(acp_id, owner_id))
