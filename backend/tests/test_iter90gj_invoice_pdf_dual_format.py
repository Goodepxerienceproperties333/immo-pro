"""iter90gj : le parser `parse_supplier_invoice_list` gere maintenant DEUX
formats de PDF Optipro :

1. FORMAT DETAILLE ("Liste des depenses") : 1 header + N allocations par
   ligne comptable. Parsing texte via regex.

2. FORMAT SIMPLE ("Factures fournisseurs" tabulaire) : 1 ligne par
   facture, sans allocations. Parsing table via pdfplumber.
   Colonnes : DATE FACTURE | DATE ECHEANCE | REFERENCE INTERNE |
   REFERENCE EXTERNE | FOURNISSEUR | LIBELLE | MONTANT HT | MONTANT TVAC

**Ticket utilisateur** : "j'ai mis un PDF mais il foire" (le nouveau PDF
tabulaire retournait 0 factures avant iter90gj).
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from import_wizard.pdf_supplier_invoice_list import parse_supplier_invoice_list

# PDFs reels du syndic (opportuniste : skip si asset inaccessible)
TABULAR_PDF = ("https://customer-assets-jt897jd0.emergentagent.net/"
               "job_c983d589-579c-4dc1-9f75-c76209275508/artifacts/"
               "dd96p4xg_Factures%20fournisseurs.pdf")
DETAILED_PDF = ("https://customer-assets-jt897jd0.emergentagent.net/"
                "job_c983d589-579c-4dc1-9f75-c76209275508/artifacts/"
                "4txlp0om_Factures%20fournisseurs%20%281%29.pdf")


def _fetch(url):
    try:
        return urllib.request.urlopen(url, timeout=15).read()
    except Exception:
        return None


def test_tabular_format_parses_all_invoices():
    """Le nouveau PDF tabulaire "Factures fournisseurs" (ACP Maria) est
    correctement parse : 6 factures avec supplier_code, montants HT/TVAC.
    """
    raw = _fetch(TABULAR_PDF)
    if raw is None:
        print("SKIP : asset inaccessible")
        return
    result = parse_supplier_invoice_list(raw)
    assert len(result) == 6, f"expected 6 invoices, got {len(result)}: {[r['supplier_name'] for r in result]}"

    # Verifie les champs cles sur toutes les lignes
    for inv in result:
        assert inv["date"], f"date manquante : {inv}"
        assert inv["supplier_code"].startswith("F"), f"supplier_code invalide : {inv['supplier_code']}"
        assert inv["internal_ref"], f"internal_ref manquante : {inv}"
        assert isinstance(inv["ht_amount"], float)
        assert isinstance(inv["tvac_amount"], float)

    # Verifie une facture specifique (2 930,00 EUR Sneyers Philippe)
    sneyers = next((r for r in result if r["supplier_code"] == "F1115"), None)
    assert sneyers is not None, "Facture Sneyers F1115 non trouvee"
    assert sneyers["ht_amount"] == 2930.00
    assert sneyers["tvac_amount"] == 3545.30

    # Verifie une facture avec montant negatif (Engie -53,79)
    engie = next((r for r in result if r["supplier_code"] == "F0110"), None)
    assert engie is not None
    assert engie["ht_amount"] == -53.79
    assert engie["tvac_amount"] == -57.03


def test_detailed_format_still_works_regression():
    """Regression : le PDF detaille "Liste des depenses" continue de
    fonctionner (55 lignes / 42 factures distinctes).
    """
    raw = _fetch(DETAILED_PDF)
    if raw is None:
        print("SKIP : asset inaccessible")
        return
    result = parse_supplier_invoice_list(raw)
    assert len(result) == 42, f"expected 42 invoices, got {len(result)}"
    # Verifie que les allocations sont bien la
    with_allocs = [r for r in result if r["allocations"]]
    assert len(with_allocs) > 0, "aucune allocation trouvee (regression parser texte)"


if __name__ == "__main__":
    test_tabular_format_parses_all_invoices()
    print("OK test_tabular_format_parses_all_invoices")
    test_detailed_format_still_works_regression()
    print("OK test_detailed_format_still_works_regression")
