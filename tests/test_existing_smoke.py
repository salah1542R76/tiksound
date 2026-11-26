"""Fumée de l'existant (travail des sessions précédentes, jamais exécuté en vrai jusqu'ici)."""
import time

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_register_verify_login(app, make_client, signup):
    c = make_client()
    me = signup(c, "a@example.com", "alice")
    assert me["username"] == "alice" and me["email"] == "a@example.com"
    c2 = make_client()
    r = c2.post("/api/auth/login", json={"email": "a@example.com", "password": "motdepasse1"})
    assert r.status_code == 200 and r.get_json()["username"] == "alice"
    r = c2.post("/api/auth/login", json={"email": "a@example.com", "password": "mauvais"})
    assert r.status_code == 401


def test_me_requires_cookie(app, make_client):
    assert make_client().get("/api/auth/me").status_code == 401


def test_patch_avatar_banner_serves_images(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    r = c.patch("/api/auth/me", json={"pic": PNG, "ban": PNG})
    assert r.status_code == 200 and r.get_json()["pic"] and r.get_json()["ban"]
    for kind in ("a", "b"):
        img = c.get(f"/api/img/alice/{kind}")
        assert img.status_code == 200 and img.headers["Content-Type"] == "image/png"
    assert c.patch("/api/auth/me", json={"pic": None}).status_code == 200
    assert c.get("/api/img/alice/a").status_code == 404


def test_ui_sync_roundtrip(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    r1 = c.patch("/api/auth/me", json={"ui": {"theme": "nuit", "blur": 12, "taille": "inconnue"}}).get_json()
    me = c.get("/api/auth/me").get_json()
    assert me["ui"] == {"theme": "nuit", "blur": 12}          # clés inconnues ignorées
    time.sleep(0.01)
    r2 = c.patch("/api/auth/me", json={"ui": {"theme": "clair"}}).get_json()
    assert r2["ui_v"] != r1["ui_v"]


def test_register_without_mail_provider_503(app, make_client):
    app.MAIL_ON = False
    r = make_client().post("/api/auth/register", json={"email": "a@example.com", "username": "alice", "password": "motdepasse1"})
    assert r.status_code == 503
