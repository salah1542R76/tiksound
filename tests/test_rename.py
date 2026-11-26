"""Renommage Tiksound -> TikWave : le nouveau nom partout où il est visible, les identifiants techniques conservés."""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ("/", "/conditions", "/confidentialite", "/signalement", "/manifest.webmanifest")


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def old_name_in(text):
    """Mentions visibles de l'ancien nom (les identifiants techniques en minuscules ne comptent pas)."""
    return re.findall(r".{0,40}Tiksound.{0,20}", text)


@pytest.mark.parametrize("path", PAGES)
def test_served_pages_show_only_the_new_name(make_client, path):
    body = make_client().get(path).get_data(as_text=True)
    # Seule exception : le bandeau de déménagement, qui doit citer l'ancien nom pour que les gens comprennent.
    body = re.sub(r"<!--moved-->.*?<!--/moved-->", "", body, flags=re.S)
    body = body.replace("X-Tiksound", "")
    assert not old_name_in(body), old_name_in(body)[:5]
    assert "TikWave" in body


def test_manifest_uses_the_new_name(make_client):
    m = json.loads(make_client().get("/manifest.webmanifest").get_data(as_text=True))
    assert m["name"] == "TikWave" and m["short_name"] == "TikWave"


def test_link_previews_use_the_new_name(make_client):
    html = make_client().get("/").get_data(as_text=True)
    assert 'og:site_name" content="TikWave"' in html
    assert 'og:title" content="TikWave' in html


def test_emails_are_signed_tikwave(app, make_client):
    r = make_client().post("/api/auth/register", json={"email": "a@example.com", "username": "alice", "password": "motdepasse1"})
    assert r.status_code == 200
    mail = app.OUTBOX[-1]
    assert "TikWave" in mail["subject"] and "TikWave" in mail["text"]
    assert "Tiksound" not in mail["subject"] + mail["text"]
    assert app.MAIL_FROM == "TikWave <no-reply@tikwave.fr>"


@pytest.mark.parametrize("name", ["tikwave", "TikWave", "tikwav", "tiksound"])
def test_brand_usernames_stay_reserved(make_client, name):
    d = make_client().get(f"/api/auth/username?u={name}").get_json()
    assert d["available"] is False


def test_new_access_keys_use_the_new_prefix(app):
    assert re.fullmatch(r"TKW(-[2-9A-HJKMNP-Z]{4}){3}", app.new_access_key())


def test_keys_issued_before_the_rename_still_work(app, make_client, signup):
    c = make_client()
    signup(c, "old@example.com", "ancien")
    old = "TKS-AAAA-BBBB-CCCC"
    app.x("INSERT INTO access_keys (id,key_hash,label,hint,max_uses,uses,days,created,revoked) "
          "VALUES ('k1',:h,'','CCCC',1,0,0,:c,0)", h=app.key_hash(old), c=app.now_iso())
    r = c.post("/api/premium/redeem", json={"key": old})
    assert r.status_code == 200 and r.get_json()["premium"] is True


# ── Identifiants techniques à NE PAS renommer (sinon perte de données ou déconnexion) ──
def test_browser_storage_keys_are_kept():
    html = read("index.html")
    assert 'localStorage.getItem("tiksound")' in html
    assert 'localStorage.setItem("tiksound"' in html
    assert 'indexedDB.open("tiksound"' in html


def test_server_secret_database_and_cookie_names_are_kept():
    src = read("server.py")
    assert '".tiksound_secret"' in src
    assert '"sqlite:///tiksound.db"' in src
    assert '"tk_sess"' in src
    assert 'r.headers["X-Tiksound"]' in src and '"X-Tiksound"' in read("sw.js")


def test_guides_point_stripe_to_the_new_webhook_address():
    guide = read("guide-premium.html") + read("PREMIUM.md")
    assert "https://tikwave.fr/api/stripe/webhook" in guide
    assert not old_name_in(guide.replace("X-Tiksound", ""))
