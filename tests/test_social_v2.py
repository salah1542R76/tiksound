"""Tendances, aperçus de liens, signalements, pages légales."""
import time

TRACK = {"url": "https://www.tiktok.com/@a/video/1", "title": "Son", "author": "A",
         "cover": "https://p16.tiktokcdn.com/c.jpg", "duration": 30}


def make_pl(c, name="Ma <playlist>"):
    return c.post("/api/playlists", json={"name": name, "tracks": [TRACK]}).get_json()["id"]


def test_feed_has_this_weeks_trends(app, make_client, signup):
    owner = make_client(); signup(owner, "o@example.com", "owner")
    old, hot = make_pl(owner, "Ancienne"), make_pl(owner, "En vogue")
    fans = []
    for i in range(3):
        f = make_client(); signup(f, f"f{i}@example.com", f"fan{i}"); fans.append(f)
    for f in fans:
        assert f.post(f"/api/playlists/{old}/like").status_code == 200
    app.x("UPDATE playlist_likes SET created='2020-01-01 00:00:00' WHERE playlist_id=:p", p=old)   # likes anciens
    fans[0].post(f"/api/playlists/{hot}/like")
    d = make_client().get("/api/feed").get_json()
    assert [p["name"] for p in d["trend"]] == ["En vogue"]
    assert d["trend"][0]["week"] == 1


def test_link_preview_for_shared_playlist_and_profile(app, make_client, signup):
    c = make_client(); signup(c, "o@example.com", "owner")
    pid = make_pl(c)
    html = make_client().get(f"/?pl={pid}").get_data(as_text=True)
    assert '<meta property="og:title" content="Ma &lt;playlist&gt; · TikWave">' in html
    assert 'og:image" content="https://p16.tiktokcdn.com/c.jpg"' in html
    assert "par @owner" in html
    html = make_client().get("/?u=owner").get_data(as_text=True)
    assert 'og:title" content="@owner sur TikWave"' in html
    plain = make_client().get("/?pl=inconnu").get_data(as_text=True)
    assert 'og:title" content="TikWave' in plain                     # titre par défaut, pas d'erreur
    r = make_client().get("/")
    assert r.headers["Cache-Control"] == "no-cache" and "X-Tiksound" in r.headers


def test_report_form_stores_and_admin_reads(app, make_client, signup, monkeypatch):
    c = make_client()
    r = c.post("/api/report", json={"url": "https://tiksound.example/?pl=1", "reason": "copyright", "name": "Label X",
                                    "email": "droits@label.example", "details": "Ce son nous appartient."})
    assert r.status_code == 201
    assert c.post("/api/report", json={"url": "pas un lien", "reason": "copyright", "email": "a@b.co"}).status_code == 400
    assert c.post("/api/report", json={"url": "https://x.example", "reason": "copyright", "email": "faux"}).status_code == 400
    monkeypatch.setattr(app, "ADMIN_EMAILS", {"admin@example.com"})
    adm = make_client(); signup(adm, "admin@example.com", "patron")
    lst = adm.get("/api/admin/reports").get_json()["reports"]
    assert len(lst) == 1 and lst[0]["name"] == "Label X" and lst[0]["status"] == "new"
    assert adm.post(f"/api/admin/reports/{lst[0]['id']}", json={"status": "done"}).status_code == 200
    assert adm.get("/api/admin/reports").get_json()["reports"][0]["status"] == "done"
    other = make_client(); signup(other, "x@example.com", "xavier")
    assert other.get("/api/admin/reports").status_code == 403


def test_report_is_rate_limited(app, make_client):
    c = make_client()
    codes = [c.post("/api/report", json={"url": "https://x.example/a", "reason": "other", "email": "a@b.co"}).status_code
             for _ in range(7)]
    assert codes[:5] == [201] * 5 and codes[-1] == 429


def test_legal_pages(app, make_client, monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "moi@example.com")
    for path, word in (("/conditions", "Conditions d'utilisation"), ("/signalement", "Signaler un contenu")):
        r = make_client().get(path)
        assert r.status_code == 200 and word in r.get_data(as_text=True), path
        assert "mailto:moi@example.com" in r.get_data(as_text=True)


def test_admin_stats(app, make_client, signup, monkeypatch):
    monkeypatch.setattr(app, "ADMIN_EMAILS", {"boss@example.com"})
    b = make_client(); signup(b, "boss@example.com", "patron")
    u = make_client(); signup(u, "u@example.com", "simple")
    app.x("UPDATE users SET gift_until=:g WHERE email='u@example.com'", g=app.FOREVER)
    d = b.get("/api/admin/stats").get_json()
    assert d["users"] == 2 and d["users_7d"] == 2 and d["premium_keys"] == 1 and d["premium_paid"] == 0
    assert u.get("/api/admin/stats").status_code == 403


def test_signed_in_member_sees_published_sounds_and_which_are_theirs(make_client, signup):
    a, b = make_client(), make_client()
    signup(a, "a@example.com", "alice")
    signup(b, "b@example.com", "bruno")
    pid = a.post("/api/posts", json={"url": "https://www.tiktok.com/@a/video/1", "title": "Tube"}).get_json()["id"]
    for client, mine in ((a, True), (b, False), (make_client(), False)):
        r = client.get("/api/posts")
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        posts = r.get_json()["posts"]
        assert [p["id"] for p in posts] == [pid] and posts[0]["mine"] is mine
