import os, re, time, uuid, hashlib, hmac, json, base64, secrets, smtplib, ssl, threading, logging
import html as _html
import urllib.request, urllib.error, urllib.parse
from email.message import EmailMessage
from email.utils import parseaddr, formataddr
from flask import Flask, request, jsonify, send_from_directory, make_response
from werkzeug.middleware.proxy_fix import ProxyFix
import yt_dlp
from sqlalchemy import create_engine, text, inspect, bindparam

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tiksound")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
os.makedirs("audio", exist_ok=True)


# ── Secret (persistant, partagé entre workers) ────────────────────────────────
def _load_secret():
    s = os.environ.get("JWT_SECRET")
    if s:
        return s
    path = os.environ.get("SECRET_FILE", ".tiksound_secret")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        s = secrets.token_hex(32)
        with os.fdopen(fd, "w") as f:
            f.write(s)
        log.warning("JWT_SECRET absent : clé générée dans %s. Définis JWT_SECRET en production.", path)
        return s
    except FileExistsError:
        for _ in range(100):
            with open(path) as f:
                s = f.read().strip()
            if s:
                return s
            time.sleep(0.05)
        raise RuntimeError("Fichier secret vide")

SECRET = _load_secret()

# ── Config ───────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("DATABASE_URL", "sqlite:///tiksound.db")
# Tolère les adresses copiées telles quelles depuis Neon : guillemets, « psql ... », postgres://, postgresql+psycopg://
DB_URL = re.sub(r"^psql\s+", "", DB_URL.strip()).strip("'\"")
DB_URL = re.sub(r"^postgres(?:ql)?(?:\+\w+)?://", "postgresql://", DB_URL)
if DB_URL.startswith("postgresql://"):   # force psycopg2 (celui installé dans requirements.txt)
    DB_URL = "postgresql+psycopg2://" + DB_URL[len("postgresql://"):]
log.info("Base de données : %s", DB_URL.split("://")[0])
IS_SQLITE = DB_URL.startswith("sqlite")
TOKEN_DAYS = 30
CODE_TTL = 15 * 60          # durée de validité d'un code
RESEND_COOLDOWN = 60        # délai avant de renvoyer un code
MAX_ATTEMPTS = 5            # essais par code
MAX_PUBLIC_PLAYLISTS = 100
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
STATIQUE = {
    "manifest.webmanifest": "application/manifest+json",
    "sw.js":                "text/javascript",
    "icon-192.png":         "image/png",
    "icon-512.png":         "image/png",
}
COLORS = 10
RESERVED = {"admin", "administrateur", "root", "support", "moderator", "moderateur", "modo", "tiksound",
            "api", "system", "systeme", "staff", "help", "aide", "contact", "null", "undefined",
            "anonymous", "anonyme", "official", "officiel", "tiktok", "me", "moi"}
USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
TT_RE = re.compile(r"^https?://([a-z0-9-]+\.)*tiktok\.com/", re.I)
SPT_RE = re.compile(r"^https://open\.spotify\.com/track/[A-Za-z0-9]{22}$")
SP_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album|track)/([A-Za-z0-9]{22})")
def ok_url(u):
    return bool(TT_RE.match(u) or SPT_RE.match(u))

# ── Email ────────────────────────────────────────────────────────────────────
RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
BREVO_KEY  = os.environ.get("BREVO_API_KEY", "")
SMTP_HOST  = os.environ.get("SMTP_HOST", "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
MAIL_FROM  = os.environ.get("MAIL_FROM") or (f"Tiksound <{SMTP_USER}>" if SMTP_USER else "Tiksound <no-reply@tiksound.app>")
MAIL_ON    = bool(RESEND_KEY or BREVO_KEY or SMTP_HOST)
if not MAIL_ON:
    log.warning("Aucun service email configuré : les codes seront affichés dans les logs du serveur.")


def send_mail(to, subject, html_body, text_body):
    name, addr = parseaddr(MAIL_FROM)
    try:
        if BREVO_KEY:
            payload = {"sender": {"name": name or "Tiksound", "email": addr}, "to": [{"email": to}],
                       "subject": subject, "htmlContent": html_body, "textContent": text_body}
            req = urllib.request.Request("https://api.brevo.com/v3/smtp/email", data=json.dumps(payload).encode(),
                                         headers={"api-key": BREVO_KEY, "Content-Type": "application/json",
                                                  "Accept": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return 200 <= r.status < 300
        if RESEND_KEY:
            payload = {"from": MAIL_FROM, "to": [to], "subject": subject, "html": html_body, "text": text_body}
            req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(payload).encode(),
                                         headers={"Authorization": f"Bearer {RESEND_KEY}",
                                                  "Content-Type": "application/json", "User-Agent": "tiksound/2"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return 200 <= r.status < 300
        if SMTP_HOST:
            msg = EmailMessage()
            msg["From"] = formataddr((name or "Tiksound", addr))
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")
            ctx = ssl.create_default_context()
            if SMTP_PORT == 465:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=15) as s:
                    if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                    s.starttls(context=ctx)
                    if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
                    s.send_message(msg)
            return True
        log.warning("=== EMAIL (non configuré) === À : %s | %s\n%s", to, subject, text_body)
        return True
    except urllib.error.HTTPError as e:
        log.error("Envoi email échoué (%s) : %s", e.code, e.read()[:400])
    except Exception as e:
        log.error("Envoi email échoué : %s", e)
    return False


def mail_code(to, code, purpose, username=""):
    u = _html.escape(username or "")
    if purpose == "register":
        subject = f"{code} est ton code Tiksound"
        title = f"Bienvenue{(' ' + u) if u else ''}"
        intro = "Voici ton code pour vérifier ton adresse email et activer ton compte Tiksound :"
    else:
        subject = f"{code} : réinitialise ton mot de passe Tiksound"
        title = "Mot de passe oublié ?"
        intro = "Voici ton code pour choisir un nouveau mot de passe :"
    outro = (f"Ce code expire dans {CODE_TTL // 60} minutes. Si tu n'es pas à l'origine de cette demande, "
             "ignore simplement cet email.")
    digits = "".join(
        f'<span style="display:inline-block;width:40px;height:52px;line-height:52px;margin:0 3px;border-radius:12px;'
        f'background:#202026;color:#ffffff;font:700 26px/52px Arial,sans-serif;text-align:center">{c}</span>' for c in code)
    html_body = f"""<!doctype html><html><body style="margin:0;background:#0b0b0c;padding:32px 12px;font-family:Arial,Helvetica,sans-serif;color:#ececee">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:460px;background:#151517;border:1px solid #26262a;border-radius:22px"><tr><td style="padding:32px">
<div style="font:800 22px Arial,sans-serif;letter-spacing:-.5px;margin-bottom:22px">Tiksound</div>
<div style="font:700 20px Arial,sans-serif;margin-bottom:10px;color:#ffffff">{title}</div>
<p style="color:#a0a0aa;font-size:15px;line-height:1.55;margin:0 0 24px">{intro}</p>
<div style="text-align:center;margin:0 0 24px;white-space:nowrap">{digits}</div>
<p style="color:#7d7d87;font-size:13px;line-height:1.55;margin:0">{outro}</p>
</td></tr></table></td></tr></table></body></html>"""
    text_body = f"{title}\n\n{intro}\n\n    {code}\n\n{outro}\n"
    return send_mail(to, subject, html_body, text_body)


# ── Base de données ──────────────────────────────────────────────────────────
engine = create_engine(DB_URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if IS_SQLITE else {})


def q(sql, **p):
    with engine.connect() as c:
        return c.execute(text(sql), p).fetchall()

def q1(sql, **p):
    r = q(sql, **p)
    return r[0] if r else None

def x(sql, **p):
    with engine.begin() as c:
        return c.execute(text(sql), p)

def now_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def init_db():
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL, color INTEGER DEFAULT 0, created TEXT, bio TEXT DEFAULT '', avatar TEXT DEFAULT '')""",
        """CREATE TABLE IF NOT EXISTS public_playlists (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, tracks TEXT NOT NULL, updated TEXT)""",
        """CREATE TABLE IF NOT EXISTS codes (
            email TEXT NOT NULL, purpose TEXT NOT NULL, code_hash TEXT NOT NULL, expires REAL NOT NULL,
            attempts INTEGER DEFAULT 0, last_sent REAL DEFAULT 0, ukey TEXT, payload TEXT,
            PRIMARY KEY (email, purpose))""",
        """CREATE TABLE IF NOT EXISTS track_likes (
            user_id TEXT NOT NULL, url TEXT NOT NULL, created TEXT, PRIMARY KEY (user_id, url))""",
        """CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT, author TEXT,
            cover TEXT, duration INTEGER DEFAULT 0, created TEXT)""",
        """CREATE TABLE IF NOT EXISTS post_likes (
            user_id TEXT NOT NULL, post_id TEXT NOT NULL, created TEXT, PRIMARY KEY (user_id, post_id))""",
        """CREATE TABLE IF NOT EXISTS playlist_likes (
            user_id TEXT NOT NULL, playlist_id TEXT NOT NULL, created TEXT, PRIMARY KEY (user_id, playlist_id))""",
        """CREATE TABLE IF NOT EXISTS libraries (
            user_id TEXT PRIMARY KEY, data TEXT NOT NULL, updated TEXT NOT NULL)""",
    ]
    for s in stmts:
        if not IS_SQLITE:  # PostgreSQL : REAL = 4 octets, trop peu précis pour un horodatage
            s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
        try: x(s)
        except Exception as e: log.warning("init_db: %s", e)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("users")}
        for col, ddl in [("bio", "TEXT DEFAULT ''"), ("avatar", "TEXT DEFAULT ''"), ("created", "TEXT")]:
            if col not in cols:
                try: x(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                except Exception: pass
    except Exception as e:
        log.warning("migration: %s", e)
    for s in ["CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_lc ON users (lower(username))",
              "CREATE INDEX IF NOT EXISTS ix_pl_user ON public_playlists (user_id)",
              "CREATE INDEX IF NOT EXISTS ix_likes_pl ON playlist_likes (playlist_id)",
              "CREATE INDEX IF NOT EXISTS ix_tlikes_url ON track_likes (url)",
              "CREATE UNIQUE INDEX IF NOT EXISTS ux_posts_user_url ON posts (user_id, url)",
              "CREATE INDEX IF NOT EXISTS ix_posts_created ON posts (created)",
              "CREATE INDEX IF NOT EXISTS ix_plikes_post ON post_likes (post_id)"]:
        try: x(s)
        except Exception as e: log.warning("index: %s (doublons de pseudo existants ?)", e)

init_db()


# ── JWT (HS256) ──────────────────────────────────────────────────────────────
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

def jwt_decode(token: str):
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

def make_token(user_id, username):
    return jwt_encode({"sub": user_id, "usr": username, "exp": time.time() + TOKEN_DAYS * 86400})

def current_user():
    tok = request.cookies.get("tk_sess") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    return jwt_decode(tok) if tok else None

def require_auth():
    u = current_user()
    if not u or not q1("SELECT id FROM users WHERE id=:id", id=u["sub"]):
        return None, (jsonify(error="Non authentifié"), 401)
    return u, None


# ── Mots de passe (sel par utilisateur) ──────────────────────────────────────
PW_ITER = 260_000

def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), PW_ITER).hex()
    return f"pbkdf2_sha256${PW_ITER}${salt}${h}"

def verify_pw(pw: str, stored: str):
    """Retourne (ok, besoin_de_rehash)."""
    if not stored:
        return False, False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, it, salt, h = stored.split("$")
            calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(it)).hex()
            return hmac.compare_digest(calc, h), False
        except Exception:
            return False, False
    legacy = hashlib.pbkdf2_hmac("sha256", pw.encode(), SECRET.encode(), 260_000).hex()
    ok = hmac.compare_digest(stored, legacy)
    return ok, ok


# ── Utilitaires ──────────────────────────────────────────────────────────────
_hits, _lock = {}, threading.Lock()

def limited(key, limit, window):
    t = time.time()
    with _lock:
        arr = [v for v in _hits.get(key, []) if t - v < window]
        if len(arr) >= limit:
            _hits[key] = arr
            return True
        arr.append(t)
        _hits[key] = arr
        if len(_hits) > 10000:
            for k in list(_hits)[:5000]:
                _hits.pop(k, None)
        return False

def ip():
    return request.remote_addr or "?"

def body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}

def err(msg, code=400, **extra):
    return jsonify(error=msg, **extra), code

def new_code():
    return f"{secrets.randbelow(1_000_000):06d}"

def code_hash(email, purpose, code):
    return hmac.new(SECRET.encode(), f"{purpose}:{email}:{code}".encode(), hashlib.sha256).hexdigest()

def store_code(email, purpose, code, ukey=None, payload=None):
    with engine.begin() as c:
        c.execute(text("DELETE FROM codes WHERE email=:e AND purpose=:p"), {"e": email, "p": purpose})
        c.execute(text("INSERT INTO codes (email,purpose,code_hash,expires,attempts,last_sent,ukey,payload) "
                       "VALUES (:e,:p,:h,:x,0,:t,:u,:pl)"),
                  {"e": email, "p": purpose, "h": code_hash(email, purpose, code), "x": time.time() + CODE_TTL,
                   "t": time.time(), "u": ukey, "pl": payload})

def check_code(row, email, purpose, code):
    if not row:
        return "Aucun code en attente. Demande un nouveau code."
    if row.expires < time.time():
        return "Ce code a expiré. Demande un nouveau code."
    if row.attempts >= MAX_ATTEMPTS:
        return "Trop d'essais. Demande un nouveau code."
    if not re.fullmatch(r"\d{6}", code or "") or not hmac.compare_digest(row.code_hash, code_hash(email, purpose, code)):
        x("UPDATE codes SET attempts=attempts+1 WHERE email=:e AND purpose=:p", e=email, p=purpose)
        left = MAX_ATTEMPTS - row.attempts - 1
        if left <= 0:
            return "Trop d'essais. Demande un nouveau code."
        return f"Code incorrect ({left} essai{'s' if left > 1 else ''} restant{'s' if left > 1 else ''})"
    return None

def username_problem(u, exclude_email=None, exclude_uid=None):
    if not USER_RE.fullmatch(u or ""):
        return "3 à 24 caractères : lettres, chiffres, _ ou -"
    if u.lower() in RESERVED:
        return "Ce pseudo est réservé"
    row = q1("SELECT id FROM users WHERE lower(username)=:u", u=u.lower())
    if row and row.id != exclude_uid:
        return "Ce pseudo est déjà pris"
    p = q1("SELECT email FROM codes WHERE purpose='register' AND ukey=:u AND expires>:n", u=u.lower(), n=time.time())
    if p and p.email != exclude_email:
        return "Ce pseudo est déjà pris"
    return None

def user_json(row):
    return {"id": row.id, "username": row.username, "color": row.color or 0, "email": row.email,
            "bio": getattr(row, "bio", "") or "", "avatar": getattr(row, "avatar", "") or "",
            "created": getattr(row, "created", None)}

def login_response(row, status=200):
    resp = make_response(jsonify(ok=True, **user_json(row)), status)
    resp.set_cookie("tk_sess", make_token(row.id, row.username), httponly=True, samesite="Lax",
                    max_age=TOKEN_DAYS * 86400, secure=request.is_secure)
    return resp

def clean_tracks(lst):
    out = []
    if not isinstance(lst, list):
        return out
    for t in lst[:300]:
        if not isinstance(t, dict):
            continue
        url = str(t.get("url") or "")[:500]
        if not ok_url(url):
            continue
        cover = str(t.get("cover") or "")[:2000]
        if not cover.startswith("https://"):
            cover = ""
        try:
            dur = max(0, min(int(float(t.get("duration") or 0)), 36000))
        except Exception:
            dur = 0
        out.append({"url": url, "title": str(t.get("title") or "Son TikTok")[:120],
                    "author": str(t.get("author") or "")[:60], "cover": cover, "duration": dur})
    return out

def liked_set(uid):
    if not uid:
        return set()
    return {r.playlist_id for r in q("SELECT playlist_id FROM playlist_likes WHERE user_id=:u", u=uid)}

def pl_summary(p, liked=frozenset()):
    tr = json.loads(p.tracks or "[]")
    return {"id": p.id, "name": p.name, "count": len(tr),
            "preview": [{"title": t.get("title", ""), "cover": t.get("cover", "")} for t in tr[:4]],
            "username": p.username, "color": p.color or 0, "avatar": p.avatar or "",
            "likes": int(p.likes or 0), "liked": p.id in liked, "updated": p.updated}

PL_SELECT = ("SELECT p.id,p.name,p.tracks,p.updated,p.user_id,u.username,u.color,u.avatar,"
             "(SELECT COUNT(*) FROM playlist_likes l WHERE l.playlist_id=p.id) AS likes "
             "FROM public_playlists p JOIN users u ON u.id=p.user_id ")

def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@app.after_request
def marque(r):
    r.headers["X-Tiksound"] = "1"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return r


# ── Auth ─────────────────────────────────────────────────────────────────────
@app.get("/api/auth/username")
def check_username():
    u = (request.args.get("u") or "").strip()
    cu = current_user()
    email = (request.args.get("email") or "").strip().lower() or None
    prob = username_problem(u, exclude_email=email, exclude_uid=cu["sub"] if cu else None)
    return jsonify(available=not prob, reason=prob or "")


@app.post("/api/auth/register")
def register():
    d = body()
    email = (d.get("email") or "").strip().lower()
    username = (d.get("username") or "").strip()
    pw = d.get("password") or ""
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        return err("Adresse email invalide")
    prob = username_problem(username, exclude_email=email)
    if prob:
        return err(prob, 409, field="username")
    if len(pw) < 8:
        return err("Mot de passe trop court (8 caractères minimum)", field="password")
    if len(pw) > 200:
        return err("Mot de passe trop long")
    if q1("SELECT id FROM users WHERE lower(email)=:e", e=email):
        return err("Un compte existe déjà avec cet email", 409, field="email")
    row = q1("SELECT last_sent FROM codes WHERE email=:e AND purpose='register'", e=email)
    if row and time.time() - row.last_sent < RESEND_COOLDOWN:
        wait = int(RESEND_COOLDOWN - (time.time() - row.last_sent)) + 1
        return err(f"Patiente {wait} s avant de redemander un code", 429, wait=wait)
    if limited("reg:" + ip(), 8, 3600):
        return err("Trop de tentatives, réessaie plus tard", 429)
    if not MAIL_ON:
        return err("L'envoi d'email n'est pas configuré sur le serveur (ajoute BREVO_API_KEY ou RESEND_API_KEY et MAIL_FROM).", 503)
    code = new_code()
    store_code(email, "register", code, ukey=username.lower(),
               payload=json.dumps({"username": username, "pw_hash": hash_pw(pw)}))
    if not mail_code(email, code, "register", username):
        x("DELETE FROM codes WHERE email=:e AND purpose='register'", e=email)
        return err("Impossible d'envoyer l'email de vérification. Réessaie plus tard.", 502)
    return jsonify(ok=True, pending=True, email=email, ttl=CODE_TTL, cooldown=RESEND_COOLDOWN)


@app.post("/api/auth/verify")
def verify():
    d = body()
    email = (d.get("email") or "").strip().lower()
    code = re.sub(r"\D", "", str(d.get("code") or ""))
    row = q1("SELECT * FROM codes WHERE email=:e AND purpose='register'", e=email)
    problem = check_code(row, email, "register", code)
    if problem:
        return err(problem)
    data = json.loads(row.payload or "{}")
    username = data.get("username", "")
    if q1("SELECT id FROM users WHERE lower(email)=:e", e=email):
        x("DELETE FROM codes WHERE email=:e AND purpose='register'", e=email)
        return err("Un compte existe déjà avec cet email", 409)
    prob = username_problem(username, exclude_email=email)
    if prob:
        return err("Ce pseudo vient d'être pris. Recommence l'inscription avec un autre pseudo.", 409)
    uid = uuid.uuid4().hex
    try:
        x("INSERT INTO users (id,email,username,pw_hash,color,created,bio,avatar) "
          "VALUES (:id,:e,:u,:h,:c,:t,'','')",
          id=uid, e=email, u=username, h=data["pw_hash"], c=secrets.randbelow(COLORS), t=now_iso())
    except Exception:
        return err("Ce pseudo ou cet email vient d'être utilisé. Recommence.", 409)
    x("DELETE FROM codes WHERE email=:e AND purpose='register'", e=email)
    log.info("Nouveau compte vérifié : %s", username)
    return login_response(q1("SELECT * FROM users WHERE id=:id", id=uid), 201)


@app.post("/api/auth/resend")
def resend():
    d = body()
    email = (d.get("email") or "").strip().lower()
    purpose = "reset" if d.get("purpose") == "reset" else "register"
    row = q1("SELECT * FROM codes WHERE email=:e AND purpose=:p", e=email, p=purpose)
    if not row:
        if purpose == "reset":
            return jsonify(ok=True, cooldown=RESEND_COOLDOWN)
        return err("Aucune inscription en attente pour cet email. Recommence l'inscription.", 404)
    if time.time() - row.last_sent < RESEND_COOLDOWN:
        wait = int(RESEND_COOLDOWN - (time.time() - row.last_sent)) + 1
        return err(f"Patiente {wait} s avant de redemander un code", 429, wait=wait)
    if limited("resend:" + ip(), 10, 3600):
        return err("Trop de demandes, réessaie plus tard", 429)
    code = new_code()
    x("UPDATE codes SET code_hash=:h, expires=:x, attempts=0, last_sent=:t WHERE email=:e AND purpose=:p",
      h=code_hash(email, purpose, code), x=time.time() + CODE_TTL, t=time.time(), e=email, p=purpose)
    username = ""
    if purpose == "register":
        try: username = json.loads(row.payload or "{}").get("username", "")
        except Exception: pass
    if not mail_code(email, code, purpose, username):
        return err("Impossible d'envoyer l'email. Réessaie plus tard.", 502)
    return jsonify(ok=True, cooldown=RESEND_COOLDOWN)


@app.post("/api/auth/login")
def login():
    d = body()
    em = (d.get("email") or "").strip().lower()
    pw = d.get("password") or ""
    if limited("login:" + ip(), 20, 600) or limited("login:" + em, 10, 600):
        return err("Trop de tentatives. Réessaie dans quelques minutes.", 429)
    row = q1("SELECT * FROM users WHERE lower(email)=:e", e=em)
    if not row:
        p = q1("SELECT payload FROM codes WHERE email=:e AND purpose='register' AND expires>:n", e=em, n=time.time())
        if p:
            try:
                if verify_pw(pw, json.loads(p.payload).get("pw_hash", ""))[0]:
                    return err("Ton compte n'est pas encore vérifié. Entre le code reçu par email.", 403, needVerify=True)
            except Exception:
                pass
        return err("Email ou mot de passe incorrect", 401)
    ok, rehash = verify_pw(pw, row.pw_hash)
    if not ok:
        return err("Email ou mot de passe incorrect", 401)
    if rehash:
        x("UPDATE users SET pw_hash=:h WHERE id=:id", h=hash_pw(pw), id=row.id)
    return login_response(row)


@app.post("/api/auth/forgot")
def forgot():
    email = (body().get("email") or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return err("Adresse email invalide")
    if limited("forgot:" + ip(), 8, 3600):
        return err("Trop de demandes, réessaie plus tard", 429)
    user = q1("SELECT username FROM users WHERE lower(email)=:e", e=email)
    if user:
        row = q1("SELECT last_sent FROM codes WHERE email=:e AND purpose='reset'", e=email)
        if not row or time.time() - row.last_sent >= RESEND_COOLDOWN:
            code = new_code()
            store_code(email, "reset", code)
            mail_code(email, code, "reset", user.username)
    return jsonify(ok=True, cooldown=RESEND_COOLDOWN)


@app.post("/api/auth/reset")
def reset_pw():
    d = body()
    email = (d.get("email") or "").strip().lower()
    code = re.sub(r"\D", "", str(d.get("code") or ""))
    pw = d.get("password") or ""
    if len(pw) < 8:
        return err("Mot de passe trop court (8 caractères minimum)")
    row = q1("SELECT * FROM codes WHERE email=:e AND purpose='reset'", e=email)
    problem = check_code(row, email, "reset", code)
    if problem:
        return err(problem)
    user = q1("SELECT * FROM users WHERE lower(email)=:e", e=email)
    if not user:
        return err("Compte introuvable", 404)
    x("UPDATE users SET pw_hash=:h WHERE id=:id", h=hash_pw(pw), id=user.id)
    x("DELETE FROM codes WHERE email=:e AND purpose='reset'", e=email)
    return login_response(user)


@app.post("/api/auth/logout")
def logout():
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie("tk_sess")
    return resp


@app.get("/api/auth/me")
def me():
    u = current_user()
    if not u:
        return err("Non authentifié", 401)
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if not row:
        return err("Utilisateur introuvable", 404)
    return jsonify(user_json(row))


@app.patch("/api/auth/me")
def update_me():
    u, e = require_auth()
    if e: return e
    d = body()
    fields, params = [], {"id": u["sub"]}
    if "color" in d:
        try: params["color"] = int(d["color"]) % COLORS
        except Exception: return err("Couleur invalide")
        fields.append("color=:color")
    if "bio" in d:
        fields.append("bio=:bio"); params["bio"] = re.sub(r"\s+", " ", str(d["bio"] or "")).strip()[:160]
    if "avatar" in d:
        av = str(d["avatar"] or "").strip()
        if len(av) > 8 or re.search(r"[<>&\"'A-Za-z0-9\s]", av):
            return err("Avatar invalide")
        fields.append("avatar=:avatar"); params["avatar"] = av
    new_name = None
    if "username" in d:
        new_name = str(d["username"] or "").strip()
        prob = username_problem(new_name, exclude_uid=u["sub"])
        if prob:
            return err(prob, 409)
        fields.append("username=:username"); params["username"] = new_name
    if fields:
        try:
            x(f"UPDATE users SET {','.join(fields)} WHERE id=:id", **params)
        except Exception:
            return err("Ce pseudo est déjà pris", 409)
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    return login_response(row) if new_name else jsonify(ok=True, **user_json(row))


@app.delete("/api/auth/me")
def delete_me():
    u, e = require_auth()
    if e: return e
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if not verify_pw(body().get("password") or "", row.pw_hash)[0]:
        return err("Mot de passe incorrect", 403)
    with engine.begin() as c:
        p = {"u": row.id}
        c.execute(text("DELETE FROM playlist_likes WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM track_likes WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM post_likes WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM post_likes WHERE post_id IN (SELECT id FROM posts WHERE user_id=:u)"), p)
        c.execute(text("DELETE FROM posts WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM playlist_likes WHERE playlist_id IN (SELECT id FROM public_playlists WHERE user_id=:u)"), p)
        c.execute(text("DELETE FROM public_playlists WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM libraries WHERE user_id=:u"), p)
        c.execute(text("DELETE FROM users WHERE id=:u"), p)
    resp = make_response(jsonify(ok=True))
    resp.delete_cookie("tk_sess")
    return resp


# ── Utilisateurs / recherche / feed ──────────────────────────────────────────
@app.get("/api/users/search")
def search_users():
    s = (request.args.get("q") or "").strip()
    if len(s) < 2: return jsonify(users=[])
    rows = q("SELECT id,username,color,avatar FROM users WHERE lower(username) LIKE :q ESCAPE '\\' LIMIT 20",
             q=f"%{like_escape(s.lower())}%")
    return jsonify(users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or ""} for r in rows])


@app.get("/api/search")
def search_all():
    s = (request.args.get("q") or "").strip()[:60]
    if len(s) < 2: return jsonify(users=[], playlists=[])
    pat = f"%{like_escape(s.lower())}%"
    users = q("SELECT id,username,color,avatar,bio,"
              "(SELECT COUNT(*) FROM public_playlists p WHERE p.user_id=users.id AND p.tracks <> '[]') AS n "
              "FROM users WHERE lower(username) LIKE :q ESCAPE '\\' ORDER BY length(username) LIMIT 20", q=pat)
    pls = q(PL_SELECT + "WHERE lower(p.name) LIKE :q ESCAPE '\\' AND p.tracks <> '[]' ORDER BY likes DESC, p.updated DESC LIMIT 24", q=pat)
    cu = current_user()
    lk = liked_set(cu["sub"] if cu else None)
    return jsonify(users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or "",
                           "bio": r.bio or "", "playlists": int(r.n or 0)} for r in users],
                   playlists=[pl_summary(p, lk) for p in pls])


@app.get("/api/feed")
def feed():
    cu = current_user()
    lk = liked_set(cu["sub"] if cu else None)
    top = q(PL_SELECT + "WHERE p.tracks <> '[]' ORDER BY likes DESC, p.updated DESC LIMIT 12")
    recent = q(PL_SELECT + "WHERE p.tracks <> '[]' ORDER BY p.updated DESC LIMIT 12")
    users = q("SELECT id,username,color,avatar,bio,"
              "(SELECT COUNT(*) FROM public_playlists p WHERE p.user_id=users.id AND p.tracks <> '[]') AS n "
              "FROM users ORDER BY COALESCE(created,'') DESC LIMIT 12")
    stats = q1("SELECT (SELECT COUNT(*) FROM users) AS u, (SELECT COUNT(*) FROM public_playlists WHERE tracks <> '[]') AS p")
    return jsonify(top=[pl_summary(p, lk) for p in top], recent=[pl_summary(p, lk) for p in recent],
                   users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or "",
                           "bio": r.bio or "", "playlists": int(r.n or 0)} for r in users],
                   stats={"users": int(stats.u or 0), "playlists": int(stats.p or 0)})


@app.get("/api/users/<username>")
def get_user(username):
    row = q1("SELECT id,username,color,avatar,bio,created FROM users WHERE lower(username)=:u", u=username.lower())
    if not row: return err("Utilisateur introuvable", 404)
    cu = current_user()
    lk = liked_set(cu["sub"] if cu else None)
    pls = q(PL_SELECT + "WHERE p.user_id=:uid ORDER BY p.updated DESC", uid=row.id)
    playlists = []
    for p in pls:
        tr = json.loads(p.tracks or "[]")
        if not tr: continue
        s = pl_summary(p, lk)
        s["tracks"] = tr
        playlists.append(s)
    return jsonify(id=row.id, username=row.username, color=row.color or 0, avatar=row.avatar or "",
                   bio=row.bio or "", created=row.created, playlists=playlists,
                   likes=sum(p["likes"] for p in playlists))


# ── Playlists publiques ──────────────────────────────────────────────────────
@app.get("/api/me/playlists")
def my_playlists():
    u, e = require_auth()
    if e: return e
    rows = q("SELECT id,name,tracks,updated FROM public_playlists WHERE user_id=:u ORDER BY updated DESC", u=u["sub"])
    return jsonify(playlists=[{"id": r.id, "name": r.name, "tracks": json.loads(r.tracks or "[]")} for r in rows])


@app.post("/api/playlists")
def create_playlist():
    u, e = require_auth()
    if e: return e
    d = body()
    nm = str(d.get("name") or "").strip()[:40]
    if not nm: return err("Nom requis")
    n = q1("SELECT COUNT(*) AS n FROM public_playlists WHERE user_id=:u", u=u["sub"]).n
    if n >= MAX_PUBLIC_PLAYLISTS:
        return err(f"Maximum {MAX_PUBLIC_PLAYLISTS} playlists publiques", 400)
    tracks = clean_tracks(d.get("tracks"))
    pid = uuid.uuid4().hex
    x("INSERT INTO public_playlists (id,user_id,name,tracks,updated) VALUES (:id,:uid,:n,:t,:up)",
      id=pid, uid=u["sub"], n=nm, t=json.dumps(tracks), up=now_iso())
    return jsonify(id=pid, name=nm, tracks=tracks)


@app.put("/api/playlists/<pid>")
def update_playlist(pid):
    u, e = require_auth()
    if e: return e
    row = q1("SELECT user_id FROM public_playlists WHERE id=:pid", pid=pid)
    if not row: return err("Playlist introuvable", 404)
    if row.user_id != u["sub"]: return err("Interdit", 403)
    d = body()
    fields, params = ["updated=:up"], {"pid": pid, "up": now_iso()}
    if "name" in d:
        nm = str(d["name"] or "").strip()[:40]
        if not nm: return err("Nom requis")
        fields.append("name=:name"); params["name"] = nm
    if "tracks" in d:
        fields.append("tracks=:tracks"); params["tracks"] = json.dumps(clean_tracks(d["tracks"]))
    x(f"UPDATE public_playlists SET {','.join(fields)} WHERE id=:pid", **params)
    return jsonify(ok=True)


@app.delete("/api/playlists/<pid>")
def delete_playlist(pid):
    u, e = require_auth()
    if e: return e
    row = q1("SELECT user_id FROM public_playlists WHERE id=:pid", pid=pid)
    if not row: return err("Playlist introuvable", 404)
    if row.user_id != u["sub"]: return err("Interdit", 403)
    with engine.begin() as c:
        c.execute(text("DELETE FROM playlist_likes WHERE playlist_id=:pid"), {"pid": pid})
        c.execute(text("DELETE FROM public_playlists WHERE id=:pid"), {"pid": pid})
    return jsonify(ok=True)


@app.get("/api/playlists/<pid>")
def get_playlist(pid):
    row = q1(PL_SELECT + "WHERE p.id=:pid", pid=pid)
    if not row: return err("Playlist introuvable", 404)
    cu = current_user()
    s = pl_summary(row, liked_set(cu["sub"] if cu else None))
    tr = json.loads(row.tracks or "[]")
    urls = list({t.get("url") for t in tr if t.get("url")})
    counts, mine_l = {}, set()
    if urls:
        with engine.connect() as c:
            for r in c.execute(text("SELECT url, COUNT(*) AS n FROM track_likes WHERE url IN :us GROUP BY url")
                               .bindparams(bindparam("us", expanding=True)), {"us": urls}):
                counts[r[0]] = int(r[1])
            if cu:
                for r in c.execute(text("SELECT url FROM track_likes WHERE user_id=:u AND url IN :us")
                                   .bindparams(bindparam("us", expanding=True)), {"u": cu["sub"], "us": urls}):
                    mine_l.add(r[0])
    for t in tr:
        t["likes"] = counts.get(t.get("url"), 0)
        t["liked"] = t.get("url") in mine_l
    s["tracks"] = tr
    s["mine"] = bool(cu and cu["sub"] == row.user_id)
    return jsonify(s)


@app.post("/api/playlists/<pid>/like")
def like_playlist(pid):
    u, e = require_auth()
    if e: return e
    if not q1("SELECT id FROM public_playlists WHERE id=:p", p=pid):
        return err("Playlist introuvable", 404)
    if q1("SELECT user_id FROM playlist_likes WHERE user_id=:u AND playlist_id=:p", u=u["sub"], p=pid):
        x("DELETE FROM playlist_likes WHERE user_id=:u AND playlist_id=:p", u=u["sub"], p=pid)
        liked = False
    else:
        try: x("INSERT INTO playlist_likes (user_id,playlist_id,created) VALUES (:u,:p,:t)", u=u["sub"], p=pid, t=now_iso())
        except Exception: pass
        liked = True
    n = q1("SELECT COUNT(*) AS n FROM playlist_likes WHERE playlist_id=:p", p=pid).n
    return jsonify(liked=liked, likes=int(n))


@app.post("/api/tracks/like")
def like_track():
    u, e = require_auth()
    if e: return e
    url = str(body().get("url") or "")[:500]
    if not ok_url(url):
        return err("Son invalide")
    if limited("tlike:" + u["sub"], 120, 600):
        return err("Trop d'actions, patiente un peu", 429)
    if q1("SELECT 1 AS o FROM track_likes WHERE user_id=:u AND url=:l", u=u["sub"], l=url):
        x("DELETE FROM track_likes WHERE user_id=:u AND url=:l", u=u["sub"], l=url)
        liked = False
    else:
        try: x("INSERT INTO track_likes (user_id,url,created) VALUES (:u,:l,:t)", u=u["sub"], l=url, t=now_iso())
        except Exception: pass
        liked = True
    n = q1("SELECT COUNT(*) AS n FROM track_likes WHERE url=:l", l=url).n
    return jsonify(liked=liked, likes=int(n))


# ── Sons publiés (indépendants, hors playlists) ──────────────────────────────
POST_SELECT = ("SELECT p.id,p.url,p.title,p.author,p.cover,p.duration,p.created,u.username,u.color,u.avatar,"
               "(SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS likes "
               "FROM posts p JOIN users u ON u.id=p.user_id ")

@app.get("/api/posts")
def list_posts():
    try: off = max(0, int(request.args.get("offset", 0)))
    except Exception: off = 0
    n = 20
    rows = q(POST_SELECT + "ORDER BY p.created DESC, p.id DESC LIMIT :n OFFSET :o", n=n + 1, o=off)
    more = len(rows) > n
    rows = rows[:n]
    cu = current_user()
    mine = set()
    if cu and rows:
        with engine.connect() as c:
            for r in c.execute(text("SELECT post_id FROM post_likes WHERE user_id=:u AND post_id IN :ids")
                               .bindparams(bindparam("ids", expanding=True)), {"u": cu["sub"], "ids": [r.id for r in rows]}):
                mine.add(r[0])
    return jsonify(more=more, posts=[{
        "id": r.id, "url": r.url, "title": r.title or "Son TikTok", "author": r.author or "", "cover": r.cover or "",
        "duration": r.duration or 0, "created": r.created, "username": r.username, "color": r.color or 0,
        "likes": int(r.likes or 0), "liked": r.id in mine, "mine": bool(cu and cu["sub"] == r.user_id)} for r in rows])


@app.post("/api/posts")
def create_post():
    u, e = require_auth()
    if e: return e
    tr = clean_tracks([body()])
    if not tr:
        return err("Son invalide (lien TikTok requis)")
    t = tr[0]
    old = q1("SELECT id FROM posts WHERE user_id=:u AND url=:l", u=u["sub"], l=t["url"])
    if old:
        return jsonify(id=old.id, already=True)
    if limited("post:" + u["sub"], 60, 3600):
        return err("Trop de publications, réessaie plus tard", 429)
    if q1("SELECT COUNT(*) AS n FROM posts WHERE user_id=:u", u=u["sub"]).n >= 1000:
        return err("Maximum 1000 sons publiés", 400)
    pid = uuid.uuid4().hex
    x("INSERT INTO posts (id,user_id,url,title,author,cover,duration,created) VALUES (:i,:u,:l,:t,:a,:c,:d,:n)",
      i=pid, u=u["sub"], l=t["url"], t=t["title"], a=t["author"], c=t["cover"], d=t["duration"], n=now_iso())
    return jsonify(id=pid)


@app.delete("/api/posts/<pid>")
def delete_post(pid):
    u, e = require_auth()
    if e: return e
    row = q1("SELECT user_id FROM posts WHERE id=:i", i=pid)
    if not row: return err("Introuvable", 404)
    if row.user_id != u["sub"]: return err("Interdit", 403)
    x("DELETE FROM post_likes WHERE post_id=:i", i=pid)
    x("DELETE FROM posts WHERE id=:i", i=pid)
    return jsonify(ok=True)


@app.post("/api/posts/<pid>/like")
def like_post(pid):
    u, e = require_auth()
    if e: return e
    if not q1("SELECT id FROM posts WHERE id=:i", i=pid):
        return err("Introuvable", 404)
    if limited("plike:" + u["sub"], 120, 600):
        return err("Trop d'actions, patiente un peu", 429)
    if q1("SELECT 1 AS o FROM post_likes WHERE user_id=:u AND post_id=:i", u=u["sub"], i=pid):
        x("DELETE FROM post_likes WHERE user_id=:u AND post_id=:i", u=u["sub"], i=pid)
        liked = False
    else:
        try: x("INSERT INTO post_likes (user_id,post_id,created) VALUES (:u,:i,:t)", u=u["sub"], i=pid, t=now_iso())
        except Exception: pass
        liked = True
    n = q1("SELECT COUNT(*) AS n FROM post_likes WHERE post_id=:i", i=pid).n
    return jsonify(liked=liked, likes=int(n))


# ── Bibliothèque synchronisée entre appareils ────────────────────────────────
def clean_library(d):
    d = d if isinstance(d, dict) else {}
    r2 = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    tracks, seen = [], set()
    raw_t = d.get("tracks") if isinstance(d.get("tracks"), list) else []
    for t in raw_t[:1500]:
        if not isinstance(t, dict):
            continue
        url = str(t.get("url") or "")[:500]
        if not ok_url(url) or url in seen:
            continue
        seen.add(url)
        remote = str(t.get("remote") or "")[:500]
        if not (r2 and remote.startswith(r2 + "/")):
            remote = ""
        try: dur = max(0, min(int(float(t.get("duration") or 0)), 36000))
        except Exception: dur = 0
        try: added = max(0, int(float(t.get("added") or 0)))
        except Exception: added = 0
        tracks.append({"url": url, "title": str(t.get("title") or "Son TikTok")[:120],
                       "author": str(t.get("author") or "")[:60], "duration": dur,
                       "fav": bool(t.get("fav")), "added": added, "remote": remote})
    pls = []
    raw_p = d.get("pls") if isinstance(d.get("pls"), list) else []
    for p in raw_p[:200]:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or "")
        if not re.fullmatch(r"[\w-]{1,40}", pid):
            continue
        raw_u = p.get("urls") if isinstance(p.get("urls"), list) else []
        urls = [u for u in raw_u[:500] if isinstance(u, str) and u in seen]
        pub_id = str(p.get("pubId") or "")
        pls.append({"id": pid, "name": str(p.get("name") or "Playlist")[:40], "urls": urls,
                    "pub": bool(p.get("pub")), "pubId": pub_id if re.fullmatch(r"[0-9a-f]{32}", pub_id) else ""})
    return {"tracks": tracks, "pls": pls}


@app.get("/api/me/library")
def get_library():
    u, e = require_auth()
    if e: return e
    r = q1("SELECT data,updated FROM libraries WHERE user_id=:u", u=u["sub"])
    return jsonify(data=json.loads(r.data) if r else None, updated=r.updated if r else None)


@app.put("/api/me/library")
def put_library():
    u, e = require_auth()
    if e: return e
    if limited("lib:" + u["sub"], 120, 600):
        return err("Trop de synchronisations, patiente un peu", 429)
    b = body()
    data = json.dumps(clean_library(b.get("data")))
    cur = q1("SELECT updated FROM libraries WHERE user_id=:u", u=u["sub"])
    if (cur.updated if cur else None) != (b.get("base") or None):
        return err("Bibliothèque modifiée sur un autre appareil", 409)
    ver = f"{time.time():.3f}"
    try:
        if cur:
            res = x("UPDATE libraries SET data=:d, updated=:v WHERE user_id=:u AND updated=:o",
                    d=data, v=ver, u=u["sub"], o=cur.updated)
            if res.rowcount == 0:
                return err("Bibliothèque modifiée sur un autre appareil", 409)
        else:
            x("INSERT INTO libraries (user_id,data,updated) VALUES (:u,:d,:v)", u=u["sub"], d=data, v=ver)
    except Exception:
        return err("Bibliothèque modifiée sur un autre appareil", 409)
    return jsonify(ok=True, updated=ver)


# ── Extraction audio ─────────────────────────────────────────────────────────
@app.get("/")
def accueil():
    return send_from_directory(".", "index.html", max_age=0)

@app.get("/api/health")
def health():
    return jsonify(ok=True, mail=MAIL_ON)

R2_OK = all(os.environ.get(k) for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL"))
_s3 = None
def s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                           aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                           aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
    return _s3

def nettoyer():
    for f in os.listdir("audio"):
        p = os.path.join("audio", f)
        try:
            if time.time() - os.path.getmtime(p) > 3600:
                os.remove(p)
        except Exception:
            pass

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

def fetch_thumb(url):
    """Télécharge la miniature (les liens TikTok expirent) pour la stocker côté appareil."""
    if not url or not url.startswith("https://"):
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.tiktok.com/"})
        with urllib.request.urlopen(req, timeout=8) as r:
            ct = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"):
                return ""
            b = r.read(700_001)
            if len(b) > 700_000:
                return ""
        return f"data:{ct};base64," + base64.b64encode(b).decode()
    except Exception:
        return ""

def _find(d, key):
    if isinstance(d, dict):
        if key in d: return d[key]
        for v in d.values():
            r = _find(v, key)
            if r is not None: return r
    elif isinstance(d, list):
        for v in d:
            r = _find(v, key)
            if r is not None: return r
    return None

@app.post("/api/spotify")
def spotify_import():
    if limited("spotify:" + ip(), 15, 600):
        return err("Trop d'imports d'un coup, patiente un peu", 429)
    m = SP_RE.search(str(body().get("url", "")))
    if not m:
        return err("Lien Spotify invalide (playlist, album ou titre)")
    kind, sid = m.group(1), m.group(2)
    try:
        req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}",
                                     headers={"User-Agent": UA, "Accept-Language": "fr,en;q=0.8"})
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read(3_000_000).decode("utf8", "ignore")
        raw = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        ent = _find(json.loads(raw.group(1)), "entity")
        items = ent.get("trackList") or ([ent] if kind == "track" else [])
        name = str(ent.get("name") or ent.get("title") or "Import Spotify")[:60]
        tracks = []
        for it in items:
            uri = str(it.get("uri") or "")
            tid = uri.split(":")[-1]
            title = str(it.get("title") or it.get("name") or "")[:120]
            if not title or len(tid) != 22:
                continue
            artist = it.get("subtitle")
            if not artist and isinstance(it.get("artists"), list) and it["artists"]:
                artist = it["artists"][0].get("name")
            try: dur = int(float(it.get("duration") or it.get("duration_ms") or 0) / 1000)
            except Exception: dur = 0
            tracks.append({"url": f"https://open.spotify.com/track/{tid}", "title": title,
                           "author": str(artist or "")[:60].replace("\xa0", " "), "cover": "", "duration": dur})
        if not tracks:
            raise ValueError("aucun titre dans la page Spotify")
    except urllib.error.HTTPError as e:
        log.warning("Import Spotify : HTTP %s sur %s/%s", e.code, kind, sid)
        return err("Spotify a refusé la lecture de cette playlist (elle doit être publique, pas privée).", 502)
    except Exception as e:
        log.warning("Import Spotify échoué (%s/%s) : %r", kind, sid, e)
        return err("Impossible de lire ce lien Spotify. Vérifie que la playlist est publique.", 502)
    return jsonify(name=name, sid=f"{kind}:{sid}", tracks=tracks[:300])

def search_candidates(prefix, query, n=5):
    """Résultats de recherche sans téléchargement. prefix : ytsearch (YouTube) ou scsearch (SoundCloud)."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True, "skip_download": True, "socket_timeout": 15}) as y:
            res = y.extract_info(f"{prefix}{n}:{query}", download=False)
        out = []
        for e in (res or {}).get("entries") or []:
            if not e:
                continue
            u = e.get("webpage_url") or e.get("url") or ""
            if not str(u).startswith("http") and prefix == "ytsearch" and e.get("id"):
                u = f"https://www.youtube.com/watch?v={e['id']}"
            if not str(u).startswith("http"):
                continue
            try: d = int(float(e.get("duration") or 0))
            except Exception: d = 0
            out.append({"url": u, "duration": d, "src": prefix})
        if not out:
            log.warning("Recherche %s : aucun résultat pour « %s »", prefix, query)
        return out
    except Exception as ex:
        log.warning("Recherche %s échouée pour « %s » : %s", prefix, query, str(ex)[:300])
        return []

def audio_from_search(opts, title, author, want):
    """Cherche le titre sur YouTube puis SoundCloud, garde les versions dont la durée colle à Spotify."""
    query = f"{author} - {title}".strip(" -")
    pools = [search_candidates("ytsearch", query), search_candidates("scsearch", query)]
    order = []
    for tol in ((0.06, 0.25) if want else (None,)):
        for cands in pools:
            for c in cands:
                if tol is None or (c["duration"] and abs(c["duration"] - want) <= max(8, want * tol)):
                    if all(c["url"] != o["url"] for o in order):
                        order.append(c)
    for c in order[:4]:
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(c["url"], download=True)
            if info and info.get("entries"):
                info = info["entries"][0]
            log.info("Spotify « %s » → %s (%s)", query, c["src"], c["url"])
            return info
        except Exception as ex:
            log.warning("Téléchargement %s échoué (%s) : %s", c["src"], c["url"], str(ex)[:300])
    return None


@app.post("/api/extract")
def extract():
    if limited("extract:" + ip(), 40, 600):
        return err("Trop de conversions d'un coup, patiente un peu", 429)
    nettoyer()
    b = body()
    raw = str(b.get("url", "")).strip()
    spotify = bool(SPT_RE.match(raw))
    if spotify:
        title_in = str(b.get("title") or "")[:120]; author_in = str(b.get("author") or "")[:60]
        url = raw
    else:
        url = trouver_lien(raw)
    if not url:
        return err("Lien TikTok invalide")
    online = R2_OK and b.get("store") == "online"
    key = hashlib.sha1(url.encode()).hexdigest()
    if online:  # déjà converti par quelqu'un : on réutilise, sans retélécharger
        try:
            m = json.loads(s3().get_object(Bucket=os.environ["R2_BUCKET"], Key=f"meta/{key}.json")["Body"].read())
            return jsonify(**m)
        except Exception:
            pass
    uid = uuid.uuid4().hex
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"audio/{uid}.%(ext)s",
        "quiet": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "128"}],
    }
    info = None
    if spotify:
        try: want = int(float(b.get("duration") or 0))
        except Exception: want = 0
        info = audio_from_search(opts, title_in, author_in, want)
        if not info:
            return err(f"Aucune version audio trouvée pour « {title_in[:50]} ».", 502)
    else:
        essais = [url.replace("/photo/", "/video/"), url] if "/photo/" in url else [url]
        for u in essais:
            try:
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(u, download=True)
                url = u
                break
            except Exception as ex:
                log.warning("Extraction TikTok échouée (%s) : %s", u, str(ex)[:300])
                continue
        if not info:
            return err("Impossible de récupérer le son. Vérifie que le lien est public.", 500)
    cover = info.get("thumbnail", "") or ""
    try: duration = int(float(info.get("duration") or 0))
    except Exception: duration = 0
    res = dict(id=uid, url=url, title=(title_in or titre(info)) if spotify else titre(info),
               author=author_in if spotify else (info.get("uploader", "") or ""),
               cover=cover, cover_data=fetch_thumb(cover), duration=duration, audio_url=f"/audio/{uid}.mp3")
    if online:
        try:
            bucket = os.environ["R2_BUCKET"]
            s3().upload_file(f"audio/{uid}.mp3", bucket, f"audio/{key}.mp3",
                             ExtraArgs={"ContentType": "audio/mpeg", "CacheControl": "public, max-age=31536000"})
            res.update(remote=True, audio_url=os.environ["R2_PUBLIC_URL"].rstrip("/") + f"/audio/{key}.mp3")
            s3().put_object(Bucket=bucket, Key=f"meta/{key}.json", Body=json.dumps(res).encode(),
                            ContentType="application/json")
            try: os.remove(f"audio/{uid}.mp3")
            except Exception: pass
        except Exception as e:
            log.warning("Envoi R2 échoué, repli en local : %s", e)
    return jsonify(**res)

@app.get("/audio/<path:f>")
def audio(f):
    return send_from_directory("audio", f, max_age=3600, conditional=True)

@app.get("/<path:f>")
def statique(f):
    if f not in STATIQUE: return "", 404
    return send_from_directory(".", f, mimetype=STATIQUE[f], max_age=0)
