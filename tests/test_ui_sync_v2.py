"""Nouvelles options d'apparence : le serveur doit les accepter (synchro entre appareils) et rejeter le reste."""


def test_new_appearance_options_are_synced(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    ui = {"theme": "foret", "bg": "etoiles", "font": "playfair", "cover": "round", "card": "line", "npbg": "accent",
          "accent2": "#FF00AA", "autoAcc": True, "viz": False, "navl": False, "zoom": 115,
          "home": {"steps": False, "tip": False, "inconnu": False, "hello": "oui"}}
    c.patch("/api/auth/me", json={"ui": ui})
    got = c.get("/api/auth/me").get_json()["ui"]
    assert got == {"theme": "foret", "bg": "etoiles", "font": "playfair", "cover": "round", "card": "line",
                   "npbg": "accent", "accent2": "#ff00aa", "autoAcc": True, "viz": False, "navl": False,
                   "zoom": 115, "home": {"steps": False, "tip": False}}


def test_bad_values_are_dropped(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    c.patch("/api/auth/me", json={"ui": {"theme": "pirate", "accent2": "red", "zoom": 999, "home": "tout",
                                         "card": "<b>", "accent": "#123456"}})
    assert c.get("/api/auth/me").get_json()["ui"] == {"zoom": 130, "accent": "#123456"}


def test_accent2_can_be_cleared(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    c.patch("/api/auth/me", json={"ui": {"accent2": "#abcdef"}})
    c.patch("/api/auth/me", json={"ui": {"accent2": ""}})
    assert c.get("/api/auth/me").get_json()["ui"] == {"accent2": ""}
