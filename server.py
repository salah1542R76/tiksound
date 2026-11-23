import os, re, time, uuid, urllib.request
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

def trouver_lien(texte):
    """Extrait le lien TikTok d'un texte collé, et suit les liens courts du téléphone (vm., vt., /t/)."""
    m = re.search(r"https?://[^\s]*tiktok\.com[^\s]*", texte or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,)")
    if re.search(r"//(vm|vt)\.tiktok\.com/|tiktok\.com/t/", url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            url = urllib.request.urlopen(req, timeout=10).geturl()
        except Exception:
            pass
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
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
    except Exception:
        return jsonify(error="Extraction impossible : TikTok bloque parfois le serveur. Réessaie dans un instant."), 500
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
