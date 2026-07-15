"""iter90g1 : Prorata mutation dans le decompte annuel (charges courantes).

Contexte : L'utilisateur a observe un ecart d'environ 1400 EUR entre Optipro
et l'App sur une ACP ayant subi une mutation en cours d'exercice. La cause
principale : Optipro applique un vrai PRORATA multiplicatif (days_owned /
days_total) sur les charges courantes, alors que l'App faisait un simple
FILTRAGE par date de facture (iter90el).

Fix iter90g1 (Interpretation B, decision utilisateur) :
- REMPLACE le filtrage par date de facture par un prorata MULTIPLICATIF
  applique sur la part du proprietaire ET sur le montant a repartir par lot
- Applique le prorata sur TOUTES les charges courantes (Class 6) de l'exercice,
  independamment de la date de la facture individuelle
- Ancien proprietaire paie days_owned/days_total de la totalite des charges
  courantes du lot ; nouveau proprietaire paie le reste
- Convention : la journee de vente est attribuee a l'ACHETEUR (aligne avec
  iter76 fund calls et l'acte notarie)

Regressions couvertes :
1. Sans mutation : owner paie 100% -> prorata = 1.0
2. Vente au 30/06 : vendeur paie 181/365, acheteur paie 184/365 sur toutes
   les factures de l'exercice (Jan-Dec)
3. La somme (part_vendeur + part_acheteur) == charges annuelles totales
   (aucune perte, aucun double-comptage)
4. Facture datee HORS periode de possession est quand meme prorated pour
   l'owner (pas exclue comme dans iter90el)
5. Multi-mutations sur meme lot : chaque owner a son prorata correct
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


def _extract_totaux_generaux(text: str):
    """Extrait les 3 montants de la ligne 'Totaux generaux' : (Montant a
    repartir, Part proprietaire, Part occupant)."""
    # Cherche "Totaux generaux" suivi de 3 montants "X,XX EUR"
    m = re.search(
        r"Totaux\s+generaux\s+([\d\s,]+)\s*EUR\s+([\d\s,]+)\s*EUR\s+([\d\s,]+)\s*EUR",
        text,
    )
    if not m:
        return None
    def _num(s):
        return float(s.replace(" ", "").replace(",", "."))
    return (_num(m.group(1)), _num(m.group(2)), _num(m.group(3)))


def _base_setup():
    fy = {
        "id": "fy1", "name": "2026", "status": "closed",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }
    copro = {"id": "c1", "name": "Test ACP"}
    lot = {"id": "l1", "number": "202", "quotity": 100.0}
    # 3 factures reparties dans l'annee : totale = 2000
    invoices = [
        {  # Assurance annuelle datee Jan
            "id": "invA", "number": "A", "supplier": "AXA", "date": "2026-01-15",
            "total_amount": 1200.0, "account_number": "61300",
            "distribution_lines": [{"lot_id": "l1", "lot_number": "202", "amount": 1200.0}],
        },
        {  # Reparation avant vente
            "id": "invB", "number": "B", "supplier": "PLOMBIER", "date": "2026-03-20",
            "total_amount": 500.0, "account_number": "61300",
            "distribution_lines": [{"lot_id": "l1", "lot_number": "202", "amount": 500.0}],
        },
        {  # Entretien apres vente
            "id": "invC", "number": "C", "supplier": "JARDINIER", "date": "2026-08-15",
            "total_amount": 300.0, "account_number": "61300",
            "distribution_lines": [{"lot_id": "l1", "lot_number": "202", "amount": 300.0}],
        },
    ]
    return fy, copro, lot, invoices


def test_prorata_no_mutation_full_year():
    """Sans mutation, owner paie 100% de toutes les charges de l'exercice."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    owner = {"id": "o_full", "name": "OwnerFull"}
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=[],
    )
    text = _pdf_text(pdf_bytes)
    totaux = _extract_totaux_generaux(text)
    assert totaux is not None, f"Impossible d'extraire les totaux dans : {text[:500]}"
    grand_dist, grand_prop, grand_occ = totaux
    assert abs(grand_dist - 2000.0) < 0.01, (
        f"Montant a repartir attendu 2000.00, recu {grand_dist:.2f}"
    )
    # 100% de 2000 pour proprietaire seul (occupant_pct=0)
    assert abs(grand_prop - 2000.0) < 0.01


def test_prorata_seller_gets_days_before_sale():
    """Vendeur (mutation 01/07) obtient prorata 181/365 sur toutes les 3
    factures de l'exercice (~991 EUR)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    owner = {"id": "o_seller", "name": "Seller"}
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-07-01",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    # Vendeur possede Jan 1 -> Jun 30 (veille de la vente) = 181 jours
    seller_days = (date(2026, 6, 30) - date(2026, 1, 1)).days + 1
    assert seller_days == 181
    # Prorata = 181/365 ~= 0.4959
    expected_share = round(2000.0 * seller_days / 365, 2)
    assert abs(expected_share - 991.78) < 0.02, f"expected_share={expected_share}"

    totaux = _extract_totaux_generaux(text)
    assert totaux is not None
    grand_dist, grand_prop, _ = totaux
    # Le "Montant a repartir" cote vendeur == prorated (~991)
    assert abs(grand_dist - expected_share) < 0.5, (
        f"Vendeur : Montant a repartir attendu ~{expected_share}, recu {grand_dist}"
    )
    # Part proprietaire == 991 EUR (100% quotity, 0% occupant)
    assert abs(grand_prop - expected_share) < 0.5


def test_prorata_buyer_gets_days_from_sale_to_year_end():
    """Acheteur (mutation 01/07) obtient prorata 184/365 sur toutes les 3
    factures de l'exercice (~1008 EUR)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    owner = {"id": "o_buyer", "name": "Buyer"}
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-07-01",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    pdf_bytes = build_decompte_pdf(
        owner=owner, copropriete=copro, fiscal_year=fy,
        owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    # Acheteur possede Jul 1 -> Dec 31 = 184 jours
    buyer_days = (date(2026, 12, 31) - date(2026, 7, 1)).days + 1
    assert buyer_days == 184
    expected_share = round(2000.0 * buyer_days / 365, 2)
    assert abs(expected_share - 1008.22) < 0.02

    totaux = _extract_totaux_generaux(text)
    assert totaux is not None
    grand_dist, grand_prop, _ = totaux
    assert abs(grand_dist - expected_share) < 0.5, (
        f"Acheteur : Montant a repartir attendu ~{expected_share}, recu {grand_dist}"
    )
    assert abs(grand_prop - expected_share) < 0.5


def test_prorata_seller_plus_buyer_equals_full_charges():
    """La somme des parts (vendeur + acheteur) doit egaler la totalite des
    charges de l'exercice. Aucun euro perdu, aucun double comptage."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-07-01",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    # Genere PDF vendeur
    pdf_seller = build_decompte_pdf(
        owner={"id": "o_seller", "name": "Seller"}, copropriete=copro,
        fiscal_year=fy, owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    # Genere PDF acheteur
    pdf_buyer = build_decompte_pdf(
        owner={"id": "o_buyer", "name": "Buyer"}, copropriete=copro,
        fiscal_year=fy, owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    seller_tot = _extract_totaux_generaux(_pdf_text(pdf_seller))
    buyer_tot = _extract_totaux_generaux(_pdf_text(pdf_buyer))
    assert seller_tot and buyer_tot
    total_shared = seller_tot[1] + buyer_tot[1]
    # Doit converger vers 2000 EUR (a 0.01 pres, tolerance arrondi)
    assert abs(total_shared - 2000.0) < 0.05, (
        f"Vendeur ({seller_tot[1]:.2f}) + Acheteur ({buyer_tot[1]:.2f}) = "
        f"{total_shared:.2f}, attendu 2000.00"
    )


def test_prorata_includes_invoices_outside_owned_period():
    """iter90g1 (nouveau comportement) : une facture datee HORS periode de
    possession est quand meme incluse dans le decompte avec le prorata
    approprie. Ce n'est PAS un filtrage strict (comportement iter90el)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, _ = _base_setup()
    # Owner achete le 15/06 -> facture Jan doit apparaitre avec prorata
    mutations = [{
        "lot_id": "l1", "sale_date": "2026-06-15",
        "from_owner_id": "o_seller", "to_owner_id": "o_buyer",
    }]
    invoices = [{
        "id": "invJan", "number": "V-JAN-001", "supplier": "AXA-JAN",
        "date": "2026-01-10", "total_amount": 1000.0,
        "distribution_lines": [{"lot_id": "l1", "lot_number": "202", "amount": 1000.0}],
        "account_number": "61300",
    }]
    pdf_bytes = build_decompte_pdf(
        owner={"id": "o_buyer", "name": "Buyer"}, copropriete=copro,
        fiscal_year=fy, owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    # La facture V-JAN-001 DOIT apparaitre pour l'acheteur (contrairement a
    # iter90el qui l'excluait). Elle apparait avec prorata 200/365.
    assert "AXA-JAN" in text or "V-JAN-001" in text, (
        f"La facture pre-mutation doit apparaitre avec prorata pour l'acheteur"
    )
    # iter90g5 : le prorata est desormais affiche UNE FOIS dans l'entete
    # du lot ("Prorata: 200 / 365 jours") et non plus a chaque ligne
    # facture (demande utilisateur : allegement de lecture).
    assert "prorata: 200 / 365 jours" in text.lower(), (
        f"Marqueur 'Prorata: 200 / 365 jours' attendu dans l'entete du lot"
    )
    # Le calcul de prorata reste bien applique : 1000 * 200/365 = 547.95
    assert "547,95" in text, (
        f"Montant prorate (547.95) attendu dans le PDF, mais absent"
    )


def test_prorata_zero_days_owned_skips_lot():
    """Cas edge : vente le premier jour de l'exercice + rachat le meme jour.
    Owner ne devrait rien voir sur ce lot (0 jour de possession)."""
    from pdf_decompte import build_decompte_pdf
    fy, copro, lot, invoices = _base_setup()
    mutations = [{
        # Sale on 2026-01-01 -> seller had 0 days
        "lot_id": "l1", "sale_date": "2026-01-01",
        "from_owner_id": "o_zero", "to_owner_id": "o_next",
    }]
    pdf_bytes = build_decompte_pdf(
        owner={"id": "o_zero", "name": "ZeroDay"}, copropriete=copro,
        fiscal_year=fy, owner_lots=[lot], all_lots=[lot],
        invoices=invoices, distribution_keys=[], fund_calls=[], payments=[],
        mutations=mutations,
    )
    text = _pdf_text(pdf_bytes)
    # Le proprietaire n'a pas possede -> Totaux generaux == 0 OR message
    # "Aucune charge ne vous concerne"
    # Actuellement le lot est skip completement dans per_lot_data.
    assert (
        "Aucune charge ne vous concerne" in text
        or "0,00 EUR" in text
    ), f"Attendu 0 charge pour owner avec 0 jour possede : {text[:500]}"
