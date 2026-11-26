import os

import pytest

URL = "https://www.tiktok.com/@someone/video/7234567890123456789"
R2_URL = "https://cdn.example"


class FakeS3:
    def __init__(self, fail=False):
        self.fail, self.uploads, self.puts = fail, [], []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        if self.fail:
            raise RuntimeError("R2 indisponible")
        assert os.path.exists(path)
        self.uploads.append(key)

    def put_object(self, **kw):
        self.puts.append(kw["Key"])

    def get_object(self, **kw):
        raise KeyError("pas en cache")


class FakeYDL:
    """Écrit audio/<uid>.mp3 comme le ferait yt-dlp + ffmpeg."""
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        with open(self.opts["outtmpl"].replace("%(ext)s", "mp3"), "wb") as f:
            f.write(b"ID3fake")
        return {"title": "Un son", "uploader": "someone", "duration": 12, "thumbnail": ""}


@pytest.fixture
def env(app, monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("R2_PUBLIC_URL", R2_URL)
    app.R2_OK = True
    app.STRIPE_KEY, app.STRIPE_PRICE = "sk_test", "price_1"
    s3 = FakeS3()
    monkeypatch.setattr(app, "s3", lambda: s3)
    monkeypatch.setattr(app.yt_dlp, "YoutubeDL", FakeYDL)
    return s3


def extract(client, store):
    return client.post("/api/extract", json={"url": URL, "store": store})


def local_mp3s():
    return [f for f in os.listdir("audio") if f.endswith(".mp3")]


def premium_client(app, make_client, signup):
    c = make_client()
    signup(c, "p@example.com", "payeur")
    app.x("UPDATE users SET gift_until=:g WHERE email='p@example.com'", g=app.FOREVER)
    return c


def free_client(make_client, signup):
    c = make_client()
    signup(c, "f@example.com", "gratuit")
    return c


def test_free_forced_online_even_if_local_requested(app, env, make_client, signup):
    r = extract(free_client(make_client, signup), "local")
    d = r.get_json()
    assert r.status_code == 200 and d["remote"] is True and d["audio_url"].startswith(R2_URL + "/audio/")
    assert len(env.uploads) == 1 and local_mp3s() == []


def test_free_r2_failure_returns_502_without_local_file(app, env, make_client, signup, monkeypatch):
    monkeypatch.setattr(app, "s3", lambda: FakeS3(fail=True))
    r = extract(free_client(make_client, signup), "local")
    assert r.status_code == 502 and r.get_json()["retry"] is True
    assert local_mp3s() == []


def test_premium_local_allowed(app, env, make_client, signup):
    r = extract(premium_client(app, make_client, signup), "local")
    d = r.get_json()
    assert r.status_code == 200 and not d.get("remote") and d["audio_url"] == f"/audio/{d['id']}.mp3"
    assert env.uploads == [] and local_mp3s() == [d["id"] + ".mp3"]


def test_premium_online_failure_still_falls_back_local(app, env, make_client, signup, monkeypatch):
    monkeypatch.setattr(app, "s3", lambda: FakeS3(fail=True))
    r = extract(premium_client(app, make_client, signup), "online")
    d = r.get_json()
    assert r.status_code == 200 and not d.get("remote") and len(local_mp3s()) == 1


def test_premium_online_goes_to_r2(app, env, make_client, signup):
    d = extract(premium_client(app, make_client, signup), "online").get_json()
    assert d["remote"] is True and len(env.uploads) == 1 and local_mp3s() == []


def test_anonymous_treated_as_free(app, env, make_client):
    r = extract(make_client(), "local")
    assert r.status_code == 200 and r.get_json()["remote"] is True and local_mp3s() == []


def test_paywall_off_keeps_old_behaviour(app, env, make_client, signup):
    app.STRIPE_KEY = ""
    r = extract(free_client(make_client, signup), "local")
    d = r.get_json()
    assert r.status_code == 200 and not d.get("remote") and env.uploads == [] and len(local_mp3s()) == 1


def test_no_r2_never_blocks_extraction(app, env, make_client, signup):
    app.R2_OK = False                                   # sans R2, paywall_on() est faux : tout reste local
    r = extract(free_client(make_client, signup), "local")
    assert r.status_code == 200 and not r.get_json().get("remote")


def test_ads_route_serves_file_and_blocks_traversal(app, make_client):
    with open("ads/pub.png", "wb") as f:
        f.write(b"\x89PNGfake")
    c = make_client()
    r = c.get("/ads/pub.png")
    assert r.status_code == 200 and r.data == b"\x89PNGfake" and "max-age=3600" in r.headers["Cache-Control"]
    assert c.get("/ads/absente.png").status_code == 404
    assert c.get("/ads/../server.py").status_code == 404
    assert c.get("/ads/%2e%2e/server.py").status_code == 404
    assert c.get("/ads/%2e%2e%2fserver.py").status_code == 404
