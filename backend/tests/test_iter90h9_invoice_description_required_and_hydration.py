"""iter90h9 + iter90ha — Description obligatoire + hydratation lignes multiples.

Contexte utilisateur (Feb 2026) :
  1. "champ description obligatoire dans la creation de facture" (iter90h9)
  2. "ce champ se remet vide une fois une facture editee ca doit reste
     comme ce qui a ete enregistre" (iter90ha)

Fixes :
- iter90h9 : `InvoiceInput.description` a un `field_validator` qui refuse
  toute description vide ou blanche. Message clair (400/422 avec exemple).
- iter90ha : quand une facture est ouverte en edition, chaque ligne dont
  la description est vide en base est pre-remplie avec la description
  generale de la facture. Comme ca ce que le user voit dans le champ =
  ce qui sera reellement sauvegarde (fin de la confusion "placeholder qui
  ressemble a une valeur mais est en fait vide en DB").
- iter90ha : idem pour le bouton "Splitter en plusieurs natures" et
  "Ajouter une ligne" -> pre-remplit avec la description generale.
"""


def test_iter90h9_description_field_validator():
    """`InvoiceInput.description` refuse une chaine vide ou blanche."""
    import sys
    from pydantic import ValidationError
    if "routes.invoices" not in sys.modules:
        __import__("routes.invoices")
    from routes.invoices import InvoiceInput

    valid_payload = {
        "number": "F-VALID", "date": "2026-05-04", "supplier": "Fournisseur",
        "description": "Facture entretien ascenseur 04/05/2026",
        "total_amount": 100.0,
    }
    obj = InvoiceInput(**valid_payload)
    assert obj.description == "Facture entretien ascenseur 04/05/2026"

    # Description vide -> ValidationError
    for bad in ["", "   ", "\t\n"]:
        try:
            InvoiceInput(**{**valid_payload, "description": bad})
            raise AssertionError(f"Description {bad!r} aurait du etre refusee")
        except ValidationError as e:
            msg = str(e)
            assert "Description obligatoire" in msg, f"Mauvais message : {msg}"


def test_iter90h9_description_is_stripped():
    """`InvoiceInput.description` strip les espaces autour (garde uniquement
    le contenu utile)."""
    from routes.invoices import InvoiceInput
    obj = InvoiceInput(
        number="F1", date="2026-05-04", supplier="X",
        description="  Reparation ascenseur  ",
        total_amount=100.0,
    )
    assert obj.description == "Reparation ascenseur"


def test_iter90ha_openedit_hydrates_line_descriptions_from_general():
    """Le code `openEditInvoice` remplit l.description || inv.description
    pour ne plus laisser un champ vide en edition."""
    with open("/app/frontend/src/pages/InvoicesPage.js") as f:
        content = f.read()
    # openEditInvoice hydrate description
    assert "l.description || inv.description || ''" in content
    # Bouton "Splitter" hydrate aussi la description
    assert "iter90ha" in content
    # Le bouton "Ajouter une ligne" propage la description generale
    assert "description: f.description || ''" in content


def test_iter90h9_frontend_marks_description_required():
    """Label 'Description *' avec l'etoile + validation UI dans saveInvoice."""
    with open("/app/frontend/src/pages/InvoicesPage.js") as f:
        content = f.read()
    assert 'Description *' in content
    assert 'data-testid="inv-description"' in content
    # Validation UI
    assert "Description obligatoire" in content or "description obligatoire" in content.lower()
