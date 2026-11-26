"""Anglais côté serveur : messages d'erreur, e-mails, pages légales, manifest, aperçus de liens."""
import ast
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EN = {"X-Lang": "en"}


def bad_login(client, headers=None, cookie=None):
    if cookie:
        client.set_cookie("tk_lang", cookie)
    r = client.post("/api/auth/login", json={"email": "no@example.com", "password": "motdepasse1"}, headers=headers or {})
    return r.get_json()["error"]


@pytest.mark.parametrize("headers,cookie,expected", [
    ({}, None, "Email ou mot de passe incorrect"),
    ({"X-Lang": "en"}, None, "Wrong email or password"),
    ({"Accept-Language": "en-US,en;q=0.9"}, None, "Wrong email or password"),
    ({"Accept-Language": "fr-CA,fr;q=0.9,en;q=0.5"}, None, "Email ou mot de passe incorrect"),
    ({"Accept-Language": "es-ES,es;q=0.9"}, None, "Wrong email or password"),
    ({"Accept-Language": "fr-FR"}, "en", "Wrong email or password"),          # le choix retenu l'emporte
    ({"X-Lang": "fr", "Accept-Language": "en-US"}, "en", "Email ou mot de passe incorrect"),
])
def test_error_messages_follow_the_language(make_client, headers, cookie, expected):
    assert bad_login(make_client(), headers, cookie) == expected


def test_messages_with_numbers_are_translated(make_client, app):
    c = make_client()
    c.post("/api/auth/register", json={"email": "a@example.com", "username": "alice", "password": "motdepasse1"}, headers=EN)
    r = c.post("/api/auth/verify", json={"email": "a@example.com", "code": "000000x"[:6]}, headers=EN)
    assert r.get_json()["error"] == "Incorrect code (4 tries left)"
    r = c.post("/api/auth/register", json={"email": "a@example.com", "username": "alice", "password": "motdepasse1"}, headers=EN)
    assert re.fullmatch(r"Wait \d+ s before asking for another code", r.get_json()["error"])


def test_authentication_and_validation_errors_are_translated(make_client):
    c = make_client()
    assert c.get("/api/auth/me", headers=EN).get_json()["error"] == "Not signed in"
    assert c.get("/api/find?q=a", headers=EN).get_json()["error"] == "Type at least 2 letters"
    assert c.get("/api/auth/username?u=ab", headers=EN).get_json()["reason"] == "3 to 24 characters: letters, numbers, _ or -"
    assert c.post("/api/extract", json={"url": "x"}, headers=EN).get_json()["error"] == "Invalid TikTok link"


def test_signup_email_is_written_in_the_user_language(app, make_client):
    make_client().post("/api/auth/register", headers=EN,
                       json={"email": "a@example.com", "username": "alice", "password": "motdepasse1"})
    m = app.OUTBOX[-1]
    assert re.fullmatch(r"\d{6} is your TikWave code", m["subject"])
    assert "Welcome alice" in m["text"] and "expires in 15 minutes" in m["text"]
    make_client().post("/api/auth/register", json={"email": "b@example.com", "username": "bruno", "password": "motdepasse1"})
    assert app.OUTBOX[-1]["subject"].endswith("est ton code TikWave")


@pytest.mark.parametrize("path,fr,en", [
    ("/confidentialite", "Politique de confidentialité", "Privacy policy"),
    ("/conditions", "Conditions d'utilisation", "Terms of use"),
    ("/signalement", "Signaler un contenu", "Report content"),
])
def test_legal_pages_exist_in_both_languages(make_client, path, fr, en):
    french = make_client().get(path).get_data(as_text=True)
    assert fr in french and 'lang="fr"' in french
    for r in (make_client().get(path + "?lang=en"), make_client().get(path, headers={"Accept-Language": "en-GB"})):
        page = r.get_data(as_text=True)
        assert r.status_code == 200 and en in page and 'lang="en"' in page and "{{CONTACT}}" not in page
    c = make_client()
    c.set_cookie("tk_lang", "en")
    assert en in c.get(path).get_data(as_text=True)


def test_english_report_form_posts_in_english(make_client):
    page = make_client().get("/signalement?lang=en").get_data(as_text=True)
    assert "X-Lang" in page          # les erreurs du formulaire reviennent aussi en anglais


def test_manifest_follows_the_browser_language(make_client):
    en = json.loads(make_client().get("/manifest.webmanifest", headers={"Accept-Language": "en-US"}).get_data(as_text=True))
    fr = json.loads(make_client().get("/manifest.webmanifest").get_data(as_text=True))
    assert fr["lang"] == "fr" and en["lang"] == "en"
    assert en["description"] != fr["description"] and en["name"] == "TikWave"
    assert [s["name"] for s in en["shortcuts"]] == ["Add a sound", "My sounds", "Explore"]


def test_link_previews_default_to_french_and_follow_the_visitor(make_client):
    assert "les sons TikTok, sans la vidéo" in make_client().get("/").get_data(as_text=True)
    en = make_client().get("/", headers={"Accept-Language": "en-US"}).get_data(as_text=True)
    assert "TikTok sounds, without the video" in en


def test_language_dictionary_is_served(make_client):
    r = make_client().get("/lang-en.js")
    assert r.status_code == 200 and "javascript" in r.mimetype and "window.TK_EN" in r.get_data(as_text=True)


def server_messages():
    """Toutes les phrases passées à err(...) / T(...) dans server.py, plus les retours des vérifications."""
    tree = ast.parse(open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("err", "T") and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.add(a.value)
    return out


def test_every_server_message_has_a_translation():
    import i18n_server
    missing = sorted(m for m in server_messages() if m not in i18n_server.EN)
    assert not missing, missing
    assert len(server_messages()) > 60          # garde-fou : les messages passent bien par err() / T()


def test_no_raw_french_error_is_left_in_the_server():
    """err(f"…") ou jsonify(error="…") échapperaient à la traduction."""
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    assert not re.search(r"\berr\(f[\"']", src)
    assert not re.search(r"jsonify\(error=[\"']", src)


def test_every_translated_app_string_is_in_the_dictionary():
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "lang-en.js"), encoding="utf-8").read()
    en = json.loads(js[js.index("window.TK_EN=") + len("window.TK_EN="):js.rindex(";")])
    keys = {json.loads('"' + m + '"') for m in re.findall(r'_t\("((?:[^"\\]|\\.)*)"', html)}
    assert len(keys) > 600
    missing = sorted(k for k in keys if k not in en)
    assert not missing, missing[:20]
    for k, v in en.items():                       # une traduction ne peut pas utiliser une case inexistante
        assert set(re.findall(r"\{(\d+)\}", v)) <= set(re.findall(r"\{(\d+)\}", k)), k
