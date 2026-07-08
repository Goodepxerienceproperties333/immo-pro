"""iter90bw - Backup legal 10 ans enrichi (Art. III.86 CDE).

Verifie que le ZIP genere par build_acp_archive_zip contient toutes les donnees
requises pour la conservation legale belge 10 ans :
- suppliers.csv avec BCE/TVA/IBAN
- owners.csv enrichi (BCE, IBAN, tier_accounts)
- lots.csv enrichi (cadastre, acte notarie)
- invoices.csv enrichi (description, TVA, BCE fournisseur, IBAN, statut)
- fund_calls_distribution.csv (repartition par proprietaire/lot)
- bank_transactions.csv enrichi (rapprochement, communication)
- documents.csv + documents_generaux/ (AG, PV, contrats)
"""
import asyncio
import io
import os
import sys
import pathlib
import uuid
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')

from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


async def _setup_full_acp():
    """Cree une ACP avec toutes les entites requises pour tester le backup enrichi."""
    from backup_service import build_acp_archive_zip

    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]

    acp_id = f'iter90bw-{uuid.uuid4()}'
    await db.coproprietes.insert_one({
        'id': acp_id, 'name': 'Iter90bw Legal Archive Test',
        'reference': 'BE-LEGAL-001', 'active': True,
        'status': 'active',
    })

    # Fiscal year
    fy_id = str(uuid.uuid4())
    await db.fiscal_years.insert_one({
        'id': fy_id, 'copropriete_id': acp_id,
        'name': '2025', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
    })

    # 2 owners avec details complets
    owner1_id = str(uuid.uuid4())
    await db.owners.insert_one({
        'id': owner1_id, 'name': 'Jean Dupont', 'last_name': 'Dupont', 'first_name': 'Jean',
        'copropriete_ids': [acp_id], 'email': 'jean@dupont.be', 'phone': '+32475123456',
        'address': 'Rue Test 1', 'postal_code': '1000', 'city': 'Bruxelles', 'country': 'Belgique',
        'bce_number': 'BE0123456789', 'iban': 'BE68539007547034', 'bic': 'GKCCBEBB',
        'vcs_code': '+++123/4567/89012+++', 'vcs_digits': '123456789012',
        'tier_accounts': {acp_id: {'provisions': '41010001', 'reserve': '41000001'}},
    })

    owner2_id = str(uuid.uuid4())
    await db.owners.insert_one({
        'id': owner2_id, 'name': 'SPRL Immo Nord', 'last_name': 'SPRL Immo Nord',
        'copropriete_ids': [acp_id], 'is_company': True,
        'bce_number': 'BE0987654321', 'vat_number': 'BE0987654321',
        'tier_accounts': {acp_id: {'provisions': '41010002', 'reserve': '41000002'}},
    })

    # Lots
    lot1_id = str(uuid.uuid4())
    await db.lots.insert_one({
        'id': lot1_id, 'copropriete_id': acp_id, 'number': 'A1', 'lot_number': 'A1',
        'description': 'Appartement 1er etage', 'type': 'appartement', 'floor': '1',
        'address': 'Rue Test 10', 'postal_code': '1000', 'city': 'Bruxelles',
        'quotity': 400, 'owner_id': owner1_id,
        'cadastral_reference': 'CAD-A1-2025',
        'acte_notarie_date': '2020-06-15', 'acte_notarie_notaire': 'Me. Notaire',
    })

    # Supplier avec details complets
    sup1_id = str(uuid.uuid4())
    await db.suppliers.insert_one({
        'id': sup1_id, 'copropriete_id': acp_id,
        'name': 'Ascenseurs SA', 'auxiliary_code': 'F00001',
        'bce_number': 'BE0111222333', 'vat_number': 'BE0111222333',
        'address': 'Bd Industriel 5', 'postal_code': '1050', 'city': 'Bruxelles', 'country': 'Belgique',
        'phone': '+3221234567', 'email': 'contact@ascenseurs.be',
        'iban': 'BE10001122334455', 'bic': 'BBRUBEBB',
        'tier_accounts': {acp_id: {'main': '44000001'}},
        'default_account': '611000',
    })

    # Invoice avec details complets
    inv1_id = str(uuid.uuid4())
    await db.invoices.insert_one({
        'id': inv1_id, 'copropriete_id': acp_id,
        'number': 'INV-2025-001', 'invoice_number': 'INV-2025-001',
        'date': '2025-06-15', 'due_date': '2025-07-15',
        'supplier_id': sup1_id, 'supplier': 'Ascenseurs SA', 'supplier_name': 'Ascenseurs SA',
        'description': 'Entretien annuel ascenseur',
        'account_number': '611000', 'total_amount': 1210.0, 'vat_amount': 210.0,
        'expense_category_id': 'cat-entretien',
        'distribution_key_id': 'key-general',
        'status': 'paid', 'amount_paid': 1210.0,
        'is_private_fee': False,
    })

    # Fund call avec distribution
    fc1_id = str(uuid.uuid4())
    await db.fund_calls.insert_one({
        'id': fc1_id, 'copropriete_id': acp_id, 'budget_id': 'bg-2025',
        'name': 'Q1 2025', 'date': '2025-01-15', 'call_type': 'provisions',
        'total_amount': 3000.0, 'reserve_amount': 500.0, 'roulement_amount': 200.0,
        'distribution_key_id': 'key-general',
        'status': 'completed',
        'distribution': [
            {'lot_id': lot1_id, 'lot_number': 'A1', 'owner_id': owner1_id,
             'owner_name': 'Jean Dupont', 'vcs_code': '+++123/4567/89012+++',
             'share': 0.4, 'amount': 1200.0, 'paid': True, 'paid_date': '2025-01-30'},
            {'lot_id': str(uuid.uuid4()), 'lot_number': 'A2', 'owner_id': owner2_id,
             'owner_name': 'SPRL Immo Nord',
             'share': 0.6, 'amount': 1800.0, 'paid': False},
        ],
    })

    # Bank transaction
    tx1_id = str(uuid.uuid4())
    await db.bank_transactions.insert_one({
        'id': tx1_id, 'copropriete_id': acp_id,
        'date': '2025-01-30', 'value_date': '2025-01-30',
        'counterparty_name': 'Jean Dupont', 'counterparty_iban': 'BE68539007547034',
        'communication': '+++123/4567/89012+++',
        'description': 'Paiement Q1 2025',
        'amount': 1200.0, 'currency': 'EUR',
        'matched': True, 'match_type': 'owner_payment', 'matched_to': owner1_id,
        'statement_number': 'STMT-2025-001',
    })

    # Journal entry (VE)
    await db.journal_entries.insert_one({
        'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
        'date': '2025-01-15', 'journal_type': 'VE',
        'reference': 'FC-Q1-2025', 'description': 'Appel Q1 2025',
        'lines': [
            {'account_number': '41010001', 'debit': 1200.0, 'credit': 0.0,
             'third_party_id': owner1_id, 'line_description': 'Appel provisions Q1'},
            {'account_number': '700000', 'debit': 0.0, 'credit': 1200.0},
        ],
    })

    # Document type "AG" - metadata only (pas de fichier reel)
    await db.documents.insert_one({
        'id': str(uuid.uuid4()), 'copropriete_id': acp_id,
        'category': 'ag_minutes', 'title': 'PV AG 2025-03-15',
        'description': 'Assemblee generale ordinaire',
        'date': '2025-03-15', 'uploaded_by': 'syndic',
        'filename': 'pv_ag_2025.pdf', 'mime_type': 'application/pdf',
        'created_at': '2025-03-20T10:00:00Z',
        # Pas de file_id - simule un doc avec meta seulement (le CSV doit tout de meme
        # lister l'entree)
    })

    # Genere le ZIP
    data, filename = await build_acp_archive_zip(db, acp_id, include_pdfs=True)

    # Cleanup
    await db.coproprietes.delete_many({'id': acp_id})
    await db.owners.delete_many({'id': {'$in': [owner1_id, owner2_id]}})
    await db.lots.delete_many({'copropriete_id': acp_id})
    await db.suppliers.delete_many({'copropriete_id': acp_id})
    await db.invoices.delete_many({'copropriete_id': acp_id})
    await db.fund_calls.delete_many({'copropriete_id': acp_id})
    await db.bank_transactions.delete_many({'copropriete_id': acp_id})
    await db.journal_entries.delete_many({'copropriete_id': acp_id})
    await db.fiscal_years.delete_many({'copropriete_id': acp_id})
    await db.documents.delete_many({'copropriete_id': acp_id})
    client.close()

    return data, filename


def _read_csv(zf, path):
    """Lit un CSV UTF-8-BOM du ZIP et retourne (header, rows)."""
    raw = zf.read(path)
    # Strip BOM
    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]
    text = raw.decode('utf-8')
    import csv as csvmod
    rows = list(csvmod.reader(io.StringIO(text), delimiter=';'))
    return rows[0], rows[1:]


def test_archive_zip_contains_all_required_files_iter90bw():
    """Verifie la presence de tous les fichiers requis pour l'archive legale."""
    data, filename = asyncio.run(_setup_full_acp())
    assert filename.startswith('archive_')
    assert filename.endswith('.zip')

    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    acp = 'Iter90bw_Legal_Archive_Test'

    # Fichiers a la racine
    assert f'{acp}/README.txt' in names
    assert f'{acp}/metadata.json' in names
    assert f'{acp}/owners.csv' in names
    assert f'{acp}/lots.csv' in names
    assert f'{acp}/suppliers.csv' in names, 'suppliers.csv obligatoire iter90bw'
    assert f'{acp}/documents.csv' in names, 'documents.csv obligatoire iter90bw'

    # Fichiers par exercice
    assert f'{acp}/2025/journal_entries.csv' in names
    assert f'{acp}/2025/invoices.csv' in names
    assert f'{acp}/2025/fund_calls.csv' in names
    assert f'{acp}/2025/fund_calls_distribution.csv' in names, 'iter90bw'
    assert f'{acp}/2025/bank_transactions.csv' in names


def test_archive_owners_csv_has_bce_iban_tier_accounts():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/owners.csv')
    assert 'bce_or_vat_number' in header
    assert 'iban' in header
    assert 'compte_provisions' in header
    assert 'compte_reserve' in header
    # 2 owners
    assert len(rows) == 2
    # Verifier que BCE 'BE0123456789' est bien dedans
    all_text = ' '.join(' '.join(r) for r in rows)
    assert 'BE0123456789' in all_text
    assert 'BE68539007547034' in all_text
    assert '41010001' in all_text
    assert '41000001' in all_text


def test_archive_suppliers_csv_has_full_details():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/suppliers.csv')
    for req in ('bce_number', 'vat_number', 'iban', 'bic', 'compte_tier', 'email', 'phone'):
        assert req in header, f'{req} manquant dans suppliers.csv'
    assert len(rows) == 1
    row = dict(zip(header, rows[0]))
    assert row['bce_number'] == 'BE0111222333'
    assert row['iban'] == 'BE10001122334455'
    assert row['compte_tier'] == '44000001'
    assert row['email'] == 'contact@ascenseurs.be'


def test_archive_lots_csv_has_cadastre_and_acte():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/lots.csv')
    for req in ('cadastral_reference', 'acte_notarie_date', 'acte_notarie_notaire', 'address'):
        assert req in header
    row = dict(zip(header, rows[0]))
    assert row['cadastral_reference'] == 'CAD-A1-2025'
    assert row['acte_notarie_date'] == '2020-06-15'


def test_archive_invoices_csv_denormalizes_supplier_bce_iban():
    """Fondamental legal : meme si le fournisseur change de BCE/IBAN dans 5 ans,
    l'archive conserve la valeur au moment de la facture."""
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/2025/invoices.csv')
    for req in ('supplier_bce', 'supplier_vat', 'supplier_iban', 'description',
                'vat_amount', 'due_date', 'expense_category_id'):
        assert req in header, f'{req} manquant dans invoices.csv'
    assert len(rows) == 1
    row = dict(zip(header, rows[0]))
    assert row['supplier_bce'] == 'BE0111222333'
    assert row['supplier_iban'] == 'BE10001122334455'
    assert row['description'] == 'Entretien annuel ascenseur'
    assert row['due_date'] == '2025-07-15'


def test_archive_fund_calls_distribution_per_owner():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/2025/fund_calls_distribution.csv')
    for req in ('owner_id', 'owner_name', 'lot_number', 'share', 'amount', 'paid'):
        assert req in header
    assert len(rows) == 2  # 2 owners dans la distribution
    all_text = ' '.join(' '.join(r) for r in rows)
    assert 'Jean Dupont' in all_text
    assert 'SPRL Immo Nord' in all_text
    assert '1200.0' in all_text
    assert '1800.0' in all_text


def test_archive_bank_transactions_csv_has_matching():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/2025/bank_transactions.csv')
    for req in ('matched', 'match_type', 'matched_to', 'communication',
                'counterparty_iban', 'statement_number'):
        assert req in header
    row = dict(zip(header, rows[0]))
    assert row['communication'] == '+++123/4567/89012+++'
    assert row['counterparty_iban'] == 'BE68539007547034'
    assert row['statement_number'] == 'STMT-2025-001'
    assert row['match_type'] == 'owner_payment'


def test_archive_documents_csv_present():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    header, rows = _read_csv(zf, 'Iter90bw_Legal_Archive_Test/documents.csv')
    for req in ('category', 'title', 'description', 'date', 'filename', 'mime_type'):
        assert req in header
    assert len(rows) == 1
    row = dict(zip(header, rows[0]))
    assert row['category'] == 'ag_minutes'
    assert row['title'] == 'PV AG 2025-03-15'
    assert row['date'] == '2025-03-15'


def test_archive_readme_mentions_legal_retention():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    readme = zf.read('Iter90bw_Legal_Archive_Test/README.txt').decode('utf-8')
    assert 'suppliers.csv' in readme
    assert 'documents.csv' in readme
    assert 'fund_calls_distribution.csv' in readme
    assert '10 ans' in readme
    assert 'III.86 CDE' in readme
    assert 'PCMN' in readme


def test_archive_metadata_json_format_version():
    data, _ = asyncio.run(_setup_full_acp())
    zf = zipfile.ZipFile(io.BytesIO(data))
    import json as jsonmod
    meta = jsonmod.loads(zf.read('Iter90bw_Legal_Archive_Test/metadata.json'))
    assert meta['format_version'] == '1.2'
    assert meta['name'] == 'Iter90bw Legal Archive Test'
    assert meta['reference'] == 'BE-LEGAL-001'
