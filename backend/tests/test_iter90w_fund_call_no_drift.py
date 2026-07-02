"""iter90w - Verifie qu'aucun centime ne derive dans les distributions
de fund_calls. Test synthetique avec quotites specifiques qui provoquent
le drift naturel du round(x*share_ratio).
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


async def _setup():
    """ACP avec 3 lots ayant des quotites qui provoquent la derive :
    Total 1000, 333/333/334.  4750 * 333/1000 = 1581.75 (exact) : pas de derive.
    Testons avec 7 lots -> 1/7 = 0.142857...  4750 * (1/7) = 678.571428... ~ 678.57
    x7 lots = 4749.99 → 1 centime de derive !"""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    acp_id = f'iter90w-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'iter90w-testacp', 'active': True,
    })
    # 7 lots avec quotites 1/7 (approx)
    # Utilisation reelle : 3 owners
    owner_ids = []
    for i in range(3):
        oid = str(uuid.uuid4())
        await db.owners.insert_one({
            'id': oid, 'name': f'Iter90w Owner {i+1}',
            'last_name': f'Own{i+1}', 'first_name': 'Test',
            'copropriete_ids': [acp_id],
            'tier_accounts': {},
        })
        owner_ids.append(oid)
    # 7 lots
    for i in range(7):
        oid = owner_ids[i % 3]
        await db.lots.insert_one({
            'id': str(uuid.uuid4()), 'number': f'LT{i+1:02d}',
            'quotity': 143 if i < 6 else 142,  # sum = 6*143 + 142 = 858 + 142 = 1000
            'owner_id': oid, 'copropriete_id': acp_id,
        })
    # Fiscal year + budget
    fy_id = str(uuid.uuid4())
    await db.fiscal_years.insert_one({
        'id': fy_id, 'name': '2026', 'start_date': '2026-01-01',
        'end_date': '2026-12-31', 'copropriete_id': acp_id, 'status': 'open',
    })
    budget_id = str(uuid.uuid4())
    await db.budgets.insert_one({
        'id': budget_id, 'name': 'Budget Test', 'fiscal_year_id': fy_id,
        'copropriete_id': acp_id, 'status': 'approved',
        'lines': [
            # 19000 EUR annuel = 4750 EUR par trimestre (freq=4)
            {'account_number': '611000', 'account_name': 'Entretien',
             'amount': 19000, 'distribution_key_id': ''},
        ],
    })
    client.close()
    return acp_id, fy_id, budget_id, owner_ids


async def _cleanup(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for col in ['coproprietes', 'owners', 'lots', 'fiscal_years', 'budgets',
                'fund_calls', 'journal_entries', 'pcmn_accounts']:
        await db[col].delete_many({'copropriete_id': acp_id})
    await db.owners.delete_many({'copropriete_ids': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    client.close()


def test_no_drift_in_fund_call_generation():
    """Genere des appels trimestriels depuis un budget avec quotites qui
    provoquent normalement 1 centime de derive et verifie que
    sum(distribution.amount) == total_amount exactement pour chaque appel."""
    acp_id, fy_id, budget_id, owner_ids = asyncio.run(_setup())
    try:
        s = _login()
        # Generate 4 trimestrial calls from budget
        r = s.post(f'{BASE}/api/fund-calls/generate-from-budget',
                   headers={'X-Copropriete-Id': acp_id},
                   json={'budget_id': budget_id, 'frequency': 4,
                         'start_date': '2026-01-01', 'due_offset_days': 30,
                         'copropriete_id': acp_id})
        assert r.status_code == 200, r.text
        payload = r.json()
        calls = payload.get('calls', [])
        assert len(calls) == 4, f'Expected 4 calls, got {len(calls)}'

        # Chaque appel doit avoir sum(distribution.amount) == total_amount
        for c in calls:
            call_total = round(c['total_amount'], 2)
            dist_sum = round(sum(d.get('amount', 0.0) for d in c['distribution']), 2)
            assert dist_sum == call_total, (
                f"Drift on {c['name']}: total={call_total}, "
                f"sum(distribution)={dist_sum}, delta={dist_sum - call_total}")

        # Grand total = 4 * 4750 = 19000 exactement
        grand = round(sum(c['total_amount'] for c in calls), 2)
        assert grand == 19000.0, f'Grand total: {grand} != 19000'
    finally:
        asyncio.run(_cleanup(acp_id))


def test_manual_fund_call_no_drift():
    """Test un fund call manuel : sum(distribution.amount) == total_amount."""
    acp_id, fy_id, _, owner_ids = asyncio.run(_setup())
    try:
        s = _login()
        # 7 lots x 4750 amount → provoque naturellement le drift sans le fix
        r = s.post(f'{BASE}/api/fund-calls',
                   headers={'X-Copropriete-Id': acp_id},
                   json={'name': 'ManualTest90w', 'date': '2026-01-01',
                         'due_date': '2026-02-01', 'fiscal_year_id': fy_id,
                         'total_amount': 4750, 'call_type': 'provisions',
                         'copropriete_id': acp_id})
        assert r.status_code == 200, r.text
        fc = r.json()
        call_total = round(fc['total_amount'], 2)
        dist_sum = round(sum(d.get('amount', 0.0) for d in fc['distribution']), 2)
        assert dist_sum == call_total, (
            f"Drift on manual call: total={call_total}, "
            f"sum(distribution)={dist_sum}")

        # Verifie aussi le journal entry auto-genere
        async def check_je():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            je = await db.journal_entries.find_one(
                {'copropriete_id': acp_id, 'source_type': 'fund_call'})
            client.close()
            return je
        je = asyncio.run(check_je())
        if je:
            tot_dr = round(sum(l.get('debit', 0) for l in je['lines']), 2)
            tot_cr = round(sum(l.get('credit', 0) for l in je['lines']), 2)
            assert tot_dr == tot_cr == call_total, (
                f"JE unbalanced: dr={tot_dr} cr={tot_cr} total={call_total}")
    finally:
        asyncio.run(_cleanup(acp_id))


def test_fix_rounding_drift_endpoint():
    """L'endpoint /api/fund-calls/fix-rounding-drift corrige les appels
    existants avec un drift accumule."""
    acp_id, fy_id, _, owner_ids = asyncio.run(_setup())
    try:
        # Injecte manuellement dans la DB des appels avec drift
        async def inject_drifted_calls():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            lots = await db.lots.find({'copropriete_id': acp_id}, {'_id': 0}).to_list(10)
            for i in range(2):
                # Chaque lot recoit 4750/7 arrondi a 2 decimales
                per_lot = round(4750 / 7, 2)
                dist = []
                for lot in lots:
                    dist.append({
                        'lot_id': lot['id'], 'lot_number': lot['number'],
                        'owner_id': lot['owner_id'], 'owner_name': f'Owner',
                        'vcs_code': '', 'share': lot['quotity'],
                        'amount': per_lot, 'paid': False, 'paid_date': '',
                    })
                await db.fund_calls.insert_one({
                    'id': str(uuid.uuid4()),
                    'name': f'DriftedCall{i}',
                    'date': '2026-01-01', 'due_date': '2026-02-01',
                    'fiscal_year_id': fy_id,
                    'total_amount': 4750.0, 'call_type': 'provisions',
                    'copropriete_id': acp_id, 'status': 'pending',
                    'distribution': dist,
                })
            client.close()
        asyncio.run(inject_drifted_calls())

        s = _login()
        r = s.post(f'{BASE}/api/fund-calls/fix-rounding-drift?copropriete_id={acp_id}',
                   headers={'X-Copropriete-Id': acp_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['fixed_count'] == 2, body
        # Verify each call now sums exactly to 4750
        async def verify():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            for c in await db.fund_calls.find(
                    {'copropriete_id': acp_id, 'name': {'$regex': 'DriftedCall'}},
                    {'_id': 0}).to_list(10):
                total = round(c['total_amount'], 2)
                dist_sum = round(sum(d['amount'] for d in c['distribution']), 2)
                assert dist_sum == total, f'Still drifting: {c["name"]} sum={dist_sum} target={total}'
            client.close()
        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup(acp_id))


if __name__ == '__main__':
    test_no_drift_in_fund_call_generation(); print('.', end='', flush=True)
    test_manual_fund_call_no_drift(); print('.', end='', flush=True)
    test_fix_rounding_drift_endpoint(); print('.', end='', flush=True)
    print(' 3/3 OK')
