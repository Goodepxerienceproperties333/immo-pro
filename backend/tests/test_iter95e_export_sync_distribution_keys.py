"""iter95e - Test e2e /api/export/owners/{copro_id} avec quotites issues des
distribution_keys (source de verite PCMN, pas le champ lot.quotity fondateur).

Utilise le token dev configure dans /app/backend/.env :
    EXPORT_SYNC_TOKEN=dev-sync-token-change-me-in-production
"""
import json
import os
import sys
import urllib.request

# Charge la conf frontend pour recuperer l'URL preview (identique a celle
# utilisee en prod par les clients externes)
def _load_env(path):
    d = {}
    for ln in open(path):
        if "=" in ln and not ln.startswith("#"):
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip().strip('"')
    return d


BASE = _load_env("/app/frontend/.env")["REACT_APP_BACKEND_URL"]
TOKEN = _load_env("/app/backend/.env")["EXPORT_SYNC_TOKEN"]
# ACP Agathe (real seed in preview DB)
COPRO_ID = "c9cfce94-96c6-4202-8a6d-0a5627b50856"


def _get(path, token=None):
    req = urllib.request.Request(f"{BASE}{path}")
    # UA "curl-like" pour eviter le bot-block Cloudflare sur "Python-urllib".
    req.add_header("User-Agent", "NextGeCopro-SyncTest/1.0")
    if token:
        req.add_header("X-Sync-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None


def test_401_without_token():
    code, _ = _get(f"/api/export/owners/{COPRO_ID}")
    assert code == 401, f"expected 401, got {code}"


def test_401_with_wrong_token():
    code, _ = _get(f"/api/export/owners/{COPRO_ID}", token="WRONG")
    assert code == 401, f"expected 401, got {code}"


def test_404_unknown_copro():
    code, _ = _get("/api/export/owners/does-not-exist-xyz", token=TOKEN)
    assert code == 404, f"expected 404, got {code}"


def test_200_full_response_shape():
    code, data = _get(f"/api/export/owners/{COPRO_ID}", token=TOKEN)
    assert code == 200
    assert data["copropriete_id"] == COPRO_ID
    assert data["copropriete_name"] == "Agathe"
    assert data["count"] >= 1
    o = data["owners"][0]
    # Cle owner
    for k in ("id", "civility", "first_name", "last_name", "email",
             "email2", "phone", "phone2", "address", "lots",
             "total_quotity_default", "total_quotity_founder"):
        assert k in o, f"missing owner key {k}"
    # Cle adresse
    for k in ("street", "postal_code", "city", "country", "full"):
        assert k in o["address"], f"missing address key {k}"
    # Cle lot
    if o["lots"]:
        lt = o["lots"][0]
        for k in ("id", "number", "description", "type", "floor",
                 "area", "quotity_founder", "quotity_default", "quotities"):
            assert k in lt, f"missing lot key {k}"


def test_quotities_come_from_distribution_keys():
    """Verifie que quotities est bien peuple depuis distribution_keys."""
    _, data = _get(f"/api/export/owners/{COPRO_ID}", token=TOKEN)
    # Cherche au moins un lot avec des quotites detaillees
    found_default = False
    found_multi = False
    for o in data["owners"]:
        for lt in o["lots"]:
            for q in lt["quotities"]:
                assert set(q.keys()) >= {"key_id", "key_name", "is_default", "share"}
                assert isinstance(q["share"], (int, float))
                if q["is_default"]:
                    found_default = True
            if len(lt["quotities"]) >= 2:
                found_multi = True
    assert found_default, "aucune cle par defaut trouvee"
    assert found_multi, "aucun lot avec plusieurs cles (attendu sur Agathe)"


def test_totals_are_coherent():
    _, data = _get(f"/api/export/owners/{COPRO_ID}", token=TOKEN)
    for o in data["owners"]:
        # total_quotity_default = somme des quotity_default non-null
        expected = round(
            sum(lt["quotity_default"] for lt in o["lots"] if lt["quotity_default"] is not None),
            4,
        )
        assert abs(o["total_quotity_default"] - expected) < 0.001, (
            f"owner {o['last_name']} total_default mismatch: "
            f"got {o['total_quotity_default']} expected {expected}"
        )


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            try:
                f()
                print(f"PASS {n}")
            except AssertionError as e:
                print(f"FAIL {n}: {e}")
                sys.exit(1)
    print("All export sync e2e tests OK")
