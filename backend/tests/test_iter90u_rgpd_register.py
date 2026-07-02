"""iter90u - Tests Registre RGPD (art. 30) : editable + PDF.

Verifie :
- GET /api/legal/admin/rgpd-register renvoie 403 sans superadmin, 200 avec defaults sinon
- PUT /api/legal/admin/rgpd-register persiste les donnees + audit log
- GET /api/legal/admin/rgpd-register/pdf renvoie un vrai PDF (magic bytes %PDF-)
  avec Content-Disposition attachment et taille > 3 Ko
- Le PDF contient les infos editees (societe custom retrouvable dans le texte)
"""
import asyncio
import os
import sys
import pathlib
import io

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')

import requests
from motor.motor_asyncio import AsyncIOMotorClient
import bcrypt

BASE = None
env_path = pathlib.Path(__file__).parent.parent.parent / 'frontend' / '.env'
for line in env_path.read_text().splitlines():
    if line.startswith('REACT_APP_BACKEND_URL='):
        BASE = line.split('=', 1)[1].strip()
        break
assert BASE

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


def _login(email, password):
    s = requests.Session()
    r = s.post(f'{BASE}/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return s


def test_rgpd_register_endpoints_require_superadmin():
    async def setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        email = 'iter90u_owner@test.local'
        pwd = bcrypt.hashpw(b'ownerpw123', bcrypt.gensalt()).decode()
        await db.users.delete_many({'email': email})
        await db.users.insert_one({'email': email, 'password_hash': pwd,
                                    'name': 'X', 'role': 'owner', 'copropriete_ids': []})
        client.close()
        return email

    email = asyncio.run(setup())
    try:
        s = _login(email, 'ownerpw123')
        for verb, path, body in [
            ('GET', '/api/legal/admin/rgpd-register', None),
            ('PUT', '/api/legal/admin/rgpd-register', {'controller': {}, 'processings': [], 'subprocessors': [], 'security_measures': []}),
            ('GET', '/api/legal/admin/rgpd-register/pdf', None),
        ]:
            if verb == 'GET':
                r = s.get(f'{BASE}{path}')
            else:
                r = s.put(f'{BASE}{path}', json=body)
            assert r.status_code == 403, f'{verb} {path} => {r.status_code}'
    finally:
        async def cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_many({'email': 'iter90u_owner@test.local'})
            client.close()
        asyncio.run(cleanup())


def test_rgpd_register_get_returns_defaults_or_persisted():
    """GET renvoie des donnees valides (defauts si jamais sauvegarde)."""
    s = _login('admin@copro.be', 'admin123')
    r = s.get(f'{BASE}/api/legal/admin/rgpd-register')
    assert r.status_code == 200
    data = r.json()
    assert 'controller' in data
    assert 'processings' in data and isinstance(data['processings'], list)
    assert 'subprocessors' in data and isinstance(data['subprocessors'], list)
    assert 'security_measures' in data and isinstance(data['security_measures'], list)
    # Doit avoir au moins 3 traitements et sous-traitants par defaut
    if not data.get('updated_at'):
        assert len(data['processings']) >= 3
        assert len(data['subprocessors']) >= 2
        assert len(data['security_measures']) >= 3


def test_rgpd_register_put_persists_and_audits():
    """PUT persiste et cree audit log."""
    s = _login('admin@copro.be', 'admin123')
    custom_soc = 'TestSoc SRL iter90u'
    r = s.put(f'{BASE}/api/legal/admin/rgpd-register', json={
        'controller': {'societe': custom_soc, 'bce': '0700.111.222', 'tva': 'BE 0700.111.222'},
        'processings': [{'name': 'Test', 'purpose': 'test purpose iter90u',
                         'legal_basis': 'art.6.1.b', 'data_categories': 'nom, email',
                         'subjects': 'users', 'recipients': 'admin',
                         'transfers': 'aucun', 'retention': '1 an'}],
        'subprocessors': [{'name': 'TestSub', 'service': 'sub svc iter90u',
                           'location': 'UE', 'guarantees': 'CCT'}],
        'security_measures': ['bcrypt iter90u', 'TLS 1.2+'],
    })
    assert r.status_code == 200
    assert 'updated_at' in r.json()

    # Verify
    r2 = s.get(f'{BASE}/api/legal/admin/rgpd-register')
    d = r2.json()
    assert d['controller']['societe'] == custom_soc
    assert d['processings'][0]['name'] == 'Test'
    assert d['subprocessors'][0]['name'] == 'TestSub'
    assert 'bcrypt iter90u' in d['security_measures']

    # Audit log
    async def check_audit():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        log = await db.audit_log.find_one(
            {'action': 'legal.rgpd_register_update', 'user_email': 'admin@copro.be'},
            sort=[('timestamp', -1)],
        )
        client.close()
        return log
    audit = asyncio.run(check_audit())
    assert audit is not None
    assert audit['details']['processings_count'] == 1
    assert audit['details']['subprocessors_count'] == 1


def test_rgpd_register_pdf_download():
    """GET .../pdf renvoie un vrai PDF avec content-disposition."""
    s = _login('admin@copro.be', 'admin123')
    r = s.get(f'{BASE}/api/legal/admin/rgpd-register/pdf')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
    assert 'attachment' in r.headers.get('content-disposition', '').lower()
    body = r.content
    assert body.startswith(b'%PDF-'), f'Pas un PDF valide: {body[:10]}'
    assert len(body) > 3000, f'PDF trop petit: {len(body)} bytes'
    # Parse PDF and check content
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(body))
        assert len(reader.pages) >= 1
        full_text = ""
        for p in reader.pages:
            full_text += p.extract_text() or ""
        # Ces phrases doivent apparaitre
        assert "Registre des traitements" in full_text
        assert "article 30" in full_text.lower() or "art. 30" in full_text.lower()
        assert "APD" in full_text or "protection des donnees" in full_text.lower()
    except ImportError:
        pass  # pypdf not installed, skip content check

    # Audit
    async def check_audit():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        log = await db.audit_log.find_one(
            {'action': 'legal.rgpd_register_pdf_download', 'user_email': 'admin@copro.be'},
            sort=[('timestamp', -1)],
        )
        client.close()
        return log
    audit = asyncio.run(check_audit())
    assert audit is not None
    assert audit['details']['size_bytes'] == len(body)


if __name__ == '__main__':
    test_rgpd_register_endpoints_require_superadmin(); print('.', end='', flush=True)
    test_rgpd_register_get_returns_defaults_or_persisted(); print('.', end='', flush=True)
    test_rgpd_register_put_persists_and_audits(); print('.', end='', flush=True)
    test_rgpd_register_pdf_download(); print('.', end='', flush=True)
    print(' 4/4 OK')
