"""iter90h1 — OwnersPage.handleDelete doit avoir un catch (409 lisible).

Contexte utilisateur (Feb 2026) :
  "erreur en supprimant un proprietaire" -> screenshot d'un overlay dev
  React rouge "Uncaught runtime error: Request failed with status code 409".

Root cause :
  Le backend renvoie 409 avec un garde-fou legitime :
    "Suppression refusee (securite comptable) : ce proprietaire est reference
     par N ecriture(s), M lot(s), ..."
  Le handleDelete etait un one-liner sans try/catch :
    `await api.delete(`/owners/${id}`)` -> Axios throw -> uncaught rejection
    -> react-scripts affiche l'overlay rouge en plein ecran.

Fix iter90h1 (frontend uniquement) :
  Try/catch avec toast rouge affichant le message detaille du backend.
  L'utilisateur voit maintenant "ce proprietaire est reference par 5
  ecritures comptables, 2 lots..." au lieu d'un ecran d'erreur.
"""


def test_iter90h1_owners_page_handle_delete_has_try_catch():
    """handleDelete doit envelopper l'appel API dans try/catch et afficher
    un toast avec le detail backend (particulierement le 409 anti-orphelin).
    """
    with open("/app/frontend/src/pages/OwnersPage.js") as f:
        content = f.read()
    # Detecter la fonction handleDelete
    assert "const handleDelete" in content
    # Extraire le corps entre le debut de handleDelete et la prochaine
    # declaration `const ` a la meme indentation (approximation robuste).
    idx_start = content.index("const handleDelete")
    idx_end = content.index("\n  const ", idx_start + 1)
    body = content[idx_start:idx_end]
    # Doit avoir try + catch + un toast d'erreur
    assert "try {" in body, f"handleDelete doit avoir un try/catch : {body[:200]}"
    assert "catch" in body, f"handleDelete doit avoir un catch : {body[:200]}"
    assert "toast.error" in body, f"handleDelete doit afficher un toast d'erreur : {body[:200]}"
    # Le message doit venir du backend (err.response.data.detail)
    assert "response?.data?.detail" in body or "response?.data" in body


def test_iter90h1_no_one_liner_await_api_delete_without_catch_in_pages():
    """Regression check : aucune page ne doit garder un pattern
    `async (...) => { ... await api.delete(...) ... };` sans try/catch."""
    import os, re
    pages_dir = "/app/frontend/src/pages"
    suspicious = []
    for root, _, files in os.walk(pages_dir):
        for f in files:
            if not f.endswith(".js"):
                continue
            fp = os.path.join(root, f)
            content = open(fp).read()
            # Match pattern : `<handlerName> = async (...) => { <body without try> };`
            for m in re.finditer(
                r'const\s+(\w+)\s*=\s*async\s*\([^)]*\)\s*=>\s*\{([^{}]{0,500})\}\s*;',
                content,
            ):
                body = m.group(2)
                if ("await api.delete" in body or "await api.post" in body) \
                   and "try" not in body and "catch" not in body:
                    suspicious.append((fp, m.group(1)))
    assert not suspicious, (
        "Handler(s) sans try/catch trouve(s) - risque 'Uncaught runtime error' :\n"
        + "\n".join(f"  - {fp} :: {fn}" for fp, fn in suspicious)
    )
