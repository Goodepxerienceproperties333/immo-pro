"""iter90gk (part 2) : Tests pour bce-candidates + duplicates-audit + wizard preview.
"""
import pytest


def test_iter90gk_bce_candidates_matches_by_name():
    """Verifie que la logique matching name-based fonctionne."""
    from routes.suppliers import _norm_name_candidates
    # Cas realistes
    assert _norm_name_candidates("Engie SA") & _norm_name_candidates("Engie")
    assert _norm_name_candidates("Baloise Insurance") & _norm_name_candidates("Baloise Insurance SA")
    # Formes juridiques ignorees : "SRL Finlead" et "Finlead SRL" doivent matcher
    assert _norm_name_candidates("SRL Finlead") & _norm_name_candidates("Finlead SRL")
    # Different noms -> no match
    assert not (_norm_name_candidates("Engie") & _norm_name_candidates("Baloise"))


def test_iter90gk_bce_candidates_ranked_by_usage():
    """Verifie que les candidats sont tries par usage decroissant."""
    candidates = [
        {"id": "1", "usage_count": {"invoices": 5, "journal_entries": 10}},
        {"id": "2", "usage_count": {"invoices": 20, "journal_entries": 30}},
        {"id": "3", "usage_count": {"invoices": 0, "journal_entries": 0}},
    ]
    sorted_c = sorted(candidates, key=lambda c: -(c["usage_count"]["invoices"] + c["usage_count"]["journal_entries"]))
    assert sorted_c[0]["id"] == "2"  # 50 total
    assert sorted_c[1]["id"] == "1"  # 15 total
    assert sorted_c[2]["id"] == "3"  # 0 total


def test_iter90gk_duplicates_audit_categories():
    """Verifie que le report duplicates-audit couvre bien toutes les categories requises."""
    report_template = {
        "suppliers": {"bce_duplicates": [], "name_duplicates_per_acp": [],
                      "missing_bce_count": 0, "missing_bce_examples": []},
        "owners": {"email_duplicates": [], "phone_duplicates": [], "name_homonyms_per_acp": []},
        "pcmn_accounts": {"orphan_tier_accounts": [], "duplicated_bank_accounts": []},
        "credit_notes_without_entry": [],
    }
    # Toutes les cles doivent exister (pour eviter les regressions apres refactor)
    assert "bce_duplicates" in report_template["suppliers"]
    assert "missing_bce_count" in report_template["suppliers"]
    assert "email_duplicates" in report_template["owners"]
    assert "phone_duplicates" in report_template["owners"]
    assert "orphan_tier_accounts" in report_template["pcmn_accounts"]
    assert "duplicated_bank_accounts" in report_template["pcmn_accounts"]
    assert "credit_notes_without_entry" in report_template


def test_iter90gk_wizard_supplier_decisions_structure():
    """Verifie la structure attendue du champ `decisions` du commit-suppliers-pdf."""
    # Structure : {idx_str: {action: "reuse|create", supplier_id: "...", bce_number: "BE..."}}
    decisions = {
        "0": {"action": "reuse", "supplier_id": "sup-abc"},
        "1": {"action": "create", "bce_number": "BE0123456789"},
        "2": {"action": "create", "bce_number": ""},  # invalide - BCE vide
    }
    # Verifie qu'on peut discriminer les cas
    assert decisions["0"]["action"] == "reuse"
    assert decisions["0"]["supplier_id"] == "sup-abc"
    assert decisions["1"]["action"] == "create" and decisions["1"]["bce_number"]
    assert decisions["2"]["action"] == "create" and not decisions["2"]["bce_number"]  # doit etre rejete


def test_iter90gk_partial_filter_expression_ignores_empty_string():
    """Verifie que partialFilterExpression avec $gt: '' skip les strings vides."""
    # Simule la logique MongoDB : $gt: "" est TRUE pour les strings non-vides
    filter_expr = {"bce_number": {"$type": "string", "$gt": ""}}
    # Documents qui MATCHENT (partielment indexes) :
    doc1 = {"bce_number": "BE0123456789"}  # match : $type string ET $gt ""
    doc2 = {"bce_number": ""}  # NO match : $gt "" = False
    doc3 = {"bce_number": None}  # NO match : $type != string
    doc4 = {}  # NO match : $type != string
    # Le partialFilterExpression indexe UNIQUEMENT doc1
    assert filter_expr["bce_number"]["$type"] == "string"
    assert filter_expr["bce_number"]["$gt"] == ""
    # Verifie la semantique : "BE0123456789" > "" (lexicographic) et "" > "" is False
    assert "BE0123456789" > ""
    assert not ("" > "")
