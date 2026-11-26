import hashlib
import hmac
import json
import time

import pytest

SECRET = "whsec_test"


def now():
    return int(time.time())


def sign(payload: bytes, secret=SECRET, t=None):
    t = now() if t is None else t
    sig = hmac.new(secret.encode(), str(t).encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


# ───────────────────────── signature ─────────────────────────
def test_signature_valid(app):
    p = b'{"id":"evt_1"}'
    assert app.verify_stripe_signature(p, sign(p), SECRET) is True


def test_signature_wrong_secret(app):
    p = b'{"id":"evt_1"}'
    assert app.verify_stripe_signature(p, sign(p, "autre"), SECRET) is False


def test_signature_tampered_payload(app):
    p = b'{"id":"evt_1"}'
    assert app.verify_stripe_signature(b'{"id":"evt_2"}', sign(p), SECRET) is False


def test_signature_too_old(app):
    p = b"{}"
    assert app.verify_stripe_signature(p, sign(p, t=now() - 301), SECRET) is False
    assert app.verify_stripe_signature(p, sign(p, t=now() - 299), SECRET) is True
    assert app.verify_stripe_signature(p, sign(p, t=now() + 400), SECRET) is False     # horloge dans le futur


def test_signature_second_v1_valid(app):
    p = b"{}"
    t = now()
    good = sign(p, t=t).split("v1=")[1]
    assert app.verify_stripe_signature(p, f"t={t},v1={'0' * 64},v1={good},v0=zz", SECRET) is True


def test_signature_malformed_or_missing(app):
    p = b"{}"
    for h in ("", None, "garbage", "t=abc,v1=00", f"t={now()}", "v1=00", f"t={now()},v1=é"):
        assert app.verify_stripe_signature(p, h, SECRET) is False
    assert app.verify_stripe_signature(p, sign(p, ""), "") is False                    # secret vide : jamais valide


# ───────────────────────── helpers webhook ─────────────────────────
@pytest.fixture
def S(app, monkeypatch):
    """Stripe configuré ; abonnement « chez Stripe » modifiable par les tests."""
    app.STRIPE_KEY, app.STRIPE_PRICE, app.STRIPE_WH, app.R2_OK = "sk_test", "price_1", SECRET, True
    st = {"sub": None, "fail": False, "retrieved": []}

    def get_sub(sub_id):
        st["retrieved"].append(sub_id)
        if st["fail"]:
            raise RuntimeError("Stripe indisponible")
        return st["sub"]
    monkeypatch.setattr(app, "stripe_get_subscription", get_sub)
    return st


def subscription(status="active", end=None, where="top", cust="cus_1", sid="sub_1", cancel=False):
    end = end or now() + 30 * 86400
    s = {"object": "subscription", "id": sid, "customer": cust, "status": status, "cancel_at_period_end": cancel,
         "metadata": {}}
    if where == "top":
        s["current_period_end"] = end
    else:
        s["items"] = {"data": [{"current_period_end": end}]}
    return s


def event(eid, etype, obj):
    return {"id": eid, "type": etype, "data": {"object": obj}}


def post_event(client, ev, secret=SECRET, t=None, raw=None):
    payload = raw if raw is not None else json.dumps(ev).encode()
    return client.post("/api/stripe/webhook", data=payload, content_type="application/json",
                       headers={"Stripe-Signature": sign(payload, secret, t)})


def user_row(app, email="a@example.com"):
    return app.q1("SELECT * FROM users WHERE email=:e", e=email)


def set_user(app, email, **cols):
    sets = ",".join(f"{k}=:{k}" for k in cols)
    app.x(f"UPDATE users SET {sets} WHERE email=:e", e=email, **cols)


@pytest.fixture
def subscriber(app, make_client, signup):
    """Utilisateur alice, déjà client Stripe cus_1."""
    c = make_client()
    me = signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", stripe_customer_id="cus_1")
    return c, me


def events_count(app):
    return app.q1("SELECT COUNT(*) AS n FROM stripe_events").n


# ───────────────────────── webhook ─────────────────────────
def test_webhook_bad_signature_400_no_change(app, S, subscriber, make_client):
    c, me = subscriber
    S["sub"] = subscription()
    ev = event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"})
    r = post_event(make_client(), ev, secret="mauvais")
    assert r.status_code == 400 and S["retrieved"] == [] and user_row(app).premium_until == 0 and events_count(app) == 0
    assert make_client().post("/api/stripe/webhook", data=b"{}", content_type="application/json").status_code == 400


def test_webhook_not_configured_503(app, S, make_client):
    app.STRIPE_WH = ""
    assert post_event(make_client(), event("e", "invoice.paid", {})).status_code == 503


def test_checkout_completed_grants_premium(app, S, subscriber, make_client):
    c, me = subscriber
    end = now() + 30 * 86400
    S["sub"] = subscription(end=end)
    ev = event("evt_1", "checkout.session.completed",
               {"mode": "subscription", "customer": "cus_1", "subscription": "sub_1", "client_reference_id": me["id"]})
    r = post_event(make_client(), ev)
    assert r.status_code == 200
    m = c.get("/api/auth/me").get_json()
    assert m["premium"] is True and m["premium_until"] == end and m["plan_status"] == "active"
    assert user_row(app).stripe_sub_id == "sub_1" and S["retrieved"] == ["sub_1"]


def test_checkout_completed_falls_back_to_client_reference_id(app, S, make_client, signup):
    c = make_client()
    me = signup(c, "a@example.com", "alice")            # pas encore de stripe_customer_id
    S["sub"] = subscription(cust="cus_9")
    ev = event("evt_1", "checkout.session.completed",
               {"mode": "subscription", "customer": "cus_9", "subscription": "sub_1", "client_reference_id": me["id"]})
    assert post_event(make_client(), ev).status_code == 200
    assert c.get("/api/auth/me").get_json()["premium"] is True and user_row(app).stripe_customer_id == "cus_9"


def test_checkout_completed_ignores_non_subscription_mode(app, S, subscriber, make_client):
    ev = event("evt_1", "checkout.session.completed", {"mode": "payment", "customer": "cus_1"})
    r = post_event(make_client(), ev)
    assert r.status_code == 200 and r.get_json()["ignored"] is True and S["retrieved"] == []


def test_invoice_paid_old_and_new_api_shapes(app, S, subscriber, make_client):
    S["sub"] = subscription()
    shapes = [
        {"customer": "cus_1", "subscription": "sub_1"},                                              # avant 2025-03-31
        {"customer": "cus_1", "parent": {"type": "subscription_details",
                                         "subscription_details": {"subscription": "sub_1"}}},      # basil et après
        {"customer": "cus_1", "lines": {"data": [{"parent": {"subscription_item_details": {"subscription": "sub_1"}}}]}},
    ]
    for i, obj in enumerate(shapes):
        set_user(app, "a@example.com", premium_until=0)
        assert post_event(make_client(), event(f"evt_{i}", "invoice.paid", obj)).status_code == 200
        assert user_row(app).premium_until > 0
    assert S["retrieved"] == ["sub_1"] * 3


def test_invoice_without_subscription_uses_users_current_one(app, S, subscriber, make_client):
    set_user(app, "a@example.com", stripe_sub_id="sub_1")
    S["sub"] = subscription()
    assert post_event(make_client(), event("evt_1", "invoice.paid", {"customer": "cus_1"})).status_code == 200
    assert S["retrieved"] == ["sub_1"] and user_row(app).premium_until > 0


def test_payment_failed_revokes(app, S, subscriber, make_client):
    c, me = subscriber
    S["sub"] = subscription()
    post_event(make_client(), event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"}))
    assert c.get("/api/auth/me").get_json()["premium"] is True
    S["sub"] = subscription(status="past_due")
    post_event(make_client(), event("evt_2", "invoice.payment_failed", {"customer": "cus_1", "subscription": "sub_1"}))
    m = c.get("/api/auth/me").get_json()
    assert m["premium"] is False and m["premium_until"] == 0 and m["plan_status"] == "past_due"


def test_out_of_order_stale_failed_event_keeps_premium(app, S, subscriber, make_client):
    c, me = subscriber
    S["sub"] = subscription()                                   # vérité actuelle : actif et payé
    post_event(make_client(), event("evt_new", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"}))
    post_event(make_client(), event("evt_old", "invoice.payment_failed", {"customer": "cus_1", "subscription": "sub_1"}))
    assert c.get("/api/auth/me").get_json()["premium"] is True


def test_replay_same_event_applied_once(app, S, subscriber, make_client):
    S["sub"] = subscription()
    ev = event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"})
    assert post_event(make_client(), ev).status_code == 200
    set_user(app, "a@example.com", premium_until=0)
    r = post_event(make_client(), ev)
    assert r.status_code == 200 and r.get_json()["duplicate"] is True
    assert user_row(app).premium_until == 0 and events_count(app) == 1 and S["retrieved"] == ["sub_1"]


def test_stripe_failure_500_then_retry_applies(app, S, subscriber, make_client):
    S["sub"], S["fail"] = subscription(), True
    ev = event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"})
    assert post_event(make_client(), ev).status_code == 500
    assert events_count(app) == 0 and user_row(app).premium_until == 0
    S["fail"] = False
    assert post_event(make_client(), ev).status_code == 200 and user_row(app).premium_until > 0


def test_active_without_period_end_is_500_not_silent(app, S, subscriber, make_client):
    S["sub"] = subscription()
    del S["sub"]["current_period_end"]
    r = post_event(make_client(), event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_1"}))
    assert r.status_code == 500 and events_count(app) == 0


def test_unknown_customer_200(app, S, make_client, signup):
    signup(make_client(), "a@example.com", "alice")
    S["sub"] = subscription(cust="cus_inconnu")
    r = post_event(make_client(), event("evt_1", "invoice.paid", {"customer": "cus_inconnu", "subscription": "sub_1"}))
    assert r.status_code == 200 and r.get_json()["unknown_user"] is True
    assert user_row(app).premium_until == 0


def test_subscription_deleted_keeps_gift(app, S, subscriber, make_client):
    c, me = subscriber
    set_user(app, "a@example.com", premium_until=now() + 86400, gift_until=app.FOREVER, plan_status="active")
    S["sub"] = subscription(status="canceled")
    r = post_event(make_client(), event("evt_1", "customer.subscription.deleted", subscription(status="canceled")))
    assert r.status_code == 200
    m = c.get("/api/auth/me").get_json()
    assert m["premium_until"] == 0 and m["plan_status"] == "canceled"
    assert m["premium"] is True and m["premium_src"] == "key"


def test_subscription_updated_stores_cancel_at_period_end(app, S, subscriber, make_client):
    c, me = subscriber
    S["sub"] = subscription(cancel=True)
    post_event(make_client(), event("evt_1", "customer.subscription.updated", subscription(cancel=True)))
    m = c.get("/api/auth/me").get_json()
    assert m["cancel_at_end"] is True and m["premium"] is True


def test_stale_other_subscription_does_not_override_current(app, S, subscriber, make_client):
    c, me = subscriber
    S["sub"] = subscription(sid="sub_new")
    post_event(make_client(), event("evt_1", "invoice.paid", {"customer": "cus_1", "subscription": "sub_new"}))
    assert user_row(app).stripe_sub_id == "sub_new" and user_row(app).premium_until > 0
    S["sub"] = subscription(status="canceled", sid="sub_old")             # l'ancien abonnement est résilié (évènement tardif)
    r = post_event(make_client(), event("evt_2", "customer.subscription.deleted", subscription(status="canceled", sid="sub_old")))
    assert r.status_code == 200
    row = user_row(app)
    assert row.stripe_sub_id == "sub_new" and row.premium_until > 0 and row.plan_status == "active"


def test_unknown_event_ignored(app, S, subscriber, make_client):
    r = post_event(make_client(), event("evt_1", "charge.refunded", {"customer": "cus_1"}))
    assert r.status_code == 200 and r.get_json()["ignored"] is True and S["retrieved"] == [] and events_count(app) == 0


def test_period_end_both_locations(app):
    assert app.period_end({"current_period_end": 100}) == 100
    assert app.period_end({"items": {"data": [{"current_period_end": 50}, {"current_period_end": 70}]}}) == 70
    assert app.period_end({"current_period_end": 100, "items": {"data": [{"current_period_end": 120}]}}) == 120
    assert app.period_end({"items": {"data": []}}) == 0 and app.period_end({}) == 0


# ───────────────────────── checkout / portail ─────────────────────────
@pytest.fixture
def pay(app, S, monkeypatch):
    calls = {"customer": [], "checkout": [], "portal": []}

    def create_customer(email, user_id):
        calls["customer"].append((email, user_id))
        return "cus_new"

    def create_checkout(customer_id, user_id, base_url):
        calls["checkout"].append((customer_id, user_id, base_url))
        return "cs_test_secret_abc"

    def create_portal(customer_id, return_url):
        calls["portal"].append((customer_id, return_url))
        return "https://billing.stripe.test/p/abc"
    monkeypatch.setattr(app, "stripe_create_customer", create_customer)
    monkeypatch.setattr(app, "stripe_create_checkout", create_checkout)
    monkeypatch.setattr(app, "stripe_create_portal", create_portal)
    return calls


def test_checkout_requires_auth_and_config(app, S, pay, make_client, signup):
    assert make_client().post("/api/stripe/checkout").status_code == 401
    c = make_client()
    signup(c, "a@example.com", "alice")
    app.STRIPE_KEY = ""
    assert c.post("/api/stripe/checkout").status_code == 503
    app.STRIPE_KEY = "sk_test"
    r = c.post("/api/stripe/checkout")
    assert r.status_code == 200 and r.get_json() == {"client_secret": "cs_test_secret_abc"}


def test_checkout_reuses_customer_and_passes_ids(app, S, pay, make_client, signup):
    c = make_client()
    me = signup(c, "a@example.com", "alice")
    c.post("/api/stripe/checkout")
    c.post("/api/stripe/checkout")
    assert pay["customer"] == [("a@example.com", me["id"])]
    assert [x[0] for x in pay["checkout"]] == ["cus_new", "cus_new"] and pay["checkout"][0][1] == me["id"]
    assert pay["checkout"][0][2] == "http://localhost"
    app.PUBLIC_URL = "https://tiksound.example"
    c.post("/api/stripe/checkout")
    assert pay["checkout"][2][2] == "https://tiksound.example"


def test_checkout_409_when_already_subscribed(app, S, pay, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", premium_until=now() + 86400, plan_status="active", stripe_customer_id="cus_1")
    r = c.post("/api/stripe/checkout")
    assert r.status_code == 409 and r.get_json()["portal"] is True and pay["checkout"] == []


def test_checkout_allowed_for_gift_holder_and_blocked_for_admin(app, S, pay, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", gift_until=app.FOREVER)
    assert c.post("/api/stripe/checkout").status_code == 200
    app.ADMIN_EMAILS = {"a@example.com"}
    assert c.post("/api/stripe/checkout").status_code == 409


def test_checkout_stripe_failure_502(app, S, pay, make_client, signup, monkeypatch):
    c = make_client()
    signup(c, "a@example.com", "alice")

    def boom(*a):
        raise RuntimeError("Stripe HS")
    monkeypatch.setattr(app, "stripe_create_checkout", boom)
    assert c.post("/api/stripe/checkout").status_code == 502


def test_portal_404_without_customer_and_url_with_one(app, S, pay, make_client, signup):
    c = make_client()
    signup(c, "a@example.com", "alice")
    assert c.post("/api/stripe/portal").status_code == 404
    set_user(app, "a@example.com", stripe_customer_id="cus_1")
    r = c.post("/api/stripe/portal")
    assert r.status_code == 200 and r.get_json()["url"] == "https://billing.stripe.test/p/abc"
    assert pay["portal"] == [("cus_1", "http://localhost/#profil")]
    assert make_client().post("/api/stripe/portal").status_code == 401


# ───────────────────────── suppression de compte ─────────────────────────
def delete_account(c, pw="motdepasse1"):
    return c.delete("/api/auth/me", json={"password": pw})


def test_delete_account_cancels_subscription_first(app, S, make_client, signup, monkeypatch):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", stripe_sub_id="sub_1", plan_status="active", premium_until=now() + 86400)
    seen = []
    monkeypatch.setattr(app, "stripe_cancel_subscription",
                        lambda sid: seen.append((sid, user_row(app) is not None)))
    assert delete_account(c).status_code == 200
    assert seen == [("sub_1", True)]                      # résilié alors que le compte existait encore
    assert user_row(app) is None


def test_delete_account_kept_when_cancel_fails(app, S, make_client, signup, monkeypatch):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", stripe_sub_id="sub_1", plan_status="active")
    S["sub"] = subscription()                              # toujours actif chez Stripe

    def boom(sid):
        raise RuntimeError("Stripe HS")
    monkeypatch.setattr(app, "stripe_cancel_subscription", boom)
    assert delete_account(c).status_code == 502 and user_row(app) is not None


def test_delete_account_when_already_canceled_in_stripe(app, S, make_client, signup, monkeypatch):
    c = make_client()
    signup(c, "a@example.com", "alice")
    set_user(app, "a@example.com", stripe_sub_id="sub_1", plan_status="active")
    S["sub"] = subscription(status="canceled")

    def boom(sid):
        raise RuntimeError("déjà résilié")
    monkeypatch.setattr(app, "stripe_cancel_subscription", boom)
    assert delete_account(c).status_code == 200 and user_row(app) is None


def test_delete_account_without_subscription_no_stripe_call(app, S, make_client, signup, monkeypatch):
    c = make_client()
    signup(c, "a@example.com", "alice")
    calls = []
    monkeypatch.setattr(app, "stripe_cancel_subscription", lambda sid: calls.append(sid))
    assert delete_account(c).status_code == 200 and calls == []
    c2 = make_client()
    signup(c2, "b@example.com", "bob")
    set_user(app, "b@example.com", stripe_sub_id="sub_9", plan_status="canceled")      # déjà résilié : rien à faire
    assert delete_account(c2).status_code == 200 and calls == []


# ───────────────────────── Checkout Studio : formulaire intégré (ui_mode "form") ─────────────────────────
class _FakeSession:
    client_secret = "cs_test_secret_xyz"
    url = None                                   # en ui_mode "form", il n'y a pas de page hébergée


def _fake_stripe(created):
    class Session:
        @staticmethod
        def create(**kw):
            created.append(kw)
            return _FakeSession()

    class Checkout:
        pass
    Checkout.Session = Session

    class Fake:
        checkout = Checkout
    return Fake


def test_checkout_session_params_match_checkout_studio(app, monkeypatch):
    app.STRIPE_PRICE = "price_real"
    created = []
    monkeypatch.setattr(app, "_stripe", lambda: _fake_stripe(created))
    secret = app.stripe_create_checkout("cus_1", "u1", "https://tiksound.example")
    assert secret == "cs_test_secret_xyz"
    # égalité stricte : aucun paramètre en trop (ni success_url / cancel_url, interdits avec le formulaire intégré)
    assert created == [dict(
        ui_mode="form", mode="subscription",
        billing_address_collection="auto", phone_number_collection={"enabled": False},
        automatic_tax={"enabled": False}, payment_method_collection="always", submit_type="auto",
        integration_identifier="custom_embedded_web_0001",
        line_items=[{"price": "price_real", "quantity": 1}],
        customer="cus_1", client_reference_id="u1",                       # le webhook retrouve l'utilisateur grâce à eux
        subscription_data={"metadata": {"user_id": "u1"}},
        return_url="https://tiksound.example/?premium=success")]


def test_plain_accepts_sdk_objects_that_are_not_dicts(app):
    """stripe-python 15 : les objets ne sont plus des dict."""
    class Obj:
        last_response = None

        def to_dict(self):
            return {"id": "sub_1", "items": {"data": []}}
    assert app._plain(Obj()) == {"id": "sub_1", "items": {"data": []}}
