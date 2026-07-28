"""iter93ac : tests regression pour le helper `utils.format`."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.format import fmt_eur, fmt_number, fmt_pct, fmt_quotity, NBSP  # noqa: E402


def test_fmt_eur_basic():
    assert fmt_eur(10800.5) == f"10{NBSP}800,50 EUR"
    assert fmt_eur(1234) == f"1{NBSP}234,00 EUR"
    assert fmt_eur(0) == "0,00 EUR"
    assert fmt_eur(0.5) == "0,50 EUR"


def test_fmt_eur_negative():
    assert fmt_eur(-1234.56) == f"-1{NBSP}234,56 EUR"
    assert fmt_eur(-0.01) == "-0,01 EUR"


def test_fmt_eur_large_amount():
    assert fmt_eur(1234567.89) == f"1{NBSP}234{NBSP}567,89 EUR"
    assert fmt_eur(1000000000) == f"1{NBSP}000{NBSP}000{NBSP}000,00 EUR"


def test_fmt_eur_edge_cases():
    assert fmt_eur(None) == "0,00 EUR"
    assert fmt_eur("") == " EUR"  # comportement historique conserve
    assert fmt_eur("invalid") == " EUR"
    assert fmt_eur("42.5") == "42,50 EUR"


def test_fmt_eur_no_suffix():
    assert fmt_eur(10800.5, with_suffix=False) == f"10{NBSP}800,50"
    assert fmt_eur(-1234.56, with_suffix=False) == f"-1{NBSP}234,56"


def test_fmt_number_decimals():
    assert fmt_number(1234.5678, 4) == f"1{NBSP}234,5678"
    assert fmt_number(1234.5, 0) == f"1{NBSP}234"
    assert fmt_number(0.123456, 6) == "0,123456"


def test_fmt_pct():
    assert fmt_pct(10.5) == "10,50 %"
    assert fmt_pct(100) == "100,00 %"
    assert fmt_pct(0.5, 4) == "0,5000 %"


def test_fmt_quotity_trim_zeros():
    """Les quotites doivent ne pas afficher de zeros de fin inutiles."""
    assert fmt_quotity(267.27) == "267,27"
    assert fmt_quotity(267.270000) == "267,27"
    assert fmt_quotity(800) == "800"
    assert fmt_quotity(0) == "0"


if __name__ == "__main__":
    for fn in [
        test_fmt_eur_basic, test_fmt_eur_negative, test_fmt_eur_large_amount,
        test_fmt_eur_edge_cases, test_fmt_eur_no_suffix, test_fmt_number_decimals,
        test_fmt_pct, test_fmt_quotity_trim_zeros,
    ]:
        fn()
        print(f"OK: {fn.__name__}")
    print("\nTous les tests passent")
