"""Vidéo de présentation de l'accueil : servie par le site lui-même, lisible par morceaux (iPhone), jamais gardée par le service worker."""


def test_promo_video_answers_range_requests(make_client):
    c = make_client()
    full = c.get("/promo.mp4?v=1")
    assert full.status_code == 200 and full.mimetype == "video/mp4"
    assert full.headers["Accept-Ranges"] == "bytes"
    assert "max-age=31536000" in full.headers["Cache-Control"]   # adresse versionnée : cache long
    r = c.get("/promo.mp4?v=1", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100           # Safari iPhone refuse une vidéo sans 206
    assert r.headers["Content-Range"].startswith("bytes 0-99/")
    d = full.data
    assert len(d) < 5_000_000
    assert 0 <= d.find(b"moov") < d.find(b"mdat")                 # faststart : la lecture démarre avant la fin du téléchargement


def test_promo_poster_is_a_small_jpeg(make_client):
    r = make_client().get("/promo-poster.jpg?v=1")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and len(r.data) < 80_000


def test_promo_is_linked_versioned_from_the_home_page():
    html = open("index.html", encoding="utf-8").read()
    assert 'src="/promo.mp4?v=' in html and 'poster="/promo-poster.jpg?v=' in html


def test_service_worker_never_caches_the_promo():
    sw = open("sw.js", encoding="utf-8").read()
    assert 'startsWith("/promo")' in sw   # une réponse 206 ne se met pas en cache, et un 200 gardé casserait la lecture sur iPhone
