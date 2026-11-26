"""Recherche de sons (YouTube, SoundCloud, Spotify si configuré) et téléchargement d'un résultat."""
import os

import pytest

YT = [{"id": f"vid{i:08d}", "title": f"Marshmello - Alone {i}", "uploader": "Marshmello", "duration": 200 + i,
       "url": f"https://www.youtube.com/watch?v=vid{i:08d}"} for i in range(8)]
YT[1]["duration"] = None            # direct : écarté
YT[2]["duration"] = 3 * 3600        # mix de 3 h : écarté
SC = [{"title": f"Alone sc {i}", "uploader": "dj", "duration": 180, "webpage_url": f"https://soundcloud.com/dj/alone-{i}",
       "url": f"https://api.soundcloud.com/tracks/{i}", "thumbnails": [{"url": "https://i1.sndcdn.com/a-t50x50.jpg", "width": 50},
                                                                       {"url": f"https://i1.sndcdn.com/a{i}-t300x300.jpg", "width": 300}]}
      for i in range(5)]


class SearchYDL:
    calls = []

    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, q, download=False):
        SearchYDL.calls.append(q)
        if q.startswith("ytsearch"):
            return {"entries": [dict(e) for e in YT]}
        if q.startswith("scsearch"):
            return {"entries": [dict(e) for e in SC]}
        raise RuntimeError(q)


@pytest.fixture
def ydl(app, monkeypatch):
    SearchYDL.calls = []
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", SearchYDL)
    return SearchYDL


def test_search_returns_five_results_per_platform(app, make_client, ydl):
    d = make_client().get("/api/find?q=marshmello alone").get_json()
    assert d["sources"] == ["youtube", "soundcloud"]
    yt, sc = d["results"]["youtube"], d["results"]["soundcloud"]
    assert len(yt) == 5 and len(sc) == 5
    assert yt[0] == {"src": "youtube", "url": "https://www.youtube.com/watch?v=vid00000000", "title": "Marshmello - Alone 0",
                     "author": "Marshmello", "duration": 200, "cover": "https://i.ytimg.com/vi/vid00000000/mqdefault.jpg"}
    assert all(r["duration"] and r["duration"] <= 1200 for r in yt)          # ni direct ni mix de 3 h
    assert sc[0]["url"] == "https://soundcloud.com/dj/alone-0" and sc[0]["cover"] == "https://i1.sndcdn.com/a0-t300x300.jpg"


def test_search_is_cached_and_validated(app, make_client, ydl):
    c = make_client()
    assert c.get("/api/find?q=a").status_code == 400
    c.get("/api/find?q=Marshmello  Alone"); n = len(ydl.calls)
    c.get("/api/find?q=marshmello alone")
    assert len(ydl.calls) == n                                                # même recherche : réponse en cache


def test_one_platform_failing_does_not_break_the_others(app, make_client, monkeypatch):
    class Half(SearchYDL):
        def extract_info(self, q, download=False):
            if q.startswith("ytsearch"):
                raise RuntimeError("Sign in to confirm you're not a bot")
            return super().extract_info(q, download)
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", Half)
    d = make_client().get("/api/find?q=alone").get_json()
    assert d["results"]["youtube"] == [] and len(d["results"]["soundcloud"]) == 5


def test_spotify_results_only_when_keys_are_set(app, make_client, ydl, monkeypatch):
    monkeypatch.setattr(app, "find_spotify", lambda q: [{"src": "spotify", "url": "https://open.spotify.com/track/" + "a" * 22,
                                                          "title": "Alone", "author": "Marshmello", "duration": 200, "cover": ""}])
    assert "spotify" not in make_client().get("/api/find?q=alone").get_json()["sources"]
    app.SPOTIFY_ID, app.SPOTIFY_SECRET = "id", "secret"
    d = make_client().get("/api/find?q=alone spotify").get_json()
    assert d["sources"] == ["youtube", "soundcloud", "spotify"] and d["results"]["spotify"][0]["title"] == "Alone"


def test_found_links_are_accepted_everywhere(app):
    for u in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
              "https://soundcloud.com/dj/alone-0"):
        assert app.ok_url(u), u
    for u in ("https://soundcloud.com/dj/sets/album", "https://www.youtube.com/playlist?list=PL1", "https://evil.com/watch?v=dQw4w9WgXcQ",
              "https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ"):
        assert not app.ok_url(u), u


class DlYDL:
    dur = 210

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        assert download is False                                              # on vérifie la durée AVANT de télécharger
        return {"title": "Marshmello - Alone (Official Video)", "uploader": "Marshmello - Topic", "duration": DlYDL.dur,
                "thumbnail": "", "webpage_url": url}

    def process_ie_result(self, info, download=True):
        with open(self.opts["outtmpl"].replace("%(ext)s", "mp3"), "wb") as f:
            f.write(b"ID3fake")
        return info


def test_download_a_youtube_result(app, make_client, monkeypatch):
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", DlYDL)
    r = make_client().post("/api/extract", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "store": "local"})
    d = r.get_json()
    assert r.status_code == 200, d
    assert d["title"] == "Marshmello - Alone (Official Video)" and d["author"] == "Marshmello"
    assert os.path.exists(d["audio_url"].lstrip("/"))


def test_too_long_results_are_refused_before_downloading(app, make_client, monkeypatch):
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", DlYDL)
    DlYDL.dur = 4 * 3600
    try:
        r = make_client().post("/api/extract", json={"url": "https://youtu.be/dQw4w9WgXcQ", "store": "local"})
    finally:
        DlYDL.dur = 210
    assert r.status_code == 400 and "20 min" in r.get_json()["error"]
    assert [f for f in os.listdir("audio") if f.endswith(".mp3")] == []


BOT = "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies"


class BlockedYDL:
    """YouTube bloque le serveur ; SoundCloud marche."""
    calls = []

    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _file(self):
        with open(self.opts["outtmpl"].replace("%(ext)s", "mp3"), "wb") as f:
            f.write(b"ID3fake")

    def extract_info(self, q, download=True):
        BlockedYDL.calls.append(q)
        if "youtube.com" in q or "youtu.be" in q or q.startswith("ytsearch"):
            raise RuntimeError(BOT)
        if q.startswith("scsearch"):
            return {"entries": [{"title": "Purpose is Glorious - Best Part Looped", "uploader": "ilijatify", "duration": 179,
                                 "webpage_url": "https://soundcloud.com/ilijatify/purpose-is-glorious"},
                                {"title": "Totally unrelated song", "uploader": "x", "duration": 30,
                                 "webpage_url": "https://soundcloud.com/x/other"}]}
        if q.startswith("https://soundcloud.com/"):
            if download:
                self._file()
            return {"title": "Purpose is Glorious - Best Part Looped", "uploader": "ilijatify", "duration": 179,
                    "thumbnail": "https://i1.sndcdn.com/x.jpg", "webpage_url": q}
        raise RuntimeError(q)

    def process_ie_result(self, info, download=True):
        self._file()
        return info


@pytest.fixture
def blocked(app, monkeypatch):
    BlockedYDL.calls = []
    app._yt_block["until"] = 0
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", BlockedYDL)
    yield BlockedYDL
    app._yt_block["until"] = 0


YT_RESULT = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Purpose is Glorious (best part looped)",
             "author": "ilostit", "duration": 125, "store": "local"}


def test_a_blocked_youtube_result_is_fetched_from_soundcloud_instead(app, make_client, blocked):
    r = make_client().post("/api/extract", json=YT_RESULT)
    d = r.get_json()
    assert r.status_code == 200, d
    assert os.path.exists(d["audio_url"].lstrip("/"))
    assert "https://soundcloud.com/ilijatify/purpose-is-glorious" in blocked.calls
    assert "https://soundcloud.com/x/other" not in blocked.calls              # pas un son au hasard
    assert d["url"] == YT_RESULT["url"] and d["title"] == YT_RESULT["title"]   # le son reste celui choisi dans la liste


def test_a_youtube_block_is_remembered_for_a_while(app, make_client, blocked):
    c = make_client()
    c.post("/api/extract", json=YT_RESULT)
    blocked.calls = []
    r = c.post("/api/extract", json=dict(YT_RESULT, url="https://youtu.be/aaaaaaaaaaa"))
    assert r.status_code == 200, r.get_json()
    assert not any("youtu" in q for q in blocked.calls)                       # on ne perd plus de temps avec YouTube


def test_spotify_skips_youtube_while_it_is_blocked(app, make_client, blocked):
    app._yt_block["until"] = app.time.time() + 600
    r = make_client().post("/api/extract", json={"url": "https://open.spotify.com/track/" + "a" * 22, "title": "Purpose is Glorious",
                                                 "author": "ilijatify", "duration": 179, "store": "local"})
    assert r.status_code == 200, r.get_json()
    assert not any(q.startswith("ytsearch") or "youtu" in q for q in blocked.calls)


def test_when_nothing_matches_the_error_says_youtube_is_blocking(app, make_client, blocked):
    r = make_client().post("/api/extract", json=dict(YT_RESULT, title="zzqx unknown", author="nobody"))
    assert r.status_code == 502 and "YouTube" in r.get_json()["error"]


def test_youtube_gets_a_javascript_runtime(app, monkeypatch):
    monkeypatch.setattr(app.shutil, "which", lambda n: "/usr/bin/" + n if n in ("deno", "node") else None)
    o = app._net_opts()
    assert o["js_runtimes"] == {"deno": {}, "node": {}}
