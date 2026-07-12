"""iter90dw - Grouping des mutations PAR COUNTERPART OWNER dans la vue du vendeur.

User bug (Feb 2026):
> "dans la vue de la situation de compte d'un vendeur, on differencie les
> proprietaires pas d'additions entre les sommes proprietaires pour les
> ajustements comptables pour les provisions pour charge"

Contexte : Matexi a vendu 27 lots a differents acheteurs (Dewinter, Lahaye,
TEUWEN, etc.). Les mutations etaient toutes agregees en une seule ligne
"Mutations (27 lots) - Fonds de roulement", additionnant les montants de
tous les acheteurs. Illisible pour le vendeur.

Fix iter90dw : `_group_movements_by_owner` accepte `self_owner_name` et
regroupe les mutations PAR COUNTERPART (l'autre owner de la mutation).
"""
from routes.reports import _group_movements_by_owner, _normalize_mutation_desc


def _mut(lot: str, amount: float, from_name: str, to_name: str,
         label: str = "Fonds de roulement (backfill retroactif via appel Q1)"):
    return {
        "date": "2025-11-07",
        "description": f"Mutation lot {lot} - {label}: {from_name} -> {to_name} ({amount:.2f} EUR)",
        "reference": f"MUT-{lot}-R",
        "account_number": "41010001",
        "debit": 0.0,
        "credit": amount,
        "journal_type": "OD",
        "third_party_id": "owner-matexi",
    }


def test_regex_captures_from_to_names():
    """Extract counterpart names from OD MUT-R description."""
    r = _normalize_mutation_desc(
        "Mutation lot 001 - Fonds de roulement (backfill retroactif via appel Q1): Matexi -> Dewinter (648.44 EUR)"
    )
    assert r is not None
    label, from_name, to_name = r
    assert "Fonds de roulement" in label
    assert from_name == "Matexi"
    assert to_name == "Dewinter"


def test_mutations_same_counterpart_grouped_together():
    """3 lots vendus a Dewinter -> une seule ligne agregee."""
    movements = [
        _mut("001", 216.14, "Matexi", "Dewinter"),
        _mut("002", 216.14, "Matexi", "Dewinter"),
        _mut("003", 216.16, "Matexi", "Dewinter"),
    ]
    result = _group_movements_by_owner(movements, self_owner_name="Matexi")
    assert len(result) == 1, f"3 lots meme acheteur -> 1 ligne, obtenu {len(result)}"
    r = result[0]
    assert "Mutations (3 lots)" in r["description"], r["description"]
    assert "-> Dewinter" in r["description"], r["description"]
    assert r["credit"] == round(216.14 + 216.14 + 216.16, 2)


def test_mutations_different_counterparts_stay_separate():
    """3 lots a Dewinter + 2 a Lahaye + 1 a TEUWEN -> 3 lignes distinctes.
    (User request : pas d'addition entre proprietaires)."""
    movements = [
        _mut("001", 216.14, "Matexi", "Dewinter"),
        _mut("002", 216.14, "Matexi", "Dewinter"),
        _mut("003", 216.16, "Matexi", "Dewinter"),
        _mut("004", 192.66, "Matexi", "Lahaye"),
        _mut("005", 192.66, "Matexi", "Lahaye"),
        _mut("006", 490.36, "Matexi", "TEUWEN"),
    ]
    result = _group_movements_by_owner(movements, self_owner_name="Matexi")
    assert len(result) == 3, (
        f"3 counterparts distincts -> 3 lignes. Obtenu {len(result)}."
    )
    # Reconstitue par counterpart
    by_cp = {}
    for r in result:
        desc = r["description"]
        if "Dewinter" in desc:
            by_cp["Dewinter"] = r
        elif "Lahaye" in desc:
            by_cp["Lahaye"] = r
        elif "TEUWEN" in desc:
            by_cp["TEUWEN"] = r

    assert "Dewinter" in by_cp
    assert "Mutations (3 lots)" in by_cp["Dewinter"]["description"]
    assert abs(by_cp["Dewinter"]["credit"] - 648.44) < 0.01

    assert "Lahaye" in by_cp
    assert "Mutations (2 lots)" in by_cp["Lahaye"]["description"]
    assert abs(by_cp["Lahaye"]["credit"] - 385.32) < 0.01

    assert "TEUWEN" in by_cp
    # 1 seul lot -> garde description originale (plus precis pour audit)
    assert "Mutation lot 006" in by_cp["TEUWEN"]["description"] or \
           "Mutations (1 lots)" in by_cp["TEUWEN"]["description"]
    assert abs(by_cp["TEUWEN"]["credit"] - 490.36) < 0.01


def test_buyer_view_counterpart_is_seller():
    """Depuis la vue d'un acheteur (Dewinter), le counterpart est le vendeur (Matexi)."""
    movements = [
        _mut("001", 216.14, "Matexi", "Dewinter"),
        _mut("002", 216.14, "Matexi", "Dewinter"),
    ]
    result = _group_movements_by_owner(movements, self_owner_name="Dewinter")
    assert len(result) == 1
    r = result[0]
    # Vue Dewinter : counterpart = Matexi (le vendeur)
    assert "Mutations (2 lots)" in r["description"]
    assert "-> Matexi" in r["description"], r["description"]


def test_matexi_matexi_no_self_reference():
    """Cas edge : si les 2 owners sont identiques (ne devrait pas arriver mais safe)."""
    movements = [
        _mut("001", 100.0, "Matexi", "Matexi"),
    ]
    result = _group_movements_by_owner(movements, self_owner_name="Matexi")
    assert len(result) == 1


def test_no_self_owner_name_uses_to_as_default():
    """Backward compat: si self_owner_name non fourni, prend to_name comme counterpart."""
    movements = [
        _mut("001", 216.14, "Matexi", "Dewinter"),
        _mut("002", 216.14, "Matexi", "Dewinter"),
    ]
    result = _group_movements_by_owner(movements)  # pas de self_owner_name
    assert len(result) == 1
    assert "-> Dewinter" in result[0]["description"]


def test_matexi_scenario_from_user_pdf():
    """Reproduit exactement le cas Matexi du PDF fourni par l'utilisateur :
    07/11: 3 lots vers Dewinter = 648.44 credit
    07/11: 3 lots vers Lahaye = 385.32 credit (meme date)
    """
    movements = [
        # 3 lots vers Dewinter le 07/11
        _mut("001", 216.14, "Matexi", "Dewinter"),
        _mut("002", 216.14, "Matexi", "Dewinter"),
        _mut("003", 216.16, "Matexi", "Dewinter"),
        # 3 lots vers Lahaye le 07/11 (meme date)
        _mut("004", 128.44, "Matexi", "Lahaye"),
        _mut("005", 128.44, "Matexi", "Lahaye"),
        _mut("006", 128.44, "Matexi", "Lahaye"),
    ]
    result = _group_movements_by_owner(movements, self_owner_name="Matexi")
    # 2 lignes distinctes: Dewinter (648.44) + Lahaye (385.32)
    assert len(result) == 2, (
        f"Meme date mais 2 counterparts distincts -> 2 lignes. "
        f"Obtenu {len(result)} : {[r['description'] for r in result]}"
    )
    for r in result:
        if "Dewinter" in r["description"]:
            assert abs(r["credit"] - 648.44) < 0.01
        elif "Lahaye" in r["description"]:
            assert abs(r["credit"] - 385.32) < 0.01
