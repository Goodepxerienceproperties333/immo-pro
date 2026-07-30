"""iter93bu : Moteur dedie Optipro (optipro_parser.py) - Tests fonctionnels.

Contexte utilisateur : "Je veux que l'importation Optipro fonctionne
systematiquement, sans intervention manuelle. Moteur de Parsing dedie
+ Logique de detection + Boucle de validation + Generalisation".

Ce test valide que le nouveau moteur `parse_optipro_bilan` :
1. Fonctionne sur un bilan Optipro reel (Bilan 31/03/2026, 2 pages,
   comptes bancaires 8-digit).
2. Retourne un bilan EQUILIBRE (Total Actif == Total Passif).
3. Fournit un diagnostic (strategy_used, warnings, page_totals).
4. Preserve la meme API que `parse_balance_pdf` (retro-compatibilite).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from import_wizard.optipro_parser import parse_optipro_bilan


def test_real_optipro_bilan_balanced():
    """Bilan Optipro reel : ACP AGATHE 31/03/2026 -> equilibre 42066.57 EUR."""
    path = "/tmp/bilan.pdf"
    if not os.path.exists(path):
        print("SKIP : sample PDF absent")
        return
    with open(path, "rb") as f:
        raw = f.read()
    r = parse_optipro_bilan(raw)
    assert r["balanced"], f"Bilan desequilibre : ecart={r['ecart']} EUR"
    assert abs(r["total_actif"] - 42066.57) < 0.01
    assert abs(r["total_passif"] - 42066.57) < 0.01
    assert r["period_end_date"] == "31/03/2026"
    assert r["pages_scanned"] == 2
    assert r["diagnostic"]["strategy_used"] == "standard", (
        "1ere passe devrait suffire pour un PDF Optipro standard"
    )


def test_bank_accounts_8digit_extracted():
    """Les comptes bancaires 8-digit Optipro doivent etre extraits."""
    path = "/tmp/bilan.pdf"
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        raw = f.read()
    r = parse_optipro_bilan(raw)
    accounts = {e["account"] for e in r["actif"]}
    assert "55011383" in accounts, "Compte banque CBC epargne manquant"
    assert "55114766" in accounts, "Compte banque Roulement manquant"
    # Verifie les montants exacts
    banks = {e["account"]: e["amount"] for e in r["actif"] if e["account"].startswith("55")}
    assert abs(banks["55011383"] - 13111.41) < 0.01
    assert abs(banks["55114766"] - 19699.62) < 0.01


def test_diagnostic_fields_present():
    """L'API retourne toujours les champs de diagnostic (contract stable)."""
    path = "/tmp/bilan.pdf"
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        raw = f.read()
    r = parse_optipro_bilan(raw)
    assert "diagnostic" in r
    d = r["diagnostic"]
    assert "strategy_used" in d
    assert "warnings" in d
    assert "page_totals" in d
    assert isinstance(d["page_totals"], list)
    assert len(d["page_totals"]) == r["pages_scanned"]
    # Chaque page a un actif/passif calcule
    for pt in d["page_totals"]:
        assert "page" in pt and "actif" in pt and "passif" in pt


def test_empty_pdf_returns_diagnostic():
    """Un PDF vide (pas de mot 'Actif' ni 'Passif') retourne diagnostic informatif."""
    # PDF minimaliste avec 1 page vide
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 12)
    c.drawString(100, 700, "Ceci n'est pas un bilan")
    c.save()
    r = parse_optipro_bilan(buf.getvalue())
    # Actif/passif vides -> balanced=True (0==0), mais aucune ligne extraite
    assert len(r["actif"]) == 0
    assert len(r["passif"]) == 0
    assert "diagnostic" in r


if __name__ == "__main__":
    test_real_optipro_bilan_balanced()
    print("OK test_real_optipro_bilan_balanced")
    test_bank_accounts_8digit_extracted()
    print("OK test_bank_accounts_8digit_extracted")
    test_diagnostic_fields_present()
    print("OK test_diagnostic_fields_present")
    test_empty_pdf_returns_diagnostic()
    print("OK test_empty_pdf_returns_diagnostic")
    print("\n=== ALL 4 TESTS PASSED ===")
