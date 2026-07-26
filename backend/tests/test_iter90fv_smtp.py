"""iter90fv - SMTP branch tests for graph_email.py

Covers:
  1. SMTP env vars parsed correctly from /app/backend/.env
  2. send_html_email routes to SMTP (not Graph) with correct MIME headers
  3. Port 465 -> use_tls=True, start_tls=False (SSL implicit)
  4. Port 587 -> use_tls=False, start_tls=True (STARTTLS)
  5. DRY-RUN when MAIL_ENABLED=false and no SMTP host
  6. aiosmtplib error propagates as RuntimeError('SMTP send failed ...')
  7. One real smoke test send to info@nextgecopro.be (loop-back)

Notes:
  - Password contains literal '$$' -> verify no shell expansion.
  - FROM_NAME contains a space -> verify formataddr renders correctly.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure /app/backend on path so we import the same module the server uses
BACKEND_DIR = Path("/app/backend")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# python-dotenv is loaded by server.py at startup; but pytest doesn't run
# server.py, so we load .env manually to mirror the runtime environment.
from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND_DIR / ".env")

import graph_email  # noqa: E402
import aiosmtplib  # noqa: E402


# ---------------------------------------------------------------------------
# 1. SMTP CONFIG PARSING
# ---------------------------------------------------------------------------
class TestSmtpConfigParsing:
    def test_env_vars_present(self):
        assert os.environ.get("SMTP_HOST") == "mail01.one2net.net"
        assert os.environ.get("SMTP_PORT") == "465"
        assert os.environ.get("SMTP_USER") == "info@nextgecopro.be"
        # Password contains two literal $ - dotenv must preserve them verbatim
        assert os.environ.get("SMTP_PASSWORD") == "Foxmulder1$$"
        assert os.environ.get("SMTP_FROM") == "info@nextgecopro.be"
        assert os.environ.get("SMTP_FROM_NAME") == "NextGe Copro"
        assert os.environ.get("MAIL_ENABLED", "").lower() == "true"

    def test_module_level_vars(self):
        assert graph_email._SMTP_HOST == "mail01.one2net.net"
        assert graph_email._SMTP_PORT == 465
        assert graph_email._SMTP_USER == "info@nextgecopro.be"
        assert graph_email._SMTP_PASSWORD == "Foxmulder1$$"
        assert graph_email._SMTP_USE_TLS is True
        assert graph_email._SMTP_FROM == "info@nextgecopro.be"
        assert graph_email._SMTP_FROM_NAME == "NextGe Copro"
        assert graph_email._MAIL_ENABLED is True

    def test_smtp_configured_true(self):
        assert graph_email._smtp_configured() is True

    def test_is_configured_true(self):
        assert graph_email.is_configured() is True


# ---------------------------------------------------------------------------
# 2. SMTP ROUTING (port 465 - SSL implicit)
# ---------------------------------------------------------------------------
class TestSmtpRouting465:
    def test_send_uses_smtp_path_and_headers(self, monkeypatch):
        calls = {}

        async def fake_send(msg, **kwargs):
            calls["msg"] = msg
            calls["kwargs"] = kwargs
            return ({}, "250 OK queued")

        monkeypatch.setattr(aiosmtplib, "send", fake_send)

        asyncio.get_event_loop().run_until_complete(
            graph_email.send_html_email(
                recipients=["test@example.com"],
                subject="X",
                html_body="<b>Y</b>",
                reply_to="foo@bar.be",
            )
        )

        assert "msg" in calls, "aiosmtplib.send was not called -> SMTP path not taken"
        msg = calls["msg"]
        assert msg["Subject"] == "X"
        assert msg["From"] == "NextGe Copro <info@nextgecopro.be>"
        assert msg["To"] == "test@example.com"
        assert msg["Reply-To"] == "foo@bar.be"
        # Body: decode the base64-encoded HTML part
        html_part = msg.get_payload()[0]
        decoded = html_part.get_payload(decode=True).decode("utf-8")
        assert "<b>Y</b>" in decoded
        assert html_part.get_content_type() == "text/html"

        kw = calls["kwargs"]
        assert kw["hostname"] == "mail01.one2net.net"
        assert kw["port"] == 465
        assert kw["username"] == "info@nextgecopro.be"
        assert kw["password"] == "Foxmulder1$$"
        assert kw["use_tls"] is True, "Port 465 must use implicit SSL (use_tls=True)"
        assert kw["start_tls"] is False, "Port 465 must NOT use STARTTLS"


# ---------------------------------------------------------------------------
# 3. PORT 587 - STARTTLS explicit
# ---------------------------------------------------------------------------
class TestSmtpRouting587:
    def test_starttls_flags(self, monkeypatch):
        calls = {}

        async def fake_send(msg, **kwargs):
            calls["kwargs"] = kwargs
            return ({}, "250 OK")

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setattr(graph_email, "_SMTP_PORT", 587)

        asyncio.get_event_loop().run_until_complete(
            graph_email.send_html_email(
                recipients=["test@example.com"],
                subject="S",
                html_body="<p>b</p>",
            )
        )
        kw = calls["kwargs"]
        assert kw["port"] == 587
        assert kw["use_tls"] is False, "Port 587 must NOT use implicit SSL"
        assert kw["start_tls"] is True, "Port 587 must use STARTTLS"


# ---------------------------------------------------------------------------
# 4. DRY-RUN when MAIL_ENABLED=False and no SMTP host
# ---------------------------------------------------------------------------
class TestDryRunFallback:
    def test_dry_run_no_send(self, monkeypatch, caplog):
        called = {"n": 0}

        async def fake_send(msg, **kwargs):
            called["n"] += 1
            return ({}, "250 OK")

        monkeypatch.setattr(aiosmtplib, "send", fake_send)
        monkeypatch.setattr(graph_email, "_MAIL_ENABLED", False)
        monkeypatch.setattr(graph_email, "_SMTP_HOST", "")

        import logging
        with caplog.at_level(logging.INFO, logger="graph_email"):
            asyncio.get_event_loop().run_until_complete(
                graph_email.send_html_email(
                    recipients=["dry@example.com"],
                    subject="DR",
                    html_body="<b>x</b>",
                )
            )
        assert called["n"] == 0, "aiosmtplib.send should NOT be called in DRY-RUN"
        assert any("DRY-RUN" in rec.message for rec in caplog.records), \
            "DRY-RUN log line missing"


# ---------------------------------------------------------------------------
# 5. SMTP ERROR PROPAGATION
# ---------------------------------------------------------------------------
class TestSmtpErrorPropagation:
    def test_auth_error_becomes_runtime_error(self, monkeypatch):
        async def fake_send(msg, **kwargs):
            raise aiosmtplib.errors.SMTPAuthenticationError(535, "bad password")

        monkeypatch.setattr(aiosmtplib, "send", fake_send)

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                graph_email.send_html_email(
                    recipients=["x@example.com"],
                    subject="err",
                    html_body="<b>e</b>",
                )
            )
        assert "SMTP send failed" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. REAL SMTP DELIVERY (single one-shot smoke test)
# ---------------------------------------------------------------------------
@pytest.mark.smoke
class TestRealSmtpSmoke:
    """ONE-SHOT real send. Do NOT parametrize / loop / retry."""

    def test_single_real_send(self):
        # Guard: only send once; if run twice in same test session, second call skips.
        if getattr(TestRealSmtpSmoke, "_sent_once", False):
            pytest.skip("Real send already performed in this session")
        TestRealSmtpSmoke._sent_once = True

        try:
            asyncio.get_event_loop().run_until_complete(
                graph_email.send_html_email(
                    recipients=["info@nextgecopro.be"],
                    subject="[testing_agent] SMTP smoke test iter90fv",
                    html_body=(
                        "<p>Automated smoke test from /app/backend/tests/"
                        "test_iter90fv_smtp.py</p>"
                    ),
                )
            )
        except Exception as e:
            pytest.fail(f"Real SMTP send raised: {e}")
