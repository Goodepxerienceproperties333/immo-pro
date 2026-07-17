"""iter90hb + iter90hc — Historique envois email + config self-service MonBureau.

Contexte utilisateur (Feb 2026) :
  "UI page 'Historique envois' (endpoint /sent-log cree plus tot)
   permettre aux syndic de configurer eux memes leurs boites emails
   dans leurs onglet mon bureau"

Fixes livres :

**iter90hb** — Page /communication/history :
  - Nouvelle route React `communication/history` -> `CommunicationHistoryPage`.
  - Menu latéral : entree "Historique envois" sous "Communication".
  - Statistiques Envoyes / Erreurs / Dry-run (cards).
  - Table avec sujet, boite, destinataires, statut, detail au clic.
  - Filtres : recherche libre + toggle "Uniquement erreurs" + limit selecteur.
  - Dialog detail : erreur Microsoft integrale + lien vers /mon-bureau.

**iter90hc** — Section Config email dans /mon-bureau :
  - Le syndic peut configurer lui-meme sa boite email (Graph OU SMTP).
  - Labels bilingues FR/EN (comme iter90h4 admin).
  - Statut "Secret configure" (evite l'incident iter90h5).
  - Badge "Verifiee" apres test reussi.
  - Verrou : si admin a verrouille la config, tous les champs sont disabled
    + bandeau rouge + boutons Save/Test grises.
  - Bouton "Tester la configuration" -> Dialog from/to + envoi via
    POST /api/syndic-config/me/test-email.
"""


def test_iter90hb_communication_history_page_exists():
    """Fichier CommunicationHistoryPage.js exposant les elements requis."""
    with open("/app/frontend/src/pages/CommunicationHistoryPage.js") as f:
        content = f.read()
    assert "CommunicationHistoryPage" in content
    # Utilise l'endpoint sent-log
    assert "/communication/sent-log" in content
    # Compteurs + table + detail dialog
    for tid in [
        "communication-history-page",
        "stats-sent",
        "stats-failed",
        "stats-dry-run",
        "history-search",
        "history-filter-failed",
        "history-limit",
        "history-detail-dialog",
    ]:
        assert f'data-testid="{tid}"' in content, f"Missing test-id: {tid}"
    # Filtre only_failed
    assert "only_failed" in content


def test_iter90hb_route_and_menu_registered():
    """Route React + entree menu latéral pour l'historique."""
    with open("/app/frontend/src/App.js") as f:
        app_content = f.read()
    assert 'import CommunicationHistoryPage from "@/pages/CommunicationHistoryPage"' in app_content
    assert 'path="communication/history"' in app_content

    with open("/app/frontend/src/components/Layout.js") as f:
        layout_content = f.read()
    assert "'/communication/history'" in layout_content
    assert "Historique envois" in layout_content
    # L'icone Send doit etre importee
    assert ", Send" in layout_content or "Send," in layout_content


def test_iter90hc_mon_bureau_has_email_config_section():
    """MonBureau expose la carte 'Configuration email' avec toutes les
    protections (verrou, secret indicator, badges verifiee)."""
    with open("/app/frontend/src/pages/MonBureauPage.js") as f:
        content = f.read()
    # Section email presente
    assert "Configuration email (Microsoft Graph / SMTP)" in content
    # Provider select + boutons
    for tid in [
        "mon-bureau-select-provider",
        "mon-bureau-btn-save-email",
        "mon-bureau-btn-test-email",
        "mon-bureau-dialog-test-email",
        "mon-bureau-test-from",
        "mon-bureau-test-to",
        "mon-bureau-test-confirm",
    ]:
        assert f'data-testid="{tid}"' in content, f"Missing test-id: {tid}"
    # Labels bilingues (identique a iter90h4 admin)
    assert "Directory (tenant) ID" in content
    assert "Application (client) ID" in content
    assert "Client Secret (Value)" in content
    # Verrou
    assert "email_config_locked" in content
    assert "mon-bureau-badge-email-locked" in content or "Verrouillee par admin" in content
    # Endpoint self test-email
    assert "/syndic-config/me/test-email" in content
    # Endpoint self save email
    assert "/syndic-config/me/email" in content


def test_iter90hc_lock_disables_edit():
    """Quand cfg.email_config_locked=true, les champs sont disabled et
    le bouton Save est grise."""
    with open("/app/frontend/src/pages/MonBureauPage.js") as f:
        content = f.read()
    # emailLocked flag
    assert "emailLocked" in content
    # Save disabled sur locked
    assert "disabled={savingEmail || emailLocked}" in content
    # Inputs disabled sur locked
    assert "disabled={emailLocked}" in content
    # Bandeau lock visible
    assert "mon-bureau-lock-banner" in content


def test_iter90hb_history_page_shows_failed_error_detail():
    """Le dialog detail affiche error_msg quand status=failed (utile pour
    tracer les erreurs Microsoft Mail.Send / MailboxNotEnabled)."""
    with open("/app/frontend/src/pages/CommunicationHistoryPage.js") as f:
        content = f.read()
    # Affichage conditionnel de error_msg
    assert "detailRow.error_msg" in content
    assert "history-detail-error" in content
    # Message d'aide pour dry-run pointe vers /mon-bureau
    assert "/mon-bureau" in content


def test_iter90hc_backend_self_test_email_endpoint_exists():
    """L'endpoint POST /api/syndic-config/me/test-email existe deja
    et est bien enregistre."""
    with open("/app/backend/routes/syndic_config.py") as f:
        content = f.read()
    assert '@router.post("/syndic-config/me/test-email")' in content
