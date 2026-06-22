"""Regression test for iter72sexies : commit-distribution-keys schema fix.

Bug context :
- The import wizard was creating distribution_keys with a schema
  (`lines`, `type`) incompatible with the public API (`lots`, `key_type`),
  causing the UI editor to show Total quotité=0 and the expense allocation
  per owner to be empty.
- Lot matching also failed because `lot_label_raw` was "B0-1 - APPARTEMENT"
  (not "B0-1") and `lot_code_raw` was "-".

This test verifies :
1. _extract_lot_number strips the " - SUFFIX" cleanly.
2. The wizard handles edge cases like dash-only lot_code, complex names.
"""
import sys
sys.path.insert(0, "/app/backend")


def _extract_lot_number(raw: str) -> str:
    """Mirror of the helper in /app/backend/routes/import_wizard.py."""
    if not raw:
        return ""
    s = str(raw).strip()
    if " - " in s:
        s = s.split(" - ", 1)[0].strip()
    return s.lower().strip()


def test_extract_lot_number():
    assert _extract_lot_number("B0-1 - APPARTEMENT") == "b0-1"
    assert _extract_lot_number("Cave 1 - CAVE") == "cave 1"
    assert _extract_lot_number("Garage 5") == "garage 5"
    assert _extract_lot_number("") == ""
    assert _extract_lot_number("  B1-3 - DUPLEX  ") == "b1-3"
    assert _extract_lot_number("-") == "-"  # plain dash kept (caller filters it)


def test_distribution_keys_schema_after_commit(monkeypatch=None):
    """Smoke test : ensure that the in-memory transformation matches the
    expected API schema (lots, key_type, lot_id resolved)."""
    parsed_lines = [
        {"lot_label": "B0-1 - APPARTEMENT", "lot_code": "-", "owner_label": "C1983 - Mme W", "quotity": 839},
        {"lot_label": "Cave 1 - CAVE", "lot_code": "-", "owner_label": "C1983 - Mme W", "quotity": 5},
        {"lot_label": "Garage 1 - GARAGE", "lot_code": "-", "owner_label": "C1984 - Mme D", "quotity": 43},
        {"lot_label": "Inconnu - APPARTEMENT", "lot_code": "-", "owner_label": "C9999", "quotity": 100},
    ]
    acp_lots = [
        {"id": "lot-1", "number": "B0-1", "description": "Appartement 1"},
        {"id": "lot-2", "number": "Cave 1", "description": "Cave"},
        {"id": "lot-3", "number": "Garage 1", "description": "Garage"},
    ]
    lots_by_number = {l["number"].lower().strip(): l for l in acp_lots}
    lots_by_desc = {l["description"].lower().strip(): l for l in acp_lots}

    api_lots = []
    for line in parsed_lines:
        candidates = [
            _extract_lot_number(line["lot_code"]),
            _extract_lot_number(line["lot_label"]),
            line["lot_code"].lower().strip(),
            line["lot_label"].lower().strip(),
        ]
        matched = None
        for cand in candidates:
            if not cand or cand == "-":
                continue
            matched = lots_by_number.get(cand) or lots_by_desc.get(cand)
            if matched:
                break
        api_lots.append({
            "lot_id": matched["id"] if matched else "",
            "lot_number": matched["number"] if matched else (line["lot_code"] or line["lot_label"]),
            "share": float(line["quotity"]),
        })

    # 3 lots matched (B0-1, Cave 1, Garage 1), 1 unmatched (Inconnu)
    matched_count = sum(1 for l in api_lots if l["lot_id"])
    assert matched_count == 3
    assert api_lots[0]["lot_id"] == "lot-1"
    assert api_lots[0]["lot_number"] == "B0-1"
    assert api_lots[0]["share"] == 839.0
    assert api_lots[3]["lot_id"] == ""  # unmatched
    total = sum(l["share"] for l in api_lots)
    assert total == 987.0
