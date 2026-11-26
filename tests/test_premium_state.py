import time


def now():
    return int(time.time())


def set_user(app, email, **cols):
    sets = ",".join(f"{k}=:{k}" for k in cols)
    app.x(f"UPDATE users SET {sets} WHERE lower(email)=:e", e=email.lower(), **cols)


def me(c):
    return c.get("/api/auth/me").get_json()


def test_free_user_not_premium(app, make_client, signup):
    c = make_client()
    m = signup(c, "a@example.com", "alice")
    assert m["premium"] is False and m["premium_until"] == 0 and m["premium_exp"] < now()
    assert m["plan_status"] == "" and m["cancel_at_end"] is False and m["is_admin"] is False


def test_admin_email_case_insensitive(app, make_client, signup):
    assert app.parse_admin_emails(" Boss@Example.com , other@x.fr ,") == {"boss@example.com", "other@x.fr"}
    app.ADMIN_EMAILS = app.parse_admin_emails("Boss@Example.com")
    m = signup(make_client(), "Boss@Example.com", "boss")
    assert m["premium"] is True and m["is_admin"] is True and m["premium_src"] == "admin"
    assert signup(make_client(), "other@example.com", "autre")["is_admin"] is False


def test_grace_period(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", premium_until=now() - 3600)
    m = me(c)
    assert m["premium"] is True and m["premium_src"] == "stripe"
    assert abs(m["premium_until"] - (now() - 3600)) <= 1          # date réelle, sans la marge
    set_user(app, "a@example.com", premium_until=now() - 49 * 3600)
    assert me(c)["premium"] is False


def test_gift_forever_and_expiry(app, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", gift_until=app.FOREVER)
    m = me(c)
    assert m["premium"] is True and m["premium_exp"] == app.FOREVER and m["premium_src"] == "key"
    set_user(app, "a@example.com", gift_until=now() - 10)
    assert me(c)["premium"] is False


def test_public_profile_only_boolean(app, make_client, signup):
    signup(make_client(), "a@example.com", "alice")
    set_user(app, "a@example.com", gift_until=app.FOREVER)
    pub = make_client().get("/api/users/alice").get_json()
    assert pub["premium"] is True
    for k in ("email", "premium_until", "premium_exp", "plan_status", "is_admin", "premium_src"):
        assert k not in pub


def test_paywall_needs_stripe_and_r2(app):
    for key, price, r2, pub, expected in [("k", "p", True, "pk", True), ("", "p", True, "pk", False),
                                          ("k", "", True, "pk", False), ("k", "p", False, "pk", False),
                                          ("k", "p", True, "", False)]:        # sans clé publique, le formulaire de paiement ne peut pas s'afficher
        app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK, app.STRIPE_PUB = key, price, r2, pub
        assert app.paywall_on() is expected


def test_health_reports_paywall(app, make_client):
    c = make_client()
    assert c.get("/api/health").get_json()["paywall"] is False
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "k", "p", True
    assert c.get("/api/health").get_json()["paywall"] is True


def test_premium_info_disabled_and_enabled(app, make_client, monkeypatch):
    c = make_client()
    assert c.get("/api/premium").get_json() == {"enabled": False, "price": None, "publishable_key": None}
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "k", "p", True
    calls = []
    monkeypatch.setattr(app, "stripe_get_price", lambda: calls.append(1) or {"amount": 299, "currency": "eur", "interval": "month"})
    j = c.get("/api/premium").get_json()
    assert j == {"enabled": True, "price": {"amount": 299, "currency": "eur", "interval": "month"}, "publishable_key": "pk_test_default"}
    c.get("/api/premium")
    assert len(calls) == 1                                   # mis en cache


def test_premium_info_survives_stripe_outage(app, make_client, monkeypatch):
    app.STRIPE_KEY, app.STRIPE_PRICE, app.R2_OK = "k", "p", True

    def boom():
        raise RuntimeError("stripe indisponible")
    monkeypatch.setattr(app, "stripe_get_price", boom)
    assert make_client().get("/api/premium").get_json() == {"enabled": True, "price": None, "publishable_key": "pk_test_default"}


def test_migration_adds_columns_to_old_db(app):
    app.x("DROP TABLE users")
    app.x("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, username TEXT UNIQUE NOT NULL, "
          "pw_hash TEXT NOT NULL, color INTEGER DEFAULT 0, created TEXT, bio TEXT DEFAULT '', avatar TEXT DEFAULT '')")
    app.x("INSERT INTO users (id,email,username,pw_hash) VALUES ('1','old@x.fr','old','h')")
    app.init_db()
    cols = {c["name"] for c in app.inspect(app.engine).get_columns("users")}
    assert {"premium_until", "plan_status", "cancel_at_end", "stripe_customer_id", "stripe_sub_id",
            "gift_until", "gift_key_id"} <= cols
    row = app.q1("SELECT * FROM users WHERE id='1'")
    assert app.premium_state(row)["premium"] is False        # anciens comptes : gratuits, pas d'erreur
    assert app.q1("SELECT COUNT(*) AS n FROM stripe_events").n == 0
    assert app.q1("SELECT COUNT(*) AS n FROM access_keys").n == 0
