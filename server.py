import os, time, uuid
from flask import Flask, request, jsonify, send_from_directory
import yt_dlp

app = Flask(__name__)
os.makedirs("audio", exist_ok=True)

def nettoyer():
    for f in os.listdir("audio"):
        p = os.path.join("audio", f)
        if time.time() - os.path.getmtime(p) > 3600:
            os.remove(p)

@app.get("/")
def accueil():
    return send_from_directory(".", "index.html")

@app.get("/api/health")
def health():
    return "ok"

@app.post("/api/extract")
def extract():
    nettoyer()
    url = (request.get_json(silent=True) or {}).get("url", "")
    if "tiktok.com" not in url:
        return jsonify(error="Lien TikTok invalide"), 400
    uid = uuid.uuid4().hex
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"audio/{uid}.%(ext)s",
        "quiet": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
    except Exception:
        return jsonify(error="Extraction impossible pour ce lien"), 500
    return jsonify(
        id=uid,
        title=info.get("track") or info.get("title") or "Son TikTok",
        author=info.get("uploader", ""),
        cover=info.get("thumbnail", ""),
        audio_url=f"/audio/{uid}.mp3",
    )

@app.get("/audio/<path:f>")
def audio(f):
    return send_from_directory("audio", f)
