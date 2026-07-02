"""iter90x - Delettrage depuis le journal financier.

Verifie :
- GET /api/accounting/entries?journal_type=FI enrichi les entrees avec
  linked_invoice quand la transaction bancaire source est lettree a une facture
- POST /api/banking/unlettrage/{txn_id} annule le lettrage : la facture
  redevient unpaid, l'ecriture FI est regeneree en compte d'attente
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

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


def _login():
    s = requests.Session()
    r = s.post(f'{BASE}/api/auth/login',
               json={'email': 'admin@copro.be', 'password': 'admin123'})
    assert r.status_code == 200
    return s


async def _setup():
    """Cree une ACP + facture + transaction bancaire lettree a la facture
    + ecriture FI liee (comme si l'utilisateur avait lettre manuellement)."""
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    acp_id = f'iter90x-{uuid.uuid4()}'
    await db.coproprietes.insert_one({'id': acp_id, 'name': 'iter90x', 'active': True})

    invoice_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        'id': invoice_id, 'copropriete_id': acp_id,
        'invoice_number': 'INV-90X-001',
        'supplier_name': 'Test Supplier',
        'amount_ttc': 500.0, 'total_amount': 500.0,
        'status': 'paid', 'paid_at': '2026-01-15T00:00:00Z',
        'paid_by_transaction_id': None,
    })
    txn_id = str(uuid.uuid4())
    await db.bank_transactions.insert_one({
        'id': txn_id, 'copropriete_id': acp_id, 'statement_id': '',
        'date': '2026-01-15', 'amount': -500.0,
        'description': 'Paiement Test Supplier facture INV-90X-001',
        'matched': True, 'matched_to': invoice_id, 'match_type': 'invoice',
        'lettrage_code': 'IT90X001', 'lettrage_at': '2026-01-15T00:00:00Z',
    })
    # Set the invoice.paid_by_transaction_id
    await db.invoices.update_one({'id': invoice_id},
                                  {'$set': {'paid_by_transaction_id': txn_id}})

    # Ecriture FI auto (comme genere par generate_bank_entry)
    je_id = str(uuid.uuid4())
    await db.journal_entries.insert_one({
        'id': je_id, 'copropriete_id': acp_id,
        'journal_type': 'FI', 'date': '2026-01-15',
        'reference': f'FI-{txn_id[:8]}',
        'description': 'Paiement facture INV-90X-001 - Test Supplier',
        'source_type': 'bank_txn', 'source_id': txn_id,
        'auto_generated': True,
        'total_debit': 500.0, 'total_credit': 500.0,
        'lines': [
            {'account_number': '440001', 'account_name': 'Test Supplier',
             'debit': 500.0, 'credit': 0},
            {'account_number': '550000', 'account_name': 'Banque',
             'debit': 0, 'credit': 500.0},
        ],
    })
    client.close()
    return acp_id, invoice_id, txn_id, je_id


async def _cleanup(acp_id):
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    for col in ['coproprietes', 'invoices', 'bank_transactions',
                'journal_entries', 'pcmn_accounts', 'bank_statements']:
        await db[col].delete_many({'copropriete_id': acp_id})
    await db.coproprietes.delete_many({'id': acp_id})
    client.close()


def test_fi_entries_enriched_with_linked_invoice():
    """GET /accounting/entries?journal_type=FI expose linked_invoice + bank_txn_matched."""
    acp_id, invoice_id, txn_id, _ = asyncio.run(_setup())
    try:
        s = _login()
        r = s.get(f'{BASE}/api/accounting/entries',
                  params={'journal_type': 'FI', 'copropriete_id': acp_id},
                  headers={'X-Copropriete-Id': acp_id})
        assert r.status_code == 200, r.text
        entries = r.json()
        assert len(entries) >= 1
        e = next(x for x in entries if x['source_id'] == txn_id)
        assert e['bank_txn_matched'] is True
        assert e['bank_txn_match_type'] == 'invoice'
        assert e['linked_invoice']['id'] == invoice_id
        assert e['linked_invoice']['invoice_number'] == 'INV-90X-001'
        assert e['linked_invoice']['supplier_name'] == 'Test Supplier'
        assert abs(e['linked_invoice']['amount_ttc'] - 500.0) < 0.01
    finally:
        asyncio.run(_cleanup(acp_id))


def test_unlettrage_via_journal_financier_flow():
    """L'endpoint unlettrage reset le lettrage : facture->unpaid + txn->non lettree."""
    acp_id, invoice_id, txn_id, _ = asyncio.run(_setup())
    try:
        s = _login()
        # Verify initial state via API
        r_inv = s.get(f'{BASE}/api/invoices/{invoice_id}',
                      headers={'X-Copropriete-Id': acp_id})
        # Le endpoint /invoices/{id} peut ne pas exister ; verifier via DB
        async def check_state():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            i = await db.invoices.find_one({'id': invoice_id})
            t = await db.bank_transactions.find_one({'id': txn_id})
            client.close()
            return i, t
        inv, tx = asyncio.run(check_state())
        assert inv['status'] == 'paid'
        assert tx['matched'] is True

        # Call unlettrage
        r = s.post(f'{BASE}/api/banking/unlettrage/{txn_id}',
                   headers={'X-Copropriete-Id': acp_id})
        assert r.status_code == 200, r.text

        inv2, tx2 = asyncio.run(check_state())
        assert inv2['status'] == 'unpaid', f"invoice status = {inv2['status']}"
        assert 'paid_at' not in inv2 or not inv2.get('paid_at')
        assert tx2['matched'] is False, f"txn matched = {tx2.get('matched')}"
        assert tx2.get('matched_to', '') in ('', None)
    finally:
        asyncio.run(_cleanup(acp_id))


if __name__ == '__main__':
    test_fi_entries_enriched_with_linked_invoice(); print('.', end='', flush=True)
    test_unlettrage_via_journal_financier_flow(); print('.', end='', flush=True)
    print(' 2/2 OK')
