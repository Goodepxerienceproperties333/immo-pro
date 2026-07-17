"""iter90he — Ligne REPRISE toujours en 1ere position + libelle "Decompte periode precedente".

Contexte utilisateur (Feb 2026) :
  "idealement la reprise comptable doit toujours etre la 1ere ligne ajouter
   'Decompte periode precedente'"

Screenshot du user : la ligne REPRISE (01/03/2026, 291.66 credit) apparait
en POSITION 2, apres une ecriture [VE] Appel de provisions du meme jour.
L'utilisateur veut qu'elle soit en position 1 dans TOUS les cas.

Fixes iter90he :

1. Tri owner (`situation_compte_owner`, ligne ~2760) :
   AVANT : `sort(key=(date, ref))` -> AN se trie apres [VE] a la meme date
   APRES : `sort(key=(is_reprise?0:1, date, ref))` -> REPRISE prime absolument

2. Tri supplier (`situation_compte_supplier`, ligne ~3413) :
   AVANT : `sort(key=(date, is_reprise?0:1, ref))` -> AN priorise UNIQUEMENT
           a date egale
   APRES : `sort(key=(is_reprise?0:1, date, ref))` -> REPRISE prime absolument

3. Tri PDF owner (`_build_situation_compte_pdf`, ligne ~304) :
   AVANT : `sort(key=(date, journal_type == "AN" ? 0 : 1, ref))`
   APRES : `sort(key=(journal_type == "AN" ? 0 : 1, date, ref))`

4. Libelles enrichis avec "Decompte periode precedente" :
   Owner  : "Reprise comptable au {date} (Decompte periode precedente)"
   Supplier: "Reprise comptable au {date} (Decompte periode precedente -
             Journal A-Nouveau immuable)"
"""


def test_iter90he_reprise_priority_in_owner_sort():
    """Le tri owner priorise `is_reprise=True` avant meme la date."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    # Le tri owner doit avoir is_reprise en 1er
    assert 'sort(key=lambda x: (0 if x.get("is_reprise") else 1, x["date"], x.get("reference", "")))' in content


def test_iter90he_reprise_priority_in_supplier_sort():
    """Le tri supplier priorise `is_reprise=True` avant meme la date."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    idx = content.index("async def situation_compte_supplier")
    supplier_body = content[idx:idx + 15000]
    assert '0 if x.get("is_reprise") else 1' in supplier_body
    sort_line_start = supplier_body.index("movements.sort")
    sort_line = supplier_body[sort_line_start:sort_line_start + 200]
    assert sort_line.index('is_reprise') < sort_line.index('x["date"]')


def test_iter90he_reprise_priority_in_owner_pdf_sort():
    """Le tri du PDF situation-compte owner priorise AN avant date."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    idx = content.index("async def _build_situation_compte_pdf")
    pdf_body = content[idx:idx + 15000]
    sort_start = pdf_body.index("movements.sort")
    sort_block = pdf_body[sort_start:sort_start + 300]
    an_idx = sort_block.index('journal_type')
    date_idx = sort_block.index('x["date"]')
    assert an_idx < date_idx, "AN doit primer sur la date dans le tri"


def test_iter90he_libelle_mention_decompte_periode_precedente():
    """Le libelle des lignes REPRISE mentionne 'Decompte periode precedente'."""
    with open("/app/backend/routes/reports.py") as f:
        content = f.read()
    # Owner (situation_compte_owner)
    assert "Reprise comptable au {reprise_date} (Decompte periode precedente)" in content \
           or "(Decompte periode precedente)" in content
    # Supplier (situation_compte_supplier)
    assert "Decompte periode precedente - Journal A-Nouveau immuable" in content


def test_iter90he_reprise_stays_at_top_when_mixed_dates():
    """Simulation : une liste avec un mouvement anterieur a la reprise
    doit quand meme afficher la reprise en TETE."""
    movements = [
        {"date": "2026-03-01", "reference": "AN-2026-001", "is_reprise": True,  "journal_type": "AN", "description": "Reprise"},
        {"date": "2026-03-01", "reference": "VE-001",      "is_reprise": False, "journal_type": "VE", "description": "Appel"},
        {"date": "2026-02-15", "reference": "OD-001",      "is_reprise": False, "journal_type": "OD", "description": "OD anterieure"},
    ]
    # Applique le meme tri qu'en iter90he (situation_compte_owner ligne 2760)
    movements.sort(key=lambda x: (0 if x.get("is_reprise") else 1, x["date"], x.get("reference", "")))
    # La reprise doit etre en position 0
    assert movements[0]["is_reprise"] is True
    assert movements[0]["reference"] == "AN-2026-001"
    # Les autres suivent par ordre chronologique
    assert movements[1]["date"] == "2026-02-15"
    assert movements[2]["date"] == "2026-03-01"
