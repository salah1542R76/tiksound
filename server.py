import os, re, time, uuid, urllib.request, urllib.error, urllib.parse
from flask import Flask, request, jsonify, send_from_directory
import yt_dlp

app = Flask(__name__)
os.makedirs("audio", exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STATIQUE = {"manifest.webmanifest": "application/manifest+json", "sw.js": "text/javascript",
            "icon-192.png": "image/png", "icon-512.png": "image/png"}

@app.after_request
def marque(r):
    r.headers["X-Tiksound"] = "1"
    return r

def nettoyer():
    for f in os.listdir("audio"):
        p = os.path.join("audio", f)
        if time.time() - os.path.getmtime(p) > 3600:
            os.remove(p)

class _Stop(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

_OP = urllib.request.build_opener(_Stop)

def suivre(url):
    """Suit les redirections à la main pour obtenir l'adresse finale, même si la page finale est bloquée."""
    for _ in range(6):
        try:
            _OP.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10)
            return url
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code in (301, 302, 303, 307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                if re.search(r"/(video|photo)/\d+|/v/\d+", url):
                    return url
                continue
            return url
        except Exception:
            return url
    return url

def trouver_lien(texte):
    """Extrait le lien TikTok d'un texte collé, et suit les liens courts du téléphone (vm., vt., /t/)."""
    m = re.search(r"https?://[^\s]*tiktok\.com[^\s]*", texte or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,)")
    if re.search(r"//(vm|vt)\.tiktok\.com/|tiktok\.com/t/", url):
        url = suivre(url)
    v = re.search(r"tiktok\.com/v/(\d+)", url)
    if v:
        url = f"https://www.tiktok.com/@_/video/{v.group(1)}"
    return url.split("?")[0]

def titre(info):
    t = re.sub(r"#\S+", "", info.get("description") or info.get("title") or "")
    t = re.sub(r"\s+", " ", t).strip()
    if t.lower().startswith("tiktok video"):
        t = ""
    if not t:
        t = info.get("track") or ""
        if t.lower().startswith(("original sound", "son original", "sonido original")):
            t = ""
    return t[:80] or "Son TikTok"

@app.get("/")
def accueil():
    return send_from_directory(".", "index.html", max_age=0)

@app.get("/api/health")
def health():
    return "ok"

@app.post("/api/extract")
def extract():
    nettoyer()
    url = trouver_lien((request.get_json(silent=True) or {}).get("url", ""))
    if not url:
        return jsonify(error="Lien TikTok invalide"), 400
    uid = uuid.uuid4().hex
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"audio/{uid}.%(ext)s",
        "quiet": True,
        "socket_timeout": 20,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
    }
    info = None
    # Les publications « diaporama photo » (/photo/) se lisent comme des vidéos : on essaie les deux formes.
    essais = [url.replace("/photo/", "/video/"), url] if "/photo/" in url else [url]
    for u in essais:
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(u, download=True)
            url = u
            break
        except Exception:
            continue
    if not info:
        return jsonify(error="Impossible de récupérer le son de ce lien. Vérifie qu’il est public, puis réessaie dans un instant."), 500
    return jsonify(
        id=uid,
        url=url,
        title=titre(info),
        author=info.get("uploader", ""),
        cover=info.get("thumbnail", ""),
        audio_url=f"/audio/{uid}.mp3",
    )

@app.get("/audio/<path:f>")
def audio(f):
    return send_from_directory("audio", f)

@app.get("/<path:f>")
def statique(f):
    if f not in STATIQUE:
        return "", 404
    return send_from_directory(".", f, mimetype=STATIQUE[f], max_age=0)
