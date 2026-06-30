"""Regression test - iter88e - CODA parser amount decimal precision.

Demande user (Feb 2026, carried over from iter85+ handoff) :
    "Verifier comment le montant CODA est parse en float. Les dernieres
    decimales doivent etre correctement interpretees selon le standard
    belge."

Le standard belge Febelfin CODA 2.6 specifie :
    Format Bedrag/Amount : N15(3) = 15 chars, 3 fixed decimals
    Exemple : 1234.56 EUR -> "000000001234560" (15 chars, 3 decimales)
    Exemple : 310.00 EUR  -> "000000000310000"

**Bug fixe (iter88e)** : `parse_amount` divisait par 100 (centimes) au lieu
de 1000 (millimes), causant une erreur x10 sur tous les montants importes.
Un debit de 310.00 EUR etait enregistre comme 3100.00 EUR.

Reference : https://www.febelfin.be/sites/default/files/files/Standard CODA 2.6.pdf

Tests :
  1. parse_amount basique : "000000000310000" -> 310.00 EUR
  2. parse_amount credit/debit signs (0 = credit positif, 1 = debit negatif)
  3. parse_amount montant complexe : 1234.56 EUR
  4. parse_amount millimes purs : 0.123 EUR (precision 3 decimales)
  5. parse_amount montants importants : 999999999.999
  6. parse_amount edge cases : 0.00, vide, non-numerique
  7. End-to-end : un fichier CODA minimal avec un mouvement -> bon montant
"""
import os
import sys

sys.path.insert(0, "/app/backend")


def test_iter88e_parse_amount_basic_310_eur():
    """310.00 EUR encode "000000000310000" doit etre parse en 310.00."""
    from coda_parser import parse_amount
    # Sign = 0 (credit), amount = 15 digits, 3 decimales fixes
    result = parse_amount("0" + "000000000310000")
    assert abs(result - 310.0) < 0.001, f"Attendu 310.00, obtenu {result}"


def test_iter88e_parse_amount_credit_sign():
    """Sign 0 -> montant positif (credit)."""
    from coda_parser import parse_amount
    result = parse_amount("0" + "000000001234560")
    assert abs(result - 1234.56) < 0.001
    assert result > 0


def test_iter88e_parse_amount_debit_sign():
    """Sign 1 -> montant negatif (debit)."""
    from coda_parser import parse_amount
    result = parse_amount("1" + "000000001234560")
    assert abs(result - (-1234.56)) < 0.001
    assert result < 0


def test_iter88e_parse_amount_decimal_precision():
    """0.123 EUR (millimes purs) doit etre parse a 3 decimales pres."""
    from coda_parser import parse_amount
    # 0.123 -> "000000000000123"
    result = parse_amount("0" + "000000000000123")
    assert abs(result - 0.123) < 0.0001, f"Attendu 0.123, obtenu {result}"


def test_iter88e_parse_amount_big_value():
    """Gros montant : 12345678.901 EUR."""
    from coda_parser import parse_amount
    # 12345678.901 -> "000012345678901"
    result = parse_amount("0" + "000012345678901")
    assert abs(result - 12345678.901) < 0.001, f"Attendu 12345678.901, obtenu {result}"


def test_iter88e_parse_amount_zero():
    """Montant nul : "000000000000000" -> 0.0."""
    from coda_parser import parse_amount
    result = parse_amount("0" + "000000000000000")
    assert result == 0.0


def test_iter88e_parse_amount_edge_cases():
    """Empty / None / non-numeric -> 0.0 (defensive)."""
    from coda_parser import parse_amount
    assert parse_amount("") == 0.0
    assert parse_amount(None) == 0.0
    assert parse_amount("0") == 0.0
    assert parse_amount("0ABCDEFG") == 0.0


def test_iter88e_parse_amount_typical_belgian_invoice_315_72():
    """Cas reel : facture syndic 315.72 EUR -> "000000000315720"."""
    from coda_parser import parse_amount
    result = parse_amount("0" + "000000000315720")
    assert abs(result - 315.72) < 0.001


def test_iter88e_parse_amount_provision_1500():
    """Cas reel : provision trimestrielle 1500.00 EUR -> "000000001500000"."""
    from coda_parser import parse_amount
    result = parse_amount("0" + "000000001500000")
    assert abs(result - 1500.0) < 0.001


def test_iter88e_full_coda_movement_amount_correct():
    """End-to-end : un fichier CODA minimal avec 1 mouvement de 310.00 EUR
    debit doit produire `movements[0].amount == -310.0`."""
    from coda_parser import parse_coda_file
    # Minimal valid CODA structure with the bare minimum to extract a movement.
    # Header (0), Old balance (1), Movement (21), New balance (8), Trailer (9).
    # The movement 21 has amount field at positions [31:47] = 16 chars
    # (1 sign + 15 amount digits, 3 fixed decimals).
    # 310.00 EUR debit = sign "1" + "000000000310000"
    # We pad each line to a sufficient length so safe_slice doesn't truncate.
    header = "0" + " " * 200
    # parse_old_balance reads:
    #   sign at pos 42 (1 char) -> use "0" (credit)
    #   balance at pos 42:58 -> sign + 15 digits
    old_balance = "1" + " " * 41 + "0" + "000000005000000" + "010126" + " " * 200
    # Movement type 2.1, sequence "0001", detail "0000", ref padded.
    # parse_movement_21 reads amount at [31:47].
    mvt = ("2" + "1" + "0001" + "0000" + " " * 21 +
           "1" + "000000000310000" +  # 310.00 EUR debit at positions 31..46
           "020126" +  # value date
           " " * 8 +   # transaction code
           " " * 54 +  # communication
           "020126" +  # entry date
           " " * 100)
    new_balance = "8" + " " * 40 + "1" + "000000004690000" + "020126" + " " * 100
    trailer = "9" + " " * 21 + "000000000310000" + "000000000000000" + " " * 50

    content = "\n".join([header, old_balance, mvt, new_balance, trailer])
    parsed = parse_coda_file(content)

    movements = parsed.get("movements", [])
    assert len(movements) == 1, f"1 mouvement attendu, trouve {len(movements)}"
    amount = movements[0]["amount"]
    # 310.00 EUR debit -> -310.00
    assert abs(amount - (-310.0)) < 0.001, (
        f"Mouvement amount doit etre -310.00 (debit), trouve {amount}"
    )
    assert movements[0]["type"] == "debit"

    # Soldes coherents : opening 5000, closing 4690 (debit de 310)
    opening = parsed["old_balance"]["balance"]
    closing = parsed["new_balance"]["balance"]
    assert abs(opening - 5000.0) < 0.001, f"Solde initial 5000 attendu, trouve {opening}"
    # closing balance has sign "1" -> debit -> negatif
    assert abs(closing - (-4690.0)) < 0.001, f"Solde final -4690 attendu, trouve {closing}"
