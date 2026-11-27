"""Logo disponible partout : onglet (tous navigateurs), iOS, Android, Windows, installation PWA."""
import json, re, struct

ICONS = {
    "/favicon.ico": "image/x-icon",
    "/favicon.svg": "image/svg+xml",
    "/favicon-16.png": "image/png",
    "/favicon-32.png": "image/png",
    "/apple-touch-icon.png": "image/png",
    "/icon-192.png": "image/png",
    "/icon-512.png": "image/png",
    "/icon-maskable-512.png": "image/png",
}


def png_size(b):
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


def test_every_icon_is_served_with_right_type(make_client):
    c = make_client()
    for url, mime in ICONS.items():
        r = c.get(url)
        assert r.status_code == 200, url
        assert r.mimetype == mime, url
        assert len(r.data) > 100, url


def test_icon_dimensions(make_client):
    c = make_client()
    for url, n in {"/favicon-16.png": 16, "/favicon-32.png": 32, "/apple-touch-icon.png": 180,
                   "/icon-192.png": 192, "/icon-512.png": 512, "/icon-maskable-512.png": 512}.items():
        assert png_size(c.get(url).data) == (n, n), url


def test_favicon_ico_holds_16_32_48(make_client):
    d = make_client().get("/favicon.ico").data
    assert d[:4] == b"\x00\x00\x01\x00"
    n = struct.unpack("<H", d[4:6])[0]
    sizes = sorted((d[6 + 16 * i] or 256) for i in range(n))
    assert sizes == [16, 32, 48]


def test_apple_touch_icon_is_opaque(make_client):
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(make_client().get("/apple-touch-icon.png").data))
    assert im.mode == "RGB" or im.getchannel("A").getextrema()[0] == 255


def test_head_declares_icons_for_every_browser(make_client):
    h = make_client().get("/").get_data(as_text=True)
    head = h.split("</head>")[0]
    for frag in ('rel="icon" href="/favicon.ico"', 'rel="shortcut icon"', 'type="image/svg+xml" href="/favicon.svg"',
                 'sizes="32x32" href="/favicon-32.png"', 'sizes="16x16" href="/favicon-16.png"',
                 'rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png"', "msapplication-TileImage"):
        assert frag in head, frag


def test_manifest_icons_exist_and_maskable_is_separate(make_client):
    c = make_client()
    m = json.loads(c.get("/manifest.webmanifest").data)
    purposes = {i["src"]: i["purpose"] for i in m["icons"]}
    assert purposes["/icon-maskable-512.png"] == "maskable"
    assert all("any" in p.split() or p == "maskable" for p in purposes.values())
    assert not any(set(p.split()) == {"any", "maskable"} for p in purposes.values())
    for i in m["icons"]:
        assert c.get(i["src"]).status_code == 200


def test_service_worker_does_not_swallow_icons():
    sw = open("sw.js").read()
    assert "tk-v13" in sw   # nouvelle version : vide l'ancien cache où manquait le logo
