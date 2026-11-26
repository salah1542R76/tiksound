"""Tests de bout en bout : vrai navigateur (Chrome), vrai serveur Flask, adaptateurs Stripe factices.

Lancer :  python3 -m pytest tests/e2e_front.py -q
(nécessite Playwright et Chrome ; CHROME_BIN pour indiquer un autre chemin que /usr/bin/google-chrome)
"""
import json
import os
import re
import threading
import time

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "media")
CHROME = os.environ.get("CHROME_BIN", "/usr/bin/google-chrome")
TIMEOUT = 9000


# ───────────────────────── infrastructure ─────────────────────────
class Live:
    def __init__(self, app, srv):
        self.app, self.srv = app, srv
        self.base = f"http://127.0.0.1:{srv.server_port}"


@pytest.fixture
def live(app, monkeypatch):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "sk_test", "price_1", True
    monkeypatch.setattr(app, "stripe_get_price", lambda: {"amount": 299, "currency": "eur", "interval": "month"})
    monkeypatch.setattr(app, "stripe_create_customer", lambda email, uid: "cus_e2e")
    monkeypatch.setattr(app, "stripe_create_checkout", lambda c, u, b: "cs_test_secret_e2e")
    monkeypatch.setattr(app, "stripe_create_portal", lambda c, r: "https://billing.stripe.test/p/e2e")
    srv = make_server("127.0.0.1", 0, app.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield Live(app, srv)
    srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        yield b
        b.close()


def _media(route):
    name = re.search(r"/__m/(\w+)\.wav", route.request.url).group(1)
    path = os.path.join(MEDIA, name + ".wav")
    if not os.path.exists(path):                      # ex. « missing.wav » : une pub dont le fichier n'existe pas
        return route.fulfill(status=404, body="")
    data = open(path, "rb").read()
    m = re.match(r"bytes=(\d+)-(\d*)", route.request.headers.get("range", ""))
    if not m:                                          # comme R2 : les requêtes « Range » permettent d'avancer dans le son
        return route.fulfill(status=200, body=data, content_type="audio/wav", headers={"Accept-Ranges": "bytes"})
    a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else len(data) - 1
    route.fulfill(status=206, body=data[a:b + 1], content_type="audio/wav",
                  headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes {a}-{b}/{len(data)}"})


# Faux script Google : met en file les demandes `push`, puis « remplit » chaque <ins> comme le vrai script le ferait.
GOOGLE_STUB = """
(function(){
  var g = window.__gs = window.__gs || {loads: 0, pushes: 0};
  g.loads++;
  var q = window.adsbygoogle = window.adsbygoogle || [];
  var pending = Array.prototype.slice.call(q); q.length = 0;
  function fill(){
    document.querySelectorAll('ins.adsbygoogle:not([data-ad-status])').forEach(function(el){
      var st = window.__gsMode || 'filled';
      el.setAttribute('data-ad-status', st);
      if (st === 'filled') el.innerHTML = '<div class="fake-creative" style="height:60px">Annonce Google</div>';
    });
  }
  q.push = function(){ g.pushes++; fill(); return q.length; };
  pending.forEach(function(){ q.push({}); });
})();
"""


# Faux Stripe.js : enregistre comment l'appli l'utilise (clé publique, client_secret, apparence, mise en page…)
STRIPE_JS_STUB = """
(function(){
  var rec = window.__stripe = {pk: null, init: null, formOpts: null, mounted: null, handlers: {}, confirmed: null, confirmFails: false,
    fire: function(ev, payload){ return rec.handlers[ev] && rec.handlers[ev](payload); }};
  window.Stripe = function(pk){
    rec.pk = pk;
    return {
      initCheckoutFormSdk: function(o){
        rec.init = o;
        return {
          createForm: function(fo){
            rec.formOpts = fo;
            return {
              mount: function(sel){ rec.mounted = sel; document.querySelector(sel).innerHTML = '<div id="fake-stripe-form">Formulaire Stripe</div>'; },
              on: function(ev, fn){ rec.handlers[ev] = fn; }
            };
          },
          loadActions: function(){
            return Promise.resolve({type: 'success', actions: {confirm: function(o){
              rec.confirmed = o;
              return rec.confirmFails ? Promise.reject(new Error('refusé')) : Promise.resolve();
            }}});
          }
        };
      }
    };
  };
})();
"""


class Ctx:
    """Un navigateur « utilisateur » : contexte isolé, cookies à lui, médias et pages Stripe simulés."""
    def __init__(self, browser, live, **opts):
        self.live = live
        self.ctx = browser.new_context(service_workers="block", **({"viewport": {"width": 1100, "height": 800}, "locale": "fr-FR"} | opts))
        self.ctx.set_default_timeout(TIMEOUT)
        self.ctx.add_init_script("localStorage.setItem('tkwel','1')")   # pas d'écran de bienvenue dans les tests (sauf test dédié)
        self.ctx.route(re.compile(r"/__m/\w+\.wav"), _media)
        self.ctx.route(re.compile(r"https://(checkout|billing)\.stripe\.test/"),
                       lambda r: r.fulfill(status=200, content_type="text/html", body="<h1>Stripe simulé</h1>"))
        self.ctx.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/"), lambda r: r.abort())
        self.stripe_js()
        self.ctx.route(re.compile(r"https://ads\.example\.test/"),
                       lambda r: r.fulfill(status=200, content_type="text/html", body="<h1>Annonceur</h1>"))
        self.page = self.ctx.new_page()
        self.logs = []
        self.page.on("pageerror", lambda e: self.logs.append(str(e)))

    def signup(self, email, username, pw="motdepasse1"):
        b = self.live.base
        r = self.ctx.request.post(b + "/api/auth/register", data={"email": email, "username": username, "password": pw})
        assert r.ok, r.text()
        code = re.match(r"(\d{6})", self.live.app.OUTBOX[-1]["subject"]).group(1)
        r = self.ctx.request.post(b + "/api/auth/verify", data={"email": email, "code": code})
        assert r.status == 201, r.text()
        return r.json()

    def seed(self, tracks=None, **extra):
        """État local de l'appli AVANT le chargement de la page (n'écrase pas un état déjà là)."""
        state = {"tracks": tracks if tracks is not None else [], "pls": [], **extra}
        raw = json.dumps(state).replace("{BASE}", self.live.base)
        self.ctx.add_init_script("if(!localStorage.getItem('tiksound')) localStorage.setItem('tiksound', %s);"
                                 % json.dumps(raw))

    def stripe_js(self, mode="ok"):
        """Simule https://js.stripe.com/dahlia/stripe.js : « ok » (faux script) ou « blocked » (hors ligne)."""
        self.ctx.route("https://js.stripe.com/dahlia/stripe.js",
                       (lambda r: r.abort()) if mode == "blocked" else
                       (lambda r: r.fulfill(status=200, content_type="application/javascript", body=STRIPE_JS_STUB)))

    def google(self, mode="filled"):
        """Simule le script AdSense : « filled » (annonce affichée), « unfilled » (aucune annonce) ou « blocked » (réseau coupé)."""
        self.google_hits, self.google_mode = [], mode

        def handler(route):
            self.google_hits.append(route.request.url)
            if self.google_mode == "blocked":
                return route.abort()
            route.fulfill(status=200, content_type="application/javascript", body=GOOGLE_STUB)
        self.ctx.route(re.compile(r"https://pagead2\.googlesyndication\.com/"), handler)
        if mode == "unfilled":
            self.ctx.add_init_script("window.__gsMode = 'unfilled';")

    def with_ads(self, ads):
        """Injecte une liste de pubs dans la page servie (remplace le début de `const ADS = [`)."""
        inner = json.dumps(ads)[1:-1]

        def handler(route):
            resp = route.fetch()
            route.fulfill(response=resp, body=resp.text().replace("const ADS = [", "const ADS = [" + inner + ",", 1))
        self.ctx.route(re.compile(r"^http://127\.0\.0\.1:\d+/(\?.*)?$"), handler)

    def open(self, hash_=""):
        self.page.goto(self.live.base + "/" + hash_)
        self.booted()
        return self.page

    def booted(self):
        self.page.wait_for_function("typeof BOOTED!=='undefined' && BOOTED===true")

    def ev(self, js):
        return self.page.evaluate(js)

    def close(self):
        self.ctx.close()


@pytest.fixture
def mk(browser, live):
    made = []

    def make(**opts):
        c = Ctx(browser, live, **opts)
        made.append(c)
        return c
    yield make
    for c in made:
        c.close()


def tracks(n=3, **kw):
    """n sons « en ligne » (lus depuis /__m/silence.wav : 60 s de silence)."""
    return [dict(id=f"t{i}", title=f"Son {i}", author="Artiste", duration=60, url="", local=False,
                 remote=f"{{BASE}}/__m/silence.wav?n={i}", added=i, **kw) for i in range(1, n + 1)]


def make_premium(live, email):
    live.app.x("UPDATE users SET gift_until=:g WHERE email=:e", g=live.app.FOREVER, e=email)


def sheet_text(c):
    """Texte de la fenêtre Premium, une fois le prix chargé (avant, il n'y a qu'un squelette vide)."""
    c.page.wait_for_selector("#gen:not([hidden])")
    c.page.wait_for_function("document.querySelector('#genb').innerText.trim().length>0")
    return c.page.inner_text("#genb")


# ═══════════════════════════ Task 6 : Premium ═══════════════════════════
def test_star_hidden_when_paywall_off(live, mk):
    live.app.STRIPE_KEY = ""
    c = mk()
    c.open()
    assert c.page.locator("#premb").is_hidden()
    assert c.ev("PAYWALL") is False and c.ev("isPrem()") is True


def test_star_visible_when_paywall_on(live, mk):
    c = mk()
    c.open()
    assert c.page.locator("#premb").is_visible() and c.ev("PAYWALL") is True


def test_sheet_shows_price_299_fr(live, mk):
    c = mk()
    c.open()
    c.page.click("#premb")
    assert re.search(r"2,99\s*€\s*/\s*mois", sheet_text(c)), sheet_text(c)


def test_anonymous_cta_opens_signup(live, mk):
    c = mk()
    c.open()
    c.page.click("#premb")
    c.page.get_by_role("button", name="Créer un compte pour continuer").click()
    c.page.wait_for_selector("#auth-modal:not([hidden])")
    assert "Créer un compte" in c.page.inner_text("#auth-box")
    c.page.wait_for_selector("#gen", state="hidden")


def open_checkout(c):
    c.signup("a@example.com", "alice")
    c.open()
    c.page.click("#premb")
    c.page.get_by_role("button", name=re.compile("Passer Premium")).click()
    c.page.wait_for_selector("#checkout-form #fake-stripe-form")


def test_free_checkout_mounts_the_embedded_form(live, mk):
    c = mk()
    open_checkout(c)
    got = c.ev("({pk: __stripe.pk, secret: __stripe.init.clientSecret, appearance: __stripe.init.appearance, "
               "layout: __stripe.formOpts.layout, mounted: __stripe.mounted})")
    assert got["pk"] == "pk_test_default"                              # clé publique donnée par le serveur
    assert got["secret"] == "cs_test_secret_e2e"
    assert got["layout"] == "expanded" and got["mounted"] == "#checkout-form"
    assert got["appearance"]["theme"] == "stripe" and got["appearance"]["variables"]["colorPrimary"] == "#0570de"
    assert c.page.url.startswith(live.base)                            # on reste dans l'appli : pas de page Stripe hébergée
    assert c.page.locator("script[src='https://js.stripe.com/dahlia/stripe.js']").count() == 1


def test_embedded_form_confirm_is_wired_and_errors_are_caught(live, mk):
    c = mk()
    open_checkout(c)
    c.ev("__stripe.fire('confirm', {id: 7})")
    assert c.ev("__stripe.confirmed.formConfirmEvent.id") == 7
    c.ev("__stripe.confirmFails = true")
    c.ev("__stripe.fire('confirm', {id: 8})")                          # le refus est attrapé et journalisé
    assert c.logs == []


def test_checkout_without_stripe_js_explains_and_keeps_the_button(live, mk):
    c = mk()
    c.stripe_js("blocked")
    c.signup("a@example.com", "alice")
    c.open()
    c.page.click("#premb")
    btn = c.page.get_by_role("button", name=re.compile("Passer Premium"))
    btn.click()
    c.page.wait_for_selector("#toasts >> text=sans connexion")
    assert c.page.locator("#checkout-form").count() == 0
    assert c.page.get_by_role("button", name=re.compile("Passer Premium")).is_enabled()


def test_redeem_key_turns_premium_and_badge(live, mk):
    live.app.ADMIN_EMAILS = {"boss@example.com"}
    boss, friend = mk(), mk()
    boss.signup("boss@example.com", "boss")
    key = boss.ctx.request.post(live.base + "/api/admin/keys", data={"label": "Marie", "uses": 1}).json()["key"]
    friend.signup("f@example.com", "friend")
    friend.open()
    assert friend.ev("isPrem()") is False
    friend.page.click("#premb")
    friend.page.fill("#rk", key.lower())
    friend.page.get_by_role("button", name="Activer").click()
    friend.page.wait_for_selector("#gen", state="hidden")
    friend.page.wait_for_function("isPrem()===true")
    assert "on" in friend.page.get_attribute("#premb", "class")
    friend.page.goto(live.base + "/#profil")
    friend.page.wait_for_selector("#profil-content .disp")
    assert friend.page.locator("#profil-content .pbadge").count() >= 1
    assert friend.page.locator("#profil-content h3 .pbadge", has_text="Actif").count() == 1     # carte Premium : « Actif »


def test_redeem_wrong_key_shows_error(live, mk):
    c = mk()
    c.signup("a@example.com", "alice")
    c.open()
    c.page.click("#premb")
    c.page.fill("#rk", "TKS-AAAA-BBBB-CCCC")
    c.page.get_by_role("button", name="Activer").click()
    c.page.wait_for_function("document.querySelector('#rk-err').textContent.includes('invalide')")
    assert c.ev("isPrem()") is False


def test_profile_storage_locked_for_free_and_value_reflects_setting(live, mk):
    free = mk()
    free.signup("a@example.com", "alice")
    free.seed(ui={"storage": "local"})
    free.open("#profil")
    free.page.wait_for_selector("#profil-content .disp")
    assert free.page.locator("#stor").count() == 1                      # plus d'identifiant en double
    sel = free.page.locator("#storsel")
    assert sel.is_disabled() and sel.input_value() == "online" and free.page.locator("#stor-lock").is_visible()

    prem = mk()
    prem.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    prem.seed(ui={"storage": "local"})
    prem.open("#profil")
    prem.page.wait_for_function("isPrem()===true")
    sel = prem.page.locator("#storsel")
    assert sel.is_enabled() and sel.input_value() == "local" and prem.page.locator("#stor-lock").is_hidden()
    sel.select_option("online")
    assert prem.ev("S.ui.storage") == "online" and prem.ev("storeMode()") == "online"


def test_admin_section_only_for_admin_key_shown_once_then_revoke(live, mk):
    live.app.ADMIN_EMAILS = {"boss@example.com"}
    user, boss = mk(), mk()
    user.signup("u@example.com", "simple")
    user.open("#profil")
    user.page.wait_for_selector("#profil-content .disp")
    assert user.page.locator("#keys-list").count() == 0

    boss.signup("boss@example.com", "boss")
    boss.open("#profil")
    boss.page.fill("#kl", "Marie")
    boss.page.get_by_role("button", name="Créer", exact=True).click()
    boss.page.wait_for_selector("#gen:not([hidden]) #newkey")
    key = boss.page.inner_text("#newkey").strip()
    assert re.fullmatch(r"TKW(-[A-Z0-9]{4}){3}", key)
    boss.page.keyboard.press("Escape")
    boss.page.wait_for_selector("#gen", state="hidden")
    boss.page.wait_for_selector("#keys-list >> text=Marie")
    assert key not in boss.page.inner_text("body")                      # affichée une seule fois
    boss.page.get_by_role("button", name="Révoquer").click()
    boss.page.click("#cfok")
    boss.page.wait_for_selector("#keys-list >> text=révoquée")


def test_return_success_polls_until_premium(live, mk):
    c = mk()
    c.signup("a@example.com", "alice")
    c.page.goto(live.base + "/?premium=success")
    c.booted()
    assert "premium" not in c.page.url
    threading.Timer(3, make_premium, (live, "a@example.com")).start()
    c.page.wait_for_selector(".toast >> text=Bienvenue dans Premium", timeout=20000)
    assert c.ev("isPrem()") is True


def test_return_cancel_shows_toast_and_cleans_url(live, mk):
    c = mk()
    c.page.goto(live.base + "/?premium=cancel")
    c.page.wait_for_selector(".toast >> text=Paiement annulé")
    assert "premium" not in c.page.url


def test_premium_cached_for_offline_until_exp(live, mk):
    future, past = int(time.time()) + 86400, int(time.time()) - 10
    c = mk()
    c.seed(paywall=True, prem={"exp": future})
    c.ctx.route(re.compile(r"/api/"), lambda r: r.abort())                # réseau coupé : on garde le cache
    c.page.goto(live.base + "/")
    c.booted()
    assert c.ev("PAYWALL") is True and c.ev("isPrem()") is True

    c2 = mk()
    c2.seed(paywall=True, prem={"exp": past})
    c2.ctx.route(re.compile(r"/api/"), lambda r: r.abort())
    c2.page.goto(live.base + "/")
    c2.booted()
    assert c2.ev("isPrem()") is False

    c3 = mk()                                                              # réseau OK mais pas connecté (401) : cache effacé
    c3.seed(paywall=True, prem={"exp": future})
    c3.open()
    c3.page.wait_for_function("!S.prem")
    assert c3.ev("isPrem()") is False


def test_mp3_menu_hidden_for_free(live, mk):
    base = [dict(id="t1", title="Local", author="A", duration=60, local=True, added=1),
            dict(id="t2", title="À télécharger", author="A", duration=60, local=False, pending=True,
                 url="https://www.tiktok.com/@x/video/1", added=2)]
    free = mk()
    free.signup("a@example.com", "alice")
    free.seed(base)
    free.open()
    free.ev("trackMenu('t1')")
    txt = free.page.inner_text("#optl")
    assert "Lire" in txt and "Enregistrer le fichier MP3" not in txt
    free.page.keyboard.press("Escape")
    free.ev("trackMenu('t2')")
    assert "Préparer pour l'écoute" in free.page.inner_text("#optl")

    prem = mk()
    prem.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    prem.seed(base)
    prem.open()
    prem.page.wait_for_function("isPrem()===true")
    prem.ev("trackMenu('t1')")
    assert "Enregistrer le fichier MP3" in prem.page.inner_text("#optl")
    prem.page.keyboard.press("Escape")
    prem.ev("trackMenu('t2')")
    assert "Télécharger sur l'appareil" in prem.page.inner_text("#optl")


def test_grab_asks_online_for_free(live, mk):
    """Un gratuit demande toujours « online » au serveur, même si son réglage dit « local »."""
    c = mk()
    c.signup("a@example.com", "alice")
    c.seed(ui={"storage": "local"})
    sent = []
    c.ctx.route(re.compile(r"/api/extract"), lambda r: (sent.append(r.request.post_data_json), r.abort()))
    c.open()
    c.ev("grab('https://www.tiktok.com/@x/video/1','tz').catch(()=>0)")
    assert sent and sent[0]["store"] == "online"


# ═══════════════════════════ Task 7 : pubs et bandeaux ═══════════════════════════
TEXT_AD = {"type": "text", "title": "Pub texte", "text": "Une phrase d'accroche", "seconds": 1,
           "url": "https://ads.example.test/offre", "cta": "Voir l'offre"}
AUDIO_AD = {"type": "audio", "title": "Pub audio", "text": "Écoute-moi", "audio": "/__m/ad.wav",
            "url": "https://ads.example.test/audio", "cta": "En savoir plus", "slots": ["track"]}


def start(c, i):
    c.ev(f"void play(['t{i}'],0)")                      # `void` : ne pas attendre la fin de la pub


def playing_src(c, i):
    c.page.wait_for_function(f"au.src.includes('n={i}') && !au.paused && !AD.on")


def play_n(c, n):
    """Lance les sons 1..n l'un après l'autre (chacun doit démarrer sans pub)."""
    for i in range(1, n + 1):
        start(c, i)
        playing_src(c, i)
        assert c.page.locator("#adbox").is_hidden(), f"pub inattendue avant le son {i}"


def free_player(mk, ads, n=8, **seed):
    c = mk()
    c.with_ads(ads)
    c.seed(tracks(n), **seed)
    c.open()
    return c


def test_ad_after_five_tracks_then_sixth_plays(live, mk):
    c = free_player(mk, [TEXT_AD])
    play_n(c, 5)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")
    assert c.ev("au.paused") is True and "publicité" in c.page.inner_text("#adbox").lower()   # le CSS met le libellé en majuscules
    assert "Pub texte" in c.page.inner_text("#adbox")
    c.page.wait_for_selector("#adbox", state="hidden")
    playing_src(c, 6)
    assert c.ev("S.adn") == 1
    start(c, 7)
    playing_src(c, 7)
    assert c.page.locator("#adbox").is_hidden() and c.ev("S.adn") == 2


def test_no_ad_for_premium(live, mk):
    c = mk()
    c.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    c.with_ads([TEXT_AD])
    c.seed(tracks(8))
    c.open()
    c.page.wait_for_function("isPrem()===true")
    play_n(c, 7)
    assert c.ev("S.adn||0") == 0


def test_no_ad_when_paywall_off(live, mk):
    live.app.STRIPE_KEY = ""
    c = free_player(mk, [TEXT_AD])
    play_n(c, 7)


def test_empty_ads_no_ad_no_banner(live, mk):
    c = mk()
    c.seed(tracks(8))
    c.open()
    play_n(c, 7)
    for h in ("accueil", "bibliotheque", "playlists", "decouvrir"):
        c.page.goto(live.base + "/#" + h)
        c.page.wait_for_timeout(300)
        assert c.page.locator(".adb").count() == 0, h


def test_no_ad_when_offline(live, mk):
    """Hors ligne, un gratuit n'entend rien : ni pub, ni compteur qui avance. La pub arrive au retour en ligne."""
    c = free_player(mk, [TEXT_AD])
    c.page.wait_for_function("PAYWALL===true")
    play_n(c, 5)
    go_offline(c)
    start(c, 6)
    wait_blocked_toast(c)
    assert c.page.locator("#adbox").is_hidden() and c.ev("S.adn") == 5
    go_online(c)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")


def test_failing_ad_audio_music_continues(live, mk):
    broken = dict(AUDIO_AD, audio="/__m/missing.wav")
    c = free_player(mk, [broken])
    play_n(c, 5)
    start(c, 6)
    playing_src(c, 6)                                   # la pub est tombée en erreur : la musique démarre quand même
    assert c.page.locator("#adbox").is_hidden() and c.ev("AD.on") is False and c.ev("S.adn") == 1


@pytest.mark.parametrize("ad", [TEXT_AD, AUDIO_AD], ids=["texte", "audio"])
def test_controls_during_ad_never_play_two_audios(live, mk, ad):
    ad = dict(ad, seconds=3)
    c = free_player(mk, [ad])
    play_n(c, 5)
    # surveillance dans la page : jamais de musique pendant la pub, jamais de temps d'écoute compté pendant la pub
    c.ev("""window.__viol=0; window.__d=0; let base=null;
            setInterval(()=>{ if(AD.on){ if(!au.paused && !/ad\\.wav/.test(au.src)) __viol++;
                                         if(base===null) base=S.stats.sec; __d=Math.max(__d,S.stats.sec-base); } else base=null; },15)""")
    c.ev("void play(['t6','t7','t8'],0)")                  # file de 3 sons : le son visé est t6
    c.page.wait_for_selector("#adbox", state="visible")
    for js in ("step(1)", "step(1)", "step(-1)", "seekBy(10)", "togglePlay()", "toggleShuffle()"):
        c.ev(js)
        assert c.page.locator("#adbox").is_visible(), js
        assert c.ev("q[qi]") == "t6", js                    # « suivant / précédent » sont ignorés pendant une pub
    c.ev("void play(['t7'],0)")                             # viser un autre son pendant la pub est permis
    assert c.page.locator("#adbox").is_visible()
    for key in ("Space", "ArrowRight", "Shift+ArrowRight", "Shift+ArrowLeft", "k"):
        c.page.keyboard.press(key)
    # deux lecteurs existent (normal / effets) mais jamais plus d'un ne joue à la fois
    assert c.ev("document.querySelectorAll('audio').length") == 2
    assert c.ev("[...document.querySelectorAll('audio')].filter(a=>!a.paused).length") <= 1
    if ad["type"] == "text":
        assert c.ev("au.paused") is True
    c.page.wait_for_selector("#adbox", state="hidden")
    playing_src(c, 7)                                   # le son visé pendant la pub est celui qui démarre
    assert c.ev("__viol") == 0 and c.ev("__d") < 0.001


def test_repeat_one_counts_towards_ad(live, mk):
    c = mk()
    c.with_ads([TEXT_AD])
    c.seed([dict(id="t1", title="Court", author="A", duration=1, url="", local=False, remote="/__m/short.wav", added=1)],
           player={"shuffle": False, "repeat": "one", "rate": "1"}, adn=3)
    c.open()
    c.ev("play(['t1'],0)")
    c.page.wait_for_selector("#adbox", state="visible", timeout=15000)
    c.page.wait_for_selector("#adbox", state="hidden")
    c.page.wait_for_function("!au.paused && !AD.on && au.src.includes('short.wav')")
    assert c.ev("S.adn") == 1


def test_sleep_timer_during_ad_stops_everything(live, mk):
    c = free_player(mk, [dict(TEXT_AD, seconds=2)])
    play_n(c, 5)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")
    c.ev("fadeOut()")
    c.page.wait_for_selector("#adbox", state="hidden")
    c.page.wait_for_timeout(800)
    assert c.ev("au.paused") is True and c.ev("AD.on") is False


def test_banners_on_four_pages_for_free_only(live, mk):
    zones = {"accueil": "#home-dyn", "bibliotheque": "#lib", "playlists": "#plsv", "decouvrir": "#discover-results"}
    free = free_player(mk, [TEXT_AD], n=14)
    for h, sel in zones.items():
        free.page.goto(live.base + "/#" + h)
        free.page.wait_for_selector(f"{sel} .adb")
    assert free.page.locator("#lib .adb").count() == 2        # en haut + après la 12e vignette

    prem = mk()
    prem.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    prem.with_ads([TEXT_AD])
    prem.seed(tracks(14))
    prem.open()
    prem.page.wait_for_function("isPrem()===true")
    for h, sel in zones.items():
        prem.page.goto(live.base + "/#" + h)
        prem.page.wait_for_timeout(500)
        assert prem.page.locator(".adb").count() == 0, h

    live.app.STRIPE_KEY = ""
    off = free_player(mk, [TEXT_AD], n=14)
    for h in zones:
        off.page.goto(live.base + "/#" + h)
        off.page.wait_for_timeout(400)
        assert off.page.locator(".adb").count() == 0, h


def test_audio_ads_never_appear_in_banners(live, mk):
    c = free_player(mk, [AUDIO_AD], n=14)
    for h in ("accueil", "bibliotheque", "playlists", "decouvrir"):
        c.page.goto(live.base + "/#" + h)
        c.page.wait_for_timeout(300)
        assert c.page.locator(".adb").count() == 0, h


def test_ad_link_only_http(live, mk):
    evil = dict(TEXT_AD, url="javascript:alert(1)", seconds=2)
    c = free_player(mk, [evil])
    c.page.goto(live.base + "/#accueil")
    c.page.wait_for_selector("#home-dyn .adb")
    assert c.page.locator(".adb a").count() == 0
    play_n(c, 5)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")
    assert c.page.locator("#adbox a").count() == 0
    assert not c.logs

    good = free_player(mk, [dict(TEXT_AD, seconds=2)])
    play_n(good, 5)
    start(good, 6)
    good.page.wait_for_selector("#adbox a")
    rel = good.page.get_attribute("#adbox a", "rel")
    assert "noopener" in rel and "sponsored" in rel and good.page.get_attribute("#adbox a", "href").startswith("https://")


def test_ad_premium_button_opens_sheet(live, mk):
    c = free_player(mk, [dict(TEXT_AD, seconds=3)])
    play_n(c, 5)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")
    c.page.get_by_role("button", name="Sans pub avec Premium").click()
    assert re.search(r"2,99\s*€", sheet_text(c)) and c.page.locator("#adbox").is_visible()


# ═══════════════════ Les sons « en ligne » (tous les nouveaux sons d'un gratuit) sont jouables partout ═══════════════════
def test_play_all_shuffle_and_surprise_work_with_online_sounds(live, mk):
    c = mk()
    c.seed(tracks(4))
    c.open()
    c.ev("void playAll(0)")
    c.page.wait_for_function("!au.paused && au.src.includes('silence.wav')")
    c.ev("au.pause(); au.removeAttribute('src')")
    c.ev("void playAll(1)")
    c.page.wait_for_function("!au.paused && au.src.includes('silence.wav')")
    c.ev("au.pause(); au.removeAttribute('src')")
    c.ev("void surprise()")
    c.page.wait_for_function("!au.paused && au.src.includes('silence.wav')")


def test_resume_last_online_sound_with_play_button(live, mk):
    c = mk()
    c.seed(tracks(3), lastId="t2")
    c.open()
    c.ev("togglePlay()")
    c.page.wait_for_function("!au.paused && au.src.includes('n=2')")


# ═══════════════════════════ Pubs rémunérées : bandeau Google AdSense ═══════════════════════════
G_CLIENT, G_SLOT = "ca-pub-1234567890123456", "1234567890"
G_SCRIPT = "script[src*='pagead2.googlesyndication.com/pagead/js/adsbygoogle.js']"


def adsense_env(monkeypatch):
    monkeypatch.setenv("ADSENSE_CLIENT", G_CLIENT)
    monkeypatch.setenv("ADSENSE_SLOT", G_SLOT)


def test_google_banner_for_free_user_requested_once(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.google()
    c.with_ads([TEXT_AD])
    c.seed(tracks(8))
    c.open()
    c.page.wait_for_selector("#adsense ins.adsbygoogle .fake-creative", state="visible")
    assert c.page.locator(G_SCRIPT).count() == 1
    assert f"client={G_CLIENT}" in c.page.get_attribute(G_SCRIPT, "src")
    ins = c.page.locator("#adsense ins.adsbygoogle")
    assert ins.count() == 1
    assert ins.get_attribute("data-ad-client") == G_CLIENT and ins.get_attribute("data-ad-slot") == G_SLOT
    assert c.page.locator("#adsense .ad-tag").inner_text().lower() == "publicité"       # libellé autorisé par Google
    assert c.page.locator(".adb").count() == 0                                          # AdSense remplace les bandeaux maison
    for h in ("bibliotheque", "playlists", "decouvrir", "accueil"):                     # naviguer ne redemande pas de pub
        c.page.goto(live.base + "/#" + h)
    c.ev("render(); render(); render();")
    c.ev("updatePremUI(); updatePremUI(); setAdsense({client: ADSENSE.client, slot: ADSENSE.slot});")
    c.ev("ping()")                                                                      # ce que fait l'appli au retour du réseau
    c.page.wait_for_timeout(300)
    assert c.ev("window.__gs.loads") == 1 and c.ev("window.__gs.pushes") == 1
    assert c.page.locator("#adsense ins.adsbygoogle").count() == 1
    assert c.logs == []


def test_google_never_inside_the_between_sounds_screen(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.google()
    c.with_ads([TEXT_AD])
    c.seed(tracks(8))
    c.open()
    c.page.wait_for_selector("#adsense ins.adsbygoogle .fake-creative", state="visible")
    play_n(c, 5)
    start(c, 6)
    c.page.wait_for_selector("#adbox", state="visible")
    assert "Pub texte" in c.page.inner_text("#adbox")                                   # la pause garde la pub maison
    assert c.page.locator("#adbox .adsbygoogle").count() == 0
    assert c.page.locator("#adbox #adsense").count() == 0
    c.page.wait_for_selector("#adbox", state="hidden")
    assert c.ev("window.__gs.pushes") == 1


def test_no_google_for_premium_not_even_the_script(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    c.google()
    c.seed(tracks(3))

    def slow_me(route):                  # /api/health répond avant /api/auth/me : on ne sait pas encore que l'utilisateur est Premium
        time.sleep(0.8)
        route.continue_()
    c.ctx.route(re.compile(r"/api/auth/me"), slow_me)
    c.open()
    c.page.wait_for_function("isPrem()===true")
    c.page.wait_for_timeout(600)
    assert c.google_hits == []
    assert c.page.locator(G_SCRIPT).count() == 0 and c.page.locator("#adsense ins").count() == 0
    assert not c.page.locator("#adsense").is_visible()


def test_no_google_when_paywall_off(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    live.app.STRIPE_KEY = ""
    c = mk()
    c.google()
    c.seed(tracks(3))
    c.open()
    c.page.wait_for_timeout(600)
    assert c.google_hits == [] and c.page.locator("#adsense ins").count() == 0


def test_no_google_without_configuration(live, mk):
    c = mk()
    c.google()
    c.seed(tracks(3))
    c.open()
    c.page.wait_for_timeout(600)
    assert c.google_hits == [] and c.page.locator("#adsense ins").count() == 0


def test_google_banner_disappears_when_user_becomes_premium(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    live.app.ADMIN_EMAILS = {"boss@example.com"}
    boss, friend = mk(), mk()
    boss.signup("boss@example.com", "boss")
    key = boss.ctx.request.post(live.base + "/api/admin/keys", data={"label": "Marie", "uses": 1}).json()["key"]
    friend.signup("f@example.com", "friend")
    friend.google()
    friend.open()
    friend.page.wait_for_selector("#adsense ins.adsbygoogle .fake-creative", state="visible")
    friend.page.click("#premb")
    friend.page.fill("#rk", key)
    friend.page.get_by_role("button", name="Activer").click()
    friend.page.wait_for_function("isPrem()===true")
    friend.page.wait_for_selector("#adsense ins", state="detached")
    assert not friend.page.locator("#adsense").is_visible()


def test_google_unfilled_leaves_no_empty_frame(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.google("unfilled")
    c.seed(tracks(3))
    c.open()
    c.page.wait_for_function("window.__gs && window.__gs.pushes===1")
    c.page.wait_for_timeout(300)
    assert not c.page.locator("#adsense").is_visible()


def test_google_blocked_keeps_the_app_working(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.google("blocked")
    c.seed(tracks(3))
    c.open()
    c.page.wait_for_function("document.querySelectorAll('#adsense ins').length===0")
    c.page.wait_for_timeout(300)
    assert not c.page.locator("#adsense").is_visible()
    start(c, 1)
    playing_src(c, 1)                                      # la musique démarre quand même
    assert c.logs == []


def test_google_retries_when_connection_comes_back(live, mk, monkeypatch):
    adsense_env(monkeypatch)
    c = mk()
    c.google("blocked")
    c.seed(tracks(3))
    c.open()
    c.page.wait_for_function("document.querySelectorAll('#adsense ins').length===0")
    assert c.ev("(window.adsbygoogle||[]).length") == 0           # aucune demande périmée ne reste en file d'attente
    c.google_mode = "filled"
    c.ev("dispatchEvent(new Event('online'))")
    c.page.wait_for_selector("#adsense ins.adsbygoogle .fake-creative", state="visible")
    assert c.ev("window.__gs.pushes") == 1


def test_footer_links_to_privacy_policy(live, mk):
    c = mk()
    c.seed(tracks(1))
    c.open()
    link = c.page.locator("footer a[href='/confidentialite']")
    assert link.count() == 1 and link.get_attribute("target") == "_blank" and "noopener" in link.get_attribute("rel")
    assert "confidentialité" in link.inner_text().lower()


# ═══════════════════ Task 8 : hors ligne = réservé à Premium ═══════════════════
def go_offline(c):
    c.ctx.set_offline(True)
    c.page.wait_for_function("navigator.onLine===false")


def go_online(c):
    c.ctx.set_offline(False)
    c.page.wait_for_function("navigator.onLine===true")


def local_tracks(n=2):
    """n sons « téléchargés sur l'appareil » (leur fichier est rangé dans la base locale par store_local_blob)."""
    return [dict(id=f"t{i}", title=f"Son {i}", author="Artiste", duration=60, url="", local=True, added=i)
            for i in range(1, n + 1)]


def store_local_blob(c, tid):
    c.page.evaluate("async id => { const b = await (await fetch('/__m/silence.wav')).blob(); await dbop('put', b, id); }", tid)


def toast_text(c):
    return c.page.inner_text("#toasts")


def wait_blocked_toast(c):
    c.page.wait_for_function("document.querySelector('#toasts').innerText.includes('réservée à Premium')")


def free_with_paywall(mk, tr):
    c = mk()
    c.seed(tr)
    c.open()
    c.page.wait_for_function("PAYWALL===true")
    return c


def test_free_offline_cannot_start_an_online_sound(live, mk):
    c = free_with_paywall(mk, tracks(3))
    go_offline(c)
    start(c, 1)
    wait_blocked_toast(c)
    assert c.ev("au.paused") is True and c.ev("au.getAttribute('src')") is None


def test_free_offline_cannot_start_a_downloaded_sound(live, mk):
    c = free_with_paywall(mk, local_tracks(2))
    store_local_blob(c, "t1")
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")          # en ligne, il se lit : le fichier est bon
    c.ev("au.pause(); au.removeAttribute('src'); au.load()")
    go_offline(c)
    c.ev("void play(['t1'],0)")
    wait_blocked_toast(c)
    assert c.ev("au.paused") is True and c.ev("au.getAttribute('src')") is None


def test_free_playing_sound_pauses_when_connection_drops_and_stays_paused(live, mk):
    c = free_with_paywall(mk, tracks(3))
    start(c, 1)
    playing_src(c, 1)
    go_offline(c)
    c.page.wait_for_function("au.paused===true")
    wait_blocked_toast(c)
    c.ev("togglePlay()")                                   # ▶ hors ligne : rien ne repart
    c.page.wait_for_timeout(300)
    assert c.ev("au.paused") is True
    go_online(c)
    c.ev("togglePlay()")                                   # de retour en ligne : ça repart
    c.page.wait_for_function("!au.paused")


def test_repeat_one_does_not_replay_when_blocked(live, mk):
    c = free_with_paywall(mk, tracks(2))
    c.ev("S.player.repeat='one'")
    start(c, 1)
    playing_src(c, 1)
    go_offline(c)
    c.page.wait_for_function("au.paused===true")
    c.ev("au.onended()")
    c.page.wait_for_timeout(300)
    assert c.ev("au.paused") is True


def test_premium_still_plays_offline(live, mk):
    c = mk()
    c.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    c.seed(local_tracks(2))
    c.open()
    c.page.wait_for_function("isPrem()===true")
    store_local_blob(c, "t1")
    go_offline(c)
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")
    assert "réservée à Premium" not in toast_text(c)


def test_premium_playing_sound_keeps_playing_when_connection_drops(live, mk):
    c = mk()
    c.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    c.seed(local_tracks(2))
    c.open()
    c.page.wait_for_function("isPrem()===true")
    store_local_blob(c, "t1")
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")
    go_offline(c)
    c.page.wait_for_timeout(500)
    assert c.ev("au.paused") is False and "réservée à Premium" not in toast_text(c)


def test_nothing_blocked_offline_when_paywall_is_off(live, mk):
    live.app.STRIPE_KEY = ""
    c = mk()
    c.seed(local_tracks(2))
    c.open()
    assert c.ev("PAYWALL") is False
    store_local_blob(c, "t1")
    go_offline(c)
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")


def test_offline_pill_explains_to_free_and_reassures_premium(live, mk):
    free = free_with_paywall(mk, tracks(1))
    go_offline(free)
    free.page.wait_for_selector("#offline", state="visible")
    assert "Premium" in free.page.inner_text("#offline")
    go_online(free)
    free.page.wait_for_selector("#offline", state="hidden")

    prem = mk()
    prem.signup("p@example.com", "payeur")
    make_premium(live, "p@example.com")
    prem.seed(tracks(1))
    prem.open()
    prem.page.wait_for_function("isPrem()===true")
    go_offline(prem)
    prem.page.wait_for_selector("#offline", state="visible")
    txt = prem.page.inner_text("#offline")
    assert "disponibles" in txt and "Premium" not in txt
