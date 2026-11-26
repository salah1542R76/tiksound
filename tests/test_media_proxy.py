"""Sons en ligne relayés par le site (même origine) : le moteur d'effets peut les traiter sans réglage CORS sur R2."""
import pytest

KEY = "audio/" + "a" * 40 + ".mp3"
DATA = bytes(range(256)) * 10000          # 2,56 Mo


@pytest.fixture
def r2(app, monkeypatch):
    app.R2_OK = True
    calls = []

    def fake(key, start, end):
        calls.append((key, start, end))
        if key != KEY:
            raise KeyError(key)
        if start >= len(DATA):
            raise IndexError
        return DATA[start:end + 1], len(DATA)
    app.r2_range = fake
    return calls


def test_range_request_returns_a_bounded_partial_chunk(app, make_client, r2):
    r = make_client().get("/api/media/" + KEY, headers={"Range": "bytes=0-"})
    assert r.status_code == 206 and r.mimetype == "audio/mpeg"
    assert len(r.data) == app.MEDIA_CHUNK and r.data == DATA[:app.MEDIA_CHUNK]
    assert r.headers["Content-Range"] == f"bytes 0-{app.MEDIA_CHUNK - 1}/{len(DATA)}"
    assert r.headers["Accept-Ranges"] == "bytes"


def test_explicit_end_and_last_chunk(app, make_client, r2):
    c = make_client()
    r = c.get("/api/media/" + KEY, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.data == DATA[100:200]
    r = c.get("/api/media/" + KEY, headers={"Range": f"bytes={len(DATA) - 50}-"})
    assert r.data == DATA[-50:] and r.headers["Content-Range"] == f"bytes {len(DATA) - 50}-{len(DATA) - 1}/{len(DATA)}"


def test_suffix_range(app, make_client, r2):
    r = make_client().get("/api/media/" + KEY, headers={"Range": "bytes=-128"})
    assert r.status_code == 206 and r.data == DATA[-128:]


def test_out_of_range_is_416(app, make_client, r2):
    r = make_client().get("/api/media/" + KEY, headers={"Range": f"bytes={len(DATA) + 5}-"})
    assert r.status_code == 416


def test_only_audio_files_from_the_bucket_can_be_read(app, make_client, r2):
    c = make_client()
    for bad in ("meta/" + "a" * 40 + ".json", "audio/../secret", "audio/abc.mp3", "audio/" + "a" * 40 + ".wav"):
        assert c.get("/api/media/" + bad, headers={"Range": "bytes=0-"}).status_code == 404, bad
    assert r2 == []


def test_missing_file_is_404_and_disabled_without_r2(app, make_client, r2):
    c = make_client()
    assert c.get("/api/media/audio/" + "b" * 40 + ".mp3", headers={"Range": "bytes=0-"}).status_code == 404
    app.R2_OK = False
    assert c.get("/api/media/" + KEY, headers={"Range": "bytes=0-"}).status_code == 404


def test_a_huge_explicit_range_is_still_capped(app, make_client, r2):
    r = make_client().get("/api/media/" + KEY, headers={"Range": "bytes=0-2500000"})
    assert r.status_code == 206 and len(r.data) == app.MEDIA_CHUNK
