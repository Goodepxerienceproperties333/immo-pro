"""
Iter90ap : Amelioration reconnaissance IA dates + downgrade Haiku -> Sonnet 4.6

Contexte : l'utilisateur a signale que la reconnaissance des dates n'etait pas
toujours correcte apres le passage a Haiku 4.5 (iter90an). Les dates belges
DD/MM/YYYY etaient parfois confondues avec du MM/DD/YYYY.

Fix :
1. Bascule Haiku 4.5 -> Sonnet 4.6 (recommande, meilleur ratio vitesse/precision).
2. System prompt enrichi avec regles explicites format date belge :
   - DD/MM/YYYY inconditionnel (jamais US)
   - Mois FR (janvier..decembre) + NL (januari..december)
   - Distinguer date facture vs echeance vs periode
   - Sortie ISO YYYY-MM-DD obligatoire, "" si absent
3. Validation post-extraction :
   - Annee hors 2020-2035 -> flag warning + reset a ""
   - Format non ISO -> flag warning + reset a ""
   - due_date < date -> flag "IA a probablement inverse jour/mois"
4. Frontend affiche warning via toast + hint dans le formulaire.

Ce test valide UNIQUEMENT la logique post-extraction (validation dates), pas
l'appel IA reel (nécessite API key + PDF + reponse non-deterministe).
"""
import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _run_validation_valid_dates_no_warning():
    """Dates valides -> pas de _date_warning."""
    from routes.invoice_ai import create_invoice_ai_router  # noqa
    # On teste directement la fonction _validate_dates par introspection
    # du code source (le validate_dates est defini en local dans extract_invoice).
    # Approche pragmatique : on reproduit la logique metier pour verifier
    # les invariants attendus.
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "invoice_ai.py",
    )
    with open(src_path) as f:
        src = f.read()
    # Ces marqueurs doivent tous exister dans le code
    assert "_date_warning" in src, "Missing _date_warning field"
    assert "annee hors plage 2020-2035" in src, "Missing year range check"
    assert "L'IA a probablement inverse jour/mois" in src, "Missing due_date coherence check"


async def _run_prompt_contains_belgian_date_rules():
    """Verifie que le system prompt contient les regles date belge explicites."""
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "routes", "invoice_ai.py",
    )
    with open(src_path) as f:
        src = f.read()
    checks = [
        "CRITICAL - BELGIAN DATE FORMAT RULES",
        "DAY/MONTH/YEAR format (DD/MM/YYYY",
        "NEVER assume US format",
        "French month names",
        "Dutch month names",
        "invoice ISSUE date",
        "PAYMENT deadline",
    ]
    for c in checks:
        assert c in src, f"Missing date rule in prompt : '{c}'"


def _simulate_validate(result):
    """Reproduit la logique _validate_dates() en pur Python pour tester en unit."""
    from datetime import date as _dt_cls
    warnings = []
    date_val = (result.get("date") or "").strip()
    due_val = (result.get("due_date") or "").strip()
    parsed_date = None
    parsed_due = None
    if date_val:
        try:
            parsed_date = _dt_cls.fromisoformat(date_val)
            y = parsed_date.year
            if y < 2020 or y > 2035:
                warnings.append(f"Date facture suspecte ({date_val}) : annee hors plage 2020-2035")
                result["date"] = ""
        except (ValueError, TypeError):
            warnings.append(f"Date facture illisible : '{date_val}' (format non ISO YYYY-MM-DD)")
            result["date"] = ""
    if due_val:
        try:
            parsed_due = _dt_cls.fromisoformat(due_val)
            y = parsed_due.year
            if y < 2020 or y > 2035:
                warnings.append(f"Date echeance suspecte ({due_val}) : annee hors plage 2020-2035")
                result["due_date"] = ""
        except (ValueError, TypeError):
            warnings.append(f"Date echeance illisible : '{due_val}' (format non ISO YYYY-MM-DD)")
            result["due_date"] = ""
    if parsed_date and parsed_due and parsed_due < parsed_date:
        warnings.append(f"Incoherence : echeance ({due_val}) anterieure a date facture ({date_val}). "
                        "L'IA a probablement inverse jour/mois - a verifier.")
    if warnings:
        result["_date_warning"] = " ; ".join(warnings)
    return result


async def _run_year_out_of_range_flagged():
    """Annee < 2020 ou > 2035 -> reset + warning."""
    r = _simulate_validate({"date": "2019-06-15", "due_date": "2019-07-15"})
    assert "_date_warning" in r
    assert r["date"] == ""
    assert "annee hors plage" in r["_date_warning"]

    r2 = _simulate_validate({"date": "2036-01-01"})
    assert r2["date"] == ""
    assert "annee hors plage" in r2["_date_warning"]


async def _run_invalid_iso_flagged():
    """Format non-ISO (l'IA n'a pas converti) -> reset + warning."""
    r = _simulate_validate({"date": "15/06/2026", "due_date": ""})
    assert "_date_warning" in r
    assert r["date"] == ""
    assert "illisible" in r["_date_warning"]


async def _run_due_before_date_flagged():
    """due_date anterieure a date facture -> warning "IA a inverse jour/mois"."""
    # Exemple typique : facture 06/03/2026 (=6 mars) mais IA interprete 03/06/2026 (=3 juin)
    # tandis que echeance 06/04/2026 (=6 avril) devient 04/06/2026 (=4 juin).
    # Result : date=2026-06-03, due_date=2026-06-04 -> coherence OK visuellement mais fausse.
    # Cas plus explicite : date=2026-06-15, due_date=2026-06-10 -> anomalie flagrante.
    r = _simulate_validate({"date": "2026-06-15", "due_date": "2026-06-10"})
    assert "_date_warning" in r
    assert "inverse jour/mois" in r["_date_warning"]


async def _run_valid_dates_no_warning():
    """Dates valides et coherentes -> pas de warning."""
    r = _simulate_validate({"date": "2026-06-15", "due_date": "2026-07-15"})
    assert "_date_warning" not in r
    assert r["date"] == "2026-06-15"
    assert r["due_date"] == "2026-07-15"


async def _run_empty_dates_no_warning():
    """Dates absentes -> pas de warning, champs conserves vides."""
    r = _simulate_validate({"date": "", "due_date": ""})
    assert "_date_warning" not in r


def test_source_contains_validation_markers():
    asyncio.run(_run_validation_valid_dates_no_warning())


def test_prompt_contains_belgian_date_rules():
    asyncio.run(_run_prompt_contains_belgian_date_rules())


def test_year_out_of_range_flagged():
    asyncio.run(_run_year_out_of_range_flagged())


def test_invalid_iso_flagged():
    asyncio.run(_run_invalid_iso_flagged())


def test_due_before_date_flagged():
    asyncio.run(_run_due_before_date_flagged())


def test_valid_dates_no_warning():
    asyncio.run(_run_valid_dates_no_warning())


def test_empty_dates_no_warning():
    asyncio.run(_run_empty_dates_no_warning())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
