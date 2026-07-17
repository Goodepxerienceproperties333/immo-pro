"""iter90hd — PDF situation de compte fournisseur + REPRISE deja en place.

Contexte utilisateur (Feb 2026) :
  "il faut aussi pouvoir du cote fournisseur balance des tiers avoir la
   possibilite de telecharger la balance des tiers par ligne de fournisseur
   comme pour les proprietaires"
  et le PDF Balance de tiers detaillee des fournisseurs doit inclure la
  ligne REPRISE quand un fournisseur avait un solde crediteur a la cloture
  de l'exercice precedent (SRL Finlead 639.69, AG Insurance 1132.92).

Analyse :
- Le backend `situation_compte_supplier` (GET /balance-tiers/suppliers/{id})
  agrege deja les ecritures AN en une ligne "REPRISE" en tete (iter90gu).
  Donc le probleme "reprise absente du PDF" est resolu automatiquement
  du moment qu'on utilise cet endpoint via le PDF.
- Pas de bouton PDF cote fournisseur -> l'utilisateur ne pouvait pas
  telecharger la situation d'un fournisseur specifique.

Fix iter90hd :
- Nouvel endpoint `GET /api/reports/balance-tiers/suppliers/{supplier_id}/pdf`
  qui appelle `situation_compte_supplier` puis rend un PDF via
  `build_balance_tiers_detailed_pdf(suppliers_detail=[detail])`.
- Frontend : nouveau bouton Download a cote du bouton Eye dans la table
  fournisseurs. La ligne REPRISE apparait naturellement dans le PDF.
"""


def test_iter90hd_supplier_pdf_endpoint_registered():
    """`GET /api/reports/balance-tiers/suppliers/{id}/pdf` existe et importe
    les modules PDF necessaires."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    assert '@router.get("/balance-tiers/suppliers/{supplier_id}/pdf")' in content
    assert "async def balance_tiers_supplier_pdf" in content
    assert "build_balance_tiers_detailed_pdf" in content
    # Reutilise la fonction situation_compte_supplier qui contient deja la REPRISE
    assert "situation_compte_supplier" in content
    # Filename explicite
    assert "situation-fournisseur-" in content


def test_iter90hd_frontend_has_download_button():
    """La page BalanceTiersPage expose un bouton Download par fournisseur."""
    with open("/app/frontend/src/pages/BalanceTiersPage.js") as f:
        content = f.read()
    # Le nouveau bouton Download
    assert "downloadSupplierPdf" in content
    assert 'data-testid={`pdf-supplier-' in content
    # Utilise l'endpoint backend
    assert "/reports/balance-tiers/suppliers/${supplierId}/pdf" in content or \
           "/reports/balance-tiers/suppliers/" in content
    # responseType blob
    assert "responseType: 'blob'" in content


def test_iter90hd_reprise_line_in_supplier_detail():
    """Le endpoint situation_compte_supplier genere une ligne 'REPRISE'
    quand des ecritures A-Nouveau existent pour ce fournisseur (iter90gu).
    C'est la meme donnee qui alimente le PDF -> la REPRISE apparait dans
    le PDF automatiquement."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    idx = content.index('async def situation_compte_supplier')
    supplier_detail_body = content[idx:idx + 15000]
    assert 'reprise_ref = "REPRISE"' in supplier_detail_body
    assert 'reprise_dates' in supplier_detail_body
    assert 'reprise_desc' in supplier_detail_body
