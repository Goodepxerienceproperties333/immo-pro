"""iter90t - Tests admin edition documents legaux.

Verifie :
- GET /api/legal/admin/documents renvoie 403 pour non-superadmin
- GET /api/legal/admin/documents renvoie 5 docs pour superadmin
- PUT /api/legal/admin/documents/{slug} avec bump_version=false : contenu edite, version inchangee
- PUT /api/legal/admin/documents/{slug} avec bump_version=true sur CGU : version+1, users doivent re-accepter
- GET /api/legal/admin/documents/{slug}/history renvoie l'historique
- Rejet contenu vide (400)
- Rejet contenu > 200 000 caracteres (400)
"""
import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
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


def test_admin_endpoints_require_superadmin():
    """Un role owner obtient 403 sur les endpoints admin."""
    async def setup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        email = 'iter90t_owner_admin@test.local'
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
            ('GET', '/api/legal/admin/documents', None),
            ('PUT', '/api/legal/admin/documents/cgu', {'content': 'evil', 'bump_version': True}),
            ('GET', '/api/legal/admin/documents/cgu/history', None),
        ]:
            if verb == 'GET':
                r = s.get(f'{BASE}{path}')
            else:
                r = s.put(f'{BASE}{path}', json=body)
            assert r.status_code == 403, f'{verb} {path} => {r.status_code} {r.text}'
    finally:
        async def cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_many({'email': 'iter90t_owner_admin@test.local'})
            client.close()
        asyncio.run(cleanup())


def test_admin_can_list_all_docs_with_content():
    """Le superadmin obtient les 5 docs avec contenu."""
    s = _login('admin@copro.be', 'admin123')
    r = s.get(f'{BASE}/api/legal/admin/documents')
    assert r.status_code == 200
    docs = r.json()
    slugs = [d['slug'] for d in docs]
    assert set(slugs) == {'cgu', 'privacy', 'mentions', 'cookies', 'disclaimer'}
    for d in docs:
        assert 'content' in d and d['content']
        assert 'version' in d


def test_admin_edit_without_bump_preserves_version():
    """Edit sans bump : contenu change, version identique."""
    s = _login('admin@copro.be', 'admin123')
    # Fetch initial
    r0 = s.get(f'{BASE}/api/legal/admin/documents')
    docs = r0.json()
    disclaimer = next(d for d in docs if d['slug'] == 'disclaimer')
    v_before = disclaimer['version']
    original_content = disclaimer['content']

    new_content = "# Disclaimer test iter90t\n\nEdit sans bump."
    r = s.put(f'{BASE}/api/legal/admin/documents/disclaimer',
              json={'content': new_content, 'bump_version': False})
    assert r.status_code == 200
    assert r.json()['version'] == v_before
    assert r.json()['bumped'] is False

    # Verify get
    r2 = s.get(f'{BASE}/api/legal/documents/disclaimer')
    assert r2.json()['content'] == new_content
    assert r2.json()['version'] == v_before

    # Restore
    s.put(f'{BASE}/api/legal/admin/documents/disclaimer',
          json={'content': original_content, 'bump_version': False})


def test_admin_edit_with_bump_forces_reaccept():
    """Edit CGU avec bump : version+1, user doit re-accepter."""
    async def reset_admin_acceptance():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # First put admin's legal_accepted at current version
        cgu = await db.legal_documents.find_one({'slug': 'cgu'})
        priv = await db.legal_documents.find_one({'slug': 'privacy'})
        await db.users.update_one(
            {'email': 'admin@copro.be'},
            {'$set': {'legal_accepted': {
                'cgu_version': cgu['version'], 'privacy_version': priv['version'],
                'accepted_at': '2026-01-01T00:00:00Z',
            }}},
        )
        client.close()
        return cgu['version'], priv['version']

    v_cgu_before, v_priv_before = asyncio.run(reset_admin_acceptance())
    s = _login('admin@copro.be', 'admin123')

    # Verify needs_accept is currently False
    ma = s.get(f'{BASE}/api/legal/my-acceptance').json()
    assert ma['needs_accept'] is False, ma

    # Fetch original CGU
    r0 = s.get(f'{BASE}/api/legal/admin/documents')
    cgu = next(d for d in r0.json() if d['slug'] == 'cgu')
    original_content = cgu['content']

    # Bump
    r = s.put(f'{BASE}/api/legal/admin/documents/cgu',
              json={'content': original_content + '\n\n<!-- iter90t bump -->',
                    'bump_version': True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['bumped'] is True
    assert body['version'] == v_cgu_before + 1
    assert 're-accepter' in body['message'].lower() or 'reaccepter' in body['message'].lower()

    # Now user must re-accept
    ma2 = s.get(f'{BASE}/api/legal/my-acceptance').json()
    assert ma2['needs_accept'] is True
    assert ma2['current_versions']['cgu_version'] == v_cgu_before + 1

    # History registered
    h = s.get(f'{BASE}/api/legal/admin/documents/cgu/history').json()
    assert any(e['version_before'] == v_cgu_before and e['version_after'] == v_cgu_before + 1
               and e['bumped'] is True and e['edited_by_email'] == 'admin@copro.be'
               for e in h)

    # Restore : accept again to not break other tests
    s.post(f'{BASE}/api/legal/accept', json={
        'cgu_version': v_cgu_before + 1, 'privacy_version': v_priv_before,
    })


def test_admin_reject_empty_and_oversize_content():
    """Rejet contenu vide 400 + contenu trop volumineux 400."""
    s = _login('admin@copro.be', 'admin123')
    r_empty = s.put(f'{BASE}/api/legal/admin/documents/mentions',
                    json={'content': '', 'bump_version': False})
    assert r_empty.status_code == 400

    r_ws = s.put(f'{BASE}/api/legal/admin/documents/mentions',
                 json={'content': '     ', 'bump_version': False})
    assert r_ws.status_code == 400

    huge = 'a' * 200_001
    r_big = s.put(f'{BASE}/api/legal/admin/documents/mentions',
                  json={'content': huge, 'bump_version': False})
    assert r_big.status_code == 400


if __name__ == '__main__':
    test_admin_endpoints_require_superadmin(); print('.', end='', flush=True)
    test_admin_can_list_all_docs_with_content(); print('.', end='', flush=True)
    test_admin_edit_without_bump_preserves_version(); print('.', end='', flush=True)
    test_admin_edit_with_bump_forces_reaccept(); print('.', end='', flush=True)
    test_admin_reject_empty_and_oversize_content(); print('.', end='', flush=True)
    print(' 5/5 OK')
