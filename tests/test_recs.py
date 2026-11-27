"""Recommandations « Pour toi » et radio : POST /api/recs."""


def post(c, n, title, author):
    r = c.post("/api/posts", json={"url": f"https://www.tiktok.com/@a/video/{n}", "title": title, "author": author, "duration": 20})
    return r.get_json()["id"]


def pl(c, name, tracks):
    return c.post("/api/playlists", json={"name": name, "tracks": tracks}).get_json()["id"]


def tr(n, author, url_user="a"):
    return {"url": f"https://www.tiktok.com/@{url_user}/video/{n}", "title": f"Son {n}", "author": author, "duration": 20}


def test_recs_rank_sounds_matching_library_artists_first(app, make_client, signup):
    o = make_client(); signup(o, "o@example.com", "owner")
    post(o, 1, "Random", "Zed"); post(o, 2, "Tchikita", "Jul"); post(o, 3, "Djadja", "Aya Nakamura")
    d = make_client().post("/api/recs", json={"artists": ["Aya Nakamura"]}).get_json()
    assert d["mode"] == "match"
    assert d["posts"][0]["title"] == "Djadja"
    assert len(d["posts"]) == 3                          # la suite = tendances
    d = make_client().post("/api/recs", json={"words": ["tchikita"]}).get_json()
    assert d["posts"][0]["title"] == "Tchikita"


def test_recs_never_expose_emails(app, make_client, signup):
    o = make_client(); signup(o, "o@example.com", "owner")
    post(o, 1, "Djadja", "Aya Nakamura")
    pl(o, "Aya mix", [tr(9, "Aya Nakamura")])
    me = make_client(); signup(me, "me@example.com", "membre")
    r = me.post("/api/recs", json={"artists": ["aya nakamura"], "words": ["djadja", "aya"]})
    d = r.get_json()
    assert d["posts"] and d["playlists"]
    assert "@example.com" not in r.get_data(as_text=True)
    assert all("email" not in x for x in d["posts"] + d["playlists"])


def test_recs_empty_library_falls_back_to_trending(app, make_client, signup):
    o = make_client(); signup(o, "o@example.com", "owner")
    post(o, 1, "Calme", "A")
    hot = post(o, 2, "Tube", "B")
    post(o, 3, "Récent", "C")
    for i in range(2):
        f = make_client(); signup(f, f"f{i}@example.com", f"fan{i}")
        assert f.post(f"/api/posts/{hot}/like").status_code == 200
    d = make_client().post("/api/recs", json={}).get_json()
    assert d["mode"] == "trend"
    assert d["posts"][0]["id"] == hot and d["posts"][0]["likes"] == 2
    assert d["playlists"] == []
    assert make_client().post("/api/recs").status_code == 200      # pas de corps du tout


def test_recs_exclude_and_paginate(app, make_client, signup):
    o = make_client(); signup(o, "o@example.com", "owner")
    for i in range(25):
        post(o, i, f"Son {i}", "Quelqu'un")
    other = make_client(); signup(other, "x@example.com", "autre")
    pl(other, "Zed only", [tr(99, "Zed")])
    base = {"artists": ["zed"], "exclude": ["https://www.tiktok.com/@a/video/0"]}
    c = make_client()
    p0 = c.post("/api/recs", json=base).get_json()
    p1 = c.post("/api/recs", json=dict(base, offset=20)).get_json()
    assert len(p0["posts"]) == 20 and p0["more"] is True
    assert len(p1["posts"]) == 4 and p1["more"] is False
    urls = [p["url"] for p in p0["posts"] + p1["posts"]]
    assert "https://www.tiktok.com/@a/video/0" not in urls and len(set(urls)) == 24
    assert [x["name"] for x in p0["playlists"]] == ["Zed only"]
    assert p1["playlists"] == []


def test_recs_playlists_matched_by_artist_never_own(app, make_client, signup):
    other = make_client(); signup(other, "x@example.com", "autre")
    pl(other, "Chez autre", [tr(1, "Jul")])
    pl(other, "Sans rapport", [tr(2, "Zed")])
    pl(other, "Faux ami", [tr(3, "Other", url_user="julien")])      # « jul » dans le lien, pas dans l'artiste
    me = make_client(); signup(me, "me@example.com", "membre")
    pl(me, "La mienne", [tr(4, "Jul")])
    d = me.post("/api/recs", json={"artists": ["jul"]}).get_json()
    assert [x["name"] for x in d["playlists"]] == ["Chez autre"]


def test_recs_bad_input_is_harmless(app, make_client, signup):
    o = make_client(); signup(o, "o@example.com", "owner")
    post(o, 1, "Normal", "Quelqu'un")
    r = make_client().post("/api/recs", json={"artists": ["%%"], "words": [None, 5, "%_%"], "exclude": ["x"] * 500,
                                              "offset": "abc", "n": "zz"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["mode"] == "trend" and [p["title"] for p in d["posts"]] == ["Normal"]   # « % » et « _ » échappés : ils ne correspondent pas à tout
    assert make_client().post("/api/recs", json={"artists": "pas une liste"}).status_code == 200
