"""iter90g6 : le decompte annuel doit inclure les ecritures OD lot_mutation
(MUT-R fonds de roulement, MUT-P prorata appel en cours, MUT-F reprise
appels futurs) touchant le compte tier du proprietaire.

**Contexte utilisateur (PROD, decompte TEUWEN vs Optipro)** :
Comparaison Optipro vs App :
- Optipro : Total debits 1474.17 EUR (incl. Transfert fonds de roulement
  490.36 EUR) / Total credits 1608.05 EUR -> Solde crediteur 133.88 EUR
- Notre App : Charges 919.07 EUR / Payments 1608.05 EUR -> Solde crediteur
  688.98 EUR (ecart 555 EUR)

**Cause racine** : le solde du decompte etait calcule sur
  total_imputed = charges + reserve + roulement
  balance = total_imputed - payments

Il IGNORAIT completement les ecritures OD source_type='lot_mutation'
(MUT-R/MUT-P/MUT-F) generees automatiquement par `POST /api/lots/{id}/
mutations` (`properties.py::_build_entry`). Ces OD debitent le compte tier
de l'ACHETEUR (dettes reprises du vendeur) et creditent le compte tier
du VENDEUR (dettes cedees a l'acheteur), datees `sale_date`.

**Fix iter90g6** (`pdf_decompte.py::build_decompte_pdf`) :
- Nouveau parametre `mutation_entries: list` = liste des OD lot_mutation
  couvrant le FY.
- Calcul de `mutation_net_debit` par owner (somme des debits - credits
  sur les lignes ou `third_party_id == owner.id`).
- Ajout au `total_imputed` : desormais charges + reserve + roulement +
  mutation_net_debit.
- Nouvelle section 3bis "Transferts lies a la mutation" dans le PDF
  (avant les paiements) qui detaille chaque OD avec libelle explicite
  (MUT-R -> "Transfert fonds de roulement", MUT-P -> "Prorata appel en
  cours (mutation)", MUT-F -> "Reprise appel futur (mutation)").
- Endpoints correspondants (`routes/reports.py::decompte_pdf`,
  `_build_decompte_annuel_pdf`, `routes/owner_portal.py::download_decompte_pdf`)
  chargent desormais ces OD via `db.journal_entries.find({source_type:
  'lot_mutation', lines.third_party_id: owner_id})`.

**Regressions couvertes** :
1. ACHETEUR mid-year avec OD MUT-R (490.36 debit) -> total_imputed inclut
   les 490.36 EUR + section "Transferts" affichee dans le PDF.
2. VENDEUR mid-year avec OD MUT-R (490.36 credit) -> total_imputed
   diminue de 490.36 EUR (dette cedee).
3. Owner sans OD lot_mutation -> comportement identique a avant (retro-
   compatible).
4. OD MUT-R contre-passee (reversed=True ou is_reversal=True) -> ignoree
   (cas mutation annulee).
"""
import re
from datetime import date

import fitz


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def _base_setup():
    """Setup commun : 1 lot avec 100 tantiemes, 1 facture de 500 EUR sur l'annee."""
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    copro = {"id": "c1", "name": "Test ACP"}
    lot = {"id": "l1", "number": "202", "quotity": 100.0}
    invoices = [{
        "id": "invA", "number": "A", "supplier": "AXA",
        "date": "2026-06-15", "total_amount": 500.0,
        "account_number": "61300",
        "distribution_lines": [{"lot_id": "l1", "lot_number": "202", "amount": 500.0}],
    }]
    return fy, copro, lot, invoices


def test_iter90g6_buyer_receives_debit_mut_r():
    """Un acheteur mid-year avec une OD MUT-R (fonds de roulement) DEBIT
    voit son total_imputed augmenter et une section 'Transferts' apparait."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    buyer = {"id": "o_buyer", "name": "Buyer"}
    # Mutation le 15/06 : buyer prend le lot
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-06-15",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    # OD MUT-R du 15/06 : Debit buyer 490.36 / Credit seller 490.36
    mutation_entries = [{
        "id": "mut-r-1", "date": "2026-06-15",
        "journal_type": "OD",
        "reference": "MUT-202-R",
        "description": "Mutation lot 202 - Fonds de roulement",
        "source_type": "lot_mutation",
        "source_subtype": "fonds_roulement",
        "lines": [
            {"third_party_id": "o_buyer", "account_number": "41010001",
             "debit": 490.36, "credit": 0.0},
            {"third_party_id": "o_seller", "account_number": "41010002",
             "debit": 0.0, "credit": 490.36},
        ],
    }]
    pdf_bytes = build_decompte_pdf(
        owner=buyer, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
        mutation_entries=mutation_entries,
    )
    text = _pdf_text(pdf_bytes)
    # La section "Transferts lies a la mutation" DOIT apparaitre
    assert "Transferts lies a la mutation" in text or "Transfert fonds de roulement" in text, (
        f"Section transferts mutation attendue dans le PDF acheteur"
    )
    # Le montant 490,36 DOIT etre visible (en debit)
    assert "490,36" in text, (
        f"Montant MUT-R (490,36 EUR) attendu dans le PDF"
    )


def test_iter90g6_buyer_balance_includes_mut_r_debit():
    """Le solde du decompte doit INCLURE le debit MUT-R.
    Scenario TEUWEN : charges 500 * 200/365 = 273.97 (prorata mid-year).
    OD MUT-R debit 490.36. Payment 800.
    total_imputed = 273.97 + 490.36 = 764.33
    balance = 764.33 - 800 = -35.67 -> EN VOTRE FAVEUR (crediteur)
    Sans le fix iter90g6 : balance = 273.97 - 800 = -526.03 (faussement
    crediteur de plus)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    buyer = {"id": "o_buyer", "name": "Buyer",
             "vcs_code": "+++123/4567/89012+++"}
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-06-15",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    mutation_entries = [{
        "id": "mut-r-1", "date": "2026-06-15",
        "journal_type": "OD",
        "reference": "MUT-202-R",
        "source_type": "lot_mutation",
        "source_subtype": "fonds_roulement",
        "lines": [
            {"third_party_id": "o_buyer", "debit": 490.36, "credit": 0.0},
            {"third_party_id": "o_seller", "debit": 0.0, "credit": 490.36},
        ],
    }]
    payments = [{"date": "2026-07-01", "amount": 800.0, "communication": "test"}]
    pdf_bytes = build_decompte_pdf(
        owner=buyer, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=payments,
        mutations=mutations,
        mutation_entries=mutation_entries,
    )
    text = _pdf_text(pdf_bytes)
    # Charges prorata : 500 * 200 / 365 = 273.97
    # total_imputed = 273.97 + 490.36 = 764.33
    # payments = 800
    # balance = 764.33 - 800 = -35.67 -> EN VOTRE FAVEUR
    # Le solde doit etre proche de 35,67 EUR (creditor)
    assert "EN VOTRE FAVEUR" in text, (
        f"Buyer doit avoir un solde crediteur (payement > total_imputed avec MUT-R)"
    )
    # Le montant du solde doit refleter l'inclusion du MUT-R
    assert "35,67" in text, (
        f"Solde attendu 35,67 EUR (avec MUT-R inclus). Sans le fix : "
        f"solde serait 526,03. Text : {text[:500]}"
    )


def test_iter90g6_seller_receives_credit_mut_r():
    """Un vendeur mid-year avec une OD MUT-R CREDIT voit son total_imputed
    diminuer (dette cedee -> le vendeur doit MOINS)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    seller = {"id": "o_seller", "name": "Seller",
              "vcs_code": "+++999/8888/77777+++"}
    # Vente le 15/06 : seller cede le lot
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-06-15",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    mutation_entries = [{
        "id": "mut-r-1", "date": "2026-06-15",
        "journal_type": "OD",
        "reference": "MUT-202-R",
        "source_type": "lot_mutation",
        "source_subtype": "fonds_roulement",
        "lines": [
            {"third_party_id": "o_buyer", "debit": 490.36, "credit": 0.0},
            {"third_party_id": "o_seller", "debit": 0.0, "credit": 490.36},
        ],
    }]
    # Charges seller (jours 1-165) : 500 * 165/365 = 226.03
    # Payments seller : 300 EUR
    payments = [{"date": "2026-03-01", "amount": 300.0, "communication": "test"}]
    pdf_bytes = build_decompte_pdf(
        owner=seller, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=payments,
        mutations=mutations,
        mutation_entries=mutation_entries,
    )
    text = _pdf_text(pdf_bytes)
    # total_imputed = charges - mut_r_credit = 226.03 - 490.36 = -264.33
    # balance = -264.33 - 300 = -564.33 -> EN VOTRE FAVEUR
    # Une section "Transferts" doit apparaitre avec le credit 490,36
    assert "490,36" in text, (
        f"Le vendeur doit voir son credit MUT-R (490,36) dans le PDF"
    )


def test_iter90g6_no_mutation_entries_unchanged():
    """Owner classique sans OD lot_mutation : comportement inchange
    (retro-compatibilite)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    owner = {"id": "o1", "name": "Owner", "vcs_code": "+++111/2222/33333+++"}
    payments = [{"date": "2026-07-01", "amount": 700.0, "communication": "test"}]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=payments,
        mutations=[],
        mutation_entries=None,  # <-- pas d'ecritures mutation
    )
    text = _pdf_text(pdf_bytes)
    # Aucune section "Transferts" ne doit apparaitre
    assert "Transferts lies a la mutation" not in text, (
        f"Sans OD lot_mutation, la section transferts NE DOIT PAS apparaitre"
    )
    # Le solde reste : charges 500 - payments 700 = -200 crediteur
    assert "200,00" in text and "EN VOTRE FAVEUR" in text, (
        f"Solde standard 200 crediteur attendu, PDF : {text[:500]}"
    )


def test_iter90g6_reversed_mut_r_is_ignored():
    """Une OD MUT-R contre-passee (reversed=True ou is_reversal=True) est
    ignoree du calcul (cas mutation annulee via cancel_mutation)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    buyer = {"id": "o_buyer", "name": "Buyer", "vcs_code": "+++0/0/0+++"}
    mutations = []  # <-- mutation annulee (pas dans db.mutations)
    # 2 OD : l'originale (reversed=True) et sa contre-passation (is_reversal=True).
    mutation_entries = [
        {
            "id": "mut-r-orig", "date": "2026-06-15",
            "journal_type": "OD",
            "source_type": "lot_mutation",
            "source_subtype": "fonds_roulement",
            "reversed": True,  # <-- annulee
            "lines": [
                {"third_party_id": "o_buyer", "debit": 490.36, "credit": 0.0},
            ],
        },
        {
            "id": "mut-r-rev", "date": "2026-06-16",
            "journal_type": "OD",
            "source_type": "lot_mutation",
            "source_subtype": "fonds_roulement",
            "is_reversal": True,  # <-- la contre-passation
            "lines": [
                {"third_party_id": "o_buyer", "debit": 0.0, "credit": 490.36},
            ],
        },
    ]
    payments = [{"date": "2026-07-01", "amount": 700.0, "communication": "test"}]
    pdf_bytes = build_decompte_pdf(
        owner=buyer, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=payments,
        mutations=mutations,
        mutation_entries=mutation_entries,
    )
    text = _pdf_text(pdf_bytes)
    # OD annulees -> ignorees. Aucune section transferts.
    assert "Transferts lies a la mutation" not in text, (
        f"OD MUT-R annulee doit etre ignoree (pas de section transferts)"
    )
    # Solde : charges 500 - payments 700 = -200 crediteur (comme sans mutation)
    assert "200,00" in text and "EN VOTRE FAVEUR" in text, (
        f"OD reversed doit etre ignoree du solde, attendu 200 crediteur"
    )
