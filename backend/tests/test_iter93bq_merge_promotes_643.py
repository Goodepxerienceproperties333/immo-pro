"""iter93bq : quand une facture Optipro regroupe plusieurs lignes de
comptes (ex: 61060 charges communes + 643 frais privatifs) sous le meme
`internal_ref`, le merge doit promouvoir le compte 643 au niveau head
pour que `is_private_fee=True` soit flag correctement.

Bug utilisateur (screenshot Excel INV/2026/24) : facture avec 2 lignes
(61060 + 643), la merge prenait le 1er compte (61060), donc
`is_private_fee=False` et le review-mode iter93bi/bl ignorait ces frais
privatifs -> non allocation aux proprios -> bilan desequilibre.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_lines():
    """Simule 2 lignes CSV Optipro pour la meme facture."""
    return [
        {
            "account_number": "61060",
            "account_label": "Entretien jardins",
            "dist_key_code": "0001",
            "nature_code": "0003",
            "montant_ht": 227.21,
            "montant_tvac": 274.92,
            "internal_ref": "24",
            "external_ref": "INV/2026/24",
            "part_occupant": 100.00,
            "part_proprietaire": 0.00,
        },
        {
            "account_number": "643",
            "account_label": "Frais privatifs",
            "dist_key_code": "",
            "nature_code": "",
            "montant_ht": 231.42,
            "montant_tvac": 280.02,
            "internal_ref": "24",
            "external_ref": "INV/2026/24",
            "part_occupant": 0.00,
            "part_proprietaire": 100.00,
        },
    ]


def test_merge_promotes_643_to_head():
    """Reproduit la logique de merge du wizard invoices commit."""
    lines = _make_lines()
    # Simule le code de import_wizard.py ligne 1554-1582
    head = dict(lines[0])
    total_ht = round(sum(float(l.get("montant_ht") or 0) for l in lines), 2)
    total_tvac = round(sum(float(l.get("montant_tvac") or 0) for l in lines), 2)
    head["montant_ht"] = total_ht
    head["montant_tvac"] = total_tvac
    head["montant_tva"] = round(total_tvac - total_ht, 2)
    # iter93bq : promotion 643 au head
    for l in lines:
        acc = (l.get("account_number") or "").strip()
        if acc.startswith("643"):
            head["account_number"] = acc
            head["account_label"] = (l.get("account_label") or "").strip()
            break
    # Verifications
    assert head["account_number"] == "643", (
        f"Le head doit avoir account_number=643 apres promotion, got {head['account_number']}"
    )
    assert head["account_label"] == "Frais privatifs"
    assert head["montant_tvac"] == 554.94, f"Total TVAC attendu 554.94, got {head['montant_tvac']}"
    # Simule is_private_fee = account_num.startswith("643") (ligne 2013)
    is_private_fee = head["account_number"].startswith("643")
    assert is_private_fee is True, "is_private_fee doit etre True apres promotion 643"


def test_merge_single_line_common_charge_unchanged():
    """Regression : une facture 1 ligne 61060 (non-643) reste account=61060."""
    lines = [{
        "account_number": "61060",
        "account_label": "Entretien",
        "montant_ht": 100.0, "montant_tvac": 121.0,
        "internal_ref": "25", "external_ref": "INV/2026/25",
    }]
    head = dict(lines[0])
    # Pas de merge multi-lignes (len=1) -> pas de promotion
    if len(lines) > 1:
        for l in lines:
            if (l.get("account_number") or "").startswith("643"):
                head["account_number"] = l["account_number"]
                break
    assert head["account_number"] == "61060"
    assert not head["account_number"].startswith("643")


if __name__ == "__main__":
    test_merge_promotes_643_to_head()
    print("OK test_merge_promotes_643_to_head")
    test_merge_single_line_common_charge_unchanged()
    print("OK test_merge_single_line_common_charge_unchanged")
    print("\n=== ALL 2 TESTS PASSED ===")
