"""Backend tests for iter90fs Support Tickets system (bug escalation)."""
import io
import os
import re
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
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


# --- Statuses endpoint ---
def test_statuses_endpoint(alpha):
    r = alpha.get(f"{BASE_URL}/api/tickets/statuses", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 7
    keys = {d["key"] for d in data}
    assert keys == {"open", "assigned", "in_progress", "testing",
                    "deployment", "closed", "rejected"}
    for d in data:
        assert d.get("label"), f"missing label for {d}"


# --- Create ticket ---
@pytest.fixture(scope="module")
def alpha_ticket(alpha):
    """Creates a ticket as syndic_alpha with a small file attachment."""
    files = [
        ("files", ("hello.txt", io.BytesIO(b"Hello ticket world!"), "text/plain")),
    ]
    data = {
        "title": "TEST_bug automated ticket creation",
        "description": "Automated pytest ticket description content longer than 10",
        "steps_to_reproduce": "1. do X\n2. do Y",
        "expected_behavior": "should work",
        "observed_behavior": "does not work",
        "requester_email": "syndic_alpha@copro.be",
    }
    r = alpha.post(f"{BASE_URL}/api/tickets", data=data, files=files, timeout=30)
    assert r.status_code == 200, r.text
    t = r.json()
    assert re.match(r"^TICK-\d{4}-\d{4}$", t["number"]), t
    assert t["status"] == "open"
    assert t["title"].startswith("TEST_bug")
    assert len(t["attachments"]) == 1
    att = t["attachments"][0]
    assert att["filename"] == "hello.txt"
    assert att["size"] == len(b"Hello ticket world!")
    assert att["content_type"].startswith("text/plain")
    assert "file_id" in att
    assert t.get("syndic_id"), "syndic_id must be set for chinese wall"
    assert "_id" not in t
    return t


def test_ticket_created_event_persisted(alpha, alpha_ticket):
    tid = alpha_ticket["id"]
    r = alpha.get(f"{BASE_URL}/api/tickets/{tid}/events", timeout=10)
    assert r.status_code == 200, r.text
    events = r.json()
    assert any(e["event_type"] == "created" and e["new_status"] == "open" for e in events)


# --- List isolation ---
def test_list_isolation_alpha_sees_own(alpha, alpha_ticket):
    r = alpha.get(f"{BASE_URL}/api/tickets", timeout=10)
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert alpha_ticket["id"] in ids


def test_list_isolation_beta_no_alpha(beta, alpha_ticket):
    r = beta.get(f"{BASE_URL}/api/tickets", timeout=10)
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert alpha_ticket["id"] not in ids, "Beta must not see Alpha's ticket"


def test_admin_sees_all(admin, alpha_ticket):
    r = admin.get(f"{BASE_URL}/api/tickets", timeout=10)
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert alpha_ticket["id"] in ids


def test_beta_get_alpha_ticket_403(beta, alpha_ticket):
    r = beta.get(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}", timeout=10)
    assert r.status_code == 403
    assert "chinese wall" in r.text.lower()


# --- Status change RBAC ---
def test_syndic_cannot_change_status(alpha, alpha_ticket):
    r = alpha.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/status",
                   json={"new_status": "assigned", "comment": "no"}, timeout=10)
    assert r.status_code == 403
    assert "superadmin" in r.text.lower()


def test_admin_change_status_ok(admin, alpha, alpha_ticket):
    tid = alpha_ticket["id"]
    r = admin.post(f"{BASE_URL}/api/tickets/{tid}/status",
                   json={"new_status": "assigned", "comment": "ok par admin"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "assigned"
    # verify event
    r2 = admin.get(f"{BASE_URL}/api/tickets/{tid}/events", timeout=10)
    evts = r2.json()
    assert any(e["event_type"] == "status_changed" and e["new_status"] == "assigned" for e in evts)


def test_admin_invalid_status(admin, alpha_ticket):
    r = admin.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/status",
                   json={"new_status": "bogus"}, timeout=10)
    assert r.status_code == 400


# --- Comments ---
def test_syndic_can_comment_own(alpha, alpha_ticket):
    r = alpha.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/comments",
                   json={"comment": "syndic comment"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event_type"] == "comment"
    assert body["comment"] == "syndic comment"


def test_admin_can_comment_any(admin, alpha_ticket):
    r = admin.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/comments",
                   json={"comment": "admin comment"}, timeout=10)
    assert r.status_code == 200


def test_empty_comment_400(alpha, alpha_ticket):
    r = alpha.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/comments",
                   json={"comment": "   "}, timeout=10)
    assert r.status_code == 400


def test_beta_cannot_comment_alpha(beta, alpha_ticket):
    r = beta.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/comments",
                  json={"comment": "hack"}, timeout=10)
    assert r.status_code == 403


# --- Attachments ---
def test_download_attachment_ok(alpha, alpha_ticket):
    fid = alpha_ticket["attachments"][0]["file_id"]
    r = alpha.get(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/attachments/{fid}",
                  timeout=15)
    assert r.status_code == 200, r.text
    assert r.content == b"Hello ticket world!"
    assert r.headers.get("Content-Type", "").startswith("text/plain")


def test_download_attachment_cross_syndic_403(beta, alpha_ticket):
    fid = alpha_ticket["attachments"][0]["file_id"]
    r = beta.get(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/attachments/{fid}",
                 timeout=10)
    assert r.status_code == 403


# --- Assign ---
def test_assign_by_admin(admin, alpha_ticket):
    # get admin user_id via /api/auth/me
    me = admin.get(f"{BASE_URL}/api/auth/me", timeout=10)
    assert me.status_code == 200, me.text
    admin_id = me.json().get("id") or me.json().get("_id") or me.json().get("user_id")
    assert admin_id, f"cannot resolve admin user id from {me.json()}"
    r = admin.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/assign",
                   json={"assigned_to_user_id": admin_id}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_to_user_id"] == admin_id
    assert body["assigned_to_name"]


def test_assign_by_syndic_403(alpha, alpha_ticket):
    r = alpha.post(f"{BASE_URL}/api/tickets/{alpha_ticket['id']}/assign",
                   json={"assigned_to_user_id": "whoever"}, timeout=10)
    assert r.status_code == 403


# --- Validation ---
def test_create_short_title(alpha):
    r = alpha.post(f"{BASE_URL}/api/tickets",
                   data={"title": "hi", "description": "long enough description"},
                   timeout=10)
    assert r.status_code == 400
    assert "titre" in r.text.lower() or "title" in r.text.lower()


def test_create_short_description(alpha):
    r = alpha.post(f"{BASE_URL}/api/tickets",
                   data={"title": "TEST_valid title", "description": "short"},
                   timeout=10)
    assert r.status_code == 400
    assert "descr" in r.text.lower()
