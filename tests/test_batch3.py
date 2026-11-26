"""Curseurs et traînées en plus, épaisseur des bords, police/couleur du pseudo, fond libre, GIF Premium, durée des sons Spotify."""
import base64
import time

GIF = "data:image/gif;base64," + base64.b64encode(b"GIF89a" + b"\x00" * 40).decode()


def set_prem(app, email, until):
    app.x("UPDATE users SET gift_until=:g WHERE email=:e", g=until, e=email)


def test_new_trails_cursors_and_border_thickness_are_saved(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    for k, vals in (("trail", ("heart", "note", "bubble")), ("cur", ("note", "diamond", "bolt", "plus"))):
        for v in vals:
            assert c.patch("/api/auth/me", json={"ui": {k: v}}).get_json()["ui"][k] == v
    assert c.patch("/api/auth/me", json={"ui": {"curw": 5}}).get_json()["ui"]["curw"] == 5
    assert c.patch("/api/auth/me", json={"ui": {"curw": 99}}).get_json()["ui"]["curw"] == 30
    ui = c.patch("/api/auth/me", json={"ui": {"theme": "perso", "tbg": "#123456"}}).get_json()["ui"]
    assert ui["theme"] == "perso" and ui["tbg"] == "#123456"
    assert "tbg" not in c.patch("/api/auth/me", json={"ui": {"tbg": "red"}}).get_json()["ui"]


def test_username_font_colour_and_new_effects_are_premium_cosmetics(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    cos = {"nfont": "pixel", "nc": "#ff00aa", "name": "ombre"}
    assert c.patch("/api/auth/me", json={"cos": cos}).status_code == 403
    set_prem(app, "a@example.com", app.FOREVER)
    for name in ("ombre", "contour", "pastel"):
        assert c.patch("/api/auth/me", json={"cos": {"name": name}}).get_json()["cos"] == {"name": name}
    assert c.patch("/api/auth/me", json={"cos": cos}).get_json()["cos"] == cos
    assert c.patch("/api/auth/me", json={"cos": {"nfont": "comic", "nc": "rose"}}).get_json()["cos"] == {}


def test_gif_avatar_and_banner_are_for_premium_only(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    assert c.patch("/api/auth/me", json={"pic": GIF}).status_code == 403
    set_prem(app, "a@example.com", app.FOREVER)
    assert c.patch("/api/auth/me", json={"pic": GIF, "ban": GIF}).status_code == 200
    img = make_client().get("/api/img/alice/a")
    assert img.status_code == 200 and img.mimetype == "image/gif" and img.data.startswith(b"GIF89a")
    set_prem(app, "a@example.com", int(time.time()) - 10)                       # Premium terminé : le GIF n'est plus servi
    assert make_client().get("/api/img/alice/a").status_code == 404


def test_a_fake_gif_is_refused(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    set_prem(app, "a@example.com", app.FOREVER)
    fake = "data:image/gif;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert c.patch("/api/auth/me", json={"pic": fake}).status_code == 400


def _ydl(lengths, got):
    class YDL:
        def __init__(self, o): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            got.append(url)
            return {"duration": lengths[url[-1]], "title": "Song"}
    return YDL


def test_spotify_match_skips_versions_of_the_wrong_length(app, monkeypatch):
    """Une version trop longue (clip, version longue) n'est pas téléchargée si une version à la bonne durée existe."""
    cands = [{"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "duration": 260, "src": "ytsearch", "title": "Artist - Song (Official Video)", "chan": "Artist"},
             {"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "duration": 201, "src": "ytsearch", "title": "Song", "chan": "Artist - Topic"}]
    monkeypatch.setattr(app, "search_candidates", lambda prefix, q, n=6: [dict(c) for c in cands] if prefix == "ytsearch" else [])
    got = []
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _ydl({"a": 260, "b": 200}, got))
    info = app.audio_from_search({}, "Song", "Artist", 200)
    assert got == ["https://www.youtube.com/watch?v=bbbbbbbbbbb"] and info["duration"] == 200


def test_spotify_match_retries_when_the_downloaded_file_has_the_wrong_length(app, monkeypatch):
    cands = [{"url": f"https://www.youtube.com/watch?v={c * 11}", "duration": 0, "src": "ytsearch", "title": "Song", "chan": "Artist"} for c in "ab"]
    monkeypatch.setattr(app, "search_candidates", lambda prefix, q, n=6: [dict(c) for c in cands] if prefix == "ytsearch" else [])
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", _ydl({"a": 330, "b": 199}, []))
    assert app.audio_from_search({}, "Song", "Artist", 200)["duration"] == 199


def test_search_results_are_ranked_by_how_well_they_match(app, make_client, monkeypatch):
    def entries(*titles):
        return [{"id": f"{i:011d}", "title": t, "uploader": "Chan", "duration": 180} for i, t in enumerate(titles)]
    monkeypatch.setattr(app, "_find_yd", lambda prefix, q, n: entries(
        "Random vlog", "Blinding Lights (cover)", "The Weeknd - Blinding Lights (Official Audio)", "Blinding Lights sped up", "Weeknd Blinding Lights lyrics"))
    r = make_client().get("/api/find?q=the weeknd blinding lights").get_json()["results"]["youtube"]
    titles = [x["title"] for x in r]
    assert titles[0] == "The Weeknd - Blinding Lights (Official Audio)"
    assert titles.index("Blinding Lights (cover)") > titles.index("Weeknd Blinding Lights lyrics")
    assert "Random vlog" not in titles[:3]
