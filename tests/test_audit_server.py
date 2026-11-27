"""Corrections de l'audit du 27/11/2026 côté serveur (sécurité, données, cache)."""
import io
import json
import os
import re
import threading
import time

import pytest

TT = "https://www.tiktok.com/@someone/video/7234567890123456789"
SPOT = "https://open.spotify.com/track/" + "a" * 22


def mp3s():
    return [f for f in os.listdir("audio") if f.endswith(".mp3")]


def write_mp3(opts):
    with open(opts["outtmpl"].replace("%(ext)s", "mp3"), "wb") as f:
        f.write(b"ID3fake")


class RecYDL:
    """Note chaque adresse demandée à yt-dlp ; n'écrit le fichier que si le filtre de yt-dlp (match_filter) l'accepte."""
    calls, info = [], {}

    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        RecYDL.calls.append(url)
        info = dict(RecYDL.info)
        mf = self.opts.get("match_filter")
        if download and not (mf and mf(info, incomplete=False)):
            write_mp3(self.opts)
        return info


@pytest.fixture
def rec(app, monkeypatch):
    RecYDL.calls, RecYDL.info = [], {"title": "Un son", "uploader": "someone", "duration": 30, "thumbnail": ""}
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", RecYDL)
    return RecYDL


# ── 3 : la synchronisation renvoie ce que le serveur a vraiment gardé ──
def test_library_put_returns_what_was_stored(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    tracks = [{"url": f"https://www.tiktok.com/@a/video/{i}", "title": "t"} for i in range(600)]
    tracks.append({"url": "https://evil.example/x", "title": "refusé"})
    pls = [{"id": "p1", "name": "Grosse", "urls": [t["url"] for t in tracks]}, {"id": "bad id!", "name": "x", "urls": []}]
    r = c.put("/api/me/library", json={"data": {"tracks": tracks, "pls": pls}, "base": None})
    d = r.get_json()
    assert r.status_code == 200 and d["ok"] is True and d["updated"]
    assert len(d["data"]["tracks"]) == 600 and "https://evil.example/x" not in [t["url"] for t in d["data"]["tracks"]]
    assert [p["id"] for p in d["data"]["pls"]] == ["p1"]
    assert len(d["data"]["pls"][0]["urls"]) == 600                     # une playlist de plus de 500 sons n'est plus coupée
    assert c.get("/api/me/library").get_json()["data"] == d["data"]


# ── 10 : la branche TikTok n'accepte que des adresses tiktok.com ──
@pytest.mark.parametrize("url", ["http://127.0.0.1:1/x?a=tiktok.com", "http://169.254.169.254/latest/meta-data/?x=tiktok.com",
                                 "https://www.youtube.com/live/abcdefghijk?tiktok.com"])
def test_extract_refuses_hosts_other_than_tiktok(app, make_client, rec, url):
    r = make_client().post("/api/extract", json={"url": url, "store": "local"})
    assert r.status_code == 400 and rec.calls == [] and mp3s() == []


def test_short_link_resolution_never_opens_another_host(app, monkeypatch):
    opened = []

    class Redirect(app.urllib.error.HTTPError):
        def __init__(self, loc):
            super().__init__("u", 302, "Found", {"Location": loc}, None)

    def fake_open(req, timeout=10):
        opened.append(req.full_url)
        if "vm.tiktok.com" in req.full_url:
            raise Redirect("http://169.254.169.254/latest/")
        return None
    monkeypatch.setattr(app._OP, "open", fake_open)
    app.trouver_lien("http://attacker.example/r?x=//vm.tiktok.com/")
    assert opened == []                                                  # « vm.tiktok.com » dans la requête : rien n'est ouvert
    out = app.trouver_lien("https://vm.tiktok.com/ZMabc/")
    assert opened == ["https://vm.tiktok.com/ZMabc/"]                   # la redirection vers l'adresse interne n'est pas suivie
    assert not app.TT_RE.match(out)


def test_real_tiktok_links_still_work(app, make_client, rec):
    r = make_client().post("/api/extract", json={"url": "regarde " + TT + "?is_from_webapp=1 !", "store": "local"})
    assert r.status_code == 200, r.get_json()
    assert rec.calls == [TT]


# ── 11 : codes de vérification ──
def test_code_attempts_are_counted_atomically(app, make_client):
    c = make_client()
    assert c.post("/api/auth/register", json={"email": "v@example.com", "username": "victime", "password": "motdepasse1"}).status_code == 200
    code = re.match(r"(\d{6})", app.OUTBOX[-1]["subject"]).group(1)
    stale = app.q1("SELECT * FROM codes WHERE email='v@example.com' AND purpose='register'")   # lu avant les essais parallèles
    app.x("UPDATE codes SET attempts=:m WHERE email='v@example.com'", m=app.MAX_ATTEMPTS)
    with app.app.test_request_context():
        assert "Trop d'essais" in app.check_code(stale, "v@example.com", "register", "000000")
        assert "Trop d'essais" in app.check_code(stale, "v@example.com", "register", code)   # même le bon code
    assert app.q1("SELECT attempts FROM codes WHERE email='v@example.com'").attempts == app.MAX_ATTEMPTS


def test_verify_and_reset_are_rate_limited_per_ip(app, make_client):
    c = make_client()
    codes = [c.post("/api/auth/verify", json={"email": f"x{i}@example.com", "code": "123456"}).status_code for i in range(20)]
    codes += [c.post("/api/auth/reset", json={"email": f"x{i}@example.com", "code": "123456", "password": "motdepasse1"}).status_code
              for i in range(20)]
    assert codes[0] == 400 and codes[-1] == 429


def test_new_codes_per_email_are_capped(app, make_client):
    c = make_client()
    assert c.post("/api/auth/register", json={"email": "v@example.com", "username": "victime", "password": "motdepasse1"}).status_code == 200
    res = []
    for _ in range(app.MAX_CODES_PER_DAY):
        app.x("UPDATE codes SET last_sent=0 WHERE email='v@example.com'")
        res.append(c.post("/api/auth/resend", json={"email": "v@example.com"}).status_code)
    assert res[:-1] == [200] * (app.MAX_CODES_PER_DAY - 1) and res[-1] == 429


def test_forgot_stops_sending_codes_after_the_cap_without_revealing_it(app, make_client, signup):
    c = make_client(); signup(c, "a@example.com", "alice")
    bad = {"REMOTE_ADDR": "203.0.113.9"}
    sent = 0
    for _ in range(app.MAX_CODES_PER_DAY + 2):
        app.x("UPDATE codes SET last_sent=0 WHERE email='a@example.com'")
        for k in [k for k in app._hits if k.startswith("forgot:")]:
            app._hits.pop(k)
        n = len(app.OUTBOX)
        assert c.post("/api/auth/forgot", json={"email": "a@example.com"}, environ_base=bad).status_code == 200
        sent += len(app.OUTBOX) - n
    assert sent == app.MAX_CODES_PER_DAY
    # le plafond vaut par adresse IP : un inconnu qui connaît l'email ne bloque pas la vraie personne pendant 24 h
    app.x("UPDATE codes SET last_sent=0 WHERE email='a@example.com'")
    n = len(app.OUTBOX)
    assert c.post("/api/auth/forgot", json={"email": "a@example.com"}, environ_base={"REMOTE_ADDR": "198.51.100.7"}).status_code == 200
    assert len(app.OUTBOX) == n + 1


# ── 12 : pas de direct, ni de son de plus de 20 min, sur aucune branche ──
def test_download_filter_refuses_long_and_live(app):
    f = app.no_long_or_live
    assert f({"duration": 4000}, incomplete=False) and f({"is_live": True, "duration": 10}, incomplete=False)
    assert f({"duration": 300}, incomplete=False) is None and f({}, incomplete=False) is None


def test_tiktok_too_long_or_live_is_refused(app, make_client, rec):
    rec.info = dict(rec.info, duration=4000)
    r = make_client().post("/api/extract", json={"url": TT, "store": "local"})
    assert r.status_code == 400 and "20 min" in r.get_json()["error"] and mp3s() == []
    rec.info = dict(rec.info, duration=0, is_live=True)
    r = make_client().post("/api/extract", json={"url": TT, "store": "local"})
    assert r.status_code == 400 and mp3s() == []


def test_spotify_search_skips_long_results_and_filtered_downloads(app, monkeypatch, tmp_path):
    entries = [{"webpage_url": "https://www.youtube.com/watch?v=longlonglon", "title": "Song 10 hours", "duration": 36000},
               {"webpage_url": "https://www.youtube.com/watch?v=okokokokoko", "title": "Song", "duration": 200}]

    class S(RecYDL):
        def extract_info(self, url, download=True):
            return {"entries": entries}
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", S)
    assert [c["duration"] for c in app.search_candidates("ytsearch", "song")] == [200]

    cands = [{"url": f"https://www.youtube.com/watch?v={c * 11}", "duration": 0, "src": "ytsearch", "title": "Song", "chan": "Artist"} for c in "ab"]
    monkeypatch.setattr(app, "search_candidates", lambda prefix, q, n=6: [dict(c) for c in cands] if prefix == "ytsearch" else [])
    got = []

    class F(RecYDL):
        def extract_info(self, url, download=True):
            got.append(url)
            if url.endswith("b" * 11):
                write_mp3(self.opts)                                     # « a » refusé par le filtre : aucun fichier écrit
            return {"title": "Song", "duration": 0}
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", F)
    info = app.audio_from_search({"outtmpl": "audio/zz.%(ext)s"}, "Song", "Artist", 0)
    assert info is not None and got[-1].endswith("b" * 11) and os.path.exists("audio/zz.mp3")


# ── 44 : un appelant ne peut pas empoisonner le cache partagé ──
class MemS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.objs[key] = b"mp3"


@pytest.fixture
def mem_r2(app, monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("R2_PUBLIC_URL", "https://cdn.example")
    app.R2_OK, app.STRIPE_KEY = True, ""
    s3 = MemS3()
    monkeypatch.setattr(app, "s3", lambda: s3)
    return s3


def test_spotify_cache_does_not_follow_the_first_callers_title(app, make_client, mem_r2, monkeypatch):
    def fake_search(opts, title, author, want):
        write_mp3(opts)
        return {"title": title, "duration": want, "thumbnail": ""}
    monkeypatch.setattr(app, "audio_from_search", fake_search)
    c = make_client()
    bad = c.post("/api/extract", json={"url": SPOT, "title": "Chanson piégée", "author": "Pirate", "duration": 180, "store": "online"}).get_json()
    good = c.post("/api/extract", json={"url": SPOT, "title": "Vrai titre", "author": "Vrai artiste", "duration": 180, "store": "online"}).get_json()
    assert bad["title"] == "Chanson piégée" and good["title"] == "Vrai titre" and good["audio_url"] != bad["audio_url"]
    again = c.post("/api/extract", json={"url": SPOT, "title": "Vrai titre", "author": "Vrai artiste", "duration": 180, "store": "online"}).get_json()
    assert again["audio_url"] == good["audio_url"]                      # même demande : le cache sert toujours


def test_found_cache_stores_the_real_title(app, make_client, mem_r2, rec):
    rec.info = {"title": "Vrai titre YouTube", "uploader": "Chaîne - Topic", "duration": 200, "thumbnail": ""}
    yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    class Pre(RecYDL):
        def process_ie_result(self, info, download=True):
            write_mp3(self.opts)
            return info
    app.yt_dlp.YoutubeDL = Pre
    c = make_client()
    first = c.post("/api/extract", json={"url": yt, "title": "Titre inventé", "author": "Pirate", "store": "online"}).get_json()
    assert first["title"] == "Titre inventé"                            # la personne garde son titre
    other = c.post("/api/extract", json={"url": yt, "store": "online"}).get_json()
    assert other["title"] == "Vrai titre YouTube" and other["author"] == "Chaîne"


# ── 42 : la page d'accueil se revalide en 304 ──
def test_home_and_manifest_answer_304_when_unchanged(app, make_client):
    c = make_client()
    for path in ("/", "/manifest.webmanifest", "/conditions"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers.get("ETag") and r.headers["Cache-Control"] == "no-cache"
        r2 = c.get(path, headers={"If-None-Match": r.headers["ETag"]})
        assert r2.status_code == 304 and r2.data == b""


# ── 46 : pas de deuxième abonnement en parallèle ──
def fake_stripe(subs, open_sessions, created, expired):
    class Session:
        @staticmethod
        def create(**kw):
            created.append(kw)
            return type("S", (), {"client_secret": "cs_x"})()

        @staticmethod
        def list(**kw):
            assert kw["customer"] == "cus_1" and kw["status"] == "open"
            return {"data": [{"id": s} for s in open_sessions]}

        @staticmethod
        def expire(sid):
            expired.append(sid)

    class Subscription:
        @staticmethod
        def list(**kw):
            assert kw["customer"] == "cus_1"
            return {"data": subs}

    class Checkout:
        pass
    Checkout.Session = Session

    class Fake:
        checkout = Checkout
    Fake.Subscription = Subscription
    return Fake


def test_checkout_refused_while_a_subscription_is_live_at_stripe(app, make_client, signup, monkeypatch):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "sk_test", "price_1", True
    c = make_client(); signup(c, "a@example.com", "alice")
    app.x("UPDATE users SET stripe_customer_id='cus_1' WHERE email='a@example.com'")   # paiement fait, webhook pas encore arrivé
    created, expired = [], []
    monkeypatch.setattr(app, "_stripe", lambda: fake_stripe([{"id": "sub_1", "status": "active"}], ["cs_old"], created, expired))
    r = c.post("/api/stripe/checkout")
    assert r.status_code == 409 and r.get_json()["portal"] is True and created == []
    assert expired == ["cs_old"]


def test_checkout_closes_older_open_sessions(app, make_client, signup, monkeypatch):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "sk_test", "price_1", True
    c = make_client(); signup(c, "a@example.com", "alice")
    app.x("UPDATE users SET stripe_customer_id='cus_1' WHERE email='a@example.com'")
    created, expired = [], []
    monkeypatch.setattr(app, "_stripe", lambda: fake_stripe([{"id": "sub_0", "status": "canceled"}], ["cs_tab_a"], created, expired))
    r = c.post("/api/stripe/checkout")
    assert r.status_code == 200 and len(created) == 1 and expired == ["cs_tab_a"]   # l'autre onglet ne peut plus payer


def test_checkout_still_checks_subscriptions_when_closing_an_old_session_fails(app, make_client, signup, monkeypatch):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "sk_test", "price_1", True
    c = make_client(); signup(c, "a@example.com", "alice")
    app.x("UPDATE users SET stripe_customer_id='cus_1' WHERE email='a@example.com'")
    created, expired = [], []
    fake = fake_stripe([{"id": "sub_1", "status": "active"}], ["cs_paid"], created, expired)

    def boom(sid):
        raise RuntimeError("session déjà payée dans l'autre onglet")
    fake.checkout.Session.expire = staticmethod(boom)
    monkeypatch.setattr(app, "_stripe", lambda: fake)
    r = c.post("/api/stripe/checkout")
    assert r.status_code == 409 and created == []


# ── 77 : /api/find répond à l'heure même si une plateforme traîne ──
def test_find_returns_on_time_when_a_source_hangs(app, make_client, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(app, "FIND_TIMEOUT", 0.5)
    monkeypatch.setattr(app, "find_yt", lambda q: gate.wait(5) and [])
    monkeypatch.setattr(app, "find_sc", lambda q: [{"src": "soundcloud", "url": "https://soundcloud.com/a/b", "title": "x y",
                                                    "author": "a", "duration": 100, "cover": ""}])
    t = time.time()
    r = make_client().get("/api/find?q=x y")
    took = time.time() - t
    gate.set()
    assert r.status_code == 200 and took < 2
    d = r.get_json()["results"]
    assert d["youtube"] == [] and len(d["soundcloud"]) == 1


def test_hung_searches_never_pile_up_threads(app, make_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    gate = threading.Event()
    pool = ThreadPoolExecutor(2)
    monkeypatch.setattr(app, "_FIND_POOL", pool)
    monkeypatch.setattr(app, "FIND_TIMEOUT", 0.2)
    monkeypatch.setattr(app, "find_yt", lambda q: gate.wait(10) and [])
    monkeypatch.setattr(app, "find_sc", lambda q: gate.wait(10) and [])
    before = threading.active_count()
    c = make_client()
    try:
        for i in range(6):
            assert c.get(f"/api/find?q=recherche {i}").status_code == 200
        assert threading.active_count() - before <= 2          # les recherches bloquées attendent leur tour
    finally:
        gate.set(); pool.shutdown(wait=True)


# ── 78 : plage inversée ──
def test_inverted_range_is_416_without_asking_r2(app, make_client):
    app.R2_OK = True
    calls = []
    app.r2_range = lambda k, a, b: calls.append((a, b)) or (b"x" * 10, 10000)
    r = make_client().get("/api/media/audio/" + "a" * 40 + ".mp3", headers={"Range": "bytes=500-100"})
    assert r.status_code == 416 and calls == []


# ── 88 : couvertures TikTok expirées ──
def test_expired_signed_covers_are_not_sent(app, make_client, signup):
    old = f"https://p19.tiktokcdn-us.com/x.image?x-expires={int(time.time()) - 60}&sig=a"
    new = f"https://p19.tiktokcdn-us.com/y.image?x-expires={int(time.time()) + 3600}&sig=b"
    c = make_client(); signup(c, "o@example.com", "owner")
    tr = [{"url": f"https://www.tiktok.com/@a/video/{i}", "title": "s", "cover": cv} for i, cv in enumerate([old, new, "https://i.ytimg.com/vi/a/mq.jpg"])]
    pid = c.post("/api/playlists", json={"name": "P", "tracks": tr}).get_json()["id"]
    prev = make_client().get("/api/feed").get_json()["recent"][0]["preview"]
    assert [p["cover"] for p in prev] == ["", new, "https://i.ytimg.com/vi/a/mq.jpg"]
    html = make_client().get(f"/?pl={pid}").get_data(as_text=True)
    assert "og:image" in html and old.replace("&", "&amp;") not in html and new.replace("&", "&amp;") in html
    full = make_client().get(f"/api/playlists/{pid}").get_json()["tracks"]
    assert full[0]["cover"] == ""


# ── 92 : cache des fichiers statiques ──
def test_static_cache_lifetimes(app, make_client):
    c = make_client()
    v = c.get("/lang-en.js?v=11").headers["Cache-Control"]
    assert "max-age=31536000" in v and "immutable" in v
    sw = c.get("/sw.js?v=11").headers["Cache-Control"]
    assert "max-age=0" in sw or "no-cache" in sw                         # le service worker se revalide toujours
    assert "max-age=86400" in c.get("/icon-192.png").headers["Cache-Control"]
