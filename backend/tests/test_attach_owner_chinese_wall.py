"""Chinese-wall tests for POST /api/owners/{owner_id}/attach-to-copro.

Verifies fix in properties.py attach_owner_to_copro (lines 953-978): the owner
being attached must already belong to the same syndic before the attach is
allowed for non-superadmin users. Superadmin still bypasses.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")

# Fixture data (pre-seeded)
ALPHA_ACP = "7375e9ae-b6c1-40e8-b632-1901553af965"
ALPHA_ACP_2 = "b6fe4e32-555a-435b-84cd-1f080cdd47c6"
BETA_ACP = "778391ef-ff95-44d3-9b6c-2d33542d09f8"

BETA_OWNER = "ca41e444-ef4a-4955-81b0-293d6742f648"        # Boxus Wivine (beta)
ALPHA_OWNER_MULTI_ACP = "1b88801f-e6b4-405b-bf61-a16d79265d52"  # Martin Pierre (alpha, ACP b6fe4e32)
ALPHA_OWNER = "172d59e5-5e30-4cd7-8b21-f4aceddcbea8"       # Dupont Jean (alpha)


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def alpha():
    return _login("syndic_alpha@copro.be", "Syndic123!")


@pytest.fixture(scope="module")
def beta():
    return _login("syndic_beta@copro.be", "Syndic123!")


@pytest.fixture(scope="module")
def admin():
    return _login("admin@copro.be", "admin123")


def _get_owner(session, owner_id):
    r = session.get(f"{BASE_URL}/api/owners/{owner_id}")
    return r


def _attach(session, owner_id, copro_id):
    return session.post(
        f"{BASE_URL}/api/owners/{owner_id}/attach-to-copro",
        json={"copropriete_id": copro_id},
    )


# --- BUG FIX A : cross-syndic must be rejected + no mutation
class TestCrossSyndicRejected:
    def test_alpha_attach_beta_owner_returns_403(self, alpha, admin):
        # snapshot beta owner's copropriete_ids via admin (source of truth)
        before = _get_owner(admin, BETA_OWNER)
        assert before.status_code == 200, before.text
        before_ids = sorted(before.json().get("copropriete_ids", []) or [])

        r = _attach(alpha, BETA_OWNER, ALPHA_ACP)
        assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"
        detail = (r.json().get("detail") or "").lower()
        assert "chinese wall" in detail, f"detail missing 'chinese wall': {detail}"

        after = _get_owner(admin, BETA_OWNER)
        assert after.status_code == 200
        after_ids = sorted(after.json().get("copropriete_ids", []) or [])
        assert before_ids == after_ids, (
            f"owner mutated on rejected attach: before={before_ids} after={after_ids}"
        )


# --- BUG FIX B : legitimate same-syndic attach must succeed + idempotent
class TestSameSyndicAttachSucceeds:
    def test_attach_alpha_owner_to_other_alpha_acp(self, alpha):
        r = _attach(alpha, ALPHA_OWNER_MULTI_ACP, ALPHA_ACP)
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert "rattache" in (body.get("message") or "").lower()
        ids = body.get("owner", {}).get("copropriete_ids", []) or []
        assert ALPHA_ACP in ids and ALPHA_ACP_2 in ids, f"ids missing: {ids}"

    def test_attach_is_idempotent(self, alpha):
        r = _attach(alpha, ALPHA_OWNER_MULTI_ACP, ALPHA_ACP)
        assert r.status_code == 200, f"idempotent call failed: {r.status_code} {r.text}"
        ids = r.json().get("owner", {}).get("copropriete_ids", []) or []
        assert ids.count(ALPHA_ACP) == 1, f"duplicated ACP in list: {ids}"


# --- BUG FIX C : target-ACP guard still fires (alpha owner -> beta ACP)
class TestTargetAcpGuard:
    def test_alpha_cannot_attach_own_owner_to_beta_acp(self, alpha):
        r = _attach(alpha, ALPHA_OWNER, BETA_ACP)
        assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"
        detail = (r.json().get("detail") or "").lower()
        assert "chinese wall" in detail or "acp" in detail


# --- BUG FIX D : superadmin bypass still works cross-syndic
class TestSuperadminBypass:
    def test_superadmin_can_attach_across_syndics(self, admin):
        r = _attach(admin, BETA_OWNER, ALPHA_ACP)
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        ids = r.json().get("owner", {}).get("copropriete_ids", []) or []
        assert ALPHA_ACP in ids, f"ACP not attached: {ids}"
        # report final state so main agent can revert
        print(f"[SUPERADMIN MUTATION] Beta owner {BETA_OWNER} copropriete_ids -> {ids}")
