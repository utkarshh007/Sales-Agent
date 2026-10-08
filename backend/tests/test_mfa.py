"""Two-factor authentication: TOTP correctness, sign-in flow, replay, recovery codes, enforcement, session revocation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import mfa
from app.models import AuditLog, User
from app.security import hash_password, limiter
from tests.helpers import make_db

PW = "Correct-Horse-9-Battery"


# ------------------------------------------------------------------ TOTP algorithm
@pytest.mark.parametrize("t, expected", [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"),
                                         (2000000000, "69279037")])
def test_totp_matches_rfc6238_vectors(t, expected):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
    assert mfa.totp(secret, at=t, digits=8) == expected


def test_codes_accepted_with_one_step_of_drift_and_never_twice():
    secret = mfa.new_secret()
    now = 1_700_000_000
    code = mfa.totp(secret, at=now)
    step = mfa.verify_totp(secret, code, None, at=now + 30)  # phone clock 30 s behind
    assert step == now // 30
    assert mfa.verify_totp(secret, code, step, at=now + 30) is None, "replayed code must be refused"
    assert mfa.verify_totp(secret, mfa.totp(secret, at=now - 90), None, at=now) is None, "too old"
    assert mfa.verify_totp(secret, "12345", None, at=now) is None


def test_recovery_codes_are_single_use():
    codes, hashes = mfa.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    left = mfa.use_recovery_code(hashes, codes[3].upper().replace("-", ""))  # tolerant of case and dashes
    assert left is not None and len(left) == 9
    assert mfa.use_recovery_code(left, codes[3]) is None


def test_secret_is_encrypted_with_a_key_derived_from_secret_key(settings):
    token = mfa.encrypt(settings, "JBSWY3DPEHPK3PXP")
    assert "JBSWY3DPEHPK3PXP" not in token and mfa.decrypt(settings, token) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(mfa.MfaUnavailable):
        mfa.encrypt(settings.model_copy(update={"SECRET_KEY": ""}), "x")


# ------------------------------------------------------------------ API
@pytest.fixture
def app_client(tmp_path, settings, monkeypatch):
    Session = make_db(tmp_path)
    state = {"settings": settings}
    for target in ("app.api.routes.get_settings", "app.api.main.get_settings", "app.api.deps.get_settings",
                   "app.security.get_settings"):
        monkeypatch.setattr(target, lambda: state["settings"])
    with Session() as s:
        s.add(User(email="analyst@x.io", password_hash=hash_password(PW), role="analyst"))
        s.add(User(email="viewer@x.io", password_hash=hash_password(PW), role="viewer"))
        s.commit()
    from app.api.main import create_app
    limiter._hits.clear()
    return TestClient(create_app()), Session, state


def _login(c, email="analyst@x.io"):
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


def _csrf(c):
    return {"X-CSRF-Token": c.cookies.get("ti_csrf")}


def _enroll(c, Session):
    setup = c.post("/api/auth/2fa/setup", headers=_csrf(c)).json()
    assert setup["qr"].startswith("data:image/svg+xml") and setup["otpauth_uri"].startswith("otpauth://totp/")
    with Session() as s:
        stored = s.query(User).filter_by(email="analyst@x.io").one().totp_secret
    assert setup["secret"] not in stored, "secret must be stored encrypted"
    r = c.post("/api/auth/2fa/enable", json={"code": mfa.totp(setup["secret"])}, headers=_csrf(c))
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]


def test_full_sign_in_with_authenticator_code(app_client):
    c, Session, _ = app_client
    _login(c)
    other = TestClient(c.app)
    _login(other)  # a second browser
    assert c.post("/api/auth/2fa/enable", json={"code": "123456"}, headers=_csrf(c)).status_code == 400  # no setup yet
    secret, codes = _enroll(c, Session)
    assert len(codes) == 10
    assert c.get("/api/auth/me").json()["mfa_enabled"] is True, "this browser stays signed in"
    assert other.get("/api/auth/me").status_code == 401, "turning 2FA on signs out every other session"

    c.post("/api/auth/logout")
    limiter._hits.clear()
    first = _login(c)
    assert first["mfa_required"] is True and "ti_session" not in c.cookies, "no session before the second factor"
    bad = c.post("/api/auth/login/verify", json={"mfa_token": first["mfa_token"], "code": "000000"})
    assert bad.status_code == 401
    # the code used to enable 2FA in this same 30 s window is spent; use a fresh step if needed
    import time
    code = mfa.totp(secret, at=time.time() + 30)
    ok = c.post("/api/auth/login/verify", json={"mfa_token": first["mfa_token"], "code": code})
    assert ok.status_code == 200 and c.get("/api/tenders").status_code == 200
    again = _login(TestClient(c.app))
    assert c.post("/api/auth/login/verify", json={"mfa_token": again["mfa_token"], "code": code}).status_code == 401, \
        "a code can't be replayed"
    with Session() as s:
        actions = [a.action for a in s.query(AuditLog).all()]
    assert "MFA_ENABLED" in actions and "LOGIN_MFA_FAILED" in actions


def test_recovery_code_signs_in_once(app_client):
    c, Session, _ = app_client
    _login(c)
    _, codes = _enroll(c, Session)
    c.post("/api/auth/logout")
    t = _login(c)["mfa_token"]
    r = c.post("/api/auth/login/verify", json={"mfa_token": t, "code": codes[0]})
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 9
    t = _login(c)["mfa_token"]
    assert c.post("/api/auth/login/verify", json={"mfa_token": t, "code": codes[0]}).status_code == 401


def test_challenge_token_is_not_a_session(app_client):
    c, _, _ = app_client
    _login(c)
    c.post("/api/auth/2fa/setup", headers=_csrf(c))
    c.cookies.clear()
    from app.security import create_mfa_challenge
    c.cookies.set("ti_session", create_mfa_challenge(1, 0))
    assert c.get("/api/auth/me").status_code == 401


def test_required_role_must_enrol_before_using_the_app(app_client):
    c, Session, state = app_client
    state["settings"] = state["settings"].model_copy(update={"MFA_REQUIRED_ROLES": "analyst"})
    assert _login(c)["mfa_setup_required"] is True
    blocked = c.get("/api/tenders")
    assert blocked.status_code == 403 and blocked.headers.get("X-MFA-Setup-Required") == "1"
    assert c.get("/api/auth/me").json()["mfa_setup_required"] is True
    secret, _ = _enroll(c, Session)
    assert c.get("/api/tenders").status_code == 200
    import time
    r = c.post("/api/auth/2fa/disable", json={"password": PW, "code": mfa.totp(secret, at=time.time() + 30)}, headers=_csrf(c))
    assert r.status_code == 403, "a required role can't turn it off"
    # roles that don't require it are unaffected
    v = TestClient(c.app)
    _login(v, "viewer@x.io")
    assert v.get("/api/tenders").status_code == 200


def test_turning_off_needs_password_and_code(app_client):
    c, Session, _ = app_client
    _login(c)
    secret, _ = _enroll(c, Session)
    import time
    code = mfa.totp(secret, at=time.time() + 30)
    assert c.post("/api/auth/2fa/disable", json={"password": "wrong-Password-1", "code": code}, headers=_csrf(c)).status_code == 400
    r = c.post("/api/auth/2fa/disable", json={"password": PW, "code": code}, headers=_csrf(c))
    assert r.status_code == 200 and r.json()["enabled"] is False
    c.post("/api/auth/logout")
    assert "mfa_required" not in _login(c), "password alone works again"


def test_cli_reset_signs_user_out_and_clears_2fa(app_client, monkeypatch):
    c, Session, _ = app_client
    _login(c)
    _enroll(c, Session)
    from app import cli
    monkeypatch.setattr(cli, "SessionLocal", Session)
    assert cli.main(["reset-2fa", "analyst@x.io"]) == 0
    assert c.get("/api/auth/me").status_code == 401
    assert "mfa_required" not in _login(c)
