"""iter90s - Tests du systeme legal (CGU, RGPD, cookies).

Verifie :
- Liste et lecture publiques des documents legaux
- Blocage 401 sur endpoints prives sans auth
- Cycle acceptation CGU (needs_accept -> false apres accept)
- Rejet accept avec versions obsoletes
- Export RGPD (fichier + audit log)
- Suppression compte : refusee sans phrase exacte, marque le user, annulable
- Refus suppression du dernier superadmin
"""
import asyncio
import os
import sys
import json
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Load env for MONGO_URL and DB_NAME
from dotenv import load_dotenv
load_dotenv(pathlib.Path(__file__).parent.parent / '.env')

import requests
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

BASE = os.environ.get('REACT_APP_BACKEND_URL')
if not BASE:
    # Read from frontend/.env
    env_path = pathlib.Path(__file__).parent.parent.parent / 'frontend' / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith('REACT_APP_BACKEND_URL='):
                BASE = line.split('=', 1)[1].strip()
                break
assert BASE, "REACT_APP_BACKEND_URL not set"

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']


def _login(email='admin@copro.be', password='admin123'):
    s = requests.Session()
    r = s.post(f'{BASE}/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return s


def test_legal_documents_public():
    """GET /api/legal/documents et /documents/{slug} accessibles sans auth."""
    r = requests.get(f'{BASE}/api/legal/documents')
    assert r.status_code == 200
    docs = r.json()
    slugs = {d['slug'] for d in docs}
    assert 'cgu' in slugs
    assert 'privacy' in slugs
    assert 'mentions' in slugs
    assert 'cookies' in slugs
    assert 'disclaimer' in slugs
    for slug in slugs:
        d = requests.get(f'{BASE}/api/legal/documents/{slug}')
        assert d.status_code == 200
        assert 'content' in d.json()
        assert d.json()['version'] >= 1


def test_legal_private_endpoints_require_auth():
    """Les endpoints non-documents exigent l'authentification."""
    for path in ['/api/legal/current-versions', '/api/legal/my-acceptance']:
        r = requests.get(f'{BASE}{path}')
        assert r.status_code == 401, f'{path} -> {r.status_code}'
    r = requests.post(f'{BASE}/api/legal/accept', json={'cgu_version': 1, 'privacy_version': 1})
    assert r.status_code == 401


def test_accept_cycle_and_versions_check():
    """Cycle : reset -> needs_accept=true -> accept -> false. Rejet si version obsolete."""
    async def reset_and_run():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.update_one(
            {'email': 'admin@copro.be'},
            {'$unset': {'legal_accepted': ''}},
        )
        client.close()

    asyncio.run(reset_and_run())

    s = _login()
    # my-acceptance -> needs_accept true
    ma = s.get(f'{BASE}/api/legal/my-acceptance').json()
    assert ma['needs_accept'] is True

    # accept with wrong version
    bad = s.post(f'{BASE}/api/legal/accept', json={'cgu_version': 999, 'privacy_version': 1})
    assert bad.status_code == 400
    assert 'obsoletes' in bad.json()['detail'].lower()

    # accept with correct versions
    cv = s.get(f'{BASE}/api/legal/current-versions').json()
    ok = s.post(f'{BASE}/api/legal/accept', json={
        'cgu_version': cv['cgu_version'], 'privacy_version': cv['privacy_version'],
    })
    assert ok.status_code == 200
    ma2 = s.get(f'{BASE}/api/legal/my-acceptance').json()
    assert ma2['needs_accept'] is False
    assert ma2['legal_accepted']['cgu_version'] == cv['cgu_version']


def test_rgpd_export():
    """L'export RGPD renvoie un JSON avec profile et un content-disposition."""
    s = _login()
    r = s.post(f'{BASE}/api/legal/rgpd/export', json={})
    assert r.status_code == 200
    body = r.json()
    assert 'profile' in body
    assert body['profile']['email'] == 'admin@copro.be'
    assert 'audit_logs' in body
    assert 'password_hash' not in body['profile']
    # Content-Disposition attachment header
    assert 'attachment' in r.headers.get('content-disposition', '').lower()


def test_rgpd_delete_needs_confirm_phrase():
    """Sans la phrase exacte, la suppression est rejetee 400."""
    s = _login()
    bad = s.post(f'{BASE}/api/legal/rgpd/delete-account', json={'confirm': 'oui'})
    assert bad.status_code == 400
    assert 'SUPPRIMER MON COMPTE' in bad.json()['detail']


def test_rgpd_delete_last_superadmin_refused():
    """Impossible de supprimer le dernier superadmin (protection)."""
    # Il faut n'avoir QU'UN seul superadmin
    async def check_only_one():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        n = await db.users.count_documents({'role': {'$in': ['superadmin', 'admin']}})
        client.close()
        return n

    n = asyncio.run(check_only_one())
    if n == 1:
        s = _login()
        r = s.post(f'{BASE}/api/legal/rgpd/delete-account',
                   json={'confirm': 'SUPPRIMER MON COMPTE'})
        assert r.status_code == 403
        assert 'dernier' in r.json()['detail'].lower()


def test_rgpd_delete_and_cancel_owner_role():
    """Un owner peut demander la suppression puis l'annuler."""
    async def setup_owner():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Create a throwaway owner user
        import bcrypt
        pwd = bcrypt.hashpw(b'testpw123', bcrypt.gensalt()).decode()
        email = 'iter90s_owner_delete@test.local'
        await db.users.delete_many({'email': email})
        r = await db.users.insert_one({
            'email': email,
            'password_hash': pwd,
            'name': 'Test Owner Delete',
            'role': 'owner',
            'copropriete_ids': [],
        })
        client.close()
        return email, str(r.inserted_id)

    email, uid = asyncio.run(setup_owner())
    try:
        s = _login(email=email, password='testpw123')
        # Request deletion
        r = s.post(f'{BASE}/api/legal/rgpd/delete-account',
                   json={'confirm': 'SUPPRIMER MON COMPTE'})
        assert r.status_code == 200, r.text
        assert r.json()['purge_in_days'] == 30

        # Verify user has deletion_requested_at
        async def check():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            u = await db.users.find_one({'_id': ObjectId(uid)})
            client.close()
            return u

        u = asyncio.run(check())
        assert u.get('deletion_requested_at') is not None
        assert u.get('deletion_purge_at_ts') is not None

        # Cancel
        c = s.post(f'{BASE}/api/legal/rgpd/cancel-deletion', json={})
        assert c.status_code == 200
        u2 = asyncio.run(check())
        assert u2.get('deletion_requested_at') is None
    finally:
        async def cleanup():
            client = AsyncIOMotorClient(MONGO_URL)
            db = client[DB_NAME]
            await db.users.delete_many({'email': email})
            client.close()
        asyncio.run(cleanup())


if __name__ == '__main__':
    test_legal_documents_public(); print('.', end='', flush=True)
    test_legal_private_endpoints_require_auth(); print('.', end='', flush=True)
    test_accept_cycle_and_versions_check(); print('.', end='', flush=True)
    test_rgpd_export(); print('.', end='', flush=True)
    test_rgpd_delete_needs_confirm_phrase(); print('.', end='', flush=True)
    test_rgpd_delete_last_superadmin_refused(); print('.', end='', flush=True)
    test_rgpd_delete_and_cancel_owner_role(); print('.', end='', flush=True)
    print(' 7/7 OK')
