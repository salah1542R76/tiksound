import re
import time

KEY_RE = re.compile(r"^TKW(-[2-9A-HJKMNP-Z]{4}){3}$")      # sans 0 O 1 I L


def now():
    return int(time.time())


def make_admin(app, make_client, signup):
    app.ADMIN_EMAILS = {"boss@example.com"}
    c = make_client()
    signup(c, "boss@example.com", "boss")
    return c


def new_key(admin, **kw):
    r = admin.post("/api/admin/keys", json=kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def friend(make_client, signup, n):
    c = make_client()
    signup(c, f"f{n}@example.com", f"friend{n}")
    return c


def me(c):
    return c.get("/api/auth/me").get_json()


def test_admin_creates_key_format(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, label="Marie", uses=1, days=0)
    assert KEY_RE.match(k["key"]) and k["label"] == "Marie" and k["max_uses"] == 1 and k["days"] == 0
    assert k["hint"] == k["key"][-4:] and k["uses"] == 0


def test_non_admin_forbidden_and_anon_401(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin)
    anon = make_client()
    assert anon.post("/api/admin/keys", json={}).status_code == 401
    assert anon.get("/api/admin/keys").status_code == 401
    user = friend(make_client, signup, 1)
    assert user.post("/api/admin/keys", json={}).status_code == 403
    assert user.get("/api/admin/keys").status_code == 403
    assert user.delete(f"/api/admin/keys/{k['id']}").status_code == 403


def test_redeem_requires_login(app, make_client):
    assert make_client().post("/api/premium/redeem", json={"key": "TKS-AAAA-BBBB-CCCC"}).status_code == 401


def test_redeem_grants_premium_forever_and_30_days(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    forever, month = new_key(admin, days=0), new_key(admin, days=30)
    f1, f2 = friend(make_client, signup, 1), friend(make_client, signup, 2)
    r = f1.post("/api/premium/redeem", json={"key": forever["key"]})
    assert r.status_code == 200 and r.get_json()["premium"] is True
    m = me(f1)
    assert m["premium_exp"] == app.FOREVER and m["premium_src"] == "key"
    assert f2.post("/api/premium/redeem", json={"key": month["key"]}).status_code == 200
    assert abs(me(f2)["premium_exp"] - (now() + 30 * 86400)) <= 5


def test_max_uses_enforced(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, uses=1)
    f1, f2 = friend(make_client, signup, 1), friend(make_client, signup, 2)
    assert f1.post("/api/premium/redeem", json={"key": k["key"]}).status_code == 200
    r = f2.post("/api/premium/redeem", json={"key": k["key"]})
    assert r.status_code == 409 and me(f2)["premium"] is False
    listed = admin.get("/api/admin/keys").get_json()["keys"][0]
    assert listed["uses"] == 1 and listed["users"] == ["friend1"]


def test_unlimited_uses(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, uses=0)
    for n in (1, 2, 3):
        assert friend(make_client, signup, n).post("/api/premium/redeem", json={"key": k["key"]}).status_code == 200
    assert admin.get("/api/admin/keys").get_json()["keys"][0]["uses"] == 3


def test_same_user_same_key_idempotent(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, uses=1)
    f1 = friend(make_client, signup, 1)
    assert f1.post("/api/premium/redeem", json={"key": k["key"]}).status_code == 200
    r = f1.post("/api/premium/redeem", json={"key": k["key"]})
    assert r.status_code == 200 and r.get_json()["already"] is True
    assert admin.get("/api/admin/keys").get_json()["keys"][0]["uses"] == 1


def test_other_key_while_gift_active_409(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    a, b = new_key(admin), new_key(admin)
    f1 = friend(make_client, signup, 1)
    assert f1.post("/api/premium/redeem", json={"key": a["key"]}).status_code == 200
    assert f1.post("/api/premium/redeem", json={"key": b["key"]}).status_code == 409


def test_admin_cannot_redeem(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin)
    assert admin.post("/api/premium/redeem", json={"key": k["key"]}).status_code == 409


def test_revoke_removes_premium_and_blocks_redeem(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, uses=0)
    f1, f2 = friend(make_client, signup, 1), friend(make_client, signup, 2)
    f1.post("/api/premium/redeem", json={"key": k["key"]})
    assert me(f1)["premium"] is True
    assert admin.delete(f"/api/admin/keys/{k['id']}").status_code == 200
    assert me(f1)["premium"] is False
    assert f2.post("/api/premium/redeem", json={"key": k["key"]}).status_code == 404
    assert admin.get("/api/admin/keys").get_json()["keys"][0]["revoked"] is True
    assert admin.delete("/api/admin/keys/inconnue").status_code == 404


def test_key_stored_hashed_never_listed(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin)
    raw = "".join(k["key"].split("-")[1:])
    stored = app.q1("SELECT key_hash FROM access_keys").key_hash
    assert re.fullmatch(r"[0-9a-f]{64}", stored) and raw not in stored
    listing = admin.get("/api/admin/keys").get_data(as_text=True)
    assert k["key"] not in listing and raw not in listing and '"key"' not in listing


def test_redeem_accepts_lowercase_without_dashes(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin)
    f1 = friend(make_client, signup, 1)
    assert f1.post("/api/premium/redeem", json={"key": " " + k["key"].lower().replace("-", "") + " "}).status_code == 200


def test_redeem_unknown_key_generic_error(app, make_client, signup):
    f1 = friend(make_client, signup, 1)
    r = f1.post("/api/premium/redeem", json={"key": "TKS-AAAA-BBBB-CCCC"})
    assert r.status_code == 404 and "invalide" in r.get_json()["error"]
    assert f1.post("/api/premium/redeem", json={}).status_code == 404


def test_redeem_rate_limited_11th_attempt_429(app, make_client, signup):
    f1 = friend(make_client, signup, 1)
    codes = [f1.post("/api/premium/redeem", json={"key": f"TKS-AAAA-BBBB-{i:04d}"}).status_code for i in range(11)]
    assert codes[:10] == [404] * 10 and codes[10] == 429


def test_create_key_clamps_values(app, make_client, signup):
    admin = make_admin(app, make_client, signup)
    k = new_key(admin, uses=99999, days=99999, label="x" * 200)
    assert k["max_uses"] == 1000 and k["days"] == 3650 and len(k["label"]) == 60
    k2 = new_key(admin, uses="n'importe quoi")
    assert k2["max_uses"] == 1                              # valeur par défaut
