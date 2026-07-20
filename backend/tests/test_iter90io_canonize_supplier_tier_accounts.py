"""iter90io : Verrouille la normalisation des comptes tier fournisseurs
au format canonique 8 chars ("44000XXX") dans le pipeline d'import.

Contexte : le pipeline d'import Optipro produisait historiquement des
comptes tier fournisseurs en 7 chars ("4400015", "4400471"), tandis que
le module courant `assign_supplier_account` cree systematiquement des
comptes canoniques en 8 chars ("44000015"). Consequence : deux lignes
distinctes dans le Bilan / Balance des Tiers pour LE MEME fournisseur
(une du wizard AN, une des ecritures AC courantes). L'audit remontait
alors des faux doublons de fournisseurs.

Fix : `tier_accounts.canonize_supplier_tier_account(number)` normalise
tout compte 440XXX (5-7 chars) vers 44000XXX (8 chars) AVANT le matching
et l'insertion en DB. Applique dans :
- `import_wizard.commit_invoices` (creation PCMN pre-alloc + sup_pcmn
  de fallback quand aucune fiche fournisseur n'existe)
- `import_wizard.commit_opening_balance` (aux_codes extraction ET boucle
  de construction des lignes AN).
"""
from __future__ import annotations


def test_canonize_pads_7_char_to_8_char():
    """iter90io-1 : un compte 7 chars Optipro ("4400015") est normalise
    au canonique 8 chars ("44000015") par insertion d'un "0" apres "440"."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("4400015") == "44000015"
    assert canonize_supplier_tier_account("4400471") == "44000471"
    assert canonize_supplier_tier_account("4400001") == "44000001"


def test_canonize_pads_6_char_to_8_char():
    """iter90io-2 : un compte 6 chars ("440015") est normalise en 8 chars
    par insertion de deux "0" apres "440"."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("440015") == "44000015"


def test_canonize_leaves_8_char_canonical_unchanged():
    """iter90io-3 : un compte deja au format canonique 8 chars reste
    inchange (idempotent)."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("44000015") == "44000015"
    assert canonize_supplier_tier_account("44000471") == "44000471"


def test_canonize_leaves_non_canonical_8_char_unchanged():
    """iter90io-4 : un compte 8 chars mais NON canonique ("44001115") est
    retourne tel quel. La resolution vers le canonique reel est deleguee
    au matching par fiche fournisseur (`_resolve_third_party`), qui
    reecrira la ligne AN en aval avec le compte declare dans
    `supplier.tier_accounts.<copro>.main`."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("44001115") == "44001115"


def test_canonize_leaves_non_supplier_account_unchanged():
    """iter90io-5 : les comptes qui ne commencent pas par "440" (bancaires,
    charges, owners, etc.) ne sont pas touches."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("551000") == "551000"
    assert canonize_supplier_tier_account("61210") == "61210"
    assert canonize_supplier_tier_account("41000015") == "41000015"
    assert canonize_supplier_tier_account("") == ""
    assert canonize_supplier_tier_account("   ") == ""


def test_canonize_strips_whitespace():
    """iter90io-6 : les espaces autour du numero sont ignores."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account("  4400015  ") == "44000015"


def test_canonize_none_safe():
    """iter90io-7 : `None` en entree renvoie une chaine vide (defensif)."""
    from tier_accounts import canonize_supplier_tier_account
    assert canonize_supplier_tier_account(None) == ""


def test_commit_invoices_uses_canonical_supplier_account_for_pcmn_prealloc():
    """iter90io-8 : `_ensure_pcmn_accounts` doit etre appele avec des
    comptes tier fournisseurs canoniques 8 chars pour les factures Optipro
    ayant un `supplier_aux_code=F0015` mais aucune fiche fournisseur
    correspondante. Verifie l'integration du helper dans
    `commit_invoices` (creation prealable des comptes PCMN)."""
    import re
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # La ligne historique buguee etait: sup_pcmn = "4400" + sup_aux[1:].zfill(3)
    # (7 chars). Elle doit avoir ete remplacee par un appel a
    # canonize_supplier_tier_account.
    assert 'canonize_supplier_tier_account("4400" + sup_aux[1:].zfill(3))' in src, (
        "Le pattern 7 chars 'sup_pcmn = \"4400\" + sup_aux[1:].zfill(3)' "
        "doit etre passe par canonize_supplier_tier_account dans "
        "commit_invoices (pre-allocation PCMN)."
    )
    # La ligne du fallback (aucune fiche fournisseur) doit aussi passer
    # par le helper.
    assert 'canonize_supplier_tier_account("4400" + supplier_aux[1:].zfill(3))' in src, (
        "Le fallback 'sup_pcmn = \"4400\" + supplier_aux[1:].zfill(3)' "
        "doit etre passe par canonize_supplier_tier_account dans "
        "commit_invoices (branche sans fiche fournisseur)."
    )
    # Aucun pattern nu ne doit subsister (sauf commentaires : filtre grep
    # sur les lignes reelles).
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Regexp : '"4400" + <aux>[1:].zfill(3)' NON precede de canonize_
        if re.search(r'"4400"\s*\+\s*\w+\[1:\]\.zfill\(3\)', line):
            if "canonize_supplier_tier_account" not in line:
                raise AssertionError(
                    f"Ligne {lineno} contient encore le pattern 7 chars nu : {line.strip()}"
                )


def test_commit_opening_balance_canonizes_supplier_accounts_before_matching():
    """iter90io-9 : dans `commit_opening_balance`, la boucle qui extrait
    les aux_codes des lignes AN ET la boucle qui construit les lignes de
    l'ecriture doivent appliquer `canonize_supplier_tier_account` en
    amont, afin qu'un compte AN "4400015" soit traite exactement comme
    "44000015" (le canonique)."""
    src = open("/app/backend/routes/import_wizard.py", encoding="utf-8").read()
    # Le helper doit avoir ete importe une fois localement.
    assert "from tier_accounts import canonize_supplier_tier_account as _canonize_sup_acc" in src, (
        "Le helper canonize_supplier_tier_account doit etre importe "
        "sous l'alias _canonize_sup_acc dans commit_opening_balance."
    )
    # Il doit etre appele au moins 2 fois : une pour l'extraction aux
    # code + une pour la boucle de construction des lignes.
    calls = src.count("_canonize_sup_acc(acc")
    assert calls >= 2, (
        f"_canonize_sup_acc doit etre appele au moins 2 fois dans "
        f"commit_opening_balance (extraction + construction). Trouve : {calls}"
    )
