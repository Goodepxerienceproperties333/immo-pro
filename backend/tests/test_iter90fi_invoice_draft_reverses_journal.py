"""iter90fi - Suppression / remise en brouillon d'une facture => contre-passation.

Regle metier (PCMN belge) :
- Suppression d'une facture (DELETE) : les ecritures d'achat (AC) et les
  eventuelles ecritures de paiement (FI) liees a la facture DOIVENT etre
  contre-passees (jamais supprimees physiquement). La facture elle-meme
  est retiree du listing (comportement choisi par l'utilisateur).
- Remise en brouillon (PUT status="draft") : idem, contre-passation des
  ecritures generees. La facture reste dans le listing mais n'est plus
  comptabilisee. Les transactions bancaires rapprochees a cette facture
  sont derapprochees automatiquement.

Cette suite verifie le comportement bout-en-bout via l'API HTTP.
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
    assert r.status_code == 200, r.text
    return s


async def _setup_acp(db, acp_id: str):
    """Cree une ACP minimale avec un compte bancaire pour les tests."""
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': f'iter90fi {acp_id[:8]}', 'active': True,
        'bank_accounts': [{
            'iban': 'BE68539007547034',
            'label': 'Banque test',
            'pcmn_number': '550000',
        }],
    })
    # PCMN accounts minimum
    for num, name, cls in [
        ('550000', 'Banque test', 5),
        ('611000', 'Charges test', 6),
        ('440000', 'Fournisseur maitre', 4),
        ('499000', 'Encaissements en suspens', 4),
    ]:
        await db.pcmn_accounts.insert_one({
            'id': str(uuid.uuid4()),
            'copropriete_id': acp_id,
            'number': num, 'name': name, 'class_num': cls,
            'parent': num[:3], 'type': 'balance', 'active': True,
        })
    # Exercice fiscal ouvert englobant 2026
    await db.fiscal_years.insert_one({
        'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
        'name': '2026', 'start_date': '2026-01-01', 'end_date': '2026-12-31',
        'status': 'open',
    })


async def _cleanup_acp(db, acp_id: str):
    await db.coproprietes.delete_many({'id': acp_id})
    await db.pcmn_accounts.delete_many({'copropriete_id': acp_id})
    await db.fiscal_years.delete_many({'copropriete_id': acp_id})
    await db.journal_entries.delete_many({'copropriete_id': acp_id})
    await db.invoices.delete_many({'copropriete_id': acp_id})
    await db.suppliers.delete_many({'copropriete_id': acp_id})
    await db.bank_statements.delete_many({'copropriete_id': acp_id})
    await db.bank_transactions.delete_many({'copropriete_id': acp_id})


def _create_invoice(s, acp_id: str, number: str = None):
    payload = {
        'number': number or f'F-{uuid.uuid4().hex[:6].upper()}',
        'date': '2026-02-10',
        'supplier': 'ACME iter90fi',
        'description': 'Test iter90fi',
        'total_amount': 121.0,
        'vat_amount': 21.0,
        'account_number': '611000',
        'status': 'unpaid',
        'copropriete_id': acp_id,
    }
    r = s.post(f'{BASE}/api/invoices', json=payload,
               headers={'X-Copropriete-Id': acp_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_delete_invoice_reverses_purchase_entry_and_removes_invoice():
    """DELETE /api/invoices/{id} => contre-passe l'ecriture AC + supprime la facture."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90fi-{uuid.uuid4()}'
        await _setup_acp(db, acp_id)
        client.close()
        return acp_id

    async def _teardown(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id = asyncio.run(_setup())
    try:
        s = _login()
        inv = _create_invoice(s, acp_id)
        inv_id = inv['id']

        # Recupere l'ecriture AC generee
        async def _fetch_entries():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            entries = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'source_type': 'invoice', 'source_id': inv_id},
                {'_id': 0},
            ).to_list(100)
            reversals = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'is_reversal': True},
                {'_id': 0},
            ).to_list(100)
            client.close()
            return entries, reversals

        entries_before, revs_before = asyncio.run(_fetch_entries())
        assert len(entries_before) == 1, f"1 AC attendue, obtenue: {len(entries_before)}"
        assert entries_before[0].get('reversed') is not True
        assert len(revs_before) == 0

        # Supprime la facture
        r = s.delete(f'{BASE}/api/invoices/{inv_id}')
        assert r.status_code == 200, r.text

        # Facture supprimee du listing
        r_get = s.get(f'{BASE}/api/invoices/{inv_id}')
        assert r_get.status_code == 404

        # Verifie que l'ecriture AC est contre-passee (pas supprimee)
        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            orig = await db.journal_entries.find_one(
                {'id': entries_before[0]['id']}, {'_id': 0},
            )
            revs = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'is_reversal': True},
                {'_id': 0},
            ).to_list(100)
            client.close()
            return orig, revs

        orig, revs_after = asyncio.run(_check())
        assert orig is not None, "L'ecriture AC originale doit persister (audit trail)"
        assert orig['reversed'] is True
        assert len(revs_after) == 1, f"1 contre-passation attendue, obtenue: {len(revs_after)}"
        rev = revs_after[0]
        assert rev['reverses_entry_id'] == orig['id']
        assert rev['total_debit'] == orig['total_debit']
        assert rev['total_credit'] == orig['total_credit']
    finally:
        asyncio.run(_teardown(acp_id))


def test_put_invoice_to_draft_reverses_purchase_entry():
    """PUT /api/invoices/{id} avec status=draft => contre-passe AC, ne
    regenere pas d'ecriture. La facture reste dans le listing."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90fi-{uuid.uuid4()}'
        await _setup_acp(db, acp_id)
        client.close()
        return acp_id

    async def _teardown(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id = asyncio.run(_setup())
    try:
        s = _login()
        inv = _create_invoice(s, acp_id)
        inv_id = inv['id']

        async def _fetch_entries():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            entries = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'source_type': 'invoice', 'source_id': inv_id},
                {'_id': 0},
            ).to_list(100)
            revs = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'is_reversal': True},
                {'_id': 0},
            ).to_list(100)
            client.close()
            return entries, revs

        entries_before, revs_before = asyncio.run(_fetch_entries())
        assert len(entries_before) == 1
        ac_id = entries_before[0]['id']
        assert len(revs_before) == 0

        # PUT avec status=draft
        payload = {
            'number': inv['number'], 'date': inv['date'],
            'supplier': inv['supplier'], 'description': inv['description'],
            'total_amount': inv['total_amount'], 'vat_amount': inv.get('vat_amount', 0),
            'account_number': inv['account_number'],
            'status': 'draft', 'copropriete_id': acp_id,
        }
        r = s.put(f'{BASE}/api/invoices/{inv_id}', json=payload)
        assert r.status_code == 200, r.text
        assert r.json()['status'] == 'draft'

        # Verifie contre-passation
        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            orig = await db.journal_entries.find_one({'id': ac_id}, {'_id': 0})
            revs = await db.journal_entries.find(
                {'copropriete_id': acp_id, 'is_reversal': True},
                {'_id': 0},
            ).to_list(100)
            client.close()
            return orig, revs

        orig, revs_after = asyncio.run(_check())
        assert orig is not None
        assert orig['reversed'] is True, "AC originale doit etre marquee reversed"
        assert len(revs_after) == 1, f"1 contre-passation attendue, obtenue: {len(revs_after)}"

        # La facture est toujours accessible
        r_get = s.get(f'{BASE}/api/invoices/{inv_id}')
        assert r_get.status_code == 200
        assert r_get.json()['status'] == 'draft'
    finally:
        asyncio.run(_teardown(acp_id))


def test_put_invoice_from_draft_back_to_unpaid_regenerates_entry():
    """PUT draft -> unpaid : recree l'ecriture d'achat."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90fi-{uuid.uuid4()}'
        await _setup_acp(db, acp_id)
        client.close()
        return acp_id

    async def _teardown(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id = asyncio.run(_setup())
    try:
        s = _login()
        inv = _create_invoice(s, acp_id)
        inv_id = inv['id']

        # 1. Passe en draft (contre-passe)
        payload_draft = {
            'number': inv['number'], 'date': inv['date'],
            'supplier': inv['supplier'], 'description': inv['description'],
            'total_amount': inv['total_amount'], 'vat_amount': inv.get('vat_amount', 0),
            'account_number': inv['account_number'],
            'status': 'draft', 'copropriete_id': acp_id,
        }
        r1 = s.put(f'{BASE}/api/invoices/{inv_id}', json=payload_draft)
        assert r1.status_code == 200

        # 2. Repasse en unpaid (doit recreer une AC)
        payload_unpaid = {**payload_draft, 'status': 'unpaid'}
        r2 = s.put(f'{BASE}/api/invoices/{inv_id}', json=payload_unpaid)
        assert r2.status_code == 200

        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            # Devrait exister au moins 1 ecriture AC ACTIVE (ni reversed ni is_reversal)
            active = await db.journal_entries.find({
                'copropriete_id': acp_id,
                'source_type': 'invoice', 'source_id': inv_id,
                'reversed': {'$ne': True},
                'is_reversal': {'$ne': True},
            }, {'_id': 0}).to_list(100)
            client.close()
            return active

        active = asyncio.run(_check())
        assert len(active) >= 1, f"1 AC active attendue apres retour a unpaid, obtenue: {len(active)}"
    finally:
        asyncio.run(_teardown(acp_id))


def test_delete_invoice_unlinks_matched_bank_transaction():
    """DELETE d'une facture rapprochee a une transaction bancaire :
    - la transaction est deraprochee (matched=False, matched_to=""),
    - l'ecriture FI existante est contre-passee,
    - une nouvelle FI est generee en suspens (compte 499000)."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90fi-{uuid.uuid4()}'
        await _setup_acp(db, acp_id)
        # Extrait bancaire "posted"
        stmt_id = str(uuid.uuid4())
        await db.bank_statements.insert_one({
            'id': stmt_id, 'copropriete_id': acp_id,
            'account_number': 'BE68539007547034', 'status': 'posted',
            'date_from': '2026-02-01', 'date_to': '2026-02-28',
        })
        client.close()
        return acp_id, stmt_id

    async def _teardown(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id, stmt_id = asyncio.run(_setup())
    try:
        s = _login()
        inv = _create_invoice(s, acp_id)
        inv_id = inv['id']

        # Rapproche une transaction bancaire manuellement en DB
        async def _bootstrap_txn():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            txn_id = str(uuid.uuid4())
            await db.bank_transactions.insert_one({
                'id': txn_id, 'statement_id': stmt_id, 'copropriete_id': acp_id,
                'date': '2026-02-15', 'amount': -121.0,
                'transaction_type': 'debit',
                'counterparty_name': 'ACME iter90fi',
                'account_number': 'BE68539007547034',
                'matched': True, 'matched_to': inv_id, 'match_type': 'invoice',
            })
            # Genere l'ecriture FI liee
            from auto_entries import generate_bank_entry
            fresh = await db.bank_transactions.find_one({'id': txn_id}, {'_id': 0})
            await generate_bank_entry(db, fresh)
            client.close()
            return txn_id

        txn_id = asyncio.run(_bootstrap_txn())

        # Suppression de la facture
        r = s.delete(f'{BASE}/api/invoices/{inv_id}')
        assert r.status_code == 200, r.text

        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            txn = await db.bank_transactions.find_one({'id': txn_id}, {'_id': 0})
            # FI actives liees a cette txn (non reversed, non is_reversal)
            active_fi = await db.journal_entries.find({
                'copropriete_id': acp_id,
                'source_type': 'bank_txn', 'source_id': txn_id,
                'reversed': {'$ne': True},
                'is_reversal': {'$ne': True},
            }, {'_id': 0}).to_list(100)
            all_fi = await db.journal_entries.find({
                'copropriete_id': acp_id,
                'source_type': 'bank_txn', 'source_id': txn_id,
            }, {'_id': 0}).to_list(100)
            reversals = await db.journal_entries.count_documents({
                'copropriete_id': acp_id, 'is_reversal': True,
            })
            client.close()
            return txn, active_fi, all_fi, reversals

        txn, active_fi, all_fi, reversals = asyncio.run(_check())
        assert txn is not None
        assert txn.get('matched') is False, "Transaction doit etre deraprochee"
        assert (txn.get('matched_to') or '') == ''
        assert (txn.get('match_type') or '') == ''
        # Une FI active existe encore (en suspens 499000)
        assert len(active_fi) == 1, f"1 FI en suspens attendue, obtenue: {len(active_fi)}"
        # Elle utilise 499000 comme contrepartie
        acc_numbers = {ln['account_number'] for ln in active_fi[0]['lines']}
        assert '499000' in acc_numbers, f"Suspens 499000 attendu, comptes: {acc_numbers}"
        # Au moins une contre-passation FI + une AC
        assert reversals >= 2, f">=2 contre-passations attendues (AC + FI), obtenue: {reversals}"
    finally:
        asyncio.run(_teardown(acp_id))


def test_put_invoice_draft_to_draft_is_idempotent():
    """PUT vers draft alors qu'elle est deja draft : pas de nouvelle
    contre-passation, pas de nouvelle ecriture AC."""
    async def _setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        acp_id = f'iter90fi-{uuid.uuid4()}'
        await _setup_acp(db, acp_id)
        client.close()
        return acp_id

    async def _teardown(acp_id):
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await _cleanup_acp(db, acp_id)
        client.close()

    acp_id = asyncio.run(_setup())
    try:
        s = _login()
        inv = _create_invoice(s, acp_id)
        inv_id = inv['id']
        payload_draft = {
            'number': inv['number'], 'date': inv['date'],
            'supplier': inv['supplier'], 'description': inv['description'],
            'total_amount': inv['total_amount'], 'vat_amount': inv.get('vat_amount', 0),
            'account_number': inv['account_number'],
            'status': 'draft', 'copropriete_id': acp_id,
        }
        # 1er passage
        s.put(f'{BASE}/api/invoices/{inv_id}', json=payload_draft)
        # 2eme passage (idempotent)
        s.put(f'{BASE}/api/invoices/{inv_id}', json=payload_draft)

        async def _check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            revs = await db.journal_entries.count_documents({
                'copropriete_id': acp_id, 'is_reversal': True,
            })
            client.close()
            return revs

        revs = asyncio.run(_check())
        assert revs == 1, f"1 seule contre-passation attendue (idempotent), obtenue: {revs}"
    finally:
        asyncio.run(_teardown(acp_id))
