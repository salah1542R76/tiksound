import os, re, time, uuid, hashlib, hmac, json, base64, secrets, smtplib, ssl, threading, logging, shutil
import html as _html
import urllib.request, urllib.error, urllib.parse
from email.message import EmailMessage
from email.utils import parseaddr, formataddr
from flask import Flask, request, jsonify, send_from_directory, make_response
from werkzeug.middleware.proxy_fix import ProxyFix
import yt_dlp
from i18n_server import T, lang
from sqlalchemy import create_engine, text, inspect, bindparam
from sqlalchemy.exc import IntegrityError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tiksound")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
os.makedirs("audio", exist_ok=True)
os.makedirs("ads", exist_ok=True)


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
FOREVER = 4102444800        # 2100-01-01 : « à vie »
PREMIUM_GRACE = 2 * 86400   # marge après la fin de période Stripe (webhook en retard, serveur en panne…)
STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_PUB = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")     # clé publique (pk_…) : le formulaire de paiement intégré en a besoin
STRIPE_WH = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

def parse_admin_emails(s):
    return {e.strip().lower() for e in (s or "").split(",") if e.strip()}

ADMIN_EMAILS = parse_admin_emails(os.environ.get("ADMIN_EMAIL", ""))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
STATIQUE = {
    "lang-en.js":           "text/javascript",
    "sw.js":                "text/javascript",
    "icon-192.png":         "image/png",
    "icon-512.png":         "image/png",
    "icon-maskable-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "favicon-16.png":       "image/png",
    "favicon-32.png":       "image/png",
    "favicon.ico":          "image/x-icon",
    "favicon.svg":          "image/svg+xml",
}
COLORS = 10
RESERVED = {"admin", "administrateur", "root", "support", "moderator", "moderateur", "modo", "tiksound", "tikwave", "tikwav",
            "api", "system", "systeme", "staff", "help", "aide", "contact", "null", "undefined",
            "anonymous", "anonyme", "official", "officiel", "tiktok", "me", "moi"}
USER_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
TT_RE = re.compile(r"^https?://([a-z0-9-]+\.)*tiktok\.com/", re.I)
SPT_RE = re.compile(r"^https://open\.spotify\.com/track/[A-Za-z0-9]{22}$")
SP_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(playlist|album|track)/([A-Za-z0-9]{22})")
# Résultats de la recherche de sons : une vidéo YouTube ou un titre SoundCloud (pas de playlist / album)
FIND_URL_RE = re.compile(r"^https://(?:(?:www\.|m\.|music\.)?youtube\.com/watch\?v=[\w-]{11}(?:&[^\s]*)?|youtu\.be/[\w-]{11}"
                         r"|(?:www\.|m\.)?soundcloud\.com/(?!sets/|discover|search|stream)[\w-]+/(?!sets/)[\w-]+/?)$")
def ok_url(u):
    return bool(TT_RE.match(u) or SPT_RE.match(u) or FIND_URL_RE.match(u))

# ── Email ────────────────────────────────────────────────────────────────────
RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
BREVO_KEY  = os.environ.get("BREVO_API_KEY", "")
SMTP_HOST  = os.environ.get("SMTP_HOST", "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
MAIL_FROM  = os.environ.get("MAIL_FROM") or (f"TikWave <{SMTP_USER}>" if SMTP_USER else "TikWave <no-reply@tikwave.fr>")
MAIL_ON    = bool(RESEND_KEY or BREVO_KEY or SMTP_HOST)
if not MAIL_ON:
    log.warning("Aucun service email configuré : les codes seront affichés dans les logs du serveur.")


def send_mail(to, subject, html_body, text_body):
    name, addr = parseaddr(MAIL_FROM)
    try:
        if BREVO_KEY:
            payload = {"sender": {"name": name or "TikWave", "email": addr}, "to": [{"email": to}],
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
                                                  "Content-Type": "application/json", "User-Agent": "tikwave/2"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return 200 <= r.status < 300
        if SMTP_HOST:
            msg = EmailMessage()
            msg["From"] = formataddr((name or "TikWave", addr))
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
        subject = T("{code} est ton code TikWave", code=code)
        title = T("Bienvenue {pseudo}", pseudo=u) if u else T("Bienvenue")
        intro = T("Voici ton code pour vérifier ton adresse email et activer ton compte TikWave :")
    else:
        subject = T("{code} : réinitialise ton mot de passe TikWave", code=code)
        title = T("Mot de passe oublié ?")
        intro = T("Voici ton code pour choisir un nouveau mot de passe :")
    outro = T("Ce code expire dans {min} minutes. Si tu n'es pas à l'origine de cette demande, ignore simplement cet email.",
              min=CODE_TTL // 60)
    digits = "".join(
        f'<span style="display:inline-block;width:40px;height:52px;line-height:52px;margin:0 3px;border-radius:12px;'
        f'background:#202026;color:#ffffff;font:700 26px/52px Arial,sans-serif;text-align:center">{c}</span>' for c in code)
    html_body = f"""<!doctype html><html><body style="margin:0;background:#0b0b0c;padding:32px 12px;font-family:Arial,Helvetica,sans-serif;color:#ececee">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:460px;background:#151517;border:1px solid #26262a;border-radius:22px"><tr><td style="padding:32px">
<div style="font:800 22px Arial,sans-serif;letter-spacing:-.5px;margin-bottom:22px">TikWave</div>
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
        """CREATE TABLE IF NOT EXISTS stripe_events (
            id TEXT PRIMARY KEY, type TEXT, created TEXT)""",
        """CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY, created TEXT, url TEXT NOT NULL, reason TEXT, name TEXT DEFAULT '',
            email TEXT NOT NULL, details TEXT DEFAULT '', status TEXT DEFAULT 'new')""",
        """CREATE TABLE IF NOT EXISTS access_keys (
            id TEXT PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL, label TEXT DEFAULT '', hint TEXT DEFAULT '',
            max_uses INTEGER DEFAULT 1, uses INTEGER DEFAULT 0, days INTEGER DEFAULT 0, created TEXT,
            revoked INTEGER DEFAULT 0)""",
    ]
    for s in stmts:
        if not IS_SQLITE:  # PostgreSQL : REAL = 4 octets, trop peu précis pour un horodatage
            s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
        try: x(s)
        except Exception as e: log.warning("init_db: %s", e)
    try:
        cols = {c["name"] for c in inspect(engine).get_columns("users")}
        for col, ddl in [("bio", "TEXT DEFAULT ''"), ("avatar", "TEXT DEFAULT ''"), ("created", "TEXT"),
                         ("avatar_img", "TEXT DEFAULT ''"), ("banner_img", "TEXT DEFAULT ''"), ("imgv", "TEXT DEFAULT ''"),
                         ("accent", "TEXT DEFAULT ''"), ("status", "TEXT DEFAULT ''"), ("ui", "TEXT DEFAULT ''"),
                         ("ui_v", "TEXT DEFAULT ''"), ("banner_color", "TEXT DEFAULT ''"), ("cosm", "TEXT DEFAULT ''"),
                         ("premium_until", "BIGINT DEFAULT 0"), ("plan_status", "TEXT DEFAULT ''"),
                         ("cancel_at_end", "INTEGER DEFAULT 0"), ("stripe_customer_id", "TEXT DEFAULT ''"),
                         ("stripe_sub_id", "TEXT DEFAULT ''"), ("gift_until", "BIGINT DEFAULT 0"),
                         ("gift_key_id", "TEXT DEFAULT ''")]:
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
              "CREATE INDEX IF NOT EXISTS ix_plikes_post ON post_likes (post_id)",
              "CREATE INDEX IF NOT EXISTS ix_users_stripe_cust ON users (stripe_customer_id)"]:
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
        return None, err("Non authentifié", 401)
    return u, None


# ── Premium : qui a droit à quoi ─────────────────────────────────────────────
def paywall_on():
    """Pubs + abonnement actifs seulement si Stripe (clés secrète et publique + tarif) ET le stockage en ligne (R2) sont configurés."""
    return bool(STRIPE_KEY and STRIPE_PRICE and STRIPE_PUB and R2_OK)

def is_admin(row):
    return (getattr(row, "email", "") or "").lower() in ADMIN_EMAILS

def premium_state(row, now=None):
    """Premium = admin, OU clé d'accès (gift_until), OU abonnement Stripe (premium_until + marge)."""
    now = time.time() if now is None else now
    pu = int(getattr(row, "premium_until", 0) or 0)
    gu = int(getattr(row, "gift_until", 0) or 0)
    if is_admin(row):
        exp, src = FOREVER, "admin"
    else:
        s_exp = pu + PREMIUM_GRACE if pu > 0 else 0
        exp = max(gu, s_exp)
        src = "" if exp <= now else ("key" if gu >= s_exp else "stripe")
    return {"premium": exp > now, "premium_until": pu, "premium_exp": exp, "src": src}

def request_premium():
    """L'utilisateur du cookie courant est-il Premium ? (anonyme = gratuit)"""
    u = current_user()
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"]) if u else None
    return bool(row and premium_state(row)["premium"])


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
    return jsonify(error=T(msg), **extra), code

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
        return T("Aucun code en attente. Demande un nouveau code.")
    if row.expires < time.time():
        return T("Ce code a expiré. Demande un nouveau code.")
    if row.attempts >= MAX_ATTEMPTS:
        return T("Trop d'essais. Demande un nouveau code.")
    if not re.fullmatch(r"\d{6}", code or "") or not hmac.compare_digest(row.code_hash, code_hash(email, purpose, code)):
        x("UPDATE codes SET attempts=attempts+1 WHERE email=:e AND purpose=:p", e=email, p=purpose)
        left = MAX_ATTEMPTS - row.attempts - 1
        if left <= 0:
            return T("Trop d'essais. Demande un nouveau code.")
        return T("Code incorrect ({n} essais restants)", n=left) if left > 1 else T("Code incorrect (1 essai restant)")
    return None

def username_problem(u, exclude_email=None, exclude_uid=None):
    if not USER_RE.fullmatch(u or ""):
        return T("3 à 24 caractères : lettres, chiffres, _ ou -")
    if u.lower() in RESERVED:
        return T("Ce pseudo est réservé")
    row = q1("SELECT id FROM users WHERE lower(username)=:u", u=u.lower())
    if row and row.id != exclude_uid:
        return T("Ce pseudo est déjà pris")
    p = q1("SELECT email FROM codes WHERE purpose='register' AND ukey=:u AND expires>:n", u=u.lower(), n=time.time())
    if p and p.email != exclude_email:
        return T("Ce pseudo est déjà pris")
    return None

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IMG_RE = re.compile(r"^data:image/(jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+$")
UI_KEYS = {"theme": ("dark", "oled", "nuit", "clair", "sepia", "foret", "ocean", "contraste", "bordeaux", "pastel", "synth", "perso"),
           "bg": ("aurora", "cover", "mesh", "plain", "image", "vagues", "etoiles", "degrade"),
           "cover": ("square", "round", "vinyl"),
           "font": ("dm", "outfit", "space", "nunito", "syne", "mono", "atkinson", "poppins", "lexend", "playfair", "caveat", "pixel"),
           "card": ("glass", "flat", "line"), "npbg": ("cover", "accent", "plain"),
           "trail": ("off", "spark", "star", "flower", "flame", "bolt", "heart", "note", "bubble"),
           "cur": ("def", "star", "heart", "ring", "arrow", "moon", "note", "diamond", "bolt", "plus"),
           "amb": ("off", "stars", "petals", "snow", "fireflies")}
UI_BOOL = ("grain", "motion", "autoAcc", "viz", "navl", "glow")
# Cosmétiques de profil (Premium) : bandeau, cadre de l'avatar, badge à côté du pseudo
COS_BANNER = ("aurore", "sunset", "ocean", "fleurs", "neon", "etoiles", "uni", "degrade", "vagues", "points", "grille", "coeurs",
              "galaxie", "lave", "holo", "damier")
COS_FRAME = ("fin", "double", "halo", "pulse", "orbite", "neon", "or", "glace", "fleurs", "etoiles", "flamme", "coeurs",
             "arcenciel", "couronne", "notes", "eclair", "pixel")
COS_EFFECT = ("etoiles", "petales", "neige", "bulles", "brillance", "aurore", "confettis", "coeurs", "lucioles", "notes", "pluie", "paillettes")
COS_NAME = ("degrade", "brille", "neon", "arcenciel", "or", "glitch", "ombre", "contour", "pastel")
COS_BADGE = ("⭐", "🔥", "🌸", "⚡", "👑", "💎", "🎧", "🌙", "🦋", "🍒", "👾", "🪩", "🎵", "🌈", "🚀", "🍀", "🐉", "❄️")
COS_COLORS = ("bc1", "bc2", "fc", "t1", "t2", "nc")     # couleurs libres : bandeau (2), décoration, thème du profil (2)

def clean_cos(d):
    out = {}
    if not isinstance(d, dict):
        return out
    for k, allowed in (("banner", COS_BANNER), ("frame", COS_FRAME), ("badge", COS_BADGE),
                       ("effect", COS_EFFECT), ("name", COS_NAME), ("nfont", UI_KEYS["font"])):
        if d.get(k) in allowed:
            out[k] = d[k]
    for k in COS_COLORS:
        v = d.get(k)
        if isinstance(v, str) and HEX_RE.match(v):
            out[k] = v.lower()
    return out
UI_NUM = {"blur": (0, 40), "radius": (4, 28), "bgo": (5, 100), "bgb": (0, 40), "zoom": (85, 130), "curw": (0, 30)}
UI_HOME = ("hello", "hero", "steps", "pins", "day", "resume", "recent", "top", "stats", "goal", "comm", "trend", "tip", "faq")

def clean_ui(d):
    """Réglages d'apparence synchronisables (rien de lié à l'appareil : taille, tri, vues…)."""
    out = {}
    if not isinstance(d, dict):
        return out
    for k, allowed in UI_KEYS.items():
        if d.get(k) in allowed:
            out[k] = d[k]
    for k in UI_BOOL:
        if k in d:
            out[k] = bool(d[k])
    for k, (lo, hi) in UI_NUM.items():
        try:
            if k in d: out[k] = max(lo, min(hi, int(float(d[k]))))
        except Exception:
            pass
    for k in ("accent", "tbg"):                                    # tbg : couleur de fond du thème « perso »
        if isinstance(d.get(k), str) and HEX_RE.match(d[k]):
            out[k] = d[k].lower()
    for k, extra in (("trc", ("", "rainbow")), ("curc", ("",))):      # couleurs de la traînée / du curseur
        v = d.get(k)
        if isinstance(v, str) and (v in extra or HEX_RE.match(v)):
            out[k] = v.lower()
    if isinstance(d.get("accent2"), str) and (d["accent2"] == "" or HEX_RE.match(d["accent2"])):
        out["accent2"] = d["accent2"].lower()                   # vide = pas de dégradé
    if isinstance(d.get("home"), dict):
        home = {k: v for k, v in d["home"].items() if k in UI_HOME and isinstance(v, bool)}
        if home: out["home"] = home
    return out

def list_badge(r):
    """Dans les listes (Explorer, recherche) : membre Premium ou non, et sa décoration d'avatar s'il l'est."""
    if not premium_state(r)["premium"]:
        return {"premium": False}
    try: c = clean_cos(json.loads(getattr(r, "cosm", "") or "{}"))
    except Exception: c = {}
    return {"premium": True, "cos": {k: c[k] for k in ("frame", "fc", "badge") if k in c}}

def pic_url_ver(row, col="avatar_img"):
    return (getattr(row, "imgv", "") or "1") if getattr(row, col, "") else ""

def user_json(row, private=True):
    d = {"id": row.id, "username": row.username, "color": row.color or 0,
         "bio": getattr(row, "bio", "") or "", "avatar": getattr(row, "avatar", "") or "",
         "created": getattr(row, "created", None),
         "pic": pic_url_ver(row, "avatar_img"), "ban": pic_url_ver(row, "banner_img"),
         "accent": getattr(row, "accent", "") or "", "status": getattr(row, "status", "") or "",
         "bcolor": getattr(row, "banner_color", "") or ""}
    if private:
        d["email"] = row.email
        try: d["ui"] = json.loads(getattr(row, "ui", "") or "{}")
        except Exception: d["ui"] = {}
        d["ui_v"] = getattr(row, "ui_v", "") or ""
    ps = premium_state(row)
    d["premium"] = ps["premium"]
    try: cos = json.loads(getattr(row, "cosm", "") or "{}") if ps["premium"] else {}
    except Exception: cos = {}
    d["cos"] = clean_cos(cos)                      # visibles seulement tant que l'abonnement est actif
    if private:
        d.update(premium_until=ps["premium_until"], premium_exp=ps["premium_exp"], premium_src=ps["src"],
                 plan_status=getattr(row, "plan_status", "") or "",
                 cancel_at_end=bool(getattr(row, "cancel_at_end", 0)), is_admin=is_admin(row))
    return d

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
        out.append({"url": url, "title": str(t.get("title") or T("Son TikTok"))[:120],
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
            "username": p.username, "color": p.color or 0, "avatar": p.avatar or "", "pic": p.pic or "",
            "likes": int(p.likes or 0), "liked": p.id in liked, "updated": p.updated, **list_badge(p)}

PL_SELECT = ("SELECT p.id,p.name,p.tracks,p.updated,p.user_id,u.username,u.color,u.avatar,u.email,u.premium_until,u.gift_until,u.cosm,(CASE WHEN COALESCE(u.avatar_img,'')<>'' THEN COALESCE(NULLIF(u.imgv,''),'1') ELSE '' END) AS pic,"
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
        return err(T("Patiente {s} s avant de redemander un code", s=wait), 429, wait=wait)
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
        return err(T("Patiente {s} s avant de redemander un code", s=wait), 429, wait=wait)
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
    if "accent" in d:
        ac = str(d["accent"] or "").strip()
        if ac and not HEX_RE.match(ac): return err("Couleur invalide")
        fields.append("accent=:accent"); params["accent"] = ac.lower()
    if "bcolor" in d:
        bc = str(d["bcolor"] or "").strip()
        if bc and not HEX_RE.match(bc): return err("Couleur invalide")
        fields.append("banner_color=:bcolor"); params["bcolor"] = bc.lower()
    if "cos" in d:
        cos = clean_cos(d["cos"])
        if cos:
            me_row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
            if not (me_row and premium_state(me_row)["premium"]):
                return err("Les cosmétiques sont réservés à Premium", 403)
        fields.append("cosm=:cosm"); params["cosm"] = json.dumps(cos, ensure_ascii=False)
    if "status" in d:
        fields.append("status=:status"); params["status"] = re.sub(r"\s+", " ", str(d["status"] or "")).strip()[:40]
    for key, col, limit, glimit in (("pic", "avatar_img", 220_000, 1_400_000), ("ban", "banner_img", 650_000, 1_900_000)):
        if key in d:
            v = d[key]
            gif = isinstance(v, str) and v.startswith("data:image/gif;")
            if v in (None, "", False):
                v = ""
            elif not isinstance(v, str) or len(v) > (glimit if gif else limit) or not IMG_RE.match(v):
                return err("Image invalide ou trop lourde")
            elif gif and base64.b64decode(v.split(",", 1)[1][:16] + "==")[:4] != b"GIF8":
                return err("Image invalide ou trop lourde")
            elif gif and not premium_state(q1("SELECT * FROM users WHERE id=:id", id=u["sub"]))["premium"]:
                return err("Les GIF animés sont réservés à Premium", 403)
            fields.append(f"{col}=:{col}"); params[col] = v
            params["imgv"] = f"{time.time():.0f}"
    if "imgv" in params:
        fields.append("imgv=:imgv")
    if "ui" in d:
        ui = clean_ui(d["ui"])
        fields.append("ui=:ui"); params["ui"] = json.dumps(ui)
        fields.append("ui_v=:ui_v"); params["ui_v"] = f"{time.time():.3f}"
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


@app.get("/api/img/<username>/<kind>")
def user_img(username, kind):
    col = {"a": "avatar_img", "b": "banner_img"}.get(kind)
    if not col:
        return "", 404
    row = q1(f"SELECT {col} AS img, email, premium_until, gift_until FROM users WHERE lower(username)=:u", u=username.lower())
    m = re.match(r"^data:(image/(?:jpeg|png|webp|gif));base64,(.+)$", (row.img if row else "") or "")
    if not m or (m.group(1) == "image/gif" and not premium_state(row)["premium"]):   # GIF animé : seulement pendant Premium
        return "", 404
    resp = make_response(base64.b64decode(m.group(2)))
    resp.headers["Content-Type"] = m.group(1)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.delete("/api/auth/me")
def delete_me():
    u, e = require_auth()
    if e: return e
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if not verify_pw(body().get("password") or "", row.pw_hash)[0]:
        return err("Mot de passe incorrect", 403)
    # Un abonnement Stripe ne doit jamais survivre au compte : on le résilie d'abord, et on garde le compte si ça échoue.
    sid = str(getattr(row, "stripe_sub_id", "") or "")
    if sid and STRIPE_KEY and (getattr(row, "plan_status", "") or "") not in ("canceled", "incomplete_expired"):
        try:
            stripe_cancel_subscription(sid)
        except Exception as e1:
            try:
                gone = str(stripe_get_subscription(sid).get("status")) in ("canceled", "incomplete_expired")
            except Exception:
                gone = False
            if not gone:
                log.error("Résiliation Stripe impossible pour %s : %s", sid, e1)
                return err("Impossible de résilier ton abonnement pour le moment : ton compte n'a pas été supprimé. Réessaie dans un instant.", 502)
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
    rows = q("SELECT id,username,color,avatar,email,premium_until,gift_until,cosm," + "(CASE WHEN COALESCE(avatar_img,'')<>'' THEN COALESCE(NULLIF(imgv,''),'1') ELSE '' END) AS pic" + " FROM users WHERE lower(username) LIKE :q ESCAPE '\\' LIMIT 20",
             q=f"%{like_escape(s.lower())}%")
    return jsonify(users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or "", "pic": r.pic or "", **list_badge(r)} for r in rows])


@app.get("/api/search")
def search_all():
    s = (request.args.get("q") or "").strip()[:60]
    if len(s) < 2: return jsonify(users=[], playlists=[])
    pat = f"%{like_escape(s.lower())}%"
    users = q("SELECT id,username,color,avatar,bio,email,premium_until,gift_until,cosm,(CASE WHEN COALESCE(avatar_img,'')<>'' THEN COALESCE(NULLIF(imgv,''),'1') ELSE '' END) AS pic,"
              "(SELECT COUNT(*) FROM public_playlists p WHERE p.user_id=users.id AND p.tracks <> '[]') AS n "
              "FROM users WHERE lower(username) LIKE :q ESCAPE '\\' ORDER BY length(username) LIMIT 20", q=pat)
    pls = q(PL_SELECT + "WHERE lower(p.name) LIKE :q ESCAPE '\\' AND p.tracks <> '[]' ORDER BY likes DESC, p.updated DESC LIMIT 24", q=pat)
    cu = current_user()
    lk = liked_set(cu["sub"] if cu else None)
    return jsonify(users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or "", "pic": r.pic or "",
                           "bio": r.bio or "", "playlists": int(r.n or 0), **list_badge(r)} for r in users],
                   playlists=[pl_summary(p, lk) for p in pls])


@app.get("/api/feed")
def feed():
    cu = current_user()
    lk = liked_set(cu["sub"] if cu else None)
    top = q(PL_SELECT + "WHERE p.tracks <> '[]' ORDER BY likes DESC, p.updated DESC LIMIT 12")
    recent = q(PL_SELECT + "WHERE p.tracks <> '[]' ORDER BY p.updated DESC LIMIT 12")
    users = q("SELECT id,username,color,avatar,bio,email,premium_until,gift_until,cosm,(CASE WHEN COALESCE(avatar_img,'')<>'' THEN COALESCE(NULLIF(imgv,''),'1') ELSE '' END) AS pic,"
              "(SELECT COUNT(*) FROM public_playlists p WHERE p.user_id=users.id AND p.tracks <> '[]') AS n "
              "FROM users ORDER BY COALESCE(created,'') DESC LIMIT 12")
    stats = q1("SELECT (SELECT COUNT(*) FROM users) AS u, (SELECT COUNT(*) FROM public_playlists WHERE tracks <> '[]') AS p")
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 7 * 86400))
    hot = q("SELECT * FROM (SELECT p.id,p.name,p.tracks,p.updated,p.user_id,u.username,u.color,u.avatar,u.email,u.premium_until,u.gift_until,u.cosm,"
            "(CASE WHEN COALESCE(u.avatar_img,'')<>'' THEN COALESCE(NULLIF(u.imgv,''),'1') ELSE '' END) AS pic,"
            "(SELECT COUNT(*) FROM playlist_likes l WHERE l.playlist_id=p.id) AS likes,"
            "(SELECT COUNT(*) FROM playlist_likes l WHERE l.playlist_id=p.id AND l.created>=:s) AS week "
            "FROM public_playlists p JOIN users u ON u.id=p.user_id WHERE p.tracks <> '[]') t "
            "WHERE week > 0 ORDER BY week DESC, likes DESC LIMIT 8", s=since)
    trend = [dict(pl_summary(p, lk), week=int(p.week or 0)) for p in hot]
    return jsonify(top=[pl_summary(p, lk) for p in top], recent=[pl_summary(p, lk) for p in recent], trend=trend,
                   users=[{"id": r.id, "username": r.username, "color": r.color or 0, "avatar": r.avatar or "", "pic": r.pic or "",
                           "bio": r.bio or "", "playlists": int(r.n or 0), **list_badge(r)} for r in users],
                   stats={"users": int(stats.u or 0), "playlists": int(stats.p or 0)})


@app.get("/api/users/<username>")
def get_user(username):
    row = q1("SELECT id,email,username,color,avatar,bio,created,avatar_img,banner_img,imgv,accent,status,banner_color,cosm,premium_until,gift_until FROM users WHERE lower(username)=:u", u=username.lower())
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
    pub = user_json(row, private=False)
    pub.update(playlists=playlists, likes=sum(p["likes"] for p in playlists))
    return jsonify(pub)


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
        return err(T("Maximum {n} playlists publiques", n=MAX_PUBLIC_PLAYLISTS), 400)
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
POST_SELECT = ("SELECT p.id,p.user_id,p.url,p.title,p.author,p.cover,p.duration,p.created,u.username,u.color,u.avatar,u.email,u.premium_until,u.gift_until,u.cosm,(CASE WHEN COALESCE(u.avatar_img,'')<>'' THEN COALESCE(NULLIF(u.imgv,''),'1') ELSE '' END) AS pic,"
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
        "id": r.id, "url": r.url, "title": r.title or T("Son TikTok"), "author": r.author or "", "cover": r.cover or "",
        "duration": r.duration or 0, "created": r.created, "username": r.username, "color": r.color or 0, "pic": r.pic or "",
        "likes": int(r.likes or 0), "liked": r.id in mine, "mine": bool(cu and cu["sub"] == r.user_id), **list_badge(r)} for r in rows])


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


# ── Stripe : appels sortants (SDK) ───────────────────────────────────────────
def _stripe():
    import stripe
    stripe.api_key = STRIPE_KEY
    return stripe

def stripe_get_price():
    p = _stripe().Price.retrieve(STRIPE_PRICE)
    rec = getattr(p, "recurring", None)
    return {"amount": int(p.unit_amount or 0), "currency": str(p.currency),
            "interval": str(getattr(rec, "interval", None) or "month")}

_price = {"t": 0.0, "v": None}

def premium_price():
    """Prix de l'abonnement lu chez Stripe, gardé 10 min (1 min après un échec)."""
    if time.time() - _price["t"] < 600:
        return _price["v"]
    try:
        v = stripe_get_price()
        _price.update(t=time.time(), v=v)
    except Exception as e:
        log.warning("Prix Stripe indisponible : %s", e)
        _price.update(t=time.time() - 540, v=None)
    return _price["v"]

@app.get("/api/premium")
def premium_info():
    on = paywall_on()
    return jsonify(enabled=on, price=premium_price() if on else None, publishable_key=STRIPE_PUB if on else None)

def _plain(o):
    """Objet Stripe -> dict Python ordinaire."""
    d = getattr(getattr(o, "last_response", None), "data", None)
    if isinstance(d, dict):
        return d
    return o.to_dict() if hasattr(o, "to_dict") else json.loads(json.dumps(o))

def stripe_create_customer(email, user_id):
    return _stripe().Customer.create(email=email, metadata={"user_id": user_id}).id

def stripe_create_checkout(customer_id, user_id, base_url):
    """Session Checkout pour le formulaire intégré (ui_mode "form") : renvoie le client_secret que le navigateur utilise."""
    s = _stripe().checkout.Session.create(
        ui_mode="form",
        mode="subscription",
        billing_address_collection="auto",
        phone_number_collection={"enabled": False},
        automatic_tax={"enabled": False},
        payment_method_collection="always",
        submit_type="auto",
        integration_identifier="custom_embedded_web_0001",
        line_items=[{"price": STRIPE_PRICE, "quantity": 1}],
        customer=customer_id, client_reference_id=user_id,                # le webhook retrouve l'utilisateur grâce à eux
        subscription_data={"metadata": {"user_id": user_id}},
        return_url=f"{base_url}/?premium=success")
    return s.client_secret

def stripe_create_portal(customer_id, return_url):
    return _stripe().billing_portal.Session.create(customer=customer_id, return_url=return_url).url

def stripe_get_subscription(sub_id):
    return _plain(_stripe().Subscription.retrieve(sub_id))

def stripe_cancel_subscription(sub_id):
    _stripe().Subscription.cancel(sub_id)

def public_base():
    return PUBLIC_URL or request.host_url.rstrip("/")

def _id(v):
    """Un champ Stripe est tantôt un id, tantôt l'objet développé."""
    return (v.get("id") if isinstance(v, dict) else v) or None

@app.post("/api/stripe/checkout")
def stripe_checkout():
    u, e = require_auth()
    if e: return e
    if not paywall_on():
        return err("L'abonnement n'est pas disponible pour le moment", 503)
    if limited("checkout:" + u["sub"], 20, 3600):
        return err("Trop d'essais, réessaie plus tard", 429)
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if is_admin(row):
        return err("Tu es administrateur : le Premium est déjà actif", 409)
    if (row.plan_status or "") in ("active", "trialing") and int(row.premium_until or 0) + PREMIUM_GRACE > time.time():
        return err("Tu as déjà un abonnement : gère-le depuis ton profil", 409, portal=True)
    try:
        cid = row.stripe_customer_id or ""
        if not cid:
            cid = stripe_create_customer(row.email, row.id)
            x("UPDATE users SET stripe_customer_id=:c WHERE id=:u", c=cid, u=row.id)
        try:
            secret = stripe_create_checkout(cid, row.id, public_base())
        except Exception as ex:
            # Client inconnu de Stripe : créé en mode test, puis passage au mode réel. On en crée un nouveau.
            if (getattr(ex, "code", ""), getattr(ex, "param", "")) != ("resource_missing", "customer"):
                raise
            cid = stripe_create_customer(row.email, row.id)
            x("UPDATE users SET stripe_customer_id=:c WHERE id=:u", c=cid, u=row.id)
            secret = stripe_create_checkout(cid, row.id, public_base())
        return jsonify(client_secret=secret)
    except Exception as ex:
        log.error("Stripe checkout : %s", ex)
        return err("Le paiement est momentanément indisponible, réessaie dans un instant", 502)

@app.post("/api/stripe/portal")
def stripe_portal():
    u, e = require_auth()
    if e: return e
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if not row.stripe_customer_id:
        return err("Aucun abonnement à gérer", 404)
    if not STRIPE_KEY:
        return err("L'abonnement n'est pas disponible pour le moment", 503)
    try:
        return jsonify(url=stripe_create_portal(row.stripe_customer_id, public_base() + "/#profil"))
    except Exception as ex:
        log.error("Stripe portail : %s", ex)
        return err("Le portail est momentanément indisponible, réessaie dans un instant", 502)

# ── Webhook Stripe ──
def verify_stripe_signature(payload, header, secret, tolerance=300, now=None):
    """Schéma Stripe : en-tête « t=...,v1=... », HMAC-SHA256 de « {t}.{corps} ». Comparaison à temps constant."""
    if not secret or not header or not isinstance(header, str):
        return False
    t, sigs = None, []
    for part in header.split(","):
        k, _, v = part.strip().partition("=")
        if k == "t": t = v
        elif k == "v1": sigs.append(v)
    try:
        ts = int(t)
    except Exception:
        return False
    if abs((time.time() if now is None else now) - ts) > tolerance:
        return False
    good = hmac.new(secret.encode(), t.encode() + b"." + payload, hashlib.sha256).hexdigest().encode()
    return any(hmac.compare_digest(good, s.encode()) for s in sigs)

def sub_id_from(o):
    """Id d'abonnement d'une facture ou d'une session, quelle que soit la version de l'API Stripe."""
    if not isinstance(o, dict): return None
    s = _id(o.get("subscription"))
    if s: return s
    s = _id(((o.get("parent") or {}).get("subscription_details") or {}).get("subscription"))
    if s: return s
    for ln in (o.get("lines") or {}).get("data") or []:
        ln = ln or {}
        s = _id((((ln.get("parent") or {}).get("subscription_item_details")) or {}).get("subscription")) or _id(ln.get("subscription"))
        if s: return s
    return None

def period_end(sub):
    """Fin de période payée : au niveau de l'abonnement (ancienne API) ou de ses lignes (API récentes)."""
    vals = [int(sub.get("current_period_end") or 0)]
    for it in (sub.get("items") or {}).get("data") or []:
        vals.append(int((it or {}).get("current_period_end") or 0))
    return max(vals)

def apply_subscription(c, row, sub):
    """Recopie l'état d'un abonnement Stripe sur l'utilisateur (dans la transaction de l'appelant).
    Ne touche jamais à gift_until : une clé d'accès survit à un abonnement résilié."""
    sid, status = str(sub.get("id") or ""), str(sub.get("status") or "")
    live = status in ("active", "trialing")
    cur = str(getattr(row, "stripe_sub_id", "") or "")
    if cur and sid and cur != sid and not live:
        return False                       # évènement tardif d'un ancien abonnement : on n'écrase pas l'actuel
    until = period_end(sub) if live else 0
    if live and until <= 0:
        raise ValueError(f"abonnement {sid} actif sans fin de période")
    c.execute(text("UPDATE users SET premium_until=:p, plan_status=:s, cancel_at_end=:k, stripe_sub_id=:sid, "
                   "stripe_customer_id=COALESCE(NULLIF(stripe_customer_id,''),:cu) WHERE id=:u"),
              {"p": until, "s": status, "k": 1 if (sub.get("cancel_at_period_end") or sub.get("cancel_at")) else 0,
               "sid": sid, "cu": _id(sub.get("customer")) or "", "u": row.id})
    return True

STRIPE_EVENTS = {"checkout.session.completed", "invoice.paid", "invoice.payment_failed",
                 "customer.subscription.updated", "customer.subscription.deleted"}

@app.post("/api/stripe/webhook")
def stripe_webhook():
    if not STRIPE_WH:
        return err("Webhook non configuré", 503)
    raw = request.get_data()
    if not verify_stripe_signature(raw, request.headers.get("Stripe-Signature"), STRIPE_WH):
        return err("Signature invalide", 400)
    try:
        ev = json.loads(raw)
        eid, etype, obj = str(ev["id"]), str(ev["type"]), ev["data"]["object"]
        assert isinstance(obj, dict)
    except Exception:
        return err("Évènement illisible", 400)
    if etype not in STRIPE_EVENTS or (etype == "checkout.session.completed" and obj.get("mode") != "subscription"):
        return jsonify(ok=True, ignored=True)
    if q1("SELECT id FROM stripe_events WHERE id=:i", i=eid):
        return jsonify(ok=True, duplicate=True)
    cust = _id(obj.get("customer"))
    row = q1("SELECT * FROM users WHERE stripe_customer_id=:c", c=cust) if cust else None
    if not row and etype == "checkout.session.completed" and obj.get("client_reference_id"):
        row = q1("SELECT * FROM users WHERE id=:i", i=str(obj["client_reference_id"]))
    if not row:
        log.warning("Webhook Stripe %s (%s) : client %s inconnu", etype, eid, cust)
        return jsonify(ok=True, unknown_user=True)
    sub_id = obj.get("id") if etype.startswith("customer.subscription.") else sub_id_from(obj)
    sub_id = sub_id or row.stripe_sub_id or None            # facture sans abonnement : on relit l'abonnement courant
    if not sub_id:
        return jsonify(ok=True, ignored=True)
    try:
        sub = stripe_get_subscription(sub_id)               # la vérité, quel que soit l'ordre d'arrivée des évènements
        with engine.begin() as c:
            c.execute(text("INSERT INTO stripe_events (id,type,created) VALUES (:i,:t,:c)"),
                      {"i": eid, "t": etype, "c": now_iso()})
            apply_subscription(c, row, sub)
    except IntegrityError:
        return jsonify(ok=True, duplicate=True)             # deux envois simultanés du même évènement
    except Exception:
        log.exception("Webhook Stripe %s (%s) : échec, Stripe réessaiera", etype, eid)
        return err("Traitement impossible", 500)
    return jsonify(ok=True)


# ── Clés d'accès (Premium offert) ────────────────────────────────────────────
KEY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"      # sans 0 O 1 I L : pas de confusion à la dictée

def new_access_key():
    return "TKW-" + "-".join("".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(3))

def key_norm(k):
    return re.sub(r"[^A-Z0-9]", "", str(k or "").upper())

def key_hash(k):
    return hmac.new(SECRET.encode(), ("key:" + key_norm(k)).encode(), hashlib.sha256).hexdigest()

def to_int(v, default, lo, hi):
    try: return max(lo, min(hi, int(float(v))))
    except Exception: return default

def require_admin():
    u, e = require_auth()
    if e: return None, e
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    if not is_admin(row):
        return None, err("Réservé à l'administrateur", 403)
    return row, None

def key_json(r, users=()):
    return {"id": r.id, "label": r.label or "", "hint": r.hint or "", "max_uses": int(r.max_uses or 0),
            "uses": int(r.uses or 0), "days": int(r.days or 0), "created": r.created,
            "revoked": bool(r.revoked), "users": list(users)}

@app.post("/api/admin/keys")
def admin_create_key():
    row, e = require_admin()
    if e: return e
    d = body()
    label = str(d.get("label") or "").strip()[:60]
    uses = to_int(d.get("uses", 1), 1, 0, 1000)          # 0 = illimité
    days = to_int(d.get("days", 0), 0, 0, 3650)          # 0 = à vie
    key = new_access_key()
    kid = uuid.uuid4().hex
    x("INSERT INTO access_keys (id,key_hash,label,hint,max_uses,uses,days,created,revoked) "
      "VALUES (:i,:h,:l,:t,:m,0,:d,:c,0)", i=kid, h=key_hash(key), l=label, t=key[-4:], m=uses, d=days, c=now_iso())
    return jsonify(key=key, **key_json(q1("SELECT * FROM access_keys WHERE id=:i", i=kid)))

@app.get("/api/admin/keys")
def admin_list_keys():
    row, e = require_admin()
    if e: return e
    by_key = {}
    for r in q("SELECT gift_key_id AS k, username FROM users WHERE gift_key_id <> '' ORDER BY username"):
        by_key.setdefault(r.k, []).append(r.username)
    rows = q("SELECT * FROM access_keys ORDER BY created DESC, id")
    return jsonify(keys=[key_json(r, by_key.get(r.id, ())) for r in rows])

@app.delete("/api/admin/keys/<kid>")
def admin_revoke_key(kid):
    row, e = require_admin()
    if e: return e
    if not q1("SELECT id FROM access_keys WHERE id=:i", i=kid):
        return err("Clé introuvable", 404)
    with engine.begin() as c:
        c.execute(text("UPDATE access_keys SET revoked=1 WHERE id=:i"), {"i": kid})
        c.execute(text("UPDATE users SET gift_until=0, gift_key_id='' WHERE gift_key_id=:i"), {"i": kid})
    return jsonify(ok=True)

@app.post("/api/premium/redeem")
def redeem_key():
    u, e = require_auth()
    if e: return e
    if limited("redeem:" + ip(), 10, 3600) or limited("redeem:" + u["sub"], 10, 3600):
        return err("Trop d'essais, réessaie plus tard", 429)
    row = q1("SELECT * FROM users WHERE id=:id", id=u["sub"])
    k = key_norm(body().get("key"))
    kr = q1("SELECT * FROM access_keys WHERE key_hash=:h", h=key_hash(k)) if k else None
    if not kr or kr.revoked:
        return err("Clé invalide ou expirée", 404)
    gift_on = int(getattr(row, "gift_until", 0) or 0) > time.time()
    if gift_on and row.gift_key_id == kr.id:
        return jsonify(ok=True, already=True, **user_json(row))
    if is_admin(row):
        return err("Tu es administrateur : le Premium est déjà actif", 409)
    if gift_on:
        return err("Tu as déjà un accès Premium offert", 409)
    with engine.begin() as c:
        res = c.execute(text("UPDATE access_keys SET uses=uses+1 WHERE id=:i AND revoked=0 "
                             "AND (max_uses=0 OR uses<max_uses)"), {"i": kr.id})
        if res.rowcount != 1:
            return err("Cette clé a déjà été utilisée le nombre maximum de fois", 409)
        until = FOREVER if not kr.days else int(time.time()) + int(kr.days) * 86400
        c.execute(text("UPDATE users SET gift_until=:g, gift_key_id=:k WHERE id=:u"),
                  {"g": until, "k": kr.id, "u": row.id})
    return jsonify(ok=True, **user_json(q1("SELECT * FROM users WHERE id=:id", id=row.id)))


# ── Extraction audio ─────────────────────────────────────────────────────────
_index = {"m": None, "h": ""}

def index_html():
    path = os.path.join(app.root_path, "index.html")
    m = os.path.getmtime(path)
    if _index["m"] != m:
        with open(path, encoding="utf-8") as f:
            _index.update(m=m, h=f.read())
    return _index["h"]

def og_tags(title, desc, image="", url=""):
    e = lambda s: _html.escape(str(s or ""), quote=True)
    t = [f'<meta property="og:title" content="{e(title)}">', f'<meta property="og:description" content="{e(desc)}">',
         '<meta property="og:type" content="website">', '<meta property="og:site_name" content="TikWave">',
         f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}">']
    if image: t.append(f'<meta property="og:image" content="{e(image)}">')
    if url: t.append(f'<meta property="og:url" content="{e(url)}">')
    return "\n".join(t)

def preview_for(args):
    """Aperçu d'un lien partagé (Messages, WhatsApp, Discord… ne lancent pas le JavaScript)."""
    base = public_base()
    pid, uname = str(args.get("pl") or "")[:64], str(args.get("u") or "")[:24]
    if pid:
        p = q1(PL_SELECT + "WHERE p.id=:id", id=pid)
        if p:
            tr = json.loads(p.tracks or "[]")
            cov = next((t.get("cover") for t in tr if str(t.get("cover", "")).startswith("https://")), "")
            desc = (T("Playlist de {n} sons par @{pseudo}. Écoute-la sur TikWave.", n=len(tr), pseudo=p.username) if len(tr) != 1
                    else T("Playlist de 1 son par @{pseudo}. Écoute-la sur TikWave.", pseudo=p.username))
            return og_tags(f"{p.name} · TikWave", desc, cov, f"{base}/?pl={pid}")
    if uname:
        u = q1("SELECT username,bio,avatar_img,imgv FROM users WHERE lower(username)=:u", u=uname.lower())
        if u:
            img = f"{base}/api/img/{u.username}/a" if getattr(u, "avatar_img", "") else ""
            return og_tags(T("@{pseudo} sur TikWave", pseudo=u.username), u.bio or T("Ses playlists de sons TikTok."), img, f"{base}/?u={u.username}")
    return og_tags(T("TikWave · les sons TikTok, sans la vidéo"), T("Colle un lien TikTok, garde uniquement le son et écoute-le en playlist, écran éteint."))

@app.get("/")
def accueil():
    try: tags = preview_for(request.args)
    except Exception:
        log.exception("aperçu de lien"); tags = ""
    r = make_response(index_html().replace("<!--og-->", tags, 1))
    r.mimetype = "text/html"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Vary"] = "Accept-Language, Cookie"
    return r

# Le navigateur choisit la langue de l'appli installée (nom des raccourcis, description)
MANIFEST_EN = {"lang": "en", "description": "Your TikTok sounds in playlists, without the video."}
SHORTCUTS_EN = ["Add a sound", "My sounds", "Explore"]

@app.get("/manifest.webmanifest")
def manifest():
    with open(os.path.join(app.root_path, "manifest.webmanifest"), encoding="utf-8") as f:
        m = json.load(f)
    if lang() == "en":
        m.update(MANIFEST_EN)
        for sc, name in zip(m.get("shortcuts", []), SHORTCUTS_EN):
            sc["name"] = name
    r = make_response(json.dumps(m, ensure_ascii=False, indent=2))
    r.mimetype = "application/manifest+json"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Vary"] = "Accept-Language, Cookie"
    return r

@app.get("/api/health")
def health():
    return jsonify(ok=True, mail=MAIL_ON, paywall=paywall_on(), adsense=adsense_public())


# ── Pubs rémunérées (Google AdSense) : identifiants dans les variables d'environnement, jamais dans le code ──
ADSENSE_PUB_RE = re.compile(r"(?:ca-)?pub-(\d{10,20})", re.I)
ADSENSE_SLOT_RE = re.compile(r"\d{6,20}")
GOOGLE_ADS_TXT_ID = "f08c47fec0942fa0"        # identifiant d'autorité de certification de Google dans ads.txt
CONTACT_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}")

def adsense_client():
    """« ca-pub-… » depuis ADSENSE_CLIENT (le préfixe « ca- » est facultatif), ou « » si la valeur est invalide."""
    m = ADSENSE_PUB_RE.fullmatch(os.environ.get("ADSENSE_CLIENT", "").strip())
    return f"ca-pub-{m.group(1)}" if m else ""

def adsense_public():
    """Ce que le navigateur a le droit de savoir : seulement avec l'abonnement actif (sinon on ne peut pas retirer les pubs)."""
    client, slot = adsense_client(), os.environ.get("ADSENSE_SLOT", "").strip()
    if paywall_on() and client and ADSENSE_SLOT_RE.fullmatch(slot):
        return {"client": client, "slot": slot}
    return None

@app.get("/ads.txt")
def ads_txt():
    client = adsense_client()
    if not client:
        return "", 404
    r = make_response(f"google.com, {client[3:]}, DIRECT, {GOOGLE_ADS_TXT_ID}\n")
    r.mimetype = "text/plain"
    r.headers["Cache-Control"] = "public, max-age=300"
    return r

def legal_page(name):
    if lang() == "en":
        name = name.replace(".html", ".en.html")
    with open(os.path.join(app.root_path, name), encoding="utf-8") as f:
        html = f.read()
    mail = os.environ.get("CONTACT_EMAIL", "").strip()
    contact = (f'<a href="mailto:{mail}">{mail}</a>' if CONTACT_RE.fullmatch(mail)
               else T("l'adresse de l'éditeur n'est pas encore renseignée."))
    r = make_response(html.replace("{{CONTACT}}", contact))
    r.mimetype = "text/html"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Vary"] = "Accept-Language, Cookie"
    return r

@app.get("/confidentialite")
def confidentialite():
    return legal_page("confidentialite.html")

@app.get("/conditions")
def conditions():
    return legal_page("conditions.html")

@app.get("/signalement")
def signalement():
    return legal_page("signalement.html")

URL_RE = re.compile(r"^https?://[^\s<>\"]{3,500}$")

@app.post("/api/report")
def report():
    if limited("rep:" + ip(), 5, 3600):
        return err("Trop de signalements envoyés, réessaie plus tard", 429)
    d = body()
    url, mail = str(d.get("url") or "").strip(), str(d.get("email") or "").strip()[:120]
    if not URL_RE.match(url): return err("Indique le lien du contenu à signaler")
    if not EMAIL_RE.match(mail): return err("Adresse e-mail invalide")
    reason = d.get("reason") if d.get("reason") in ("copyright", "other") else "other"
    rid = uuid.uuid4().hex
    x("INSERT INTO reports (id,created,url,reason,name,email,details,status) VALUES (:i,:c,:u,:r,:n,:e,:d,'new')",
      i=rid, c=now_iso(), u=url, r=reason, n=str(d.get("name") or "").strip()[:120], e=mail, d=str(d.get("details") or "").strip()[:2000])
    log.warning("Nouveau signalement (%s) : %s", reason, url)
    return jsonify(ok=True, id=rid), 201

@app.get("/api/admin/reports")
def admin_reports():
    _, e = require_admin()
    if e: return e
    rows = q("SELECT * FROM reports ORDER BY created DESC LIMIT 100")
    return jsonify(reports=[{"id": r.id, "created": r.created, "url": r.url, "reason": r.reason, "name": r.name or "",
                             "email": r.email, "details": r.details or "", "status": r.status or "new"} for r in rows])

@app.get("/api/admin/stats")
def admin_stats():
    _, e = require_admin()
    if e: return e
    now = int(time.time())
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 7 * 86400))
    r = q1("SELECT (SELECT COUNT(*) FROM users) AS u, (SELECT COUNT(*) FROM users WHERE COALESCE(created,'')>=:s) AS u7,"
           "(SELECT COUNT(*) FROM users WHERE COALESCE(premium_until,0)>:n) AS paid,"
           "(SELECT COUNT(*) FROM users WHERE COALESCE(gift_until,0)>:n) AS gift,"
           "(SELECT COUNT(*) FROM public_playlists WHERE tracks <> '[]') AS pl, (SELECT COUNT(*) FROM posts) AS po,"
           "(SELECT COUNT(*) FROM reports WHERE status='new') AS rep", s=since, n=now)
    return jsonify(users=int(r.u or 0), users_7d=int(r.u7 or 0), premium_paid=int(r.paid or 0), premium_keys=int(r.gift or 0),
                   playlists=int(r.pl or 0), posts=int(r.po or 0), reports_new=int(r.rep or 0))

@app.post("/api/admin/reports/<rid>")
def admin_report_status(rid):
    _, e = require_admin()
    if e: return e
    st = body().get("status")
    if st not in ("new", "done"): return err("Statut invalide")
    x("UPDATE reports SET status=:s WHERE id=:i", s=st, i=rid)
    return jsonify(ok=True)

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

# ── Sons en ligne relayés par le site : même origine, donc le moteur d'effets (bass boost, distorted…) peut les traiter
MEDIA_RE = re.compile(r"^audio/[0-9a-f]{40}\.mp3$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
MEDIA_CHUNK = 1 << 20            # 1 Mo par réponse : le lecteur redemande la suite, aucune connexion ne reste bloquée

def r2_range(key, start, end):
    """(octets, taille totale) d'un morceau d'un fichier R2. KeyError = absent, IndexError = hors du fichier."""
    try:
        o = s3().get_object(Bucket=os.environ["R2_BUCKET"], Key=key, Range=f"bytes={start}-{end}")
    except Exception as e:
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        if code in ("NoSuchKey", "404"): raise KeyError(key)
        if code == "InvalidRange": raise IndexError(start)
        raise
    return o["Body"].read(), int(str(o.get("ContentRange", "")).rsplit("/", 1)[-1] or o["ContentLength"])

def r2_cors():
    """Essaie d'autoriser la lecture directe des sons R2 par le site (CORS). Sans les droits « Admin », on garde le relais."""
    try:
        s3().put_bucket_cors(Bucket=os.environ["R2_BUCKET"], CORSConfiguration={"CORSRules": [{
            "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "HEAD"], "AllowedHeaders": ["*"],
            "ExposeHeaders": ["Content-Length", "Content-Range", "Accept-Ranges"], "MaxAgeSeconds": 86400}]})
        log.info("R2 : lecture directe autorisée (CORS) — les effets marchent sur les sons en ligne sans relais")
    except Exception as e:
        log.info("R2 : CORS non modifiable avec cette clé (%s) — les effets passeront par le relais /api/media", str(e)[:120])

if R2_OK and os.environ.get("R2_AUTO_CORS", "1") != "0":
    threading.Thread(target=r2_cors, daemon=True).start()

@app.get("/api/media/<path:key>")
def media(key):
    if not R2_OK or not MEDIA_RE.match(key):
        return "", 404
    if limited("media:" + ip(), 1500, 600):
        return "", 429
    rng = RANGE_RE.match(request.headers.get("Range", "").strip())
    try:
        if rng and not rng.group(1) and rng.group(2):               # « les N derniers octets »
            _, total = r2_range(key, 0, 0)
            a = max(0, total - int(rng.group(2))); b = total - 1
        else:
            a = int(rng.group(1) or 0) if rng else 0
            b = int(rng.group(2)) if rng and rng.group(2) else a + MEDIA_CHUNK - 1
        b = min(b, a + MEDIA_CHUNK - 1)
        data, total = r2_range(key, a, b)
    except KeyError:
        return "", 404
    except IndexError:
        return "", 416
    except Exception as e:
        log.warning("Relais audio R2 échoué (%s) : %s", key, str(e)[:200])
        return "", 502
    if a >= total:
        return "", 416
    r = make_response(data, 206 if rng or len(data) < total else 200)
    r.mimetype = "audio/mpeg"
    r.headers["Accept-Ranges"] = "bytes"
    r.headers["Cache-Control"] = "public, max-age=86400"
    if r.status_code == 206:
        r.headers["Content-Range"] = f"bytes {a}-{a + len(data) - 1}/{total}"
    return r

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
    return t[:80] or T("Son TikTok")

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

BAD_WORDS = ("cover", "karaoke", "instrumental", "reaction", "nightcore", "sped up", "speed up", "slowed",
             "8d", "remix", "live", "tutorial", "lesson", "piano", "acoustic", "mashup", "bass boosted", "reverb",
             "type beat", "lyric video", "shorts")
STOP = {"the", "a", "an", "of", "and", "feat", "ft", "featuring", "with", "x", "&", "-", "official", "audio", "video"}

def _norm(t):
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", str(t or "").lower())
    t = re.sub(r"\s-\s(remaster(ed)?|\d{4}|live|mono|stereo|single|radio|version|edit|bonus).*$", " ", t)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t, flags=re.U)).strip()

def _tokens(t):
    return [w for w in _norm(t).split() if w not in STOP and len(w) > 1]

def search_candidates(prefix, query, n=6):
    """Résultats de recherche sans téléchargement. prefix : ytsearch (YouTube) ou scsearch (SoundCloud)."""
    if prefix == "ytsearch" and yt_blocked():
        return []
    try:
        o = {"quiet": True, "extract_flat": True, "skip_download": True, "socket_timeout": 15}
        o.update(_net_opts())
        with yt_dlp.YoutubeDL(o) as y:
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
            out.append({"url": u, "duration": d, "src": prefix, "title": str(e.get("title") or ""),
                        "chan": str(e.get("uploader") or e.get("channel") or "")})
        if not out:
            log.warning("Recherche %s : aucun résultat pour « %s »", prefix, query)
        return out
    except Exception as ex:
        log.warning("Recherche %s échouée pour « %s » : %s", prefix, query, str(ex)[:300])
        return []

# YouTube bloque souvent les serveurs d'hébergement (« confirm you're not a bot », 429, 403).
# Dès qu'il le fait, on passe directement par SoundCloud pendant un moment au lieu de perdre du temps.
_yt_block = {"until": 0}
YT_BLOCK_S = int(os.environ.get("YT_BLOCK_S", "900"))
YT_BLOCK_RE = re.compile(r"not a bot|sign in to confirm|HTTP Error 429|HTTP Error 403|PO Token|Only images are available", re.I)

def yt_blocked():
    return time.time() < _yt_block["until"]

def _note_yt_error(msg):
    if YT_BLOCK_RE.search(str(msg or "")):
        if not yt_blocked():
            log.warning("YouTube bloque le serveur : passage par SoundCloud pendant %d min", YT_BLOCK_S // 60)
        _yt_block["until"] = time.time() + YT_BLOCK_S

def _is_yt(url):
    return bool(re.match(r"^https://(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/", str(url or "")))

def _net_opts():
    """Options réseau : moteur JavaScript pour YouTube (obligatoire depuis fin 2025), cookies / proxy optionnels."""
    o = {}
    rts = {rt: {} for rt in ("deno", "node", "bun") if shutil.which(rt)}
    if rts:
        o["js_runtimes"] = rts
    ck = os.environ.get("YTDLP_COOKIES")
    if ck and os.path.exists(ck):
        o["cookiefile"] = ck
    if os.environ.get("YTDLP_PROXY"):
        o["proxy"] = os.environ["YTDLP_PROXY"]
    return o

def _score(c, title, author, want):
    """Plus c'est haut, plus la version ressemble au titre Spotify. Négatif = à éviter."""
    ct = _norm(c["title"] + " " + c["chan"])
    tk, ak = _tokens(title), _tokens(author.split(",")[0])
    sc = 0.0
    if tk:
        sc += 4.0 * sum(1 for w in tk if w in ct) / len(tk)
    if ak:
        sc += 2.0 * sum(1 for w in ak if w in ct) / len(ak)
    want_bad = _norm(title + " " + author)
    for w in BAD_WORDS:
        if w in ct and w not in want_bad:
            sc -= 2.5
    if "topic" in c["chan"].lower() or "official audio" in c["title"].lower():
        sc += 1.5
    if want and c["duration"]:
        diff = abs(c["duration"] - want)
        if diff <= max(4, want * 0.03): sc += 4.0
        elif diff <= max(8, want * 0.08): sc += 2.0
        elif diff <= max(15, want * 0.2): sc -= 0.5
        else: sc -= 6.0
    elif want and not c["duration"]:
        sc -= 0.5
    return sc

def audio_from_search(opts, title, author, want):
    """Cherche le titre sur YouTube puis SoundCloud avec plusieurs formulations, classe les résultats
    (titre, artiste, durée) et essaie les meilleurs jusqu'à ce qu'un téléchargement réussisse."""
    first = re.split(r",|&| feat\.?| ft\.?", author or "")[0].strip()
    clean = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip() or title
    queries = []
    for qq in (f"{first} {clean}".strip(), f"{first} - {title} audio".strip(" -"),
               f"{clean} {author}".strip(), f"{clean} official audio".strip()):
        if qq and qq.lower() not in [x.lower() for x in queries]:
            queries.append(qq)
    t0, tried, seen, last = time.time(), 0, set(), None
    tol = max(6, want * 0.05) if want else 0
    base = str(opts.get("outtmpl", "")).split(".%(")[0]
    best = None                                  # (écart, info) : la plus proche, gardée de côté si aucune n'a la bonne durée

    def _park(keep):
        """Met de côté (ou efface) le fichier d'une version à la mauvaise durée avant l'essai suivant."""
        f = base + ".mp3"
        if base and os.path.exists(f):
            if keep: os.replace(f, base + ".alt.mp3")
            else: os.remove(f)
    for qi_, query in enumerate(queries):
        if time.time() - t0 > 85 or tried >= 7:
            break
        pools = [search_candidates("ytsearch", query, 6)]
        if qi_ == 0 or not pools[0] or yt_blocked():
            pools.append(search_candidates("scsearch", query, 4))
        cands = []
        for p in pools:
            for c in p:
                if c["url"] in seen:
                    continue
                c["score"] = _score(c, title, author, want)
                cands.append(c)
        cands.sort(key=lambda c: -c["score"])
        for c in cands:
            if c["score"] < 1.5 and tried >= 2:
                continue            # trop éloigné du titre demandé
            if time.time() - t0 > 85 or tried >= 7:
                break
            if c["src"] == "ytsearch" and yt_blocked():
                continue
            seen.add(c["url"]); tried += 1
            try:
                o = dict(opts); o.update(_net_opts())
                with yt_dlp.YoutubeDL(o) as y:
                    info = y.extract_info(c["url"], download=True)
                if info and info.get("entries"):
                    info = info["entries"][0]
                gap = abs(float(info.get("duration") or 0) - want) if want and info.get("duration") else 0
                if gap > tol:
                    log.info("Spotify « %s » : %s a la mauvaise durée (écart %.0f s), on continue", query, c["url"], gap)
                    if best is None or gap < best[0]:
                        best = (gap, info); _park(True)
                    else:
                        _park(False)
                    continue
                if base and os.path.exists(base + ".alt.mp3"):
                    os.remove(base + ".alt.mp3")
                log.info("Spotify « %s » → %s (%s) score=%.1f", query, c["src"], c["url"], c["score"])
                return info
            except Exception as ex:
                last = str(ex)[:300]
                if c["src"] == "ytsearch":
                    _note_yt_error(last)
                log.warning("Téléchargement %s échoué (%s) : %s", c["src"], c["url"], last)
    if best:                                     # aucune version exacte : la plus proche plutôt que rien
        if base and os.path.exists(base + ".alt.mp3"):
            os.replace(base + ".alt.mp3", base + ".mp3")
        log.warning("Spotify : « %s - %s » sans version exacte, écart %.0f s", author, title, best[0])
        return best[1]
    log.warning("Spotify : aucune version trouvée pour « %s - %s » (%d essais)", author, title, tried)
    return None


def _loose(t):
    return [w for w in re.sub(r"[^\w\s]", " ", str(t or "").lower(), flags=re.U).split() if w not in STOP and len(w) > 1]

def soundcloud_twin(opts, title, author, want):
    """Le même son sur SoundCloud, quand YouTube refuse de le donner. None si rien ne ressemble assez."""
    tk = _loose(title)
    if not tk:
        return None
    cands, seen = [], set()
    for q in (title, f"{title} {author}".strip()):
        for c in search_candidates("scsearch", q, 8):
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            ct = set(_loose(c["title"] + " " + c["chan"]))
            match = sum(1 for w in tk if w in ct) / len(tk)
            if match >= 0.6:
                cands.append((-match, abs((c["duration"] or 0) - want) if want else 0, c["url"]))
        if cands:
            break
    for _, _, u in sorted(cands)[:3]:
        try:
            o = dict(opts); o.update(_net_opts())
            with yt_dlp.YoutubeDL(o) as y:
                info = y.extract_info(u, download=True)
            log.info("YouTube indisponible : « %s » récupéré sur SoundCloud (%s)", title, u)
            return info
        except Exception as ex:
            log.warning("Téléchargement SoundCloud échoué (%s) : %s", u, str(ex)[:300])
    return None


# ── Recherche de sons : 5 résultats par plateforme, téléchargeables comme un lien TikTok ──
SPOTIFY_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
FIND_N, FIND_MAX_DUR = 5, 1200
_find_cache, _sp_token = {}, {"t": "", "exp": 0}

def _cover(e, src):
    if src == "youtube" and e.get("id"):
        return f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
    best = ""
    for t in e.get("thumbnails") or []:
        u = str(t.get("url") or "")
        if u.startswith("https://"):
            best = u
            if (t.get("width") or 0) >= 300:
                break
    return best or (str(e.get("thumbnail") or "") if str(e.get("thumbnail") or "").startswith("https://") else "")

def _find_items(entries, src):
    out = []
    for e in entries or []:
        if not e:
            continue
        if src == "youtube":
            url = f"https://www.youtube.com/watch?v={e['id']}" if e.get("id") else str(e.get("url") or "")
        else:
            url = str(e.get("webpage_url") or e.get("url") or "").split("?")[0]
        if not FIND_URL_RE.match(url):
            continue
        try: d = int(float(e.get("duration") or 0))
        except Exception: d = 0
        if not d or d > FIND_MAX_DUR:
            continue                                   # direct, ou mix de plusieurs heures
        author = re.sub(r"\s*-\s*Topic$", "", str(e.get("uploader") or e.get("channel") or ""))[:60]
        out.append({"src": src, "url": url, "title": str(e.get("title") or "")[:120], "author": author,
                    "duration": d, "cover": _cover(e, src)})
        if len(out) >= 12:
            break
    return out

def _rank(q, items):
    """Le plus ressemblant d'abord : mots de la recherche retrouvés dans titre + artiste, phrase exacte en bonus,
    reprises / versions modifiées (cover, sped up, lyrics…) en dernier sauf si on les a demandées."""
    want, qn = _tokens(q), _norm(q)
    def score(it):
        t = _norm(it["title"] + " " + it["author"])
        s = sum(1 for w in want if w in t) / len(want) if want else 0
        s += 0.5 if qn and qn in _norm(it["title"]) else 0
        raw = (it["title"] + " " + it["author"]).lower()          # avec les parenthèses : « (cover) », « [sped up] »
        s -= sum(0.6 for w in BAD_WORDS + ("lyrics",) if w in raw and w not in q.lower())
        s += 0.3 if "official" in t or "topic" in it["author"].lower() else 0
        return s
    return sorted(items, key=score, reverse=True)

def _find_yd(prefix, q, n):
    o = {"quiet": True, "extract_flat": True, "skip_download": True, "socket_timeout": 12}
    o.update(_net_opts())
    with yt_dlp.YoutubeDL(o) as y:
        return (y.extract_info(f"{prefix}{n}:{q}", download=False) or {}).get("entries") or []

def find_yt(q):
    return _find_items(_find_yd("ytsearch", q, 10), "youtube")

def find_sc(q):
    return _find_items(_find_yd("scsearch", q, 8), "soundcloud")

def find_spotify(q):
    """Titres Spotify (clés développeur gratuites requises). Le son est ensuite trouvé sur YouTube/SoundCloud."""
    if time.time() > _sp_token["exp"]:
        tok = base64.b64encode(f"{SPOTIFY_ID}:{SPOTIFY_SECRET}".encode()).decode()
        req = urllib.request.Request("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                                     headers={"Authorization": "Basic " + tok, "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=8) as r:
            j = json.loads(r.read())
        _sp_token.update(t=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)) - 60)
    req = urllib.request.Request("https://api.spotify.com/v1/search?" + urllib.parse.urlencode({"q": q, "type": "track", "limit": FIND_N}),
                                 headers={"Authorization": "Bearer " + _sp_token["t"]})
    with urllib.request.urlopen(req, timeout=8) as r:
        items = json.loads(r.read()).get("tracks", {}).get("items") or []
    out = []
    for it in items:
        imgs = (it.get("album") or {}).get("images") or []
        out.append({"src": "spotify", "url": f"https://open.spotify.com/track/{it.get('id', '')}", "title": str(it.get("name") or "")[:120],
                    "author": ", ".join(a.get("name", "") for a in it.get("artists") or [])[:60],
                    "duration": int((it.get("duration_ms") or 0) / 1000), "cover": (imgs[1] if len(imgs) > 1 else imgs[0])["url"] if imgs else ""})
    return [x for x in out if SPT_RE.match(x["url"])]

@app.get("/api/find")
def find():
    q = re.sub(r"\s+", " ", str(request.args.get("q", ""))).strip()[:100]
    if len(q) < 2:
        return err("Écris au moins 2 lettres")
    jobs = {"youtube": find_yt, "soundcloud": find_sc}
    if SPOTIFY_ID and SPOTIFY_SECRET:
        jobs["spotify"] = find_spotify
    key = (q.lower(), tuple(jobs))
    hit = _find_cache.get(key)
    if hit and time.time() - hit[0] < 900:
        return jsonify(**hit[1])
    if limited("find:" + ip(), 30, 600):
        return err("Trop de recherches d'un coup, patiente un peu", 429)
    from concurrent.futures import ThreadPoolExecutor
    out = {}
    with ThreadPoolExecutor(len(jobs)) as ex:
        futs = {k: ex.submit(f, q) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                out[k] = _rank(q, f.result(timeout=25))[:FIND_N]
            except Exception as e:
                log.warning("Recherche %s échouée pour « %s » : %s", k, q, str(e)[:200])
                out[k] = []
    res = {"q": q, "sources": list(jobs), "results": out}
    if any(out.values()):
        if len(_find_cache) > 300:
            _find_cache.clear()
        _find_cache[key] = (time.time(), res)
    return jsonify(**res)


@app.post("/api/extract")
def extract():
    if limited("extract:" + ip(), 40, 600):
        return err("Trop de conversions d'un coup, patiente un peu", 429)
    nettoyer()
    b = body()
    raw = str(b.get("url", "")).strip()
    spotify = bool(SPT_RE.match(raw))
    found = bool(FIND_URL_RE.match(raw))
    title_in = str(b.get("title") or "")[:120]; author_in = str(b.get("author") or "")[:60]
    if spotify:
        url = raw
    elif found:
        url = raw
    else:
        url = trouver_lien(raw)
    if not url:
        return err("Lien TikTok invalide")
    # Gratuit (ou anonyme) quand l'abonnement est actif : stockage en ligne imposé, jamais de fichier local.
    forced = paywall_on() and not request_premium()
    online = R2_OK and (b.get("store") == "online" or forced)
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
    opts.update(_net_opts())
    info = None
    if spotify:
        try: want = int(float(b.get("duration") or 0))
        except Exception: want = 0
        info = audio_from_search(opts, title_in, author_in, want)
        if not info:
            return err(T("Aucune version audio trouvée pour « {titre} ».", titre=title_in[:50]), 502, retry=True)
    elif found:
        yt = _is_yt(url)
        if not (yt and yt_blocked()):
            try:
                with yt_dlp.YoutubeDL(opts) as y:
                    pre = y.extract_info(url, download=False)
                    if pre.get("is_live") or (pre.get("duration") or 0) > FIND_MAX_DUR:
                        return err("Ce son est trop long (plus de 20 min) ou en direct.")
                    info = y.process_ie_result(pre, download=True)
            except Exception as ex:
                log.warning("Téléchargement %s échoué : %s", url, str(ex)[:300])
                if yt:
                    _note_yt_error(ex)
        if not info and yt and title_in:
            try: want = int(float(b.get("duration") or 0))
            except Exception: want = 0
            info = soundcloud_twin(opts, title_in, author_in, want)
        if not info:
            if yt and yt_blocked():
                return err("YouTube bloque le serveur en ce moment et ce son est introuvable sur SoundCloud. Essaie un résultat SoundCloud.", 502, retry=True)
            return err("Impossible de récupérer ce son pour le moment. Essaie un autre résultat.", 502, retry=True)
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
    res = dict(id=uid, url=url, title=(title_in or titre(info)) if spotify else (title_in or str(info.get("title") or "")[:120] or titre(info)) if found else titre(info),
               author=author_in if spotify or (found and author_in) else re.sub(r"\s*-\s*Topic$", "", info.get("uploader", "") or ""),
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
            if forced:
                log.warning("Envoi R2 échoué (stockage en ligne imposé) : %s", e)
                try: os.remove(f"audio/{uid}.mp3")
                except Exception: pass
                return err("Le stockage en ligne ne répond pas, réessaie dans un instant.", 502, retry=True)
            log.warning("Envoi R2 échoué, repli en local : %s", e)
    return jsonify(**res)

@app.get("/ads/<path:f>")
def ads_file(f):
    return send_from_directory(os.path.abspath("ads"), f, max_age=3600, conditional=True)

@app.get("/audio/<path:f>")
def audio(f):
    return send_from_directory("audio", f, max_age=3600, conditional=True)

@app.get("/<path:f>")
def statique(f):
    if f not in STATIQUE: return "", 404
    return send_from_directory(".", f, mimetype=STATIQUE[f], max_age=0)
