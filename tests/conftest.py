import importlib
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [HERE, ROOT]

import _shims  # noqa: E402

_shims.install()

ENV_TO_CLEAR = ["DATABASE_URL", "STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET", "ADMIN_EMAIL",
                "PUBLIC_URL", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
                "R2_PUBLIC_URL", "RESEND_API_KEY", "SMTP_HOST", "YTDLP_COOKIES", "YTDLP_PROXY", "SECRET_FILE", "ADSENSE_CLIENT", "ADSENSE_SLOT", "CONTACT_EMAIL", "STRIPE_PUBLISHABLE_KEY"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Module server rechargé sur une base vide ; les emails partent dans app.OUTBOX."""
    monkeypatch.chdir(tmp_path)
    for k in ENV_TO_CLEAR:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JWT_SECRET", "secret-de-test")
    monkeypatch.setenv("BREVO_API_KEY", "x")
    import server
    server = importlib.reload(server)
    server.OUTBOX = []
    server.STRIPE_PUB = "pk_test_default"      # clé publique Stripe (STRIPE_PUBLISHABLE_KEY) : la plupart des tests veulent l'abonnement actif

    def fake_send(to, subject, html_body, text_body):
        server.OUTBOX.append({"to": to, "subject": subject, "text": text_body})
        return True

    server.send_mail = fake_send
    server.app.config["TESTING"] = True
    return server


@pytest.fixture
def make_client(app):
    return lambda: app.app.test_client()


@pytest.fixture
def signup(app):
    def _signup(client, email, username, pw="motdepasse1"):
        r = client.post("/api/auth/register", json={"email": email, "username": username, "password": pw})
        assert r.status_code == 200, r.get_json()
        code = re.match(r"(\d{6})", app.OUTBOX[-1]["subject"]).group(1)
        r = client.post("/api/auth/verify", json={"email": email, "code": code})
        assert r.status_code == 201, r.get_json()
        return r.get_json()
    return _signup
