"""Pubs rémunérées (Google AdSense) : configuration par variables d'environnement, ads.txt, page de confidentialité."""
import pytest

CLIENT = "ca-pub-1234567890123456"
SLOT = "1234567890"
ADS_TXT = "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n"


def paywall(app, on=True):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = ("k", "p", True) if on else ("", "", False)


def test_ads_txt_is_404_without_client(make_client):
    assert make_client().get("/ads.txt").status_code == 404


def test_ads_txt_declares_google_as_direct_seller(make_client, monkeypatch):
    monkeypatch.setenv("ADSENSE_CLIENT", CLIENT)
    r = make_client().get("/ads.txt")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    assert r.get_data(as_text=True) == ADS_TXT


@pytest.mark.parametrize("raw", ["pub-1234567890123456", "  ca-pub-1234567890123456  ", "CA-PUB-1234567890123456"])
def test_client_id_is_normalised(make_client, monkeypatch, raw):
    monkeypatch.setenv("ADSENSE_CLIENT", raw)
    assert make_client().get("/ads.txt").get_data(as_text=True) == ADS_TXT


@pytest.mark.parametrize("bad", ["", "ca-pub-12", "ca-pub-123456789012345a", "<script>alert(1)</script>",
                                 "ca-pub-1234567890123456\nevil.example, 99, DIRECT",
                                 "pub-1234567890123456, evil.example"])
def test_malformed_client_is_ignored_everywhere(app, make_client, monkeypatch, bad):
    monkeypatch.setenv("ADSENSE_CLIENT", bad)
    monkeypatch.setenv("ADSENSE_SLOT", SLOT)
    paywall(app)
    c = make_client()
    assert c.get("/ads.txt").status_code == 404
    assert c.get("/api/health").get_json()["adsense"] is None


def test_ads_txt_served_even_while_paywall_is_off(app, make_client, monkeypatch):
    """Google doit pouvoir vérifier le site avant que l'abonnement ne soit branché."""
    monkeypatch.setenv("ADSENSE_CLIENT", CLIENT)
    paywall(app, False)
    assert make_client().get("/ads.txt").status_code == 200


def test_health_adsense_is_null_by_default(make_client):
    assert make_client().get("/api/health").get_json()["adsense"] is None


def test_health_adsense_exposes_client_and_slot_when_paywall_on(app, make_client, monkeypatch):
    monkeypatch.setenv("ADSENSE_CLIENT", CLIENT)
    monkeypatch.setenv("ADSENSE_SLOT", SLOT)
    paywall(app)
    assert make_client().get("/api/health").get_json()["adsense"] == {"client": CLIENT, "slot": SLOT}


def test_health_adsense_hidden_while_paywall_is_off(app, make_client, monkeypatch):
    """Sans abonnement, personne ne peut retirer les pubs : on n'en affiche pas."""
    monkeypatch.setenv("ADSENSE_CLIENT", CLIENT)
    monkeypatch.setenv("ADSENSE_SLOT", SLOT)
    paywall(app, False)
    assert make_client().get("/api/health").get_json()["adsense"] is None


@pytest.mark.parametrize("slot", ["", "abc", "12", "123456789012345678901", "1234567890; alert(1)"])
def test_health_adsense_needs_a_valid_slot(app, make_client, monkeypatch, slot):
    monkeypatch.setenv("ADSENSE_CLIENT", CLIENT)
    monkeypatch.setenv("ADSENSE_SLOT", slot)
    paywall(app)
    c = make_client()
    assert c.get("/api/health").get_json()["adsense"] is None
    assert c.get("/ads.txt").status_code == 200


def test_privacy_page_describes_real_processors(make_client):
    r = make_client().get("/confidentialite")
    assert r.status_code == 200 and r.mimetype == "text/html"
    html = r.get_data(as_text=True)
    for word in ("Politique de confidentialité", "Stripe", "Google AdSense", "supprimer ton compte"):
        assert word in html, word


def test_privacy_page_shows_contact_only_when_valid(make_client, monkeypatch):
    c = make_client()
    assert "mailto:" not in c.get("/confidentialite").get_data(as_text=True)
    monkeypatch.setenv("CONTACT_EMAIL", "moi@example.com")
    assert 'href="mailto:moi@example.com"' in c.get("/confidentialite").get_data(as_text=True)
    monkeypatch.setenv("CONTACT_EMAIL", '"><script>alert(1)</script>')
    html = c.get("/confidentialite").get_data(as_text=True)
    assert "<script>alert" not in html and "mailto:" not in html
