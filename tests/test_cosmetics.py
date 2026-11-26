"""Cosmétiques de profil (bandeau, cadre, badge) et options d'apparence Premium : validation + réservé aux abonnés."""
import time


def set_prem(app, email, until):
    app.x("UPDATE users SET gift_until=:g WHERE email=:e", g=until, e=email)


COS = {"banner": "fleurs", "frame": "etoiles", "badge": "🔥"}


def test_free_account_cannot_set_cosmetics(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    r = c.patch("/api/auth/me", json={"cos": COS})
    assert r.status_code == 403
    assert c.get("/api/auth/me").get_json()["cos"] == {}


def test_free_account_can_still_clear_cosmetics(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    assert c.patch("/api/auth/me", json={"cos": {}}).status_code == 200


def test_premium_sets_cosmetics_and_unknown_values_are_dropped(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    set_prem(app, "a@example.com", app.FOREVER)
    c.patch("/api/auth/me", json={"cos": {"banner": "fleurs", "frame": "<b>", "badge": "A", "x": "y"}})
    assert c.get("/api/auth/me").get_json()["cos"] == {"banner": "fleurs"}
    c.patch("/api/auth/me", json={"cos": COS})
    assert c.get("/api/auth/me").get_json()["cos"] == COS
    c.patch("/api/auth/me", json={"cos": {"banner": "", "frame": "flamme"}})
    assert c.get("/api/auth/me").get_json()["cos"] == {"frame": "flamme"}


def test_every_banner_frame_and_badge_offered_is_accepted(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    set_prem(app, "a@example.com", app.FOREVER)
    for b in app.COS_BANNER:
        assert c.patch("/api/auth/me", json={"cos": {"banner": b}}).status_code == 200
    assert c.get("/api/auth/me").get_json()["cos"] == {"banner": app.COS_BANNER[-1]}
    assert len(app.COS_BANNER) >= 6 and len(app.COS_FRAME) >= 5 and len(app.COS_BADGE) >= 8


def test_cosmetics_are_public_only_while_premium(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    set_prem(app, "a@example.com", app.FOREVER)
    c.patch("/api/auth/me", json={"cos": COS})
    assert make_client().get("/api/users/alice").get_json()["cos"] == COS
    set_prem(app, "a@example.com", int(time.time()) - 10 * 86400)     # abonnement fini (au-delà de la marge)
    assert make_client().get("/api/users/alice").get_json()["cos"] == {}
    assert c.get("/api/auth/me").get_json()["cos"] == {}
    set_prem(app, "a@example.com", app.FOREVER)                         # il revient avec l'abonnement
    assert c.get("/api/auth/me").get_json()["cos"] == COS


def test_new_appearance_options_are_synced_and_checked(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    c.patch("/api/auth/me", json={"ui": {"trail": "flame", "cur": "heart", "amb": "petals", "glow": True}})
    assert c.get("/api/auth/me").get_json()["ui"] == {"trail": "flame", "cur": "heart", "amb": "petals", "glow": True}
    c.patch("/api/auth/me", json={"ui": {"trail": "laser", "cur": "<img>", "amb": "pluie"}})
    assert c.get("/api/auth/me").get_json()["ui"] == {}


def test_discord_like_cosmetics_with_free_colours(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    set_prem(app, "a@example.com", app.FOREVER)
    cos = {"banner": "degrade", "bc1": "#FF0000", "bc2": "#00ff00", "frame": "halo", "fc": "#123abc",
           "effect": "petales", "t1": "#111111", "t2": "#222222", "name": "brille", "badge": "👑"}
    c.patch("/api/auth/me", json={"cos": cos})
    got = c.get("/api/auth/me").get_json()["cos"]
    assert got == dict(cos, bc1="#ff0000")
    c.patch("/api/auth/me", json={"cos": {"bc1": "red", "fc": "#12", "effect": "<x>", "name": "gras", "t1": "#abcdef"}})
    assert c.get("/api/auth/me").get_json()["cos"] == {"t1": "#abcdef"}
    for k, lst in (("frame", app.COS_FRAME), ("effect", app.COS_EFFECT), ("name", app.COS_NAME)):
        for v in lst:
            assert c.patch("/api/auth/me", json={"cos": {k: v}}).get_json()["cos"] == {k: v}
    assert len(app.COS_FRAME) >= 10 and len(app.COS_EFFECT) >= 8 and len(app.COS_BANNER) >= 11


def test_free_account_cannot_set_only_colours_either(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    assert c.patch("/api/auth/me", json={"cos": {"t1": "#abcdef"}}).status_code == 403


def test_cursor_and_trail_colours_are_synced(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    c.patch("/api/auth/me", json={"ui": {"trc": "#FF00AA", "curc": "#00ffcc"}})
    assert c.get("/api/auth/me").get_json()["ui"] == {"trc": "#ff00aa", "curc": "#00ffcc"}
    c.patch("/api/auth/me", json={"ui": {"trc": "rainbow", "curc": ""}})
    assert c.get("/api/auth/me").get_json()["ui"] == {"trc": "rainbow", "curc": ""}
    c.patch("/api/auth/me", json={"ui": {"trc": "bleu", "curc": "rainbow"}})
    assert c.get("/api/auth/me").get_json()["ui"] == {}


NEW_COS = {"banner": ["galaxie", "lave", "holo", "damier"], "frame": ["arcenciel", "couronne", "notes", "eclair", "pixel"],
           "effect": ["notes", "pluie", "paillettes"], "name": ["arcenciel", "or", "glitch"], "badge": ["🎵", "🌈", "🚀", "🍀", "🐉", "❄️"]}


def test_new_premium_decorations_are_accepted_for_premium_only(app, make_client, signup):
    free, prem = make_client(), make_client()
    signup(free, "f@example.com", "gratuit"); signup(prem, "p@example.com", "payeur")
    set_prem(app, "p@example.com", app.FOREVER)
    for kind, values in NEW_COS.items():
        for v in values:
            assert prem.patch("/api/auth/me", json={"cos": {kind: v}}).get_json()["cos"] == {kind: v}, (kind, v)
            assert free.patch("/api/auth/me", json={"cos": {kind: v}}).status_code == 403


def _vip_and_free(app, make_client, signup):
    vip, free = make_client(), make_client()
    signup(vip, "vip@example.com", "vip"); signup(free, "free@example.com", "simple")
    set_prem(app, "vip@example.com", app.FOREVER)
    vip.patch("/api/auth/me", json={"cos": {"frame": "couronne", "badge": "👑", "banner": "galaxie"}})
    for c, n in ((vip, 1), (free, 2)):
        c.post("/api/playlists", json={"name": f"Liste {n}", "tracks": [{"url": f"https://www.tiktok.com/@a/video/{n}", "title": "Son"}]})
        c.post("/api/posts", json={"url": f"https://www.tiktok.com/@a/video/{n}", "title": "Son"})
    return vip, free


def test_explore_lists_say_who_is_premium_with_their_avatar_decoration(app, make_client, signup):
    _vip_and_free(app, make_client, signup)
    c = make_client()
    feed = c.get("/api/feed").get_json()
    posts = c.get("/api/posts").get_json()["posts"]
    search = c.get("/api/search?q=i").get_json()
    rows = feed["users"] + feed["top"] + feed["recent"] + posts + search["users"] + search["playlists"]
    rows += c.get("/api/users/search?q=i").get_json()["users"]
    by = {}
    for r in rows:
        by.setdefault(r["username"], []).append(r)
    assert all(r["premium"] is True and r["cos"] == {"frame": "couronne", "badge": "👑"} for r in by["vip"])
    assert all(r["premium"] is False and "cos" not in r for r in by["simple"])
    assert "@example.com" not in str(rows)                     # l'e-mail sert au calcul, jamais envoyé


def test_premium_mark_disappears_when_premium_ends(app, make_client, signup):
    _vip_and_free(app, make_client, signup)
    set_prem(app, "vip@example.com", int(time.time()) - 10)
    users = make_client().get("/api/feed").get_json()["users"]
    assert [u["premium"] for u in users if u["username"] == "vip"] == [False]
