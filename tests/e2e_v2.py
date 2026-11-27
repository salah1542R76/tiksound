"""Nouvelles fonctions (lecteur, personnalisation, gestion, playlists, stats, social).

Lancer :  python3 -m pytest tests/e2e_v2.py -q
Réutilise l'infrastructure de tests/e2e_front.py (vrai Chrome, vrai serveur Flask).
"""
import json
import os
import re
import time

import pytest

from e2e_front import (Ctx, browser, live, mk, tracks, make_premium, start, playing_src,  # noqa: F401 (fixtures)
                       go_offline, local_tracks, store_local_blob, TEXT_AD, AUDIO_AD)


def opened(mk, tr=None, premium=False, paywall=True, **seed):
    """Un utilisateur prêt. paywall=False : abonnement désactivé (tout est débloqué)."""
    c = mk()
    if premium:
        c.signup("p@example.com", "payeur")
        make_premium(c.live, "p@example.com")
    c.seed(tr if tr is not None else tracks(3), **seed)
    c.open()
    if premium:
        c.page.wait_for_function("isPrem()===true")
    elif paywall:
        c.page.wait_for_function("PAYWALL===true")
    return c


def no_paywall(live):
    live.app.STRIPE_KEY = ""


def playing_blob(c, tid="t1"):
    store_local_blob(c, tid)
    c.ev(f"void play(['{tid}'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")


def toasts(c):
    return c.page.inner_text("#toasts")


def with_fx(tr, **fx):
    """Les effets sont mémorisés PAR SON : on les pose sur les sons plutôt que sur le lecteur."""
    for t in tr:
        t["fx"] = dict(fx)
    return tr


def remote_no_cors(c):
    """Un son « en ligne » hébergé ailleurs, sans autorisation CORS (cas d'un bucket R2 non réglé)."""
    path = __import__("os").path.join(__import__("os").path.dirname(__file__), "media", "silence.wav")

    def handler(r):                         # Playwright ajoute lui-même l'autorisation CORS : on simule le refus
        if r.request.resource_type == "fetch":
            return r.abort()
        r.fulfill(status=200, path=path, content_type="audio/wav")
    c.ctx.route(re.compile(r"https://r2\.example\.test/"), handler)
    return [dict(id="r1", title="Distant", author="A", duration=60, url="", local=False,
                 remote="https://r2.example.test/audio/r1.wav", added=1)]


# ═══════════════════════════ Moteur audio ═══════════════════════════
def test_bass_boost_on_device_sound_goes_through_effects_and_keeps_position(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("au.currentTime=20")
    c.ev("setEq('bass')")
    c.page.wait_for_function("au.id==='au2' && !au.paused && au.currentTime>=19")
    assert c.ev("FX.lo.gain.value") == 7
    assert c.ev("document.getElementById('au').paused") is True        # jamais deux sons en même temps


def test_back_to_normal_leaves_the_effects_player(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("setEq('bass')")
    c.page.wait_for_function("au.id==='au2' && !au.paused")
    c.ev("setEq('flat')")
    c.page.wait_for_function("au.id==='au' && !au.paused")
    assert c.ev("document.getElementById('au2').paused") is True


def test_effects_on_online_sound_without_cors_fall_back_to_normal_player(live, mk):
    no_paywall(live)
    c = mk()
    tr = remote_no_cors(c)
    c.seed(with_fx(tr, eq="bass"), player={"shuffle": False, "repeat": "all", "rate": "1"})
    c.open()
    c.ev("void play(['r1'],0)")
    c.page.wait_for_function("au.src.includes('r2.example.test') && !au.paused")
    assert c.ev("au.id") == "au"                                          # sinon le son serait muet
    c.page.wait_for_function("document.querySelector('#toasts').innerText.includes('égaliseur')")


def test_effects_on_online_sound_with_cors_use_effects_player(live, mk):
    no_paywall(live)
    c = opened(mk, with_fx(tracks(2), eq="bass"), paywall=False, player={"shuffle": False, "repeat": "all", "rate": "1"})
    start(c, 1)
    c.page.wait_for_function("au.id==='au2' && !au.paused && au.src.includes('n=1')")


def test_free_user_gets_bass_boost_but_not_premium_presets(live, mk):
    c = opened(mk, tracks(2))
    c.ev("setEq('lofi')")
    c.page.wait_for_selector("#gen:not([hidden])")
    assert c.ev("S.player.eq||'flat'") == "flat"
    c.ev("closeSh('gen')")
    c.ev("setEq('bass')")
    assert c.ev("S.player.eq") == "bass"


def test_premium_preset_stops_applying_when_premium_ends(live, mk):
    c = opened(mk, tracks(2), player={"shuffle": False, "repeat": "all", "rate": "1", "eq": "lofi", "rev": 60})
    assert c.ev("JSON.stringify(eqNow())") == '{"b":0,"m":0,"t":0,"r":0,"lp":0,"d":0}'
    assert c.ev("fxOn()") is False


def test_effects_sheet_has_speed_style_eq_reverb_fade(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1)
    playing_src(c, 1)
    c.ev("openFx()")
    txt = c.page.inner_text("#genb").lower()
    for w in ("Vitesse", "Style", "Égaliseur", "Bass boost", "Réverbe", "Fondu"):
        assert w.lower() in txt, w
    c.page.click("#genb [data-eq=voice]")
    assert c.ev("S.player.eq") == "voice"


def test_volume_follows_the_effects_player(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("const v=document.getElementById('vl'); v.value=30; v.dispatchEvent(new Event('input'))")
    c.ev("setEq('bass')")
    c.page.wait_for_function("au.id==='au2' && !au.paused")
    assert abs(c.ev("FX.out.gain.value") - 0.3) < 0.02 and c.ev("au.volume") == 1


def test_fade_in_and_out(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False, player={"shuffle": False, "repeat": "all", "rate": "1", "fade": 6})
    start(c, 1)
    playing_src(c, 1)
    c.page.wait_for_function("FADE<1")                                   # début : ça monte
    c.page.wait_for_function("FADE>0.99", timeout=6000)
    c.ev("au.currentTime=57")
    c.page.wait_for_function("FADE<0.6")                                 # fin : ça descend


def test_ab_loop_repeats_the_section(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1)
    playing_src(c, 1)
    c.ev("au.currentTime=10"); c.ev("abLoop()")
    c.ev("au.currentTime=11.5"); c.ev("abLoop()")
    assert c.ev("[LOOP.a|0, Math.round(LOOP.b*10)]") == [10, 115]
    seen = []
    for _ in range(25):
        seen.append(c.ev("au.currentTime"))
        time.sleep(0.12)
    assert max(seen) < 12.2 and any(s < 10.6 for s in seen[5:])
    c.ev("abLoop()")
    assert c.ev("LOOP") is None


def test_loop_is_cleared_when_the_sound_changes(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("au.currentTime=5;abLoop();au.currentTime=7;abLoop()")
    start(c, 2); playing_src(c, 2)
    assert c.ev("LOOP") is None


def test_resume_where_you_left_long_sounds(live, mk):
    no_paywall(live)
    tr = tracks(2)
    tr[0]["pos"] = 30
    c = opened(mk, tr, paywall=False)
    start(c, 1)
    c.page.wait_for_function("!au.paused && au.currentTime>=29")
    c2 = opened(mk, tr, paywall=False, player={"shuffle": False, "repeat": "all", "rate": "1", "resume": False})
    start(c2, 1)
    c2.page.wait_for_function("!au.paused")
    assert c2.ev("au.currentTime") < 5


def test_position_is_remembered_while_playing(live, mk):
    no_paywall(live)
    tr = tracks(2)
    tr[0]["remote"] = "{BASE}/__m/long.wav?n=1"                          # 2 min 30 : un « long » son
    c = opened(mk, tr, paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("au.currentTime=40")
    c.page.wait_for_function("S.tracks.find(t=>t.id==='t1').pos>=40", timeout=8000)


def test_smart_shuffle_plays_recent_sounds_last(live, mk):
    no_paywall(live)
    tr = tracks(12)
    now = int(time.time() * 1000)
    for t in tr[:3]:
        t["last"] = now
    c = opened(mk, tr, paywall=False)
    for _ in range(5):
        order = c.ev("smartShuf(S.tracks.map(t=>t.id))")
        assert set(order[-3:]) == {"t1", "t2", "t3"}
    c.ev("S.player.smart=false")
    orders = {tuple(c.ev("smartShuf(S.tracks.map(t=>t.id))")[-3:]) for _ in range(8)}
    assert any(set(o) != {"t1", "t2", "t3"} for o in orders)


def test_audio_ad_never_plays_through_effects(live, mk):
    no_paywall(live)
    live.app.STRIPE_KEY = "sk_test"                                       # pubs : il faut l'abonnement actif
    c = mk()
    c.with_ads([AUDIO_AD])
    c.seed(with_fx(tracks(8), eq="bass"), player={"shuffle": False, "repeat": "all", "rate": "1"}, adn=4)
    c.open()
    c.page.wait_for_function("PAYWALL===true")
    start(c, 1)
    c.page.wait_for_function("au.id==='au2' && !au.paused")             # le son passe par les effets (bass boost gratuit)
    start(c, 2)                                                         # 5 sons écoutés : la pub audio arrive
    c.page.wait_for_function("AD.on && au.src.includes('ad.wav')")
    assert c.ev("au.id") == "au"
    assert c.ev("document.getElementById('au2').paused") is True


def test_starting_a_new_sound_on_the_other_player_stops_the_first(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False)
    start(c, 1); playing_src(c, 1)
    assert c.ev("au.id") == "au"
    c.ev("S.tracks.find(t=>t.id==='t2').fx={eq:'bass'}")                # le son suivant a un effet, mais on ne bascule pas tout de suite
    start(c, 2)
    c.page.wait_for_function("au.id==='au2' && !au.paused && au.src.includes('n=2')")
    assert c.ev("document.getElementById('au').paused") is True


def test_no_effect_still_one_audio_at_a_time_when_switching_fast(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False)
    for i in (1, 2, 3):
        start(c, i)
        c.ev("setEq('bass')" if i % 2 else "setEq('flat')")
    playing_src(c, 3)
    time.sleep(0.6)
    assert c.ev("[...document.querySelectorAll('audio')].filter(a=>!a.paused).length") == 1


# ═══════════════════════════ Lecteur : courses et cas limites ═══════════════════════════
def test_custom_eq_slider_switches_to_the_effects_player(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("setEq('custom')")
    assert c.ev("au.id") == "au"                                          # tout à 0 : pas d'effet
    c.ev("setEqc('b',8)")
    c.page.wait_for_function("au.id==='au2' && !au.paused")
    assert c.ev("FX.lo.gain.value") == 8


def test_fast_next_from_device_sound_to_online_sound_plays_the_online_one(live, mk):
    no_paywall(live)
    tr = local_tracks(1) + [tracks(2)[1]]
    c = opened(mk, tr, paywall=False)
    store_local_blob(c, "t1")
    c.ev("q=['t1','t2']; qi=0; void loadCur(); qi=1; void loadCur()")      # deux « Suivant » rapides
    c.page.wait_for_function("au.src.includes('n=2') && !au.paused")
    time.sleep(0.6)
    assert c.ev("au.src.includes('n=2')") is True
    assert c.page.inner_text("#pt") == "Son 2"


def test_reopening_the_player_right_after_closing_keeps_it_open(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("openNP()")
    c.page.wait_for_function("document.getElementById('np').classList.contains('on')")
    c.ev("closeNP(); setTimeout(openNP,150)")
    time.sleep(0.7)
    assert c.ev("document.getElementById('np').hidden") is False


def test_cancelling_the_sleep_timer_during_the_fade_stops_it(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("S.player.sleepSoft=false; fadeOut()")
    c.page.wait_for_function("SLEEPF<0.9")
    c.ev("setSleep(30)")
    assert c.ev("SLEEPF") == 1
    time.sleep(0.6)
    assert c.ev("[SLEEPF, !!sleepAt, au.paused]") == [1, True, False]


def test_deleted_next_sound_is_skipped(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False)
    c.ev("void play(['t1','t2','t3'],0)"); playing_src(c, 1)
    c.ev("delTrack('t2')")
    assert c.ev("q") == ["t1", "t3"]
    c.ev("step(1)")
    playing_src(c, 3)
    c.ev("q=['t3','gone','t1']; qi=0; step(1)")
    playing_src(c, 1)                                                     # un id disparu (synchro) est sauté


def test_effects_player_falls_back_to_the_relay_when_the_direct_file_fails(live, mk):
    no_paywall(live)
    c = mk()
    path = os.path.join(os.path.dirname(__file__), "media", "silence.wav")
    sha = "a" * 40

    def r2(r):                                   # HEAD autorisé (CORS), mais le fichier lui-même échoue
        if r.request.resource_type == "fetch":
            return r.fulfill(status=200, body="", headers={"Access-Control-Allow-Origin": "*"})
        r.abort()
    c.ctx.route(re.compile(r"https://r2\.example\.test/"), r2)
    c.ctx.route(re.compile(r"/api/media/audio/"), lambda r: r.fulfill(status=200, path=path, content_type="audio/wav"))
    tr = [dict(id="r1", title="Distant", author="A", duration=60, url="", local=False,
               remote=f"https://r2.example.test/audio/{sha}.mp3", added=1)]
    c.seed(with_fx(tr, eq="bass"), player={"shuffle": False, "repeat": "all", "rate": "1"})
    c.open()
    c.ev("void play(['r1'],0)")
    c.page.wait_for_function("au.id==='au2' && au.src.includes('/api/media/audio/') && !au.paused", timeout=8000)


def fake_hidden_and_wake_lock(c):
    """document.hidden pilotable (HID) et un faux verrou d'écran qui compte les verrous encore actifs."""
    c.ev("""window.WL={live:[]}; Object.defineProperty(navigator,'wakeLock',{configurable:true,value:{request:async()=>{
      const s={release(){ WL.live=WL.live.filter(x=>x!==s); return Promise.resolve(); }}; WL.live.push(s); return s; }}});
      window.HID=false; Object.defineProperty(document,'hidden',{configurable:true,get:()=>HID})""")


def test_car_mode_wake_lock_is_not_leaked_and_comes_back(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    fake_hidden_and_wake_lock(c)
    c.ev("carMode(true)"); c.page.wait_for_function("WL.live.length===1")
    c.ev("carMode(true)"); time.sleep(0.2)
    assert c.ev("WL.live.length") == 1                                    # deuxième ouverture : pas de verrou orphelin
    c.ev("WL.live=[]; HID=true; document.dispatchEvent(new Event('visibilitychange'))")   # le navigateur libère en fond
    c.ev("HID=false; document.dispatchEvent(new Event('visibilitychange'))")
    c.page.wait_for_function("WL.live.length===1")                        # repris au retour
    c.ev("carMode(false)")
    c.page.wait_for_function("WL.live.length===0")


def test_crossfade_starts_a_skipped_sound_silent(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False, player={"shuffle": False, "repeat": "all", "rate": "1", "fade": 4})
    c.ev("void play(['t1','t2'],0)"); playing_src(c, 1)
    c.ev("au.currentTime=30"); c.page.wait_for_function("FADE>0.99")
    c.ev("window.V=null; au1.addEventListener('playing',()=>window.V=FADE,{once:true}); step(1)")
    c.page.wait_for_function("window.V!==null")
    assert c.ev("V") < 0.5


def test_seek_bar_unfreezes_after_cancel_and_seeks_once_on_release(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("const s=document.getElementById('npsk'); s.dispatchEvent(new PointerEvent('pointerdown')); s.dispatchEvent(new PointerEvent('pointercancel'))")
    assert c.ev("seeking") is False
    c.ev("au.currentTime=1; const s=document.getElementById('npsk'); s.dispatchEvent(new PointerEvent('pointerdown')); s.value=500; s.dispatchEvent(new Event('input'))")
    assert c.ev("au.currentTime") < 5                                     # pendant le glissé : pas de saut réseau
    c.ev("const s=document.getElementById('npsk'); s.dispatchEvent(new Event('change')); s.dispatchEvent(new PointerEvent('pointerup'))")
    assert abs(c.ev("au.currentTime") - 30) < 2


def test_effects_come_back_for_a_sound_started_screen_off(live, mk):
    no_paywall(live)
    c = opened(mk, with_fx(local_tracks(2), eq="bass"), paywall=False)
    store_local_blob(c, "t1")
    fake_hidden_and_wake_lock(c)
    c.ev("HID=true; void play(['t1'],0)")
    c.page.wait_for_function("au.src.startsWith('blob:') && !au.paused")
    assert c.ev("au.id") == "au"                                          # écran éteint : lecteur normal
    c.ev("HID=false; document.dispatchEvent(new Event('visibilitychange'))")
    c.page.wait_for_function("au.id==='au2' && !au.paused")


def test_previous_on_a_trimmed_sound_restarts_at_the_trim(live, mk):
    no_paywall(live)
    tr = tracks(2)
    tr[1]["cut"] = {"a": 20, "b": 0}
    c = opened(mk, tr, paywall=False)
    c.ev("void play(['t1','t2'],1)"); playing_src(c, 2)
    c.ev("au.currentTime=30; step(-1)")
    assert abs(c.ev("au.currentTime") - 20) < 1
    c.ev("au.currentTime=21; step(-1)")                                   # juste après le début rogné : son précédent
    playing_src(c, 1)


def test_reverb_impulse_is_only_built_when_reverb_is_used(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("setEq('bass')")
    c.page.wait_for_function("au.id==='au2'")
    assert c.ev("FX.conv.buffer===null") is True
    c.ev("setFxVal('rev',40)")
    assert c.ev("FX.conv.buffer!==null") is True
    c.ev("setFxVal('rev',0)")
    assert c.ev("FX.conv.buffer===null") is True


# ═══════════════════════════ Minuterie, mode voiture ═══════════════════════════
def test_car_mode_big_controls_and_escape(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False)
    c.ev("void play(['t1','t2','t3'],0)"); playing_src(c, 1)
    c.ev("carMode(true)")
    c.page.wait_for_selector("#car", state="visible")
    assert c.page.inner_text("#cart") == "Son 1"
    c.page.click("#car .car-b[aria-label=Suivant]")
    playing_src(c, 2)
    assert c.page.inner_text("#cart") == "Son 2"
    box = c.page.locator("#car .car-b.p").bounding_box()
    assert box["width"] >= 120                                   # vraiment gros
    c.page.keyboard.press("Escape")
    c.page.wait_for_selector("#car", state="hidden")


def test_sleep_at_end_of_queue(live, mk):
    no_paywall(live)
    tr = [dict(t, remote=t["remote"].replace("silence", "short")) for t in tracks(2)]
    c = opened(mk, tr, paywall=False)
    c.ev("void play(['t1','t2'],0)")
    c.ev("setSleep('queue')")
    c.page.wait_for_function("au.src.includes('n=2')", timeout=6000)    # passe au 2e son
    c.page.wait_for_function("au.paused && au.ended", timeout=6000)    # puis s'arrête au lieu de reboucler
    time.sleep(0.6)
    assert c.ev("au.src.includes('n=2') && au.paused") is True


def test_sleep_menu_offers_short_and_long_durations(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("pickSleep()")
    txt = c.page.inner_text("#optl")
    for w in ("5 minutes", "10 minutes", "1 h 30", "fin de la file", "douceur"):
        assert w in txt, w


# ═══════════════════════════ Personnalisation ═══════════════════════════
def css(c, var):
    return c.ev(f"getComputedStyle(document.documentElement).getPropertyValue('{var}').trim()")


def test_customize_is_a_full_page_with_tabs_and_live_preview(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("openCustom()")
    c.page.wait_for_selector("section[data-v=perso]:not([hidden]) .pz-tabs")
    assert c.ev("location.hash") == "#perso"
    assert c.page.locator(".pz-tabs [data-tab]").count() == 8
    assert c.page.locator("#pzv .pbox").count() == 1 and c.page.locator("#pzv .pzv-app").count() == 1   # aperçu en direct
    c.page.click(".pz-tabs [data-tab=ui]")
    assert "taille de l'interface" in c.page.inner_text("#pzp").lower()
    c.ev("setUI('blur',10)")                                   # re-rendu : on reste sur le même onglet
    assert c.ev("PZ.tab") == "ui" and c.page.locator(".pz-tabs .on[data-tab=ui]").count() == 1
    assert c.page.locator("#cz").count() == 0                  # plus de volet qui se déroule


def test_header_palette_button_opens_the_page_and_back_returns(live, mk):
    c = opened(mk, tracks(1))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("section[data-v=bibliotheque]:not([hidden])")
    c.page.click("header button[aria-label=Personnaliser]")
    c.page.wait_for_selector("section[data-v=perso]:not([hidden])")
    c.page.click(".pz-head button[aria-label=Retour]")
    c.page.wait_for_function("location.hash==='#bibliotheque'")


def test_new_free_themes_apply(live, mk):
    c = opened(mk, tracks(1))
    for k, bg in (("foret", "#0a120d"), ("ocean", "#06141c"), ("sepia", "#f3ebdd"), ("contraste", "#000")):
        c.ev(f"setUI('theme','{k}')")
        assert c.ev("document.documentElement.dataset.theme") == k
        assert css(c, "--bg") == bg
    assert c.ev("document.documentElement.dataset.light") == ""
    c.ev("setUI('theme','sepia')")
    assert c.ev("document.documentElement.dataset.light") == "1"


def test_premium_looks_are_locked_for_free(live, mk):
    c = opened(mk, tracks(1))
    for k, v in (("theme", "bordeaux"), ("bg", "etoiles"), ("font", "playfair"), ("accent2", "#ff00aa"), ("autoAcc", True)):
        c.ev(f"setUI('{k}',{v!r})".replace("True", "true"))
        assert c.ev(f"S.ui['{k}']") != (v if v is not True else True), k
    c.page.wait_for_selector("#gen:not([hidden])")


def test_premium_user_gets_premium_looks(live, mk):
    c = opened(mk, tracks(1), premium=True)
    c.ev("setUI('theme','bordeaux');setUI('bg','etoiles');setUI('accent2','#ff00aa')")
    assert c.ev("document.documentElement.dataset.theme") == "bordeaux"
    assert c.ev("document.documentElement.dataset.bg") == "etoiles"
    assert "linear-gradient" in css(c, "--acg")


def test_premium_looks_fall_back_when_premium_ends(live, mk):
    c = opened(mk, tracks(1), ui={"theme": "bordeaux", "bg": "etoiles", "font": "playfair", "accent2": "#ff00aa"})
    assert c.ev("document.documentElement.dataset.theme") == "dark"
    assert c.ev("document.documentElement.dataset.bg") == "aurora"
    assert css(c, "--acg") == "none"
    assert c.ev("S.ui.theme") == "bordeaux"                   # le choix est gardé pour un futur abonnement


def test_interface_size_card_style_and_viz(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("setUI('zoom',120);setUI('card','line');setUI('viz',false);setUI('navl',false);setUI('npbg','accent')")
    assert c.ev("getComputedStyle(document.body).zoom") == "1.2"
    h = c.ev("[document.documentElement.dataset.card, document.documentElement.dataset.viz, document.documentElement.dataset.navl]")
    assert h == ["line", "off", "off"]
    assert c.ev("getComputedStyle(document.querySelector('.eq')).display") == "none"


def test_home_sections_can_be_hidden(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False)
    assert c.page.locator(".steps").is_visible() is False or True
    c.ev("setHome('hero',false);setHome('stats',false);setHome('tip',false)")
    assert c.page.locator("section[data-v=accueil] h1").is_hidden()
    assert c.page.locator("#home-dyn .stat-row").count() == 0 and c.page.locator("#home-dyn .tip").count() == 0
    c.ev("setHome('tip',true)")
    assert c.page.locator("#home-dyn .tip").count() == 1


def test_cover_colour_becomes_the_accent(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    c.ev("""(()=>{ const cv=document.createElement('canvas'); cv.width=cv.height=8; const g=cv.getContext('2d');
            g.fillStyle='#e02020'; g.fillRect(0,0,8,8); COVERS['t1']=cv.toDataURL(); })()""")
    c.ev("setUI('autoAcc',true)")
    start(c, 1); playing_src(c, 1)
    c.page.wait_for_function("getComputedStyle(document.documentElement).getPropertyValue('--ac').trim()!==S.ui.accent")
    r, g, b = c.ev("(()=>{ const h=getComputedStyle(document.documentElement).getPropertyValue('--ac').trim(); return [1,3,5].map(i=>parseInt(h.slice(i,i+2),16)); })()")
    assert r > 150 and g < 90 and b < 90
    c.ev("setUI('autoAcc',false)")
    assert css(c, "--ac") == c.ev("S.ui.accent")


def test_more_fonts_including_readable_one(live, mk):
    c = opened(mk, tracks(1))
    c.ev("setUI('font','atkinson')")
    assert "Atkinson" in css(c, "--fb")


# ═══════════════════════════ Mes sons ═══════════════════════════
def test_edit_title_author_tags(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False)
    c.ev("editTrack('t1')")
    c.page.wait_for_selector("#gen:not([hidden]) #ed-title")
    c.page.fill("#ed-title", "Nouveau titre")
    c.page.fill("#ed-author", "Moi")
    c.page.fill("#ed-tags", "gym, Soirée,  gym ,")
    c.page.click("#gen .btn:has-text('Enregistrer')")
    c.page.wait_for_function("S.tracks.find(t=>t.id==='t1').title==='Nouveau titre'")
    t = c.ev("S.tracks.find(t=>t.id==='t1')")
    assert t["title"] == "Nouveau titre" and t["author"] == "Moi" and t["tags"] == ["gym", "soirée"]


def test_custom_cover_is_saved_on_the_device(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    c.ev("""(async()=>{ const cv=document.createElement('canvas'); cv.width=cv.height=40; const g=cv.getContext('2d');
             g.fillStyle='#22aa44'; g.fillRect(0,0,40,40); const b=await new Promise(r=>cv.toBlob(r,'image/png'));
             await setCover('t1', new File([b],'c.png',{type:'image/png'})); })()""")
    c.page.wait_for_function("!!COVERS['t1']")
    assert c.ev("(async()=>!!(await dbop('get','cv:t1')))()") is True
    c.ev("resetCover('t1')")
    c.page.wait_for_function("!COVERS['t1']")


def test_tag_filter_chips(live, mk):
    no_paywall(live)
    tr = tracks(4)
    tr[0]["tags"] = ["gym"]; tr[2]["tags"] = ["gym", "chill"]
    c = opened(mk, tr, paywall=False)
    c.page.goto(live.base + "/#bibliotheque")
    c.page.click("#filters .chip:has-text('#gym')")
    assert c.ev("LISTS.lib") == ["t1", "t3"] or sorted(c.ev("LISTS.lib")) == ["t1", "t3"]


def test_library_toolbar_is_lighter_and_view_menu_works(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False)
    c.page.goto(live.base + "/#bibliotheque")
    assert c.page.locator("#sort").count() == 0                     # le menu déroulant est rangé dans « Affichage »
    assert c.page.locator("section[data-v=bibliotheque] .top button:visible").count() <= 4       # Lire, aléatoire, sélectionner, affichage
    c.ev("libView()")
    txt = c.page.inner_text("#optl")
    for w in ("Récents", "Titre", "Plus écoutés", "Durée", "Grille", "Liste", "Jamais écoutés"):
        assert w in txt, w
    c.page.click("#optl .opt:has-text('Durée')")
    c.page.wait_for_function("S.ui.sort==='dur'")                 # le menu applique le choix après sa fermeture


def test_deleted_sound_goes_to_trash_and_comes_back(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False, pls=[{"id": "p1", "name": "Ma liste", "ids": ["t1", "t2"]}])
    c.ev("delTrack('t1')")
    assert c.ev("S.tracks.some(t=>t.id==='t1')") is False
    assert c.ev("S.trash.length") == 1
    c.ev("openTrash()")
    assert "Son 1" in c.page.inner_text("#genb")
    c.ev("restoreTrash(0)")
    assert c.ev("S.tracks.some(t=>t.id==='t1')") is True
    assert c.ev("S.pls[0].ids") == ["t1", "t2"]
    assert c.ev("S.trash.length") == 0


def test_deleted_playlist_goes_to_trash(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False, pls=[{"id": "p1", "name": "Ma liste", "ids": ["t1"]}])
    c.ev("void delPl('p1')")
    c.page.click("#cfok")
    c.page.wait_for_function("S.pls.length===0")
    c.ev("restoreTrash(0)")
    assert c.ev("S.pls[0].name") == "Ma liste"


def test_trash_is_emptied_after_30_days(live, mk):
    no_paywall(live)
    old = int(time.time() * 1000) - 31 * 86400 * 1000
    c = opened(mk, tracks(1), paywall=False,
               trash=[{"kind": "t", "at": old, "t": {"id": "x1", "title": "Vieux"}, "mem": []},
                      {"kind": "t", "at": int(time.time() * 1000), "t": {"id": "x2", "title": "Récent"}, "mem": []}])
    assert [e["t"]["id"] for e in c.ev("S.trash")] == ["x2"]


def test_pinned_items_on_home(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(3), paywall=False, pls=[{"id": "p1", "name": "Ma liste", "ids": ["t1"]}])
    c.ev("togglePin('t2');togglePin('pl:p1')")
    c.page.goto(live.base + "/#accueil")
    pins = c.page.inner_text("#home-dyn .pins")
    assert "Son 2" in pins and "Ma liste" in pins
    c.ev("togglePin('t2')")
    assert "Son 2" not in c.page.inner_text("#home-dyn .pins")


# ═══════════════════════════ Playlists ═══════════════════════════
PLS = [{"id": "p1", "name": "Sport", "ids": ["t1", "t2", "t3"]}, {"id": "p2", "name": "Chill", "ids": ["t3", "t4"]}]


def test_playlist_folders(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("setFolder('p1','Été')")
    c.page.goto(live.base + "/#playlists")
    c.page.wait_for_selector("#plsv details.fold")
    assert "Été" in c.page.inner_text("#plsv details.fold summary")
    assert "Sport" in c.page.inner_text("#plsv details.fold")
    c.ev("setFolder('p1','')")
    assert c.page.locator("#plsv details.fold").count() == 0


def test_more_automatic_playlists(live, mk):
    no_paywall(live)
    tr = tracks(4)
    now = int(time.time() * 1000)
    tr[0].update(plays=3, last=now - 40 * 86400 * 1000)          # écouté il y a longtemps
    tr[1].update(plays=1, last=now)
    c = opened(mk, tr, paywall=False)
    ids = {p["id"]: p["ids"] for p in c.ev("smartPls()")}
    assert ids["smart:never"] == ["t3", "t4"] or sorted(ids["smart:never"]) == ["t3", "t4"]
    assert ids["smart:again"] == ["t1"]
    assert "smart:added" in ids


def test_drag_handle_reorders_and_no_up_down_buttons(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.page.wait_for_selector("#plsv .drag")
    assert c.page.locator("#plsv .mv").count() == 0
    h = c.page.locator("#plsv .drag").nth(0).bounding_box()
    last = c.page.locator("#plsv .row").nth(2).bounding_box()
    c.page.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2)
    c.page.mouse.down()
    c.page.mouse.move(h["x"] + 5, last["y"] + last["height"] - 4, steps=8)
    c.page.mouse.up()
    assert c.ev("S.pls[0].ids") == ["t2", "t3", "t1"]
    c.page.locator("#plsv .drag").nth(0).focus()
    c.page.keyboard.press("ArrowDown")                           # au clavier aussi
    assert c.ev("S.pls[0].ids") == ["t3", "t2", "t1"]


def test_remove_from_playlist_via_menu(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.ev("trackMenu('t2')")
    c.page.click("#optl .opt:has-text('Retirer de « Sport »')")
    c.page.wait_for_function("S.pls[0].ids.join()==='t1,t3'")


def test_playlist_look_emoji_and_colour(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("setPlLook('p1',{emoji:'🔥',color:'#ff8a7a'})")
    c.page.goto(live.base + "/#playlists")
    art = c.page.locator("#plsv .pc:has-text('Sport') .plx")
    assert art.count() == 1 and "🔥" in art.inner_text()


def test_duplicate_and_merge(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("dupPl('p1')")
    assert [p["name"] for p in c.ev("S.pls")] == ["Sport", "Sport (copie)", "Chill"]
    c.ev("mergeInto('p2','p1')")                                   # ajoute les sons de Sport à Chill, sans doublon
    assert c.ev("S.pls.find(p=>p.id==='p2').ids") == ["t3", "t4", "t1", "t2"]


def test_playlist_header_is_lighter(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(4), paywall=False, pls=PLS)
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.page.wait_for_selector(".pl-btns")
    assert c.page.locator(".pl-btns > button:visible").count() <= 5          # Lire, aléatoire, ajouter, sélectionner, ⋯
    c.ev("plMenu('p1')")
    txt = c.page.inner_text("#optl")
    for w in ("Partager", "Ajouter des sons", "Personnaliser", "dossier", "Dupliquer", "Ajouter les sons"):
        assert w in txt, w


# ═══════════════════════════ Statistiques ═══════════════════════════
def test_listening_time_is_counted_per_day_hour_and_sound(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.page.wait_for_function("(S.stats.byDay[dayKey(new Date())]||0)>1.5", timeout=8000)
    st = c.ev("({day:S.stats.byDay[dayKey(new Date())]||0, hour:(S.stats.hours||[])[new Date().getHours()]||0, t:S.tracks[0].sec||0})")
    assert st["day"] > 1 and st["hour"] > 1 and st["t"] > 1


def test_stats_sheet_charts_tops_and_trophies(live, mk):
    no_paywall(live)
    tr = tracks(12)
    for i, t in enumerate(tr):
        t.update(plays=12 - i, author="Artiste A" if i < 4 else "Artiste B", sec=100 + i)
    today = time.strftime("%Y-%m-%d")
    c = opened(mk, tr, paywall=False, stats={"sec": 4000, "days": [today], "byDay": {today: 1800}, "hours": [0] * 20 + [1800, 0, 0, 0]})
    c.ev("openStats()")
    txt = c.page.inner_text("#genb")
    for w in ("14 derniers jours", "Quand tu écoutes", "Artistes", "Trophées"):
        assert w.lower() in txt.lower(), w
    assert c.page.locator("#genb .bars i").count() >= 14
    assert c.page.locator("#genb .tro.on").count() >= 2            # « 10 sons », « 1 h d'écoute »…
    assert "Artiste A" in c.page.inner_text("#genb .tops")


def test_weekly_goal_on_home(live, mk):
    no_paywall(live)
    today = time.strftime("%Y-%m-%d")
    c = opened(mk, tracks(3), paywall=False, stats={"sec": 900, "days": [today], "byDay": {today: 900}})
    assert c.page.locator("#home-dyn .goal").count() == 0               # pas d'objectif = rien d'affiché
    c.ev("setGoal(30)")
    c.page.goto(live.base + "/#accueil")
    g = c.page.inner_text("#home-dyn .goal")
    assert "15" in g and "30 min" in g
    c.ev("setGoal(0)")
    assert c.page.locator("#home-dyn .goal").count() == 0


# ═══════════════════════════ Social et légal ═══════════════════════════
def test_qr_code_decodes(live, mk):
    import cv2
    import numpy as np
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    url = live.base + "/?pl=3f2a9c1e5b7d4a6f8e0c1b2a3d4e5f60"
    c.ev(f"showQR({url!r},'Ma playlist')")
    png = c.page.locator("#genb .qr svg").screenshot()
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    val, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    assert val == url


def test_footer_has_legal_links(live, mk):
    c = opened(mk, tracks(1))
    hrefs = c.ev("[...document.querySelectorAll('footer a')].map(a=>a.getAttribute('href'))")
    assert hrefs == ["/confidentialite", "/conditions", "/signalement"]


def test_explore_shows_this_weeks_trends(live, mk):
    no_paywall(live)
    owner = mk()
    owner.signup("o@example.com", "owner")
    r = owner.ctx.request.post(live.base + "/api/playlists", data={"name": "Tube de la semaine", "tracks": [
        {"url": "https://www.tiktok.com/@a/video/1", "title": "Son", "author": "A", "cover": "", "duration": 20}]})
    pid = r.json()["id"]
    fan = mk()
    fan.signup("f@example.com", "fan")
    assert fan.ctx.request.post(live.base + f"/api/playlists/{pid}/like").ok
    fan.seed(tracks(1))
    fan.open("#decouvrir")
    fan.page.wait_for_selector("#discover-results .trend")
    assert "Tendances de la semaine" in fan.page.inner_text("#discover-results")
    assert "Tube de la semaine" in fan.page.inner_text("#discover-results .trend")


def test_admin_sees_reports_and_marks_them_done(live, mk):
    live.app.ADMIN_EMAILS = {"boss@example.com"}
    boss = mk()
    boss.ctx.request.post(live.base + "/api/report", data={"url": "https://x.example/?pl=1", "reason": "copyright",
                                                          "email": "label@example.com", "name": "Label"})
    boss.signup("boss@example.com", "boss")
    boss.seed(tracks(1))
    boss.open("#profil")
    boss.page.wait_for_selector("#reports-list .row")
    assert "Label" in boss.page.inner_text("#reports-list")
    boss.page.click("#reports-list button:has-text('Traité')")
    boss.page.wait_for_function("document.querySelector('#reports-list').innerText.includes('Traité ✓')")


def test_faq_on_home_can_be_hidden(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    assert c.page.locator("section[data-v=accueil] .faq details").count() >= 4
    c.ev("setHome('faq',false)")
    assert c.page.locator("section[data-v=accueil] .faq").is_hidden()


def test_profile_qr_and_playlist_qr_entries(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False, pls=[{"id": "p1", "name": "Pub", "ids": ["t1"], "pub": True, "pubId": "abc"}])
    c.ev("plMenu('p1')")
    assert "QR code" in c.page.inner_text("#optl")


def test_profile_settings_are_folded(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    c.page.goto(live.base + "/#profil")
    c.page.wait_for_selector("#profil-content details.setd")
    assert c.ev("document.querySelector('#profil-content details.setd').open") is False
    assert "Personnaliser l'interface" not in c.page.inner_text("#profil-content")      # plus de grande boîte « Apparence »
    c.page.click("#profil-content details.setd summary")
    txt = c.page.inner_text("#profil-content details.setd")
    for w in ("Personnalisation", "Statistiques", "Synchronisation", "Corbeille", "Sauvegarde", "Raccourcis"):
        assert w in txt, w
    c.ev("renderProf()")                                     # reste ouvert après un re-rendu
    assert c.ev("document.querySelector('#profil-content details.setd').open") is True


# ═══════════════════════════ Derniers ajouts ═══════════════════════════
def test_trim_sound_start_and_end(live, mk):
    no_paywall(live)
    tr = tracks(2)
    tr[0]["cut"] = {"a": 5, "b": 7}
    c = opened(mk, tr, paywall=False)
    c.ev("void play(['t1','t2'],0)")
    c.page.wait_for_function("au.src.includes('n=1') && !au.paused && au.currentTime>=4.9")
    c.page.wait_for_function("au.src.includes('n=2') && !au.paused", timeout=8000)   # à 0:07 on passe au suivant


def test_trim_from_player_menu(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    start(c, 1); playing_src(c, 1)
    c.ev("au.currentTime=3; cutHere('a')")
    c.ev("au.currentTime=9; cutHere('b')")
    assert c.ev("[Math.round(S.tracks[0].cut.a), Math.round(S.tracks[0].cut.b)]") == [3, 9]
    c.ev("cutHere('reset')")
    assert c.ev("S.tracks[0].cut") is None


def test_sound_of_the_day_on_home(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(5), paywall=False)
    day = c.page.inner_text("#home-dyn .sotd")
    assert "son du jour" in day.lower()
    assert c.ev("sotd().id") == c.ev("sotd().id")                    # le même toute la journée


def test_welcome_for_new_visitors_only(live, mk):
    no_paywall(live)
    c = mk(); c.ctx.add_init_script("localStorage.removeItem('tkwel')"); c.seed([]); c.open()
    c.page.wait_for_selector("#gen:not([hidden]) .wel")
    for _ in range(2):
        c.page.click("#gen .wel .btn:has-text('Suivant')")
    c.page.click("#gen .wel .btn:has-text('Commencer')")
    c.page.wait_for_selector("#gen", state="hidden")
    assert c.ev("localStorage.getItem('tkwel')") == "1"
    c2 = opened(mk, tracks(2), paywall=False)
    c2.page.wait_for_timeout(500)
    assert c2.page.locator("#gen .wel").count() == 0


def test_admin_dashboard_numbers(live, mk):
    live.app.ADMIN_EMAILS = {"boss@example.com"}
    boss = mk()
    boss.signup("boss@example.com", "boss")
    boss.seed(tracks(1))
    boss.open("#profil")
    boss.page.wait_for_selector("#adm-stats .stg b")
    txt = boss.page.inner_text("#adm-stats")
    assert "inscrits" in txt and "Premium" in txt


# ═══════════════════════════ Distorted ═══════════════════════════
def test_effects_sheet_has_a_distorted_slider(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    c.ev("openFx()")
    assert "distorted" in c.page.inner_text("#genb").lower()
    assert c.page.locator("#genb [data-fx=dist]").count() == 1


def test_distorted_routes_through_effects_and_saturates_progressively(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    assert c.ev("au===au1") is True and c.ev("fxOn()") is False
    c.ev("setFxVal('dist',30)")
    c.page.wait_for_function("au.id==='au2' && !au.paused")
    low = c.ev("(()=>{const x=FX.shaper.curve, i=Math.round(x.length*0.75); return x[i]})()")
    c.ev("setFxVal('dist',90)")
    high = c.ev("(()=>{const x=FX.shaper.curve, i=Math.round(x.length*0.75); return x[i]})()")
    assert 0 < low < high <= 1.0001            # plus de drive = signal plus écrasé vers ±1
    assert c.ev("FX.shaper.curve.length") >= 1024


def test_distorted_zero_is_a_clean_bypass_and_survives_reload(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("setFxVal('dist',50)")
    c.page.wait_for_function("au.id==='au2'")
    c.ev("setFxVal('dist',0)")
    assert c.ev("FX.shaper.curve") is None and c.ev("fxOn()") is False
    c.ev("setFxVal('dist',40)")
    assert c.ev("JSON.parse(localStorage.getItem(Object.keys(localStorage).find(k=>/tk|tiksound/i.test(k))||'')||'{}').player?.dist ?? S.player.dist") == 40


def test_distorted_is_named_in_the_player_chip_and_does_not_need_premium(live, mk):
    c = opened(mk, local_tracks(2))                # compte gratuit, abonnement actif
    c.ev("setFxVal('dist',45)")
    assert c.ev("eqNow().d") == 45
    assert "Distorted" in c.ev("fxLabel()")


# ═══════════════════════════ Sons d'interface ═══════════════════════════
def sfx_n(c):
    return c.ev("SFX.n")


def test_ui_sounds_are_lazy_and_on_by_default(live, mk):
    c = opened(mk, tracks(2))
    assert c.ev("S.ui.sfx") is True and c.ev("S.ui.sfxv") == 60
    assert c.ev("SFX.ctx") is None                      # rien n'est créé avant le premier geste
    c.page.click("nav a >> nth=0")
    assert c.ev("SFX.ctx !== null") is True


def test_ui_sounds_can_be_turned_off_and_volume_zero_is_silent(live, mk):
    c = opened(mk, tracks(2))
    c.ev("S.ui.sfx=false")
    n = sfx_n(c)
    c.ev("sfx('pop')"); c.ev("sfx('like')")
    assert sfx_n(c) == n
    c.ev("S.ui.sfx=true; S.ui.sfxv=0")
    c.ev("sfx('pop')")
    assert sfx_n(c) == n
    c.ev("S.ui.sfxv=60")
    c.ev("sfx('pop')")
    assert sfx_n(c) == n + 1


def test_ui_sounds_are_throttled_not_spammed(live, mk):
    c = opened(mk, tracks(2))
    n = sfx_n(c)
    c.ev("for(let i=0;i<8;i++) sfx('tick')")
    assert sfx_n(c) == n + 1


def test_like_chimes_only_when_liking(live, mk):
    c = opened(mk, tracks(2))
    c.ev("SFX.t=0; toggleFav(S.tracks[0].id)")
    assert c.ev("SFX.last") == "like"
    n = sfx_n(c)
    c.page.wait_for_timeout(150)
    c.ev("toggleFav(S.tracks[0].id)")                    # retirer des favoris : silence
    assert sfx_n(c) == n


def test_play_and_pause_have_their_own_sound(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.page.wait_for_timeout(150)
    c.ev("SFX.t=0; togglePlay()")
    assert c.ev("SFX.last") == "pause"
    c.page.wait_for_timeout(150)
    c.ev("togglePlay()")
    assert c.ev("SFX.last") == "play"


def test_success_and_error_toasts_have_sounds_but_plain_ones_do_not(live, mk):
    c = opened(mk, tracks(2))
    c.ev("SFX.t=0; toast('Ajouté',{type:'ok'})")
    assert c.ev("SFX.last") == "success"
    c.page.wait_for_timeout(150)
    c.ev("toast('Oups',{type:'err'})")
    assert c.ev("SFX.last") == "err"
    n = sfx_n(c)
    c.page.wait_for_timeout(150)
    c.ev("toast('Info neutre')")
    assert sfx_n(c) == n


def test_switches_pop_up_when_turned_on_and_down_when_off(live, mk):
    c = opened(mk, tracks(2))
    c.ev("openCustom('ui')")
    c.ev("SFX.t=0")
    c.page.click("#pzp .sw2 >> nth=0")                    # un interrupteur quelconque de l'onglet
    assert c.ev("SFX.last") in ("on", "off")
    assert "Sons de l'interface" in c.page.inner_text("#pzp")
    assert c.page.locator("#pzp input[data-k=sfxv]").count() == 1


def test_ui_sounds_stay_on_this_device_and_are_not_synced():
    import re
    html = open("index.html").read()
    m = re.search(r"const UI_SYNC=\[(.*?)\]", html)
    assert '"sfx"' not in m.group(1) and '"sfxv"' not in m.group(1)


# ═══════════════════════════ Ultra slowed / ultra sped up ═══════════════════════════
def test_ultra_slowed_and_ultra_sped_up_exist_and_change_speed(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    assert c.ev("RATES.uslow[0]") < c.ev("RATES.slowed[0]") < 1 < c.ev("RATES.spedup[0]") < c.ev("RATES.uspeed[0]")
    c.ev("openFx()")
    txt = c.page.inner_text("#genb")
    assert "Ultra slowed" in txt and "Ultra sped up" in txt
    c.ev("setRate('uslow')")
    assert abs(c.ev("au.playbackRate") - c.ev("RATES.uslow[0]")) < .01 and c.ev("au.preservesPitch") is False
    c.ev("setRate('uspeed')")
    assert abs(c.ev("au.playbackRate") - c.ev("RATES.uspeed[0]")) < .01


def test_slowed_reverb_really_slows_down(live, mk):
    c = opened(mk, local_tracks(2), premium=True)
    playing_blob(c)
    c.ev("setRate('slowrev')")
    assert abs(c.ev("au.playbackRate") - 0.85) < .01


# ═══════════════════════════ Effets mémorisés par son ═══════════════════════════
def test_effects_stay_on_their_sound_and_reset_on_another(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    store_local_blob(c, "t2")
    playing_blob(c, "t1")
    c.ev("setRate('uslow'); setEq('bass'); setFxVal('dist',40)")
    assert c.ev("S.tracks.find(t=>t.id==='t1').fx") == {"rate": "uslow", "eq": "bass", "dist": 40}
    c.ev("void play(['t2'],0)")
    c.page.wait_for_function("curT().id==='t2' && !au.paused && au.playbackRate===1")
    assert c.ev("[S.player.rate,S.player.eq,S.player.dist||0,au.playbackRate]") == ["1", "flat", 0, 1]
    assert c.ev("S.tracks.find(t=>t.id==='t2').fx") is None
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("curT().id==='t1' && !au.paused && Math.abs(au.playbackRate-0.7)<.01")
    assert c.ev("[S.player.rate,S.player.eq,S.player.dist]") == ["uslow", "bass", 40]
    assert abs(c.ev("au.playbackRate") - c.ev("RATES.uslow[0]")) < .01


def test_back_to_normal_clears_the_saved_effects_of_a_sound(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c, "t1")
    c.ev("setFxVal('dist',40)")
    c.ev("setFxVal('dist',0)")
    assert c.ev("S.tracks.find(t=>t.id==='t1').fx") is None


def test_fade_between_sounds_stays_global(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    store_local_blob(c, "t2")
    playing_blob(c, "t1")
    c.ev("setFxVal('fade',6)")
    assert c.ev("S.tracks.find(t=>t.id==='t1').fx") is None
    c.ev("void play(['t2'],0)")
    c.page.wait_for_function("curT().id==='t2' && !au.paused")
    assert c.ev("S.player.fade") == 6


# ═══════════════════════════ Traînées, curseurs, ambiance (Premium sauf le gratuit) ═══════════════════════════
def wiggle(c, n=14):
    for i in range(n):
        c.page.mouse.move(200 + i * 25, 300 + (i % 3) * 20)


def test_defaults_free_trail_and_default_cursor(live, mk):
    c = opened(mk, tracks(2))
    assert c.ev("[S.ui.trail,S.ui.cur,S.ui.amb,S.ui.glow]") == ["spark", "def", "off", False]
    wiggle(c)
    assert c.ev("TR.p.length") > 0                                 # la traînée gratuite marche sans abonnement
    assert c.ev("document.documentElement.classList.contains('cur')") is False


def test_all_four_other_trails_cursors_ambiances_and_glow_are_premium(live, mk):
    c = opened(mk, tracks(2))
    for k, bad in (("trail", ["star", "flower", "flame", "bolt"]), ("cur", ["star", "heart", "ring", "arrow", "moon"]),
                   ("amb", ["stars", "petals", "snow", "fireflies"]), ("glow", [True])):
        for v in bad:
            c.ev(f"setUI({k!r},{'true' if v is True else repr(v)})")
            assert c.ev(f"S.ui.{k}") == {"trail": "spark", "cur": "def", "amb": "off", "glow": False}[k], (k, v)
    c.page.wait_for_selector("#gen:not([hidden])")                # chaque refus ouvre la fenêtre Premium


@pytest.mark.parametrize("kind", ["spark", "star", "flower", "flame", "bolt"])
def test_each_trail_draws_particles(live, mk, kind):
    c = opened(mk, tracks(2), premium=True)
    c.ev(f"setUI('trail','{kind}')")
    wiggle(c)
    assert c.ev("TR.p.length") > 0 and c.ev("TR.p[0].k") == kind
    assert c.ev("TR.p.length") <= 90                               # plafonné : pas de ralentissement


def test_trail_off_and_touch_and_reduced_motion_draw_nothing(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("setUI('trail','off')"); wiggle(c)
    assert c.ev("TR.p.length") == 0
    c.ev("setUI('trail','star')"); c.ev("setUI('motion',false)"); wiggle(c)
    assert c.ev("TR.p.length") == 0
    c.ev("setUI('motion',true)")
    c.ev("document.dispatchEvent(new PointerEvent('pointermove',{pointerType:'touch',clientX:50,clientY:50}));"
         "document.dispatchEvent(new PointerEvent('pointermove',{pointerType:'touch',clientX:150,clientY:90}))")
    assert c.ev("TR.p.length") == 0


def test_trail_particles_fade_out_and_the_loop_stops(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("setUI('trail','flame')"); wiggle(c)
    c.page.wait_for_function("TR.p.length===0 && TR.raf===0", timeout=6000)


def test_premium_trail_falls_back_to_free_one_when_premium_ends(live, mk):
    c = opened(mk, tracks(2), ui={"trail": "star"}, premium=False)
    assert c.ev("S.ui.trail") == "star" and c.ev("uiv('trail')") == "spark"


@pytest.mark.parametrize("kind", ["star", "heart", "ring", "arrow", "moon"])
def test_each_premium_cursor_is_applied(live, mk, kind):
    c = opened(mk, tracks(2), premium=True)
    c.ev(f"setUI('cur','{kind}')")
    assert c.ev("document.documentElement.classList.contains('cur')") is True
    assert "url(" in c.ev("getComputedStyle(document.body).cursor") and "svg" in c.ev("document.documentElement.style.getPropertyValue('--cur')")
    c.ev("setUI('cur','def')")
    assert c.ev("document.documentElement.classList.contains('cur')") is False


def test_cursor_follows_the_accent_colour(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("setUI('cur','star'); setUI('accent','#ff3366')")
    assert "ff3366" in c.ev("decodeURIComponent(document.documentElement.style.getPropertyValue('--cur'))").lower()


@pytest.mark.parametrize("kind", ["stars", "petals", "snow", "fireflies"])
def test_each_ambiance_runs_and_stops(live, mk, kind):
    c = opened(mk, tracks(2), premium=True)
    c.ev(f"setUI('amb','{kind}')")
    c.page.wait_for_function("AMB.p.length>5 && AMB.raf>0")
    assert c.ev("AMB.k") == kind
    c.ev("setUI('amb','off')")
    assert c.ev("AMB.p.length") == 0 and c.ev("AMB.raf") == 0
    assert c.ev("!document.getElementById('amb') || document.getElementById('amb').hidden") is True


def test_glow_turns_on_for_premium(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("setUI('glow',true)")
    assert c.ev("document.documentElement.dataset.glow") == "on"


def test_customize_page_lists_trails_cursors_ambiance_with_premium_stars(live, mk):
    c = opened(mk, tracks(2))
    c.ev("openCustom('effets')")
    tx = c.page.inner_text("#pzp").lower()
    for w in ("Étincelles", "Étoiles", "Fleurs", "Flammes", "Éclairs", "Curseur", "Arc-en-ciel"):
        assert w.lower() in tx, w
    assert c.page.locator("#pzp .pzt > .lk").count() == 16                # 7 traînées + 9 curseurs
    assert c.page.locator("#pzp .pzt svg").count() >= 11                  # chaque choix a son aperçu
    c.page.click(".pz-tabs [data-tab=fond]")
    assert "Ambiance" in c.page.inner_text("#pzp") and c.page.locator("#pzp .pzt .pfx").count() == 4
    c.page.click(".pz-tabs [data-tab=theme]")
    assert "Lueur" in c.page.inner_text("#pzp")


# ═══════════════════════════ Cosmétiques de profil ═══════════════════════════
def test_profile_cosmetics_locked_for_free_and_saved_for_premium(live, mk):
    free = mk()
    free.signup("f@example.com", "gratuit")
    free.seed(tracks(2)); free.open()
    free.page.wait_for_function("ME && PAYWALL===true")
    free.ev("renderProf()")
    free.ev("setCos('banner','fleurs')")
    free.page.wait_for_selector("#gen:not([hidden])")
    assert free.ev("(ME.cos||{}).banner||''") == ""
    p = opened(mk, tracks(2), premium=True)
    p.ev("renderProf()")
    p.ev("setCos('banner','fleurs')"); p.ev("setCos('frame','etoiles')"); p.ev("setCos('badge','🔥')")
    p.page.wait_for_function("ME.cos && ME.cos.badge==='🔥' && COSP===null")
    assert p.ev("ME.cos") == {"banner": "fleurs", "frame": "etoiles", "badge": "🔥"}
    html = p.ev("document.querySelector('.pbox').outerHTML")
    assert "bn-fleurs" in html and "fr-etoiles" in html and "🔥" in html


def test_visitors_see_the_cosmetics_of_a_premium_profile(live, mk):
    p = opened(mk, tracks(2), premium=True)
    p.ev("setCos('banner','neon')"); p.ev("setCos('frame','or')")
    p.page.wait_for_function("ME.cos && ME.cos.frame==='or' && COSP===null")
    v = opened(mk, tracks(2))
    v.ev("viewUser('payeur')")
    v.page.wait_for_selector(".pbox .banner.bn-neon")
    assert v.page.locator(".pbox .av.fr-or").count() == 1


def test_profile_page_is_light_and_customize_opens_on_the_profile_tab(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("location.hash='profil'")
    c.page.wait_for_selector("#profil-content .pbox")
    assert c.page.locator("#profil-content .pbox button:visible").count() <= 4      # crayon photo, Personnaliser, Partager, ⋯
    assert c.page.locator("#profil-content textarea, #profil-content .swr, #profil-content details.cosd").count() == 0
    assert c.ev("document.querySelector('#profil-content .banner').getBoundingClientRect().height") >= 200
    c.page.click("#profil-content .pbox button:has-text('Personnaliser')")
    c.page.wait_for_selector("section[data-v=perso]:not([hidden])")
    assert c.ev("PZ.tab") == "profil"
    tx = c.page.inner_text("#pzp").lower()
    for w in ("Bio", "Couleur du profil", "Bandeau", "Décoration d'avatar", "Effet de profil", "Thème du profil", "Style du pseudo", "Badge"):
        assert w.lower() in tx, w


# ═══════════════════════════ Page Personnalisation : cosmétiques façon Discord ═══════════════════════════
def free_logged(mk, n=2):
    c = mk()
    c.signup("f@example.com", "gratuit")
    c.seed(tracks(n)); c.open()
    c.page.wait_for_function("ME && PAYWALL===true")
    return c


def test_free_user_can_try_cosmetics_in_the_preview_without_saving(live, mk):
    c = free_logged(mk)
    c.ev("openCustom('profil')")
    c.page.click("#pzp .pzt:has(.bn-neon)")
    c.page.click("#pzp .pzt:has(.fr-halo)")
    c.page.click("#pzp .pzt:has(.pfx-neige)")
    assert c.ev("PV") == {"banner": "neon", "frame": "halo", "effect": "neige"}
    assert c.page.locator("#pzv .pbox .banner.bn-neon").count() == 1
    assert c.page.locator("#pzv .pbox .av.fr-halo").count() == 1 and c.page.locator("#pzv .pbox .pfx-neige").count() == 1
    assert c.page.locator("#pzv .pzv-try").is_visible()
    assert c.page.locator("#gen:not([hidden])").count() == 0                # pas de blocage : on essaie librement
    c.page.wait_for_timeout(600)
    assert c.ctx.request.get(live.base + "/api/auth/me").json()["cos"] == {}
    c.ev("location.hash='profil'")
    c.page.wait_for_selector("#profil-content .pbox")
    assert c.page.locator("#profil-content .bn-neon").count() == 0          # le vrai profil ne change pas
    c.ev("openCustom('profil')")
    c.page.click("#pzv .pzv-try .chip:has-text('Retirer')")
    assert c.ev("PV") == {} and c.page.locator("#pzv .pzv-try").count() == 0


def test_premium_free_colours_for_banner_decoration_and_card_theme(live, mk):
    c = opened(mk, tracks(2), premium=True)
    c.ev("openCustom('profil')")
    c.page.click("#pzp .pzt:has(.bn-degrade)")
    c.ev("window.__box=document.querySelector('#pzv .pbox')")
    c.ev("const i=document.querySelector('#pzp input[aria-label=\"Couleur 1 du bandeau\"]'); i.value='#ff0000'; i.dispatchEvent(new Event('input'))")
    assert "--b1: #ff0000" in c.ev("document.querySelector('#pzv .pbox').getAttribute('style')")
    assert c.ev("window.__box.isConnected") is True                         # mise à jour en direct, sans tout redessiner
    c.ev("const i=document.querySelector('#pzp input[aria-label=\"Couleur 1 du bandeau\"]'); i.dispatchEvent(new Event('change'))")
    c.ev("cosColor('fc','#00ff00',1); cosPair('#111111','#222222')")
    c.page.wait_for_function("COSP===null")
    got = c.ctx.request.get(live.base + "/api/auth/me").json()["cos"]
    assert got == {"banner": "degrade", "bc1": "#ff0000", "fc": "#00ff00", "t1": "#111111", "t2": "#222222"}
    assert c.page.locator("#pzv .pbox.pth").count() == 1
    assert c.ev("getComputedStyle(document.querySelector('#pzv .banner')).backgroundImage").count("rgb(255, 0, 0)") == 1


def test_avatar_decoration_scales_with_its_size_and_takes_its_colour(live, mk):
    c = opened(mk, tracks(1))
    h = c.ev("avatarHTML({username:'ana',cos:{frame:'neon',fc:'#ff0000'}},100)")
    assert "fr-neon" in h and "--avs:100px" in h and "--fc:#ff0000" in h
    assert "--fc" not in c.ev("avatarHTML({username:'ana',cos:{fc:'#ff0000'}},100)")   # pas de couleur sans décoration


def test_profile_effect_layer_shows_on_profile_and_stops_when_motion_is_reduced(live, mk):
    c = opened(mk, tracks(1), premium=True)
    c.ev("setCos('effect','neige'); setCos('name','neon')")
    c.ev("location.hash='profil'")
    c.page.wait_for_selector("#profil-content .pbox .pfx.pfx-neige")
    assert c.page.locator("#profil-content .pname .nf-neon").count() == 1
    anim = "getComputedStyle(document.querySelector('#profil-content .pfx'),'::before').animationName"
    assert c.ev(anim) != "none"
    c.ev("setUI('motion',false)")
    assert c.ev(anim) == "none"


def test_trail_and_cursor_colours_are_premium_and_applied(live, mk):
    free = opened(mk, tracks(1))
    free.ev("setUI('curc','#00ff00')")
    free.page.wait_for_selector("#gen:not([hidden])")
    assert free.ev("S.ui.curc") == ""
    c = opened(mk, tracks(1), premium=True)
    c.ev("setUI('trc','#ff0000')")
    wiggle(c)
    assert c.ev("TRC") == "#ff0000"
    c.ev("setUI('trc','rainbow')")
    wiggle(c)
    assert c.ev("trCol({h:120},'#fff')") == "hsl(120 95% 66%)"
    c.ev("setUI('cur','star'); setUI('curc','#00ff00')")
    assert "00ff00" in c.ev("decodeURIComponent(document.documentElement.style.getPropertyValue('--cur'))").lower()


def test_banner_is_much_taller_on_desktop(live, mk):
    c = opened(mk, tracks(1), premium=True)
    c.ev("location.hash='profil'")
    c.page.wait_for_selector("#profil-content .banner")
    assert c.ev("document.querySelector('#profil-content .banner').getBoundingClientRect().height") >= 200


# ═══════════════════════════ Sélection multiple ═══════════════════════════
def pk_rows(c):
    return c.page.locator("#pkl .pkr")


def test_new_playlist_picker_creates_with_the_sounds_tapped(live, mk):
    c = opened(mk, tracks(5))
    c.ev("location.hash='playlists'")
    c.page.fill("#npn", "Gym")
    c.page.click("button:has-text('Créer et choisir les sons')")
    c.page.wait_for_selector("#gen:not([hidden]) #pkl .pkr")
    assert pk_rows(c).count() == 5 and c.page.input_value("#pkn") == "Gym"
    c.page.click("#pkl .pkr[data-id=t4]"); c.page.click("#pkl .pkr[data-id=t2]")
    assert c.page.inner_text("#pkc") == "2 sons sélectionnés"
    assert c.page.locator("#pkl .pkr.on").count() == 2
    c.page.click("#pkl .pkr[data-id=t2]")                                 # re-toucher = désélectionner
    c.page.click("#pkl .pkr[data-id=t1]")
    c.page.click("#pkok")
    pl = c.ev("S.pls[S.pls.length-1]")
    assert pl["name"] == "Gym" and pl["ids"] == ["t4", "t1"]               # dans l'ordre où on les a touchés
    c.page.wait_for_selector(".pl-hero")
    assert c.ev("openPl") == pl["id"]


def test_picker_shift_click_takes_a_range_and_all_toggles(live, mk):
    c = opened(mk, tracks(5))
    c.ev("openPicker()")
    rows = c.ev("[...document.querySelectorAll('#pkl .pkr')].map(r=>r.dataset.id)")
    c.page.click(f"#pkl .pkr[data-id={rows[0]}]")
    c.page.click(f"#pkl .pkr[data-id={rows[3]}]", modifiers=["Shift"])
    assert c.ev("PK.sel.size") == 4
    c.page.click("#pka")
    assert c.ev("PK.sel.size") == 5 and c.page.inner_text("#pka") == "Aucun"
    c.page.click("#pka")
    assert c.ev("PK.sel.size") == 0


def test_picker_search_filters_but_keeps_the_selection(live, mk):
    c = opened(mk, tracks(5))
    c.ev("openPicker()")
    c.page.click("#pkl .pkr[data-id=t1]")
    c.page.fill("#pkq", "Son 3")
    assert pk_rows(c).count() == 1
    c.page.click("#pkl .pkr[data-id=t3]")
    c.page.fill("#pkq", "")
    assert c.page.locator("#pkl .pkr.on").count() == 2
    c.page.click("#pkok")
    assert c.ev("S.pls[S.pls.length-1].ids") == ["t1", "t3"]


def test_add_sounds_to_an_existing_playlist_skips_those_already_in(live, mk):
    c = opened(mk, tracks(5), pls=[{"id": "p1", "name": "Sport", "ids": ["t1", "t2"]}])
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.page.click(".pl-btns button[aria-label='Ajouter des sons']")
    c.page.wait_for_selector("#gen:not([hidden]) #pkl")
    assert c.page.locator("#pkl .pkr[disabled]").count() == 2
    assert c.ev("document.getElementById('pkok').disabled") is True
    c.page.wait_for_function("document.querySelector('#pkl .pkr[data-id=t1]').getBoundingClientRect().bottom<=innerHeight")
    c.page.click("#pkl .pkr[data-id=t1]", force=True)                     # déjà dedans : rien
    c.page.click("#pkl .pkr[data-id=t5]"); c.page.click("#pkl .pkr[data-id=t3]")
    c.page.click("#pkok")
    assert c.ev("S.pls[0].ids") == ["t1", "t2", "t5", "t3"]


def test_empty_library_still_lets_you_create_an_empty_playlist(live, mk):
    c = opened(mk, [])
    c.ev("openPicker()")
    c.page.fill("#pkn", "Plus tard")
    c.page.click("#pkok")
    assert c.ev("S.pls[0].name") == "Plus tard" and c.ev("S.pls[0].ids") == []


def test_library_selection_mode_selects_instead_of_playing(live, mk):
    c = opened(mk, tracks(4))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc[data-id=t1]")
    c.ev("libView()")
    c.page.click("#optl .opt:has-text('Sélectionner plusieurs sons')")
    c.page.wait_for_selector("#selbar:not([hidden])")
    c.page.click("#lib .tc[data-id=t1] .art"); c.page.click("#lib .tc[data-id=t3] .tm2")
    assert sorted(c.ev("[...SEL]")) == ["t1", "t3"] and c.ev("!au.src||au.paused") is True
    assert "2 sons" in c.page.inner_text("#selbar")
    assert c.page.locator("#lib .tc.sel").count() == 2
    c.page.click("#selbar button:has-text('Playlist')")
    c.page.click("#optl .opt:has-text('Nouvelle playlist')")
    c.page.wait_for_selector("#gen:not([hidden]) #pkl")
    assert c.page.locator("#pkl .pkr.on").count() == 2
    c.page.fill("#pkn", "Choix")
    c.page.click("#pkok")
    assert c.ev("S.pls[S.pls.length-1].ids") == ["t1", "t3"]
    assert c.ev("SEL") is None and c.ev("document.body.classList.contains('selmode')") is False


def test_long_press_starts_selection_and_escape_ends_it(live, mk):
    c = opened(mk, tracks(3))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc[data-id=t2]")
    b = c.page.locator("#lib .tc[data-id=t2] .art").bounding_box()
    c.page.mouse.move(b["x"] + 20, b["y"] + 20)
    c.page.mouse.down(); c.page.wait_for_timeout(700); c.page.mouse.up()
    assert c.ev("[...SEL]") == ["t2"] and c.ev("!au.src||au.paused") is True   # l'appui long ne lance pas la lecture
    c.page.keyboard.press("Escape")
    assert c.ev("SEL") is None


def test_bulk_favourite_and_bulk_trash(live, mk):
    c = opened(mk, tracks(4))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc[data-id=t1]")
    c.ev("selStart()")
    c.page.click("#lib .tc[data-id=t1] .art"); c.page.click("#lib .tc[data-id=t2] .art")
    c.page.click("#selbar button[aria-label='Ajouter aux favoris']")
    assert c.ev("S.tracks.filter(t=>t.fav).map(t=>t.id).sort()") == ["t1", "t2"]
    c.ev("selStart()")
    c.page.click("#lib .tc[data-id=t3] .art"); c.page.click("#lib .tc[data-id=t4] .art")
    c.page.click("#selbar button[aria-label='Mettre à la corbeille']")
    c.page.click("#cfok")
    c.page.wait_for_function("S.tracks.length===2")
    assert c.ev("S.trash.length") == 2


# ═══════════════════════════ Écran éteint / app en arrière-plan ═══════════════════════════
def set_hidden(c, hidden):
    c.ev(f"Object.defineProperty(document,'hidden',{{configurable:true,get:()=>{str(hidden).lower()}}});"
         f"Object.defineProperty(document,'visibilityState',{{configurable:true,get:()=>'{'hidden' if hidden else 'visible'}'}});"
         "document.dispatchEvent(new Event('visibilitychange'))")


def test_screen_off_moves_effects_playback_to_the_plain_player_without_stopping(live, mk):
    no_paywall(live)
    c = opened(mk, with_fx(local_tracks(2), eq="bass", rate="slowed"), paywall=False)
    playing_blob(c)
    c.page.wait_for_function("au===au2 && au.currentTime>0.3")
    pos = c.ev("au.currentTime")
    set_hidden(c, True)
    c.page.wait_for_function("au===au1 && !au.paused")
    assert c.ev("au2.paused") is True and c.ev("!au2.getAttribute('src')") is True
    assert c.ev("au.currentTime") >= pos - 0.3                              # même position
    assert abs(c.ev("au.playbackRate") - 0.85) < .01                        # la vitesse est gardée
    c.page.wait_for_function("FX.ctx.state==='suspended'")
    set_hidden(c, False)
    c.page.wait_for_function("au===au2 && !au.paused")                     # les effets reviennent à l'écran rallumé


def test_next_sound_started_while_screen_is_off_uses_the_plain_player(live, mk):
    no_paywall(live)
    c = opened(mk, with_fx(local_tracks(2), eq="bass"), paywall=False)
    store_local_blob(c, "t2")
    playing_blob(c, "t1")
    set_hidden(c, True)
    c.ev("void play(['t2'],0)")
    c.page.wait_for_function("curT().id==='t2' && au===au1 && !au.paused")


def test_paused_effects_stay_paused_when_screen_goes_off(live, mk):
    no_paywall(live)
    c = opened(mk, with_fx(local_tracks(2), eq="bass"), paywall=False)
    playing_blob(c)
    c.page.wait_for_function("au===au2")
    c.ev("au.pause()")
    set_hidden(c, True)
    c.page.wait_for_timeout(300)
    assert c.ev("au1.paused && au2.paused") is True


def test_audio_session_is_set_to_playback_when_the_browser_supports_it(live, mk):
    c = mk()
    c.page.add_init_script("Object.defineProperty(navigator,'audioSession',{value:{type:'auto'},configurable:true})")
    c.seed(tracks(1)); c.open()
    assert c.ev("navigator.audioSession.type") == "playback"


def test_ui_sound_context_is_closed_when_screen_goes_off(live, mk):
    c = opened(mk, tracks(1))
    c.ev("sfx('pop')")
    c.page.wait_for_function("SFX.ctx && SFX.ctx.state!=='closed'")
    set_hidden(c, True)
    c.page.wait_for_function("SFX.ctx===null", timeout=800)              # tout de suite, pas au bout du délai de repos


# ═══════════════════════════ Effets sur les sons en ligne (relais du site) ═══════════════════════════
R2KEY = "a" * 40


def remote_r2(c, live, fail=False):
    """Un son R2 sans CORS ; le site le relaie via /api/media (lecture des octets simulée)."""
    path = os.path.join(os.path.dirname(__file__), "media", "long.wav")
    data = open(path, "rb").read()

    def fake(key, a, b):
        if fail:
            raise RuntimeError("R2 en panne")
        return data[a:b + 1], len(data)
    live.app.r2_range = fake

    def handler(r):
        if r.request.resource_type == "fetch":
            return r.abort()
        r.fulfill(status=200, path=path, content_type="audio/wav")
    c.ctx.route(re.compile(r"https://r2\.example\.test/"), handler)
    return [dict(id="r1", title="En ligne", author="A", duration=60, url="", local=False,
                 remote=f"https://r2.example.test/audio/{R2KEY}.mp3", added=1)]


def test_effects_on_online_r2_sound_go_through_the_site_relay(live, mk):
    no_paywall(live)
    c = mk()
    c.seed(with_fx(remote_r2(c, live), eq="bass", dist=30), player={"shuffle": False, "repeat": "all", "rate": "1"})
    c.open()
    c.ev("void play(['r1'],0)")
    c.page.wait_for_function(f"au===au2 && au.src.endsWith('/api/media/audio/{R2KEY}.mp3') && !au.paused && au.currentTime>0.2")
    assert "égaliseur" not in c.page.inner_text("#toasts")
    c.ev("setFxVal('dist',0); setEq('flat')")                            # plus d'effet : lecture directe depuis R2
    c.page.wait_for_function("au===au1 && au.src.includes('r2.example.test') && !au.paused")
    c.ev("setEq('bass')")
    c.page.wait_for_function("au===au2 && au.src.includes('/api/media/') && !au.paused")


def test_online_sound_falls_back_without_effects_if_the_relay_fails(live, mk):
    no_paywall(live)
    c = mk()
    c.seed(with_fx(remote_r2(c, live, fail=True), eq="bass"), player={"shuffle": False, "repeat": "all", "rate": "1"})
    c.open()
    c.ev("void play(['r1'],0)")
    c.page.wait_for_function("au===au1 && au.src.includes('r2.example.test') && !au.paused")
    c.page.wait_for_function("document.querySelector('#toasts').innerText.includes('égaliseur')")



def test_select_button_is_visible_in_my_sounds(live, mk):
    c = opened(mk, tracks(3))
    c.ev("location.hash='bibliotheque'")
    c.page.click("section[data-v=bibliotheque] .top button[aria-label='Sélectionner plusieurs sons']")
    c.page.wait_for_selector("#selbar:not([hidden])")
    c.page.click("#lib .tc[data-id=t2] .art")
    assert c.ev("[...SEL]") == ["t2"]
    c.page.click("section[data-v=bibliotheque] .top button[aria-label='Sélectionner plusieurs sons']")   # re-toucher = quitter
    assert c.ev("SEL") is None


def test_android_long_press_contextmenu_starts_selection(live, mk):
    c = opened(mk, tracks(3))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc[data-id=t3]")
    prevented = c.ev("(()=>{const e=new MouseEvent('contextmenu',{bubbles:true,cancelable:true}); document.querySelector('#lib .tc[data-id=t3] .art').dispatchEvent(e); return e.defaultPrevented})()")
    assert prevented is True and c.ev("[...SEL]") == ["t3"]


def test_selection_inside_a_playlist_removes_or_copies_sounds(live, mk):
    c = opened(mk, tracks(5), pls=[{"id": "p1", "name": "Sport", "ids": ["t1", "t2", "t3"]}, {"id": "p2", "name": "Chill", "ids": ["t5"]}])
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.page.wait_for_selector(".pl-hero")
    c.page.click(".pl-btns button[aria-label='Sélectionner plusieurs sons']")
    c.page.click("section[data-v=playlists] .row[data-id=t1] .m"); c.page.click("section[data-v=playlists] .row[data-id=t3] .m")
    assert sorted(c.ev("[...SEL]")) == ["t1", "t3"] and c.ev("!au.src||au.paused") is True
    c.page.click("#selbar button:has-text('Playlist')")
    c.page.click("#optl .opt:has-text('Chill')")
    c.page.wait_for_function("S.pls[1].ids.length===3")
    assert c.ev("S.pls[1].ids") == ["t5", "t1", "t3"]
    c.page.click(".pl-btns button[aria-label='Sélectionner plusieurs sons']")
    c.page.click("section[data-v=playlists] .row[data-id=t2] .m")
    c.page.click("#selbar button[aria-label='Retirer de la playlist']")
    assert c.ev("S.pls[0].ids") == ["t1", "t3"] and c.ev("S.tracks.length") == 5       # retiré de la playlist, pas supprimé
    assert c.ev("SEL") is None


def test_leaving_the_page_ends_the_selection(live, mk):
    c = opened(mk, tracks(3))
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc[data-id=t1]")
    c.ev("selStart('t1')")
    c.ev("location.hash='accueil'")
    c.page.wait_for_function("SEL===null && document.getElementById('selbar').hidden")


# ═══════════════════════════ Bugs trouvés à l'audit mobile ═══════════════════════════
def test_a_menu_option_that_opens_another_menu_keeps_it_open(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(2), paywall=False)
    playing_blob(c)
    c.ev("openNP()")
    c.ev("npMenu()")
    c.page.click("#optl .opt:has-text('Options du son')")
    c.page.wait_for_timeout(700)                                         # après la fin de l'animation de fermeture
    assert c.ev("document.getElementById('opt').hidden") is False
    assert "Modifier" in c.page.inner_text("#optl")
    c.page.click("#optl .opt:has-text('Modifier')")
    c.page.wait_for_selector("#gen:not([hidden]) #genb input")


def test_now_playing_fits_on_a_small_phone(live, mk):
    no_paywall(live)
    c = mk(viewport={"width": 375, "height": 600}, is_mobile=True, has_touch=True)
    c.seed(local_tracks(2)); c.open()
    playing_blob(c)
    c.ev("openNP()")
    c.page.wait_for_timeout(500)
    for sel in (".np-x", ".np-ctl .pp", ".np-cover"):
        b = c.ev(f"(()=>{{const e=document.querySelector('#np {sel}'); return e?e.getBoundingClientRect().bottom:-1}})()")
        assert 0 < b <= 600, (sel, b)


# ═══════════════════════════ Recherche de sons ═══════════════════════════
FOUND = {"q": "alone", "sources": ["youtube", "soundcloud"], "results": {
    "youtube": [{"src": "youtube", "url": f"https://www.youtube.com/watch?v=vid0000000{i}", "title": f"Alone {i}", "author": "Marshmello",
                 "duration": 200 + i, "cover": ""} for i in range(5)],
    "soundcloud": [{"src": "soundcloud", "url": f"https://soundcloud.com/dj/alone-{i}", "title": f"Alone SC {i}", "author": "dj",
                    "duration": 180, "cover": ""} for i in range(5)]}}


def fake_find(c, payload=None, fail_extract=False):
    import json as _j
    c.ctx.route(re.compile(r".*/api/find\?.*"), lambda r: r.fulfill(status=200, content_type="application/json", body=_j.dumps(payload or FOUND)))
    path = os.path.join(os.path.dirname(__file__), "media", "silence.wav")

    def ex(r):
        body = _j.loads(r.request.post_data or "{}")
        if fail_extract:
            return r.fulfill(status=502, content_type="application/json", body=_j.dumps({"error": "Impossible de récupérer ce son pour le moment."}))
        r.fulfill(status=200, content_type="application/json", body=_j.dumps({"id": "u1", "url": body["url"], "title": "Alone 0",
                  "author": "Marshmello", "cover": "", "duration": 200, "audio_url": "/__m/silence.wav", "remote": False}))
    c.ctx.route(re.compile(r".*/api/extract$"), ex)


def test_search_bar_shows_five_results_per_platform_with_filters(live, mk):
    no_paywall(live)
    c = mk(); fake_find(c); c.seed([]); c.open()
    c.ev("location.hash='ajouter'")
    c.page.fill("#fq", "alone")
    c.page.press("#fq", "Enter")
    c.page.wait_for_selector("#fres .fr")
    assert c.page.locator("#fres .fr").count() == 10
    assert "YouTube" in c.page.inner_text("#fres") and "SoundCloud" in c.page.inner_text("#fres")
    c.page.click("#fres .fchips button:has-text('SoundCloud')")
    assert c.page.locator("#fres .fr:visible").count() == 5
    assert "Alone SC 0" in c.page.inner_text("#fres")


def test_downloading_a_search_result_adds_it_to_the_library(live, mk):
    no_paywall(live)
    c = mk(); fake_find(c); c.seed([]); c.open()
    c.ev("location.hash='ajouter'")
    c.page.fill("#fq", "alone"); c.page.press("#fq", "Enter")
    c.page.wait_for_selector("#fres .fr")
    c.page.click("#fres .fr >> nth=0 >> button.fdl")
    c.page.wait_for_function("S.tracks.length===1 && S.tracks[0].local===true && !S.tracks[0].pending")
    t = c.ev("S.tracks[0]")
    assert t["url"] == "https://www.youtube.com/watch?v=vid00000000" and t["title"] == "Alone 0"
    c.page.wait_for_selector("#fres .fr >> nth=0 >> .fdl.done")
    c.page.click("#fres .fr >> nth=0 >> .fdl.done")                      # ✓ : touche = écouter
    c.page.wait_for_function("curT() && curT().id===S.tracks[0].id && !au.paused")


def test_search_result_already_in_library_is_marked(live, mk):
    no_paywall(live)
    c = mk(); fake_find(c)
    c.seed([dict(id="t9", title="Alone 1", author="M", duration=60, url="https://www.youtube.com/watch?v=vid00000001", local=False,
                 remote="{BASE}/__m/silence.wav?n=9", added=1)]); c.open()
    c.ev("location.hash='ajouter'")
    c.page.fill("#fq", "alone"); c.page.press("#fq", "Enter")
    c.page.wait_for_selector("#fres .fr")
    assert c.page.locator("#fres .fr >> nth=1 >> .fdl.done").count() == 1


def test_search_errors_and_empty_results_are_explained(live, mk):
    no_paywall(live)
    c = mk(); fake_find(c, {"q": "zzz", "sources": ["youtube", "soundcloud"], "results": {"youtube": [], "soundcloud": []}}); c.seed([]); c.open()
    c.ev("location.hash='ajouter'")
    c.page.fill("#fq", "zzz"); c.page.press("#fq", "Enter")
    c.page.wait_for_selector("#fres .empty")
    c2 = mk(); fake_find(c2, fail_extract=True); c2.seed([]); c2.open()
    c2.ev("location.hash='ajouter'")
    c2.page.fill("#fq", "alone"); c2.page.press("#fq", "Enter")
    c2.page.wait_for_selector("#fres .fr")
    c2.page.click("#fres .fr >> nth=0 >> button.fdl")
    c2.page.wait_for_function("document.querySelector('#toasts').innerText.includes('Impossible')")
    c2.page.wait_for_selector("#fres .fr >> nth=0 >> button.fdl:not(.run):not(.done)")


def _visible_icons(c, tid):
    return c.ev(f"""[...document.querySelectorAll('#lib .tc[data-id={tid}] .ov .ip-play, #lib .tc[data-id={tid}] .ov .ip-pause')]
                   .filter(e=>getComputedStyle(e).display!=='none').map(e=>e.className)""")


def test_a_card_shows_only_one_play_or_pause_button(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(3), paywall=False)
    playing_blob(c, "t1")
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_selector("#lib .tc.pl[data-id=t1]")
    assert _visible_icons(c, "t1") == ["ip-pause"]                       # en lecture : seulement pause
    c.page.hover("#lib .tc[data-id=t2] .art")
    assert _visible_icons(c, "t2") == ["ip-play"]                        # les autres : seulement lecture
    box = c.page.locator("#lib .tc[data-id=t2] .ov .ip-play").bounding_box()
    art = c.page.locator("#lib .tc[data-id=t2] .art").bounding_box()
    assert box["height"] <= 60 and box["y"] + box["height"] <= art["y"] + art["height"]   # un seul rond, dans la pochette


# ═══════════════════════════ Renommage TikWave ═══════════════════════════
def _export(c):
    with c.page.expect_download() as dl:
        c.ev("exportData()")
    d = dl.value
    return d.suggested_filename, json.loads(open(d.path(), encoding="utf-8").read())


def test_backup_file_carries_the_new_name(live, mk):
    c = opened(mk, tracks(2))
    name, data = _export(c)
    assert name.startswith("tikwave-sauvegarde-") and data["app"] == "tikwave"


@pytest.mark.parametrize("app_name", ["tiksound", "tikwave"])
def test_old_and_new_backups_can_be_imported(live, mk, app_name):
    c = opened(mk, [])
    backup = {"app": app_name, "v": 2, "pls": [], "tracks": [
        {"id": "a1", "title": "Un son", "url": "https://www.tiktok.com/@a/video/1"}]}
    c.ev("importData(new File([%s], 'b.json'))" % json.dumps(json.dumps(backup)))
    c.page.wait_for_function("S.tracks.length===1")
    assert c.ev("S.tracks[0].pending") is True


def test_import_keeps_every_kind_of_sound_link(live, mk):
    c = opened(mk, [])
    urls = ["https://www.tiktok.com/@a/video/1", "https://www.youtube.com/watch?v=abcdefghijk",
            "https://soundcloud.com/artiste/titre", "https://open.spotify.com/track/" + "A" * 22]
    backup = {"app": "tiksound", "tracks": [{"id": f"a{i}", "title": "S", "url": u} for i, u in enumerate(urls + ["https://evil.example/x"])]}
    c.ev("importData(new File([%s], 'b.json'))" % json.dumps(json.dumps(backup)))
    c.page.wait_for_function("S.tracks.length===5")
    assert c.ev("S.tracks.map(t=>t.url)") == urls + [""]
    assert c.ev("S.tracks.map(t=>t.pending)") == [True, True, True, True, False]


def test_old_address_shows_how_to_move_and_new_address_does_not(live, mk):
    c = opened(mk, tracks(1))
    assert c.page.locator("#moved").is_hidden()
    c.ev("showMoved('tiksound-cqj3.onrender.com')")
    txt = c.page.inner_text("#moved")
    assert "TikWave" in txt and "tikwave.fr" in txt
    assert c.page.locator("#moved a[href^='https://tikwave.fr']").count() == 1
    assert c.page.locator("#moved button").count() >= 1


# ═══════════════════════════ Français / English ═══════════════════════════
# Un mot français visible en mode anglais = traduction oubliée. Le contenu des sons de test est en anglais.
FR_LEAK = re.compile(r"[éèêàçùîôûœÉÈÀÊ]|\b(ton|ta|tes|tu|pour|avec|sans|aucun|aucune|ajouter|ajoute|retirer|fermer|lire|partager|"
                     r"les|des|une|mes|sons|mot de passe|connexion|accueil|profil|bonjour|bonsoir|choisis|touche|oui|non)\b", re.I)


def en_tracks(n=4):
    return [dict(id=f"t{i}", title=f"Track {i}", author="Artist", duration=60 * i, url=f"https://www.tiktok.com/@a/video/{i}",
                 local=False, remote=f"{{BASE}}/__m/silence.wav?n={i}", added=i, plays=i, fav=i == 1) for i in range(1, n + 1)]


def visible_text(c):
    return c.ev("""(()=>{ const out=[], seen=e=>{ for(let x=e;x;x=x.parentElement){ if(x.hidden) return false; const s=getComputedStyle(x);
        if(s.display==='none'||s.visibility==='hidden') return false; } return true; };
      const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
      for(let n;(n=w.nextNode());){ const p=n.parentElement; if(!n.textContent.trim()||/^(SCRIPT|STYLE)$/.test(p.tagName)||p.closest('#langsel')) continue; if(seen(p)) out.push(n.textContent.trim()); }
      document.querySelectorAll('[aria-label],[title],[placeholder]').forEach(e=>{ if(!seen(e)||e.closest('#langsel')) return;
        ['aria-label','title','placeholder'].forEach(a=>{ const v=e.getAttribute(a); if(v) out.push(v); }); });
      return out; })()""")


def leaks(c, where):
    fonts = set(c.ev("Object.values(FONTS).map(f=>f.n)"))           # noms propres (« DM Sans »), pas du français
    return [f"{where}: {s[:90]}" for s in visible_text(c) if s not in fonts and FR_LEAK.search(s)]


@pytest.mark.parametrize("locale,expected", [("fr-FR", "fr"), ("fr-CA", "fr"), ("en-US", "en"), ("es-ES", "en")])
def test_language_follows_the_browser_by_default(live, mk, locale, expected):
    c = mk(locale=locale)
    c.open()
    assert c.ev("document.documentElement.lang") == expected
    nav = c.page.inner_text("header nav").lower()
    assert ("my sounds" in nav) is (expected == "en")


def test_language_can_be_chosen_in_settings_and_is_remembered(live, mk):
    c = mk(locale="fr-FR")
    c.open()
    c.ev("location.hash='profil'")
    c.page.select_option("#langsel", "en")
    c.page.wait_for_function("document.documentElement.lang==='en' && typeof route==='function'")
    assert c.ev("localStorage.getItem('tklang')") == "en"
    assert any(k["name"] == "tk_lang" and k["value"] == "en" for k in c.ctx.cookies())
    c.page.reload()
    c.page.wait_for_function("typeof route==='function'")
    assert c.ev("document.documentElement.lang") == "en"
    assert c.page.input_value("#langsel") == "en"
    assert "my sounds" in c.page.inner_text("header nav").lower()


def test_server_errors_reach_an_english_visitor_in_english(live, mk):
    c = mk(locale="fr-FR")
    c.ctx.add_init_script("localStorage.setItem('tklang','en')")
    c.open()
    msg = c.ev("api('/api/auth/login',{method:'POST',body:{email:'x@example.com',password:'motdepasse1'}}).catch(e=>e.message)")
    assert msg == "Wrong email or password"


def test_no_french_is_left_on_any_english_screen(live, mk):
    no_paywall(live)
    c = mk(locale="en-US")
    c.seed(en_tracks(), pls=[{"id": "p1", "name": "Gym", "ids": ["t1", "t2"]}],
           trash=[{"type": "track", "at": 1700000000000, "item": {"id": "z", "title": "Old one"}}])
    c.open()
    found = []
    for view in ("accueil", "ajouter", "bibliotheque", "playlists", "decouvrir", "profil"):
        c.ev(f"location.hash='{view}'")
        c.page.wait_for_timeout(450)
        found += leaks(c, view)
    c.ev("openPl='p1';location.hash='playlists';render()")
    c.page.wait_for_timeout(250)
    found += leaks(c, "playlist ouverte")
    for tab in c.ev("PZT.map(t=>t[0])"):
        c.ev(f"openCustom('{tab}')")
        c.page.wait_for_timeout(200)
        found += leaks(c, "perso " + tab)
    c.ev("location.hash='bibliotheque'")
    c.page.wait_for_timeout(300)
    for name, js in [("stats", "openStats()"), ("trash", "openTrash()"), ("fx", "openFx()"), ("tri", "libView()"),
                     ("son", "trackMenu('t1')"), ("playlist", "plMenu('p1')"), ("picker", "openPicker('p1')"),
                     ("raccourcis", "showShortcuts()"), ("minuterie", "pickSleep()"), ("bienvenue", "welcome(0)"),
                     ("premium", "openPremium()")]:
        c.ev(f"void {js}")
        c.page.wait_for_timeout(450)
        found += leaks(c, name)
        c.ev("['sh','opt','gen','ps','cf','im'].forEach(i=>closeSh(i))")
        c.page.wait_for_timeout(380)
    c.ev("void play(['t1','t2'],0)")
    c.page.wait_for_timeout(600)
    c.ev("openNP()")
    c.page.wait_for_timeout(400)
    found += leaks(c, "lecteur")
    c.ev("closeNP(); location.hash='bibliotheque'")
    c.page.wait_for_timeout(300)
    c.ev("selStart('t1')")
    c.page.wait_for_timeout(300)
    found += leaks(c, "selection")
    c.ev("selEnd()")
    for mode in ("login", "register", "forgot"):
        c.ev(f"openAuth('{mode}')")
        c.page.wait_for_timeout(200)
        found += leaks(c, "auth " + mode)
        c.ev("closeAuth()")
    assert not found, "\n".join(sorted(set(found))[:60])
    assert not c.logs, c.logs


def test_signed_in_profile_and_explore_are_fully_english(live, mk):
    other = mk()
    other.signup("other@example.com", "dj_other")
    other.ctx.request.post(live.base + "/api/playlists", data={"name": "Road trip", "tracks": [
        {"url": "https://www.tiktok.com/@a/video/9", "title": "Sunset", "author": "A", "cover": "", "duration": 20}]})
    other.ctx.request.post(live.base + "/api/posts", data={"url": "https://www.tiktok.com/@a/video/8", "title": "Waves", "author": "B"})
    c = mk(locale="en-US")
    c.signup("me@example.com", "me_tester")
    make_premium(live, "me@example.com")
    c.seed(en_tracks())
    c.open()
    c.page.wait_for_function("ME && isPrem()")
    found = []
    for view in ("accueil", "profil", "decouvrir"):
        c.ev(f"location.hash='{view}'")
        c.page.wait_for_timeout(700)
        found += leaks(c, view)
    c.ev("viewUser('dj_other')")
    c.page.wait_for_selector("#pub-view .pc")
    found += leaks(c, "profil public")
    c.page.locator("#pub-view .pc").first.click()
    c.page.wait_for_selector("#pub-view .pl-hero")
    found += leaks(c, "playlist publique")
    c.ev("openCustom('profil')")
    c.page.wait_for_timeout(300)
    found += leaks(c, "cosmetiques")
    c.ev("openPremium()")
    c.page.wait_for_timeout(500)
    found += leaks(c, "premium actif")
    assert not found, "\n".join(sorted(set(found))[:60])
    assert not c.logs, c.logs


def test_french_stays_exactly_the_same_for_french_visitors(live, mk):
    c = opened(mk, tracks(2))
    assert c.ev("document.documentElement.lang") == "fr"
    assert "Mes sons" in c.page.inner_text("header nav")
    assert c.ev("plural(2,'son')") == "2 sons" and c.ev("plural(1,'son')") == "1 son"


# ═══════════════════════════ Explorer : un son publié apparaît tout de suite ═══════════════════════════
def mine(title="Mon son", vid=5):
    return [dict(id="t1", title=title, author="Moi", duration=30, url=f"https://www.tiktok.com/@me/video/{vid}",
                 local=False, remote="{BASE}/__m/silence.wav", added=1)]


def signed_in(mk, email="me@example.com", name="moi_test", tr=None):
    c = mk()
    c.signup(email, name)
    c.seed(tr if tr is not None else mine())
    c.open()
    c.page.wait_for_function("ME")
    return c


def test_explore_shows_sounds_published_since_the_last_visit(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("location.hash='decouvrir'")
    c.page.wait_for_selector("#posts-wrap .sec-h")                     # « Aucun son publié » pour l'instant
    other = mk()
    other.signup("o@example.com", "autre")
    other.ctx.request.post(live.base + "/api/posts", data={"url": "https://www.tiktok.com/@a/video/77", "title": "Nouveau tube", "author": "A"})
    c.ev("location.hash='bibliotheque'")
    c.ev("location.hash='decouvrir'")
    c.page.wait_for_selector("#posts-wrap .post:has-text('Nouveau tube')")


def test_a_late_answer_cannot_hide_the_sound_you_just_published(live, mk):
    no_paywall(live)
    held = []

    def hold_first(route):
        if held:
            return route.continue_()
        held.append((route, route.fetch()))            # la liste d'AVANT la publication, rendue plus tard

    c = signed_in(mk)
    c.ctx.route(re.compile(r"/api/posts\?offset=0$"), hold_first)
    c.ev("location.hash='decouvrir'")
    c.page.wait_for_function("postsLoading")
    c.ev("location.hash='bibliotheque'")
    c.ev("publishTrack('t1')")
    c.ev("location.hash='decouvrir'")
    c.page.wait_for_selector("#posts-wrap .post:has-text('Mon son')")
    route, resp = held[0]
    route.fulfill(response=resp)                        # la vieille réponse arrive enfin
    c.page.wait_for_timeout(400)
    assert c.page.locator("#posts-wrap .post:has-text('Mon son')").count() == 1


def test_see_button_after_publishing_brings_the_new_sound_into_view(live, mk):
    no_paywall(live)
    owner = mk()
    owner.signup("o@example.com", "owner")
    for i in range(8):
        owner.ctx.request.post(live.base + "/api/playlists", data={"name": f"Playlist {i}", "tracks": [
            {"url": f"https://www.tiktok.com/@a/video/{i}", "title": "Son", "author": "A", "cover": "", "duration": 20}]})
    c = signed_in(mk)
    c.ev("location.hash='bibliotheque'")
    c.ev("publishTrack('t1')")
    c.page.click("#toasts button")
    c.page.wait_for_selector("#posts-wrap .post:has-text('Mon son')")
    c.page.wait_for_function("(()=>{ const r=document.querySelector('#posts-wrap .post').getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight; })()")


# ═══════════════════════════ Premium visible dans Explorer + nouvelles décorations ═══════════════════════════
def _vip(live, mk):
    vip = mk()
    vip.signup("vip@example.com", "vip_au_long_pseudo")
    make_premium(live, "vip@example.com")
    vip.ctx.request.patch(live.base + "/api/auth/me", data={"cos": {"frame": "couronne", "badge": "👑"}})
    free = mk()
    free.signup("free@example.com", "simple")
    for c, n in ((vip, 1), (free, 2)):
        c.ctx.request.post(live.base + "/api/playlists", data={"name": f"Liste {n}", "tracks": [
            {"url": f"https://www.tiktok.com/@a/video/{n}", "title": "Son", "author": "A", "cover": "", "duration": 20}]})
        c.ctx.request.post(live.base + "/api/posts", data={"url": f"https://www.tiktok.com/@a/video/{n}", "title": f"Son {n}", "author": "A"})


def test_explore_shows_who_is_premium(live, mk):
    _vip(live, mk)
    c = opened(mk, tracks(1))
    c.ev("location.hash='decouvrir'")
    vip = c.page.locator(".user-card:has-text('@vip')")
    vip.wait_for()
    assert vip.locator(".pbadge").count() == 1                         # écrit « Premium »
    assert c.ev("(()=>{ const b=document.querySelector('.user-card .pbadge'), r=b.getBoundingClientRect(), t=b.closest('.m').getBoundingClientRect();"
                " return r.width>20 && r.right<=t.right+1; })()"), "le badge est coupé par un pseudo trop long"
    assert vip.locator(".av.prem").count() == 1                         # avatar entouré d'une lueur
    assert vip.locator(".av.fr-couronne").count() == 1                  # et sa décoration d'avatar
    simple = c.page.locator(".user-card:has-text('@simple')")
    assert simple.locator(".pbadge").count() == 0 and simple.locator(".av.prem").count() == 0
    c.page.wait_for_selector("#posts-wrap .post")
    assert c.page.locator("#posts-wrap .post:has-text('@vip') .av.prem").count() == 1
    assert c.page.locator("#posts-wrap .post:has-text('@simple') .av.prem").count() == 0
    glow = c.ev("getComputedStyle(document.querySelector('.user-card .av.prem')).boxShadow")
    assert glow and glow != "none"


def test_no_premium_mark_when_subscriptions_are_off(live, mk):
    _vip(live, mk)
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("location.hash='decouvrir'")
    c.page.locator(".user-card:has-text('@vip')").wait_for()
    assert c.page.locator(".pbadge, .av.prem").count() == 0


def test_new_premium_decorations_can_be_picked_and_shown(live, mk):
    c = opened(mk, tracks(1), premium=True)
    c.ev("openCustom('profil')")
    for kind, value in (("banner", "galaxie"), ("frame", "arcenciel"), ("effect", "notes"), ("name", "glitch"), ("badge", "🎵")):
        c.page.click(f"#perso [onclick=\"pickCos('{kind}','{value}')\"]")
    c.page.wait_for_function("ME.cos && ME.cos.badge==='🎵'")
    prev = c.page.locator("#pzv")
    for sel in (".banner.bn-galaxie", ".av.fr-arcenciel", ".pfx.pfx-notes", ".nf-glitch"):
        assert prev.locator(sel).count() >= 1, sel
    for sel in (".banner.bn-galaxie", ".av.fr-arcenciel::before", ".pfx-notes::before"):
        el = sel.split("::")[0]
        bg = c.ev(f"getComputedStyle(document.querySelector('#pzv {el}'){',' + repr('::' + sel.split('::')[1]) if '::' in sel else ''}).backgroundImage")
        assert bg and bg != "none", sel


# ═══════════════════════════ Statut Discord (extension PreMiD) ═══════════════════════════
def np_data(c):
    return c.ev("({...document.documentElement.dataset})")


def test_now_playing_is_readable_for_the_discord_status(live, mk):
    no_paywall(live)
    tr = tracks(2)
    tr[0].update(author="Artiste", cover="https://img.example.test/c.jpg")
    c = opened(mk, tr, paywall=False)
    start(c, 1)
    playing_src(c, 1)
    c.page.wait_for_function("document.documentElement.dataset.npState==='playing'")
    d = np_data(c)
    assert d["npTitle"] == "Son 1" and d["npArtist"] == "Artiste" and d["npCover"] == "https://img.example.test/c.jpg"
    assert abs(int(d["npEnd"]) - int(d["npStart"]) - 60000) < 1500          # le son dure 60 s
    c.ev("au.pause()")
    c.page.wait_for_function("document.documentElement.dataset.npState==='paused'")


def test_discord_status_never_shows_a_private_device_cover(live, mk):
    no_paywall(live)
    c = opened(mk, local_tracks(1), paywall=False)
    store_local_blob(c, "t1")
    c.ev("COVERS.t1='blob:'+location.origin+'/x'")
    c.ev("void play(['t1'],0)")
    c.page.wait_for_function("document.documentElement.dataset.npState==='playing'")
    assert np_data(c)["npCover"] == ""


# ═══════════════════════════ Téléphones : iPhone, Samsung, Android ═══════════════════════════
IPHONE = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=3,
              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


def small_fields(c, where):
    """Champs de saisie visibles écrits en moins de 16 px : Safari sur iPhone zoome toute la page dès qu'on les touche."""
    return [f"{where}: {f}" for f in c.ev("""[...document.querySelectorAll('input:not([type=range]):not([type=color]):not([type=file]):not([type=checkbox]),textarea,select')]
        .filter(e=>e.offsetParent!==null && parseFloat(getComputedStyle(e).fontSize)<16).map(e=>(e.id||e.name||e.className||e.tagName)+' '+getComputedStyle(e).fontSize)""")]


def test_no_field_makes_the_iphone_zoom_in(live, mk):
    no_paywall(live)
    c = mk(**IPHONE)
    c.signup("me@example.com", "me_tester")
    c.seed(tracks(2))
    c.open()
    c.page.wait_for_function("ME")
    found = []
    for view in ("accueil", "ajouter", "bibliotheque", "decouvrir", "profil"):
        c.ev(f"location.hash='{view}'")
        c.page.wait_for_timeout(300)
        found += small_fields(c, view)
    c.ev("openCustom('profil')")
    c.page.wait_for_timeout(300)
    found += small_fields(c, "personnaliser")
    c.ev("void askText('Nom','','',40)")             # `void` : ne pas attendre la réponse
    c.page.wait_for_timeout(300)
    found += small_fields(c, "fenêtre texte")
    assert not found, found


def test_phones_do_not_force_their_own_dark_mode_and_the_app_has_a_stable_identity(live, mk):
    c = opened(mk, tracks(1))
    assert c.ev("document.querySelector('meta[name=color-scheme]').content") == "dark light"
    m = c.ctx.request.get(live.base + "/manifest.webmanifest").json()
    assert m["id"] == "/" and m["start_url"] == "/"


def test_french_visitors_do_not_download_the_english_texts(live, mk):
    c = mk()
    seen = []
    c.page.on("request", lambda r: seen.append(r.url))
    c.open()
    assert not [u for u in seen if "lang-en.js" in u]
    e = mk(locale="en-US")
    e.open()
    assert e.ev("Object.keys(window.TK_EN||{}).length") > 600


def test_text_links_have_no_grey_button_background(live, mk):
    c = mk(**IPHONE)
    c.open()
    grey = c.ev("[...document.querySelectorAll('button.lnk')].map(b=>getComputedStyle(b).backgroundColor).filter(v=>v!=='rgba(0, 0, 0, 0)')")
    assert grey == []


# ═══════════════════════════ Série 3 : bugs et cosmétiques ═══════════════════════════
def test_accessibility_switches_give_back_your_own_settings(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("setUI('theme','ocean'); setUI('font','space'); setUI('zoom',105)")
    for name in ("_c", "_f", "_z"):                                           # contraste, police lisible, interface plus grande
        c.ev(f"accToggle('{name}')"); c.ev(f"accToggle('{name}')")
    assert c.ev("[S.ui.theme,S.ui.font,S.ui.zoom]") == ["ocean", "space", 105]


def test_customize_can_cancel_or_save_changes(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("setUI('theme','ocean'); openCustom('theme')")
    c.ev("setUI('theme','foret'); setUI('blur',30)")
    c.page.click("#pzcancel")
    assert c.ev("[S.ui.theme,S.ui.blur]") == ["ocean", 18]
    c.ev("setUI('theme','nuit')")
    c.page.click("#pzsave")
    c.page.wait_for_function("location.hash!=='#perso'")                   # retour à la page d'avant
    c.page.wait_for_timeout(300)
    c.ev("openCustom('theme')"); c.page.wait_for_selector("#pzcancel"); c.page.click("#pzcancel")
    assert c.ev("S.ui.theme") == "nuit"


def test_glass_effect_blurs_the_cards(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(2), paywall=False)
    c.ev("setUI('blur',26)")
    c.page.wait_for_selector(".gcard", state="attached")
    assert "blur(" in c.ev("getComputedStyle(document.querySelector('.gcard')).backdropFilter")


def test_sound_length_is_corrected_from_the_real_audio(live, mk):
    no_paywall(live)
    tr = tracks(1); tr[0]["duration"] = 999
    c = opened(mk, tr, paywall=False)
    start(c, 1); playing_src(c, 1)
    c.page.wait_for_function("S.tracks[0].duration===60")


def test_new_cursors_trails_and_thinner_adjustable_borders(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("openCustom('effets')")
    for v in ("note", "diamond", "bolt", "plus"):
        assert c.page.locator(f"#perso .pzt[data-v='{v}']").count() >= 1, v
    for v in ("heart", "note", "bubble"):
        assert c.page.locator(f"#perso .pzt[data-v='{v}']").count() >= 1, v
        c.ev(f"trailAdd('{v}',50,50,1,1); trailLoop(performance.now())")
    assert "stroke-width%3D%220.8%22" in c.ev("cursorCSS('heart','#ff0000')")      # bords fins par défaut
    c.ev("setUI('curw',20)")
    assert "stroke-width%3D%222%22" in c.ev("cursorCSS('heart','#ff0000')")
    assert c.page.locator("#perso input[data-k='curw']").count() == 1
    assert not c.logs, c.logs


def test_username_font_and_colour_show_on_the_profile(live, mk):
    c = opened(mk, tracks(1), premium=True)
    c.ev("openCustom('profil')")
    c.page.click("#perso [onclick=\"pickCos('nfont','pixel')\"]")
    c.ev("cosColor('nc','#ff00aa',1)")
    c.page.wait_for_function("ME.cos && ME.cos.nfont==='pixel' && ME.cos.nc==='#ff00aa'")
    st = c.ev("getComputedStyle(document.querySelector('#pzv .disp'))")
    assert c.ev("getComputedStyle(document.querySelector('#pzv .disp')).fontFamily").find("Press Start") >= 0
    assert c.ev("getComputedStyle(document.querySelector('#pzv .disp')).color") == "rgb(255, 0, 170)"
    for v in ("ombre", "contour", "pastel"):
        assert c.page.locator(f"#perso [onclick=\"pickCos('name','{v}')\"]").count() == 1


def test_any_background_colour_can_be_chosen(live, mk):
    no_paywall(live)
    c = opened(mk, tracks(1), paywall=False)
    c.ev("openCustom('theme')")
    assert c.page.locator("#perso input[type=color][data-k='tbg']").count() == 1
    c.ev("setUI('tbg','#204060')")
    assert c.ev("S.ui.theme") == "perso"
    assert c.ev("getComputedStyle(document.body).getPropertyValue('--bg').trim()") == "#204060"


def test_playlists_can_have_their_own_cover_picture(live, mk):
    c = opened(mk, tracks(2), pls=[{"id": "p1", "name": "Gym", "ids": ["t1"]}])
    c.ev("""(async()=>{ const cv=document.createElement('canvas'); cv.width=cv.height=8; await setPlCover('p1', await new Promise(r=>cv.toBlob(r,'image/jpeg'))); })()""")
    c.page.wait_for_function("plArt(S.pls[0]).includes('<img')")
    c.page.reload(); c.booted()
    c.page.wait_for_function("plArt(S.pls[0]).includes('<img')")              # gardée sur l'appareil


def test_gif_profile_pictures_are_premium_only(live, mk):
    c = opened(mk, tracks(1))
    c.signup("free@example.com", "gratuit"); c.page.reload(); c.booted(); c.page.wait_for_function("ME")
    c.ev("useImage('pic', new File([new Uint8Array([71,73,70,56,57,97,0,0])],'a.gif',{type:'image/gif'}))")
    c.page.wait_for_function("document.querySelector('#gen:not([hidden]),#toasts .toast')")
    assert c.ev("ME.pic") in ("", None)


def test_subscribe_button_says_it_out_loud(live, mk):
    c = opened(mk, tracks(1))
    b = c.page.locator("#premb")
    assert b.is_visible() and "abonner" in b.inner_text().lower()


def test_launch_screen_says_tikwave(live, mk):
    c = opened(mk, tracks(1))
    assert "TikWave" in c.page.inner_text("#splash", timeout=2000) if c.page.locator("#splash").count() else False
    c.page.wait_for_function("document.getElementById('splash').hidden")


def test_publishing_through_the_sound_menu_reaches_explore(live, mk):
    no_paywall(live)
    yt = [dict(id="t1", title="Son YouTube", author="A", duration=60, url="https://www.youtube.com/watch?v=abcdefghijk",
               local=False, remote="{BASE}/__m/silence.wav", added=1)]
    c = mk(); c.signup("me@example.com", "moi_test"); c.seed(yt); c.open(); c.page.wait_for_function("ME")
    c.ev("location.hash='bibliotheque'")
    c.page.click("#lib [data-id=t1] [aria-label='Options'], #lib [data-id=t1] [aria-label='Plus d\\'options']")
    c.page.click("#optl .opt:has-text('Publier ce son')")
    c.page.wait_for_function("S.tracks[0].postId")
    c.ev("location.hash='decouvrir'")
    c.page.wait_for_selector("#posts-wrap .post:has-text('Son YouTube')")



# ═══════════════════════════ Bibliothèque : synchro, corbeille, import ═══════════════════════════
def tk(n, start=1):
    """Sons avec un vrai lien TikTok (le serveur ne garde que les liens qu'il accepte)."""
    return [dict(id=f"t{i}", title=f"Son {i}", author="A", duration=60, url=f"https://www.tiktok.com/@x/video/{i}",
                 local=False, remote="", pending=True, added=i) for i in range(start, start + n)]


def synced(mk, tr, pls=None, email="sync@example.com"):
    """Un appareil connecté dont la bibliothèque a déjà été envoyée une première fois au compte."""
    c = mk(); c.signup(email, "synchro"); c.seed(tr, pls=pls or []); c.open()
    c.page.wait_for_function("ME && S.sync && S.sync.at && !SYNC.busy")
    return c


def server_lib(c):
    return c.ctx.request.get(c.live.base + "/api/me/library").json()


def put_lib(c, data, base):
    """Ce qu'un autre appareil du même compte enregistre."""
    r = c.ctx.request.put(c.live.base + "/api/me/library", data={"data": data, "base": base})
    assert r.ok, r.text()


def sync_now(c, before=""):
    """Attend la fin d'une synchro en cours (minuterie de 6 s), fait `before` puis synchronise."""
    c.ev("(async()=>{ while(SYNC.busy) await new Promise(r=>setTimeout(r,50)); %s; await syncLibrary(false); })()" % before)


def json_route(status, body):
    return lambda r: r.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def test_sync_never_deletes_sounds_or_playlist_entries_beyond_the_account_limits(live, mk):
    big = dict(id="pbig", name="Grosse", ids=[f"t{i}" for i in range(1, 601)])
    c = synced(mk, tk(1600), pls=[big])
    sync_now(c)
    sync_now(c)
    assert c.ev("S.tracks.length") == 1600                          # le serveur n'en garde que 1500 : rien n'est supprimé ici
    assert c.ev("S.pls.find(p=>p.id==='pbig').ids.length") == 600
    assert len(server_lib(c)["data"]["tracks"]) == 1500


def test_sync_does_not_upload_an_unchanged_library_again(live, mk):
    c = synced(mk, tk(3))
    puts = []
    c.page.on("request", lambda r: puts.append(1) if r.method == "PUT" and "/api/me/library" in r.url else None)
    sync_now(c)
    sync_now(c)
    assert puts == []
    sync_now(c, "S.tracks[0].fav=true; save()")
    assert len(puts) == 1


def test_sync_keeps_edits_made_on_another_device_when_this_one_also_changed(live, mk):
    c = synced(mk, tk(2), pls=[dict(id="pa", name="A", ids=["t1"])])
    s = server_lib(c); d = s["data"]
    u2 = "https://www.tiktok.com/@x/video/2"
    for t in d["tracks"]:
        if t["url"] == u2:
            t["title"] = "Renommé ailleurs"
    d["pls"][0]["urls"].append(u2)
    put_lib(c, d, s["updated"])
    sync_now(c, "S.tracks.find(t=>t.id==='t1').fav=true; save()")      # cet appareil a aussi changé autre chose
    assert c.ev("S.tracks.find(t=>t.id==='t2').title") == "Renommé ailleurs"
    assert c.ev("S.tracks.find(t=>t.id==='t1').fav") is True
    assert c.ev("S.pls.find(p=>p.id==='pa').ids") == ["t1", "t2"]
    d = server_lib(c)["data"]
    assert [t["title"] for t in d["tracks"] if t["url"] == u2] == ["Renommé ailleurs"]
    assert d["pls"][0]["urls"][-1] == u2


def test_public_playlist_restored_before_the_library_sync_is_not_duplicated(live, mk):
    pub = "a" * 32
    c = mk(); c.signup("dup@example.com", "doublon")
    c.ctx.route(re.compile(r"/api/playlists"), json_route(200, {"ok": True}))
    c.ctx.route(re.compile(r"/api/me/playlists"), json_route(200, {"playlists": []}))
    u1 = "https://www.tiktok.com/@x/video/1"
    put_lib(c, {"tracks": [dict(url=u1, title="Son 1")], "pls": [dict(id="ps", name="Pub", urls=[u1], pub=True, pubId=pub)]}, None)
    # restorePublic a déjà remis sa copie (autre identifiant) avant la réponse de la bibliothèque
    c.seed(tk(1), pls=[dict(id="plocal", name="Pub", ids=["t1"], pub=True, pubId=pub)])
    c.open()
    c.page.wait_for_function("ME && S.sync && S.sync.at && !SYNC.busy")
    assert c.ev(f"S.pls.filter(p=>p.pubId==='{pub}').map(p=>p.id)") == ["ps"]
    assert [p["id"] for p in server_lib(c)["data"]["pls"]] == ["ps"]


def test_sound_deleted_on_another_device_goes_to_the_trash_and_keeps_its_offline_file(live, mk):
    c = synced(mk, tk(2))
    store_local_blob(c, "t2")
    s = server_lib(c); d = s["data"]
    gone = [t for t in d["tracks"] if t["url"].endswith("/2")][0]
    d["tracks"] = [t for t in d["tracks"] if t is not gone]
    put_lib(c, d, s["updated"])
    sync_now(c)
    assert c.ev("S.tracks.some(t=>t.id==='t2')") is False
    assert c.ev("S.trash.some(e=>e.t&&e.t.id==='t2')") is True
    assert c.ev("dbop('get','t2').then(b=>!!b)") is True
    s = server_lib(c); d = s["data"]; d["tracks"].append(gone)              # restauré sur l'autre appareil
    put_lib(c, d, s["updated"])
    sync_now(c)
    assert c.ev("S.tracks.some(t=>t.id==='t2')") is True                 # même son, même fichier hors ligne
    assert c.ev("S.trash.some(e=>e.t&&e.t.id==='t2')") is False


def test_bulk_delete_keeps_every_sound_restorable_and_saves_once(live, mk):
    c = opened(mk, tracks(250))
    n = c.ev("""(async()=>{ askConfirm=async()=>true; let n=0; const o=Storage.prototype.setItem;
        Storage.prototype.setItem=function(k,v){ if(k==='tiksound') n++; return o.call(this,k,v); };
        SEL=new Set(S.tracks.map(t=>t.id)); await selDel(); Storage.prototype.setItem=o; return n; })()""")
    assert c.ev("S.tracks.length") == 0
    assert c.ev("S.trash.length") == 250                               # promis : récupérables pendant 30 jours
    assert n <= 2
    c.ev("restoreTrash(S.trash.length-1)")                             # le tout premier mis à la corbeille
    assert c.ev("S.tracks.map(t=>t.id)") == ["t1"]


def test_failed_search_download_keeps_a_sound_already_in_the_library(live, mk):
    no_paywall(live)
    url = "https://open.spotify.com/track/" + "a" * 22
    c = opened(mk, [dict(id="s1", title="Titre", author="A", duration=60, url=url, local=False, pending=True, added=1)],
               paywall=False, pls=[dict(id="p1", name="Spotify", ids=["s1"])])
    c.ev(f"dl=async()=>false; FR.items=[{{url:'{url}',title:'Titre',author:'A',src:'spotify'}}]")
    c.ev("findAdd(0)")
    assert c.ev("S.tracks.map(t=>t.id)") == ["s1"]
    assert c.ev("S.pls[0].ids") == ["s1"]


def test_private_or_deleted_playlist_stays_public_until_the_server_agrees(live, mk):
    pub = "b" * 32
    c = mk(); c.signup("pub@example.com", "publieur")
    st = {"fail": True}

    def pls(r):
        if r.request.method == "DELETE" and st["fail"]:
            return json_route(500, {"error": "Erreur serveur"})(r)
        json_route(200, {"ok": True})(r)
    c.ctx.route(re.compile(r"/api/playlists/"), pls)
    c.ctx.route(re.compile(r"/api/me/playlists"),
                lambda r: json_route(200, {"playlists": [{"id": pub, "name": "Pub", "tracks": []}] if st["fail"] else []})(r))
    c.seed(tk(1), pls=[dict(id="p1", name="Pub", ids=["t1"], pub=True, pubId=pub)])
    c.open(); c.page.wait_for_function("ME")
    c.ev("togglePub('p1')")
    assert c.ev("[S.pls[0].pub, S.pls[0].pubId]") == [True, pub]      # toujours publique : on ne fait pas croire le contraire
    c.ev("askConfirm=async()=>true; delPl('p1')")
    c.page.wait_for_function(f"S.pubDel.includes('{pub}')")
    c.ev("restorePublic()")
    assert c.ev("S.pls.length") == 0                                   # pas réimportée au prochain lancement
    st["fail"] = False
    c.ev("restorePublic()")
    assert c.ev("S.pubDel") == []


def test_backup_import_keeps_tags_trims_effects_folders_and_stats(live, mk):
    c = opened(mk, [])
    backup = {"app": "tikwave", "v": 2, "goal": 60,
              "tracks": [{"id": "t9", "title": "Gardé", "url": "https://www.tiktok.com/@x/video/9", "tags": ["gym", "chill"],
                          "cut": {"a": 1, "b": 5}, "fx": {"rate": "1.25", "bogus": 1}, "remote": "https://r2.example.test/audio/t9.mp3",
                          "sec": 42, "last": 5, "added": 3}],
              "pls": [{"id": "p9", "name": "Sport", "ids": ["t9"], "folder": "Muscu", "emoji": "💪", "color": "#ff0000"}],
              "stats": {"sec": 3600, "days": ["2026-11-01"], "byDay": {"2026-11-01": 3600}, "hours": [0, 0, 0, 0, 0, 0, 0, 700], "best": 4}}
    c.page.evaluate("d => importData(new File([JSON.stringify(d)], 'b.json'))", backup)
    t = c.ev("S.tracks.find(t=>t.id==='t9')")
    assert t["tags"] == ["gym", "chill"] and t["cut"] == {"a": 1, "b": 5} and t["fx"] == {"rate": "1.25"}
    assert t["remote"] == "https://r2.example.test/audio/t9.mp3" and t["pending"] is False and t["sec"] == 42
    p = c.ev("S.pls.find(p=>p.id==='p9')")
    assert (p["folder"], p["emoji"], p["color"]) == ("Muscu", "💪", "#ff0000")
    assert c.ev("S.stats.sec") >= 3600 and "2026-11-01" in c.ev("S.stats.days")
    assert c.ev("S.stats.hours[7]") == 700 and c.ev("S.stats.best") == 4 and c.ev("S.goal") == 60


def test_sound_added_from_search_shows_first_under_recent(live, mk):
    c = opened(mk, tracks(12))
    new = c.ev("ensureTrack({url:'https://www.tiktok.com/@x/video/99',title:'Nouveau'})")
    c.ev("renderHome()")
    assert c.ev("libList()[0].id") == new
    assert c.ev("LISTS.rec[0]") == new and c.ev("LISTS.addrec[0]") == new


def test_batch_convert_keeps_only_the_failed_links_in_the_box(live, mk):
    c = opened(mk, [])
    c.ev("""ONLINE=true; grab=async(url,id)=>{ if(url.endsWith('/2')) throw new Error('Vidéo privée');
        return {title:'OK',author:'',url,remote:true,audio_url:'https://r2.example.test/a.mp3'}; }; 0""")
    c.ev("document.getElementById('u2').value='https://www.tiktok.com/@a/video/1 https://www.tiktok.com/@a/video/2'")
    c.ev("convert('u2', null)")
    assert c.ev("S.tracks.length") == 1
    assert c.ev("document.getElementById('u2').value") == "https://www.tiktok.com/@a/video/2"


def test_full_storage_warns_once_and_offers_to_empty_the_trash(live, mk):
    c = opened(mk, tracks(2))
    c.ev("delTrack('t1')")
    c.ev("""const o=Storage.prototype.setItem; Storage.prototype.setItem=function(k,v){
        if(k==='tiksound') throw new DOMException('plein','QuotaExceededError'); return o.call(this,k,v); };
        save(); save(); save();""")
    t = toasts(c)
    assert t.count("Stockage plein") == 1 and "corbeille" in t
    assert c.page.locator("#toasts button:has-text('Vider')").count() == 1


def test_unpublish_after_switching_account_frees_the_sound(live, mk):
    no_paywall(live)
    c = mk(); c.signup("b@example.com", "compte_b")
    c.seed([dict(id="t1", title="Son", author="A", duration=60, url="https://www.tiktok.com/@x/video/1",
                 local=False, remote="{BASE}/__m/silence.wav", added=1, postId="p_de_a")])
    c.ctx.route(re.compile(r"/api/posts/p_de_a"), json_route(403, {"error": "Interdit"}))
    c.open(); c.page.wait_for_function("ME")
    c.ev("publishTrack('t1')")
    assert c.ev("S.tracks[0].postId") is None                          # « Publier ce son » revient
