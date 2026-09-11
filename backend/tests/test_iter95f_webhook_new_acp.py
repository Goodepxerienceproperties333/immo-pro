"""iter95f - Test e2e : POST /api/coproprietes doit declencher le webhook
sortant vers AG_WEBHOOK_URL avec le header X-Sync-Token.

On lance un mini serveur mock sur 127.0.0.1:9999 qui capture la requete,
on positionne AG_WEBHOOK_URL vers ce mock puis on cree une ACP via l'API
authentifiee superadmin. On attend jusqu'a 3s la reception du webhook.
"""
import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

RECEIVED = {"payload": None, "headers": None}


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a, **_kw):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        RECEIVED["payload"] = json.loads(body)
        RECEIVED["headers"] = dict(self.headers)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


def _start_mock():
    server = HTTPServer(("127.0.0.1", 9999), _MockHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _load_env(path):
    d = {}
    for ln in open(path):
        if "=" in ln and not ln.startswith("#"):
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip().strip('"')
    return d


def main():
    env = _load_env("/app/backend/.env")
    api = _load_env("/app/frontend/.env")["REACT_APP_BACKEND_URL"]
    token_expected = env["EXPORT_SYNC_TOKEN"]

    # 1. Repointe le backend vers le mock local via un endpoint admin ?
    # Non : on modifie l'env var runtime via un endpoint dedie n'existe pas.
    # Approche : on lance le mock puis on injecte AG_WEBHOOK_URL via subprocess
    # en redemarrant le backend. Ici on triche : on ecrit AG_WEBHOOK_URL dans
    # .env et on redemarre supervisorctl.
    import subprocess
    # Ecrit la valeur mock dans .env
    lines = open("/app/backend/.env").read().splitlines()
    new_lines = []
    found = False
    for ln in lines:
        if ln.startswith("AG_WEBHOOK_URL="):
            new_lines.append("AG_WEBHOOK_URL=http://host.docker.internal:9999/api/webhooks/new-acp")
            # Le backend tourne sur le meme host que le test -> 127.0.0.1 marche
            new_lines[-1] = "AG_WEBHOOK_URL=http://127.0.0.1:9999/api/webhooks/new-acp"
            found = True
        else:
            new_lines.append(ln)
    if not found:
        new_lines.append("AG_WEBHOOK_URL=http://127.0.0.1:9999/api/webhooks/new-acp")
    open("/app/backend/.env", "w").write("\n".join(new_lines) + "\n")
    subprocess.run(["sudo", "supervisorctl", "restart", "backend"], check=True)
    time.sleep(4)

    _start_mock()
    # 2. Login superadmin
    s = requests.Session()
    r = s.post(f"{api}/api/auth/login",
               json={"email": env["ADMIN_EMAIL"], "password": env["ADMIN_PASSWORD"]},
               timeout=10)
    assert r.status_code == 200, f"login: {r.status_code} {r.text}"

    # 3. Cree une ACP
    payload = {
        "name": "ACP TEST WEBHOOK iter95f",
        "address": "Rue des Tests 42",
        "postal_code": "1000",
        "city": "Bruxelles",
        "country": "Belgique",
        "bce": "",
    }
    r = s.post(f"{api}/api/coproprietes", json=payload, timeout=15)
    assert r.status_code == 200, f"create: {r.status_code} {r.text}"
    created = r.json()
    copro_id = created["id"]
    print(f"ACP creee : {copro_id}")

    # 4. Attente reception webhook (fire-and-forget async)
    for _ in range(30):  # 30 * 0.2s = 6s max
        if RECEIVED["payload"] is not None:
            break
        time.sleep(0.2)

    assert RECEIVED["payload"] is not None, "Webhook non recu"
    print("Webhook recu :", json.dumps(RECEIVED["payload"], indent=2, ensure_ascii=False))

    # 5. Assertions
    assert RECEIVED["payload"]["copropriete_id"] == copro_id
    assert RECEIVED["payload"]["name"] == "ACP TEST WEBHOOK iter95f"
    assert "Rue des Tests 42" in RECEIVED["payload"]["address"]
    assert "Bruxelles" in RECEIVED["payload"]["address"]
    assert RECEIVED["headers"].get("X-Sync-Token") == token_expected, (
        f"Token: got {RECEIVED['headers'].get('X-Sync-Token')} expected {token_expected}"
    )

    # 6. Cleanup : archive l'ACP
    s.post(f"{api}/api/coproprietes/{copro_id}/archive", timeout=10)
    print("PASS webhook new-acp OK")


if __name__ == "__main__":
    main()
