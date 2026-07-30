"""iter93bp : parser cles de repartition PDF multi-pages avec libelles
alphanumeriques a points/hyphens (ex: G.3-A.1.1, G34-P21).

Contexte utilisateur : "des cles de repartitions peuvent avoir plusieurs
pages ce n'est pas normal adoucis le parsing pour que tout soit identifie".
Le regex de fallback texte n'acceptait que les libelles au format `A 001`
(lettre + espace + alphanumerique). Ne matchait donc pas `G.3-A.1.1` ni
`G34-P21` (formats Sogis/Finlead avec points ou tirets sans espace).

Fix : regex assoupli `[A-Z][A-Za-z0-9.\-\s]*?` (non-greedy avec dots,
hyphens, whitespaces internes).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from import_wizard.pdf_utils import parse_distribution_keys_pdf


def _load_pdf_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def test_dotted_lot_labels_multipage():
    """PDF Finlead 'Cle de repartition' avec libelles G.3-A.1.1 et G34-P21
    sur 2 pages -> doit extraire les 102 lignes."""
    pdf_bytes = _load_pdf_bytes("/tmp/dist_keys.pdf")
    result = parse_distribution_keys_pdf(pdf_bytes)
    keys = result.get("keys", [])
    assert len(keys) == 1, f"Expected 1 key, got {len(keys)}"
    key = keys[0]
    assert key["code"] == "0001", f"Expected code '0001', got {key['code']}"
    assert "Charges communes" in key["name"], f"Unexpected name: {key['name']}"
    lines = key.get("lines") or []
    assert len(lines) == 102, f"Expected 102 lines, got {len(lines)}"
    # Verifie total quotites (equilibre 100 000)
    total = round(key.get("total_quotities") or 0, 2)
    assert total == 100000.0, f"Expected total_quotities=100000, got {total}"
    # Verifie qu'un lot a point (G.3-A.1.1) est bien extrait
    dotted_labels = [l for l in lines if l["lot_label"].startswith("G.3")]
    assert len(dotted_labels) > 0, "Aucun lot G.3-* extrait"
    # Verifie qu'un lot avec tiret (G34-P) est bien extrait (page 2)
    hyphen_labels = [l for l in lines if l["lot_label"].startswith("G34-P")]
    assert len(hyphen_labels) > 0, "Aucun lot G34-P* extrait (page 2 non parsee)"
    # Verifie que la quotite parsee est correcte
    first = lines[0]
    assert first["quotity"] == 2859.0, f"1re quotite attendue 2859.0, got {first['quotity']}"


if __name__ == "__main__":
    test_dotted_lot_labels_multipage()
    print("OK test_dotted_lot_labels_multipage - parser detecte 102 lignes multi-pages")
