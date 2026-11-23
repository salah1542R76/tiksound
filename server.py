import os, re, time, uuid, hashlib, hmac, json, base64, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, make_response
import yt_dlp
from sqlalchemy import create_engine, text

app = Flask(__name__)
os.makedirs("audio", exist_ok=True)

# ── Config ──────────────────────────────────────────────────────────────────
SECRET     = os.environ.get("JWT_SECRET", "change-me-in-prod-" + uuid.uuid4().hex)
DB_URL     = os.environ.get("DATABASE_URL", "sqlite:///tiksound.db")
TOKEN_DAYS = 30
UA         = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
STATIQUE   = {
    "manifest.webmanifest": "application/manifest+json",
    "sw.js":                "text/javascript",
    "icon-192.png":         "image/png",
    "icon-512.png":         "image/png",
}

# ── Database ─────────────────────────────────────────────────────────────────
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {})

def db():
    return engine.connect()

def init_db():
    with db() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id       TEXT PRIMARY KEY,
            email    TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            pw_hash  TEXT NOT NULL,
            color    INTEGER DEFAULT 0,
            created  TEXT DEFAULT (datetime('now'))
        )"""))
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS public_playlists (
            id        TEXT PRIMARY KEY,
            user_id   TEXT NOT NULL,
            name      TEXT NOT NULL,
            tracks    TEXT NOT NULL,          -- JSON array [{url,title,author,cover}]
            updated   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )"""))
        c.commit()

init_db()

# ── JWT (HS256, manual, no deps) ─────────────────────────────────────────────
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _unb64(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)

def jwt_encode(payload: dict) -> str:
    hdr = _b64(b'{"alg":"HS256","typ":"JWT"}')
    pay = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(SECRET.encode(), f"{hdr}.{pay}".encode(), hashlib.sha256).digest())
    return f"{hdr}.{pay}.{sig}"

def jwt_decode(token: str) -> dict | None:
    try:
        hdr, pay, sig = token.split(".")
        expected = _b64(hmac.new(SECRET.encode(), f"{hdr}.{pay}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(pay))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None

def make_token(user_id: str, username: str) -> str:
    return jwt_encode({"sub": user_id, "usr": username, "exp": time.time() + TOKEN_DAYS * 86400})

def current_user():
    tok = request.cookies.get("tk_sess") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    return jwt_decode(tok) if tok else None

def require_auth():
    u = current_user()
    if not u:
        return None, (jsonify(error="Non authentifié"), 401)
    return u, None

def hash_pw(pw: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), SECRET.encode(), 260_000).hex()

# ── Helpers ───────────────────────────────────────────────────────────────────
@app.after_request
def marque(r):
    r.headers["X-Tiksound"] = "1"
    return r

def nettoyer():
    for f in os.listdir("audio"):
        p = os.path.join("audio", f)
        if time.time() - os.path.getmtime(p) > 3600:
            try: os.remove(p)
            except: pass

class _Stop(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

_OP = urllib.request.build_opener(_Stop)

def suivre(url):
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
    m = re.search(r"https?://[^\s]*tiktok\.com[^\s]*", texte or "")
    if not m: return None
    url = m.group(0).rstrip(".,)")
    if re.search(r"//(vm|vt)\.tiktok\.com/|tiktok\.com/t/", url):
        url = suivre(url)
    v = re.search(r"tiktok\.com/v/(\d+)", url)
    if v: url = f"https://www.tiktok.com/@_/video/{v.group(1)}"
    return url.split("?")[0]

def titre(info):
    t = re.sub(r"#\S+", "", info.get("description") or info.get("title") or "")
    t = re.sub(r"\s+", " ", t).strip()
    if t.lower().startswith("tiktok video"): t = ""
    if not t:
        t = info.get("track") or ""
        if t.lower().startswith(("original sound", "son original", "sonido original")): t = ""
    return t[:80] or "Son TikTok"

def user_row(row) -> dict:
    return {"id": row.id, "username": row.username, "color": row.color}

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register():
    d       = request.get_json(silent=True) or {}
    email   = (d.get("email") or "").strip().lower()
    username= re.sub(r"[^a-zA-Z0-9_\-]", "", (d.get("username") or "")).strip()[:24]
    pw      = d.get("password") or ""
    if not email or "@" not in email:
        return jsonify(error="Email invalide"), 400
    if len(username) < 3:
        return jsonify(error="Pseudo trop court (3 car. min)"), 400
    if len(pw) < 6:
        return jsonify(error="Mot de passe trop court (6 car. min)"), 400
    uid = uuid.uuid4().hex
    try:
        with db() as c:
            c.execute(text("INSERT INTO users (id,email,username,pw_hash) VALUES (:id,:e,:u,:h)"),
                      {"id": uid, "e": email, "u": username, "h": hash_pw(pw)})
            c.commit()
    except Exception as ex:
        msg = str(ex)
        if "email" in msg:    return jsonify(error="Email déjà utilisé"), 409
        if "username" in msg: return jsonify(error="Pseudo déjà pris"), 409
        return jsonify(error="Erreur serveur"), 500
    tok = make_token(uid, username)
    resp = make_response(jsonify(ok=True, username=username, id=uid, color=0))
    resp.set_cookie("tk_sess", tok, httponly=True, samesite="Lax", max_age=TOKEN_DAYS*86400, secure=request.is_secure)
    return resp

@app.post("/api/auth/login")
def login():
    d  = request.get_json(silent=True) or {}
    em = (d.get("email") or "").strip().lower()
    pw = d.get("password") or ""
    with db() as c:
        row = c.execute(text("SELECT * FROM users WHERE email=:e"), {"e": em}).fetchone()
    if not row or not hmac.compare_digest(row.pw_hash, hash_pw(pw)):
        return jsonify(error="Email ou mot de passe incorrect"), 401
    tok = make_token(row.id, row.username)
    resp = make_response(jsonify(ok=True, username=row.username, id=row.id, color=row.color))
    resp.set_cookie("tk_sess", tok, httponly=True, samesite="Lax", max_age=TOKEN_DAYS*86400, secure=request.is_secure)
    return resp

@app.post("/api/auth/logout")
def logout():
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie("tk_sess")
    return resp

@app.get("/api/auth/me")
def me():
    u = current_user()
    if not u: return jsonify(error="Non authentifié"), 401
    with db() as c:
        row = c.execute(text("SELECT * FROM users WHERE id=:id"), {"id": u["sub"]}).fetchone()
    if not row: return jsonify(error="Utilisateur introuvable"), 404
    return jsonify(id=row.id, username=row.username, color=row.color, email=row.email)

@app.patch("/api/auth/me")
def update_me():
    u, err = require_auth()
    if err: return err
    d = request.get_json(silent=True) or {}
    fields, params = [], {"id": u["sub"]}
    if "color" in d:
        fields.append("color=:color"); params["color"] = int(d["color"]) % 5
    if not fields: return jsonify(ok=True)
    with db() as c:
        c.execute(text(f"UPDATE users SET {','.join(fields)} WHERE id=:id"), params)
        c.commit()
    return jsonify(ok=True)

# ── User / search routes ───────────────────────────────────────────────────────
@app.get("/api/users/search")
def search_users():
    q  = (request.args.get("q") or "").strip()
    if len(q) < 2: return jsonify(users=[])
    with db() as c:
        rows = c.execute(text(
            "SELECT id,username,color FROM users WHERE username LIKE :q LIMIT 20"
        ), {"q": f"%{q}%"}).fetchall()
    return jsonify(users=[{"id": r.id, "username": r.username, "color": r.color} for r in rows])

@app.get("/api/users/<username>")
def get_user(username):
    with db() as c:
        row = c.execute(text("SELECT id,username,color FROM users WHERE username=:u"), {"u": username}).fetchone()
    if not row: return jsonify(error="Utilisateur introuvable"), 404
    with db() as c:
        pls = c.execute(text(
            "SELECT id,name,tracks,updated FROM public_playlists WHERE user_id=:uid ORDER BY updated DESC"
        ), {"uid": row.id}).fetchall()
    playlists = [{"id": p.id, "name": p.name, "tracks": json.loads(p.tracks), "updated": p.updated} for p in pls]
    return jsonify(id=row.id, username=row.username, color=row.color, playlists=playlists)

# ── Public playlist routes ─────────────────────────────────────────────────────
@app.post("/api/playlists")
def create_playlist():
    u, err = require_auth()
    if err: return err
    d  = request.get_json(silent=True) or {}
    nm = (d.get("name") or "").strip()[:40]
    if not nm: return jsonify(error="Nom requis"), 400
    pid = uuid.uuid4().hex
    with db() as c:
        c.execute(text("INSERT INTO public_playlists (id,user_id,name,tracks) VALUES (:id,:uid,:n,'[]')"),
                  {"id": pid, "uid": u["sub"], "n": nm})
        c.commit()
    return jsonify(id=pid, name=nm, tracks=[])

@app.put("/api/playlists/<pid>")
def update_playlist(pid):
    u, err = require_auth()
    if err: return err
    with db() as c:
        row = c.execute(text("SELECT user_id FROM public_playlists WHERE id=:pid"), {"pid": pid}).fetchone()
    if not row: return jsonify(error="Playlist introuvable"), 404
    if row.user_id != u["sub"]: return jsonify(error="Interdit"), 403
    d  = request.get_json(silent=True) or {}
    fields, params = ["updated=datetime('now')"], {"pid": pid}
    if "name" in d:   fields.append("name=:name");     params["name"]   = d["name"][:40]
    if "tracks" in d: fields.append("tracks=:tracks"); params["tracks"] = json.dumps(d["tracks"])
    with db() as c:
        c.execute(text(f"UPDATE public_playlists SET {','.join(fields)} WHERE id=:pid"), params)
        c.commit()
    return jsonify(ok=True)

@app.delete("/api/playlists/<pid>")
def delete_playlist(pid):
    u, err = require_auth()
    if err: return err
    with db() as c:
        row = c.execute(text("SELECT user_id FROM public_playlists WHERE id=:pid"), {"pid": pid}).fetchone()
    if not row: return jsonify(error="Playlist introuvable"), 404
    if row.user_id != u["sub"]: return jsonify(error="Interdit"), 403
    with db() as c:
        c.execute(text("DELETE FROM public_playlists WHERE id=:pid"), {"pid": pid})
        c.commit()
    return jsonify(ok=True)

@app.get("/api/playlists/<pid>")
def get_playlist(pid):
    with db() as c:
        row = c.execute(text(
            "SELECT p.*,u.username,u.color FROM public_playlists p JOIN users u ON u.id=p.user_id WHERE p.id=:pid"
        ), {"pid": pid}).fetchone()
    if not row: return jsonify(error="Playlist introuvable"), 404
    return jsonify(id=row.id, name=row.name, tracks=json.loads(row.tracks),
                   username=row.username, color=row.color, updated=row.updated)

# ── Existing extract / audio routes ───────────────────────────────────────────
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
    uid  = uuid.uuid4().hex
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"audio/{uid}.%(ext)s",
        "quiet": True,
        "socket_timeout": 20,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
    }
    info = None
    essais = [url.replace("/photo/", "/video/"), url] if "/photo/" in url else [url]
    for u in essais:
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(u, download=True)
            url = u; break
        except Exception:
            continue
    if not info:
        return jsonify(error="Impossible de récupérer le son. Vérifie que le lien est public."), 500
    return jsonify(id=uid, url=url, title=titre(info), author=info.get("uploader", ""),
                   cover=info.get("thumbnail", ""), audio_url=f"/audio/{uid}.mp3")

@app.get("/audio/<path:f>")
def audio(f):
    return send_from_directory("audio", f)

@app.get("/<path:f>")
def statique(f):
    if f not in STATIQUE: return "", 404
    return send_from_directory(".", f, mimetype=STATIQUE[f], max_age=0)
