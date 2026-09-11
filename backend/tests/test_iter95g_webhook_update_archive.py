"""iter95g - Test e2e des 3 webhooks AG : new-acp, acp-updated, acp-archived.

Meme technique qu'iter95f : mock HTTP local sur 127.0.0.1:9998 qui capture
toutes les requetes recues. On configure AG_WEBHOOK_BASE_URL sur ce mock,
puis on execute Create -> Update -> Archive.
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

RECEIVED = []  # liste de (event_from_url, headers, payload)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a, **_kw):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        # url path last segment = event slug
        event = self.path.rstrip("/").rsplit("/", 1)[-1]
        RECEIVED.append({
            "event": event,
            "headers": dict(self.headers),
            "payload": json.loads(body),
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


def _start_mock(port=9998):
    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _load_env(path):
    d = {}
    for ln in open(path):
        if "=" in ln and not ln.startswith("#"):
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip().strip('"')
    return d


def _wait(pred, timeout=6.0, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def main():
    env = _load_env("/app/backend/.env")
    api = _load_env("/app/frontend/.env")["REACT_APP_BACKEND_URL"]
    token_expected = env["EXPORT_SYNC_TOKEN"]

    # Configure AG_WEBHOOK_BASE_URL sur le mock local
    lines = open("/app/backend/.env").read().splitlines()
    new_lines = []
    found_base, found_legacy = False, False
    for ln in lines:
        if ln.startswith("AG_WEBHOOK_BASE_URL="):
            new_lines.append("AG_WEBHOOK_BASE_URL=http://127.0.0.1:9998/api/webhooks")
            found_base = True
        elif ln.startswith("AG_WEBHOOK_URL="):
            new_lines.append("AG_WEBHOOK_URL=")  # ne pas polluer
            found_legacy = True
        else:
            new_lines.append(ln)
    if not found_base:
        new_lines.append("AG_WEBHOOK_BASE_URL=http://127.0.0.1:9998/api/webhooks")
    if not found_legacy:
        new_lines.append("AG_WEBHOOK_URL=")
    open("/app/backend/.env", "w").write("\n".join(new_lines) + "\n")
    subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True)
    time.sleep(4)

    _start_mock()

    s = requests.Session()
    r = s.post(f"{api}/api/auth/login",
               json={"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]},
               timeout=10)
    assert r.status_code == 200, f"login: {r.status_code} {r.text}"

    # === 1. CREATE ===
    RECEIVED.clear()
    create_payload = {
        "name": "ACP Webhook Test iter95g",
        "address": "Avenue du Sync 7", "postal_code": "1050",
        "city": "Ixelles", "country": "Belgique",
    }
    r = s.post(f"{api}/api/coproprietes", json=create_payload, timeout=15)
    assert r.status_code == 200
    copro_id = r.json()["id"]

    assert _wait(lambda: any(x["event"] == "new-acp" for x in RECEIVED)), "new-acp non recu"
    new_ev = next(x for x in RECEIVED if x["event"] == "new-acp")
    assert new_ev["payload"]["copropriete_id"] == copro_id
    assert new_ev["payload"]["name"] == "ACP Webhook Test iter95g"
    assert "Avenue du Sync 7" in new_ev["payload"]["address"]
    assert "Ixelles" in new_ev["payload"]["address"]
    assert new_ev["headers"]["X-Sync-Token"] == token_expected
    print("PASS new-acp recu")

    # === 2. UPDATE ===
    RECEIVED.clear()
    upd_payload = dict(create_payload)
    upd_payload["name"] = "ACP Webhook Test iter95g RENAMED"
    upd_payload["city"] = "Uccle"
    r = s.put(f"{api}/api/coproprietes/{copro_id}", json=upd_payload, timeout=15)
    assert r.status_code == 200, f"update: {r.status_code} {r.text}"

    assert _wait(lambda: any(x["event"] == "acp-updated" for x in RECEIVED)), "acp-updated non recu"
    upd_ev = next(x for x in RECEIVED if x["event"] == "acp-updated")
    assert upd_ev["payload"]["copropriete_id"] == copro_id
    assert upd_ev["payload"]["name"] == "ACP Webhook Test iter95g RENAMED"
    assert "Uccle" in upd_ev["payload"]["address"]
    assert upd_ev["headers"]["X-Sync-Token"] == token_expected
    print("PASS acp-updated recu (avec name/adresse mis a jour)")

    # === 3. ARCHIVE ===
    RECEIVED.clear()
    r = s.post(f"{api}/api/coproprietes/{copro_id}/archive", timeout=10)
    assert r.status_code == 200

    assert _wait(lambda: any(x["event"] == "acp-archived" for x in RECEIVED)), "acp-archived non recu"
    arch_ev = next(x for x in RECEIVED if x["event"] == "acp-archived")
    assert arch_ev["payload"]["copropriete_id"] == copro_id
    assert arch_ev["payload"]["name"] == "ACP Webhook Test iter95g RENAMED"
    assert arch_ev["headers"]["X-Sync-Token"] == token_expected
    print("PASS acp-archived recu")

    print("\nAll iter95g webhook events OK")


if __name__ == "__main__":
    main()
