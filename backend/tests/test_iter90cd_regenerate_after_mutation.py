"""iter90cd - Bug regeneration appels apres mutation (double-comptage prorata).

Regle metier belge :
- Un appel de fonds est integralement impute au proprietaire a sa date d'EMISSION.
- La repartition prorata temporis entre vendeur/acquereur est realisee SEPAREMENT
  par l'OD "Mutation Prorata" (properties.py).
- Aucune ventilation entre vendeur et acquereur au niveau de l'appel lui-meme.

Bug avant iter90cd :
- Appel Q4 emis 01/10 -> 100% Matexi (correct)
- Mutation 15/11 -> OD Prorata: credit Matexi X, debit Buyer X (correct)
- Suppression + regeneration Q4 -> _split_lot_entry_by_mutations splittait
  l'appel entre Matexi et Buyer (INCORRECT). Mais l'OD Prorata reste (elle est
  manually_edited=True, exclue de _delete_auto_entries). -> DOUBLE-COMPTAGE.

Fix iter90cd :
- _distribute_amount pour les budget lines utilise desormais
  _rebind_owner_at_call_date (au lieu de _split_lot_entry_by_mutations).
- Result : appel entierement impute au proprietaire a la date d'emission.
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


async def _setup_matexi_mutation():
    """Cree une ACP avec :
    - Matexi (vendeur) initial owner
    - Un lot avec quotity 100
    - Une mutation Matexi -> Buyer le 15/11/2025
    - Un budget avec 4 appels trimestriels (Q1..Q4 2025) : Q4 emis 01/10 AVANT mutation.
    """
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90cd-{uuid.uuid4()}'
    await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90cd-Acacia', 'active': True})

    matexi_id = str(uuid.uuid4())
    buyer_id = str(uuid.uuid4())
    for oid, name in [(matexi_id, 'Matexi'), (buyer_id, 'Buyer')]:
        await db.owners.insert_one({
            'id': oid, 'name': name, 'last_name': name,
            'copropriete_ids': [acp_id],
            'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
        })

    lot_id = str(uuid.uuid4())
    # Etat ACTUEL du lot = Buyer (mutation deja effectuee)
    await db.lots.insert_one({
        'id': lot_id, 'copropriete_id': acp_id,
        'number': 'A1', 'lot_number': 'A1',
        'owner_id': buyer_id, 'quotity': 100,
    })

    # Mutation historique : Matexi -> Buyer le 15/11/2025
    await db.mutations.insert_one({
        'id': str(uuid.uuid4()), 'copropriete_id': acp_id, 'lot_id': lot_id,
        'from_owner_id': matexi_id, 'to_owner_id': buyer_id,
        'sale_date': '2025-11-15',
    })

    key_id = str(uuid.uuid4())
    await db.distribution_keys.insert_one({
        'id': key_id, 'copropriete_id': acp_id, 'name': 'Charges',
        'is_default': True,
        'lots': [{'lot_id': lot_id, 'share': 100}],
    })

    fy_id = str(uuid.uuid4())
    await db.fiscal_years.insert_one({
        'id': fy_id, 'copropriete_id': acp_id, 'name': '2025',
        'start_date': '2025-01-01', 'end_date': '2025-12-31',
    })

    budget_id = str(uuid.uuid4())
    await db.budgets.insert_one({
        'id': budget_id, 'copropriete_id': acp_id, 'fiscal_year_id': fy_id,
        'status': 'approved',
        'lines': [{
            'account_number': '611000', 'account_name': 'Charges',
            'amount': 1600.0, 'distribution_key_id': key_id,  # 400 EUR/trimestre
        }],
    })

    client.close()
    return acp_id, matexi_id, buyer_id, budget_id, key_id


async def _cleanup(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for coll in ('coproprietes', 'owners', 'lots', 'distribution_keys',
                 'budgets', 'fund_calls', 'fiscal_years', 'mutations',
                 'journal_entries'):
        await db[coll].delete_many({'copropriete_id': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    client.close()


def test_iter90cd_regenerate_appel_before_mutation_stays_on_seller():
    """Appel Q4 (emission 01/10, avant mutation 15/11) apres regeneration : 100% Matexi.
    Aucun split entre Matexi/Buyer. Le prorata temporis est gere par l'OD mutation."""
    acp_id, matexi_id, buyer_id, budget_id, key_id = asyncio.run(_setup_matexi_mutation())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preview-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2025-01-01',
            'frequency': 4,
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        calls = r.json()['calls']
        assert len(calls) == 4

        # Q1 emis 01-01 (avant mutation) -> 100% Matexi
        q1 = calls[0]
        assert q1['date'] == '2025-01-01'
        by_owner_q1 = {e['owner_id']: e for e in q1['distribution']}
        assert matexi_id in by_owner_q1, f"Matexi doit avoir 100% Q1 : {q1['distribution']}"
        assert buyer_id not in by_owner_q1, (
            f"Buyer NE DOIT PAS apparaitre en Q1 avant mutation : {q1['distribution']}"
        )
        assert by_owner_q1[matexi_id]['amount'] == 400.0

        # Q2 emis 01-04 (avant mutation) -> 100% Matexi
        q2 = calls[1]
        by_owner_q2 = {e['owner_id']: e for e in q2['distribution']}
        assert matexi_id in by_owner_q2
        assert buyer_id not in by_owner_q2
        assert by_owner_q2[matexi_id]['amount'] == 400.0

        # Q3 emis 01-07 (avant mutation) -> 100% Matexi
        q3 = calls[2]
        by_owner_q3 = {e['owner_id']: e for e in q3['distribution']}
        assert matexi_id in by_owner_q3
        assert buyer_id not in by_owner_q3

        # Q4 emis 01-10 (mutation 15/11 -> dans Q4) -> 100% Matexi (regle iter90cd)
        q4 = calls[3]
        assert q4['date'] == '2025-10-01'
        by_owner_q4 = {e['owner_id']: e for e in q4['distribution']}
        assert matexi_id in by_owner_q4, (
            f"Matexi doit avoir 100% Q4 (emis avant mutation) : {q4['distribution']}"
        )
        assert buyer_id not in by_owner_q4, (
            f"Buyer NE DOIT PAS apparaitre au niveau de l'appel Q4 (iter90cd) : "
            f"{q4['distribution']}"
        )
        assert by_owner_q4[matexi_id]['amount'] == 400.0

    finally:
        asyncio.run(_cleanup(acp_id))


def test_iter90cd_appel_after_mutation_goes_to_buyer():
    """Appel Q4/2026 (emission 01/10/2026, apres mutation 15/11/2025) : 100% Buyer."""
    acp_id, matexi_id, buyer_id, budget_id, key_id = asyncio.run(_setup_matexi_mutation())

    # Update fiscal_year + budget to 2026
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    async def _update():
        await db.fiscal_years.update_one(
            {'copropriete_id': acp_id},
            {'$set': {'name': '2026', 'start_date': '2026-01-01', 'end_date': '2026-12-31'}},
        )

    asyncio.run(_update())
    client.close()

    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preview-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        calls = r.json()['calls']

        # Tous les appels 2026 (apres mutation 15/11/2025) doivent aller au Buyer
        for c in calls:
            by_owner = {e['owner_id']: e for e in c['distribution']}
            assert buyer_id in by_owner, f"Buyer doit avoir l'appel : {c}"
            assert matexi_id not in by_owner, (
                f"Matexi ne doit plus apparaitre en 2026 : {c}"
            )

    finally:
        asyncio.run(_cleanup(acp_id))


def test_iter90cd_no_mutation_uses_current_owner():
    """Regression : sans mutation, le comportement standard s'applique (owner actuel)."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    acp_id = f'iter90cd-nomut-{uuid.uuid4()}'

    async def _setup():
        await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90cd-nomut', 'active': True})
        oid = str(uuid.uuid4())
        await db.owners.insert_one({
            'id': oid, 'name': 'Owner', 'copropriete_ids': [acp_id],
            'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
        })
        lid = str(uuid.uuid4())
        await db.lots.insert_one({
            'id': lid, 'copropriete_id': acp_id, 'number': 'A1',
            'owner_id': oid, 'quotity': 100,
        })
        key_id = str(uuid.uuid4())
        await db.distribution_keys.insert_one({
            'id': key_id, 'copropriete_id': acp_id, 'name': 'Charges',
            'is_default': True, 'lots': [{'lot_id': lid, 'share': 100}],
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
            'lines': [{'account_number': '611000', 'account_name': 'Charges',
                       'amount': 1600.0, 'distribution_key_id': key_id}],
        })
        return oid, budget_id

    async def _cleanup2():
        for coll in ('coproprietes', 'owners', 'lots', 'distribution_keys',
                     'budgets', 'fund_calls', 'fiscal_years', 'mutations',
                     'journal_entries'):
            await db[coll].delete_many({'copropriete_id': acp_id})
        await db.coproprietes.delete_many({'id': acp_id})

    oid, budget_id = asyncio.run(_setup())
    try:
        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/preview-from-budget', json={
            'budget_id': budget_id,
            'copropriete_id': acp_id,
            'start_date': '2026-01-01',
            'frequency': 4,
            'due_offset_days': 30,
        })
        assert r.status_code == 200, r.text
        for c in r.json()['calls']:
            by_owner = {e['owner_id']: e for e in c['distribution']}
            assert oid in by_owner
            assert by_owner[oid]['amount'] == 400.0
    finally:
        asyncio.run(_cleanup2())
        client.close()
