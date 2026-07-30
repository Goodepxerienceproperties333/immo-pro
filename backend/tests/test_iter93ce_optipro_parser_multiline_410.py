"""iter93ce - Regression : parser Optipro capture tous les sous-comptes 410
(y compris 4101xxx et 4102xxx) + labels multi-lignes.

Contexte : le handoff signalait que le parser ratait ~24 sous-comptes du
compte 410 (468,62 EUR manquants) a cause de labels multi-lignes et
coordonnees x0 decalees. En realite, la strategie multi-passes (iter93bu)
gere deja correctement ces cas ; ces tests verrouillent l'etat pour eviter
toute regression future.

Bilan de reference : ACP Agathe 31/03/2027 (Total 49.383,51 EUR).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from import_wizard.optipro_parser import parse_optipro_bilan  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "optipro_bilan_31_03_2027.pdf")


def _load():
    if not os.path.exists(FIXTURE):
        pytest.skip(f"Fixture PDF absent : {FIXTURE}")
    with open(FIXTURE, "rb") as f:
        return f.read()


def test_bilan_2027_balanced_and_totals():
    """Le bilan 31/03/2027 doit s'equilibrer a 49.383,51 EUR."""
    r = parse_optipro_bilan(_load())
    assert r["balanced"], f"Bilan desequilibre : ecart={r['ecart']}"
    assert abs(r["total_actif"] - 49383.51) < 0.01
    assert abs(r["total_passif"] - 49383.51) < 0.01
    assert r["period_end_date"] == "31/03/2027"
    assert r["diagnostic"]["strategy_used"] == "standard"


def test_all_28_actif_410_subaccounts_captured():
    """28 sous-comptes ACTIF 410 doivent tous etre extraits (Optipro reference).

    Cas critiques verifies :
    - 4101xxx (2 comptes : 4101000 = 428.61, 4101004 = 21.95)
    - 4102xxx (2 comptes : 4102167 = 12.76, 4102466 = 5.30)
    - Labels multi-lignes (CANTERO DIAZ - VARGAS BAQUERO Miguel - Catalina)
    """
    r = parse_optipro_bilan(_load())
    subs = [x for x in r["actif"] if x["account"].startswith("410") and x["is_subaccount"]]
    assert len(subs) == 28, f"Nombre de sous-comptes ACTIF 410 = {len(subs)}, attendu 28"

    # Somme sous-comptes = main 410 (2769.50 EUR)
    total_subs = round(sum(s["amount"] for s in subs), 2)
    assert abs(total_subs - 2769.50) < 0.01, (
        f"Somme sous-comptes ACTIF 410 = {total_subs}, attendu 2769.50"
    )

    # Verifier presence specifique des 4101/4102 rates auparavant
    accounts = {s["account"]: s["amount"] for s in subs}
    assert accounts.get("4101000") == 428.61, "4101000 De Wergifosse (428.61) manquant"
    assert accounts.get("4101004") == 21.95, "4101004 Vanrobaeys (21.95) manquant"
    assert accounts.get("4102167") == 12.76, "4102167 Kloos (12.76) manquant"
    assert accounts.get("4102466") == 5.30, "4102466 CANTERO DIAZ (5.30) manquant"
    # Somme 4101/4102 = 468.62 EUR (montant historique du bug)
    subs_4101_4102 = sum(
        v for k, v in accounts.items()
        if k.startswith("4101") or k.startswith("4102")
    )
    assert abs(subs_4101_4102 - 468.62) < 0.01, (
        f"Sous-comptes 4101/4102 = {subs_4101_4102}, attendu 468.62"
    )


def test_multiline_label_captured_correctly():
    """Le label multi-ligne 'CANTERO DIAZ - VARGAS BAQUERO Miguel - Catalina' est complet."""
    r = parse_optipro_bilan(_load())
    target = next((x for x in r["actif"] if x["account"] == "4100970"), None)
    assert target is not None, "Compte 4100970 non trouve"
    assert target["amount"] == 570.22
    label = target["label"]
    assert "CANTERO DIAZ" in label
    assert "VARGAS BAQUERO" in label
    assert "Miguel" in label
    assert "Catalina" in label


def test_all_9_passif_410_subaccounts_captured():
    """9 sous-comptes PASSIF 410 (createurs) doivent tous etre extraits."""
    r = parse_optipro_bilan(_load())
    subs = [x for x in r["passif"] if x["account"].startswith("410") and x["is_subaccount"]]
    assert len(subs) == 9, f"Sous-comptes PASSIF 410 = {len(subs)}, attendu 9"
    total_subs = round(sum(s["amount"] for s in subs), 2)
    assert abs(total_subs - 2389.75) < 0.01, (
        f"Somme sous-comptes PASSIF 410 = {total_subs}, attendu 2389.75"
    )
    accounts = {s["account"] for s in subs}
    assert "4101003" in accounts, "4101003 SRL Smarteon (538.10) manquant"


def test_no_subaccount_sum_warnings():
    """La validation somme sous-comptes = main doit ne produire AUCUN warning."""
    r = parse_optipro_bilan(_load())
    warnings = r["diagnostic"]["warnings"]
    # Verifie pas de warning sur 410 sub-account sum mismatch
    for w in warnings:
        assert "410" not in w or "sous-comptes" not in w.lower(), (
            f"Warning inattendu sur 410 sub-accounts : {w}"
        )


def test_bank_accounts_8digit_extracted():
    """Les comptes bancaires 8-digit doivent etre correctement extraits."""
    r = parse_optipro_bilan(_load())
    banks = {e["account"]: e["amount"] for e in r["actif"] if e["account"].startswith("55")}
    assert abs(banks.get("55011383", 0) - 13111.41) < 0.01
    assert abs(banks.get("55114766", 0) - 28023.42) < 0.01


def test_490_charges_a_reporter_captured():
    """Le compte 490 'Charges a reporter' (3743.74 EUR) doit etre capture."""
    r = parse_optipro_bilan(_load())
    row_490 = next((x for x in r["actif"] if x["account"] == "490"), None)
    assert row_490 is not None, "Compte 490 non trouve"
    assert abs(row_490["amount"] - 3743.74) < 0.01


if __name__ == "__main__":
    test_bilan_2027_balanced_and_totals()
    print("OK test_bilan_2027_balanced_and_totals")
    test_all_28_actif_410_subaccounts_captured()
    print("OK test_all_28_actif_410_subaccounts_captured")
    test_multiline_label_captured_correctly()
    print("OK test_multiline_label_captured_correctly")
    test_all_9_passif_410_subaccounts_captured()
    print("OK test_all_9_passif_410_subaccounts_captured")
    test_no_subaccount_sum_warnings()
    print("OK test_no_subaccount_sum_warnings")
    test_bank_accounts_8digit_extracted()
    print("OK test_bank_accounts_8digit_extracted")
    test_490_charges_a_reporter_captured()
    print("OK test_490_charges_a_reporter_captured")
    print("\n=== ALL 7 TESTS PASSED ===")
