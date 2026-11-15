"""
REDZONE - user accounts.

Guests can use the whole map. An account is needed to save events, priority
sites and their alerts. There is NO email verification: an account works as soon
as it is created. Email features (password reset by code, email alerts) switch on
only when an SMTP server is configured (SMTP_HOST); otherwise they are hidden.

Security design
  Passwords   Argon2id (argon2-cffi defaults: 64 MiB memory, 3 passes, 4 lanes).
              Never stored or logged in plain text. Hashes are upgraded automatically
              at login if the parameters are ever raised.
              (Plain SHA-256 is NOT used for passwords: it is fast, so leaked hashes
              could be guessed by the billions per second.)
  Sessions    Session tokens are 32 random bytes; only their SHA-256 is stored, so a
              database leak gives no usable session.
  Email codes Email confirmation and password reset use a 6-digit code sent by email
              (works with any inbox). Only an HMAC-SHA256 of the code is stored (keyed
              with a server secret). A code lasts 15 minutes, allows 5 tries, works once,
              and a new code cancels the previous one.
  Sessions    HttpOnly + SameSite=Lax cookie (and Secure when the site runs on HTTPS),
              14 days, renewed while used. Logout and password changes revoke them.
  CSRF        Every state-changing /api request must carry the header X-RedZone: 1,
              which other websites cannot add to a request (see server.py).
  Abuse       Rate limits per IP and per email; 10 wrong passwords lock the account
              for 15 minutes; the same answer whether or not an email exists.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import hmac
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notify  # noqa: E402

PH = PasswordHasher()                                   # Argon2id
DUMMY_HASH = PH.hash("not-a-real-password-just-for-timing")
COOKIE = "rz_session"
SESSION_DAYS = 14
CODE_MINUTES = 15
CODE_TRIES = 5
RESEND_SECONDS = 60
MAX_FAILED = 10
LOCK_MINUTES = 15
MIN_PASSWORD = 10
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("REDZONE_ADMIN_EMAILS", "").split(",") if e.strip()}

router = APIRouter()
DB = None                                               # set by server.py: init(db)


def init(db):
    global DB
    DB = db
    db.execute("CREATE SEQUENCE IF NOT EXISTS user_id_seq")
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER DEFAULT nextval('user_id_seq') PRIMARY KEY,
        email VARCHAR UNIQUE, name VARCHAR, password_hash VARCHAR,
        email_verified BOOLEAN DEFAULT false, failed_logins INTEGER DEFAULT 0,
        locked_until TIMESTAMP, created_at TIMESTAMP DEFAULT current_timestamp, last_login TIMESTAMP)""")
    db.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash VARCHAR PRIMARY KEY, user_id INTEGER, created_at TIMESTAMP,
        expires_at TIMESTAMP, user_agent VARCHAR)""")
    db.execute("""CREATE TABLE IF NOT EXISTS email_codes (
        user_id INTEGER, purpose VARCHAR, code_hash VARCHAR, expires_at TIMESTAMP,
        attempts INTEGER DEFAULT 0, used BOOLEAN DEFAULT false, created_at TIMESTAMP)""")
    # owned data: events and priority sites now belong to a user
    db.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS user_id INTEGER")
    db.execute("ALTER TABLE priority_sites ADD COLUMN IF NOT EXISTS user_id INTEGER")


def cur():
    return DB.cursor()


def _load_secret():
    """Server secret for code hashes: REDZONE_SECRET, or a random key kept in data/secret.key."""
    env = os.environ.get("REDZONE_SECRET", "").strip()
    if env:
        return env.encode()
    path = Path(__file__).resolve().parents[1] / "data" / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_text().strip().encode()


SECRET = _load_secret()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sha256(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_cookies():
    return notify.base_url().startswith("https://") or os.environ.get("REDZONE_SECURE_COOKIES") == "1"


def client_ip(request):
    if os.environ.get("REDZONE_TRUST_PROXY") == "1":     # only behind your own HTTPS proxy
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


# ---------- rate limiting (in memory, per server process) ----------
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limit(scope, key, limit, window_s):
    t = time.time()
    with _hits_lock:
        q = _hits[(scope, key)]
        while q and q[0] < t - window_s:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
        q.append(t)


# ---------- 6-digit email codes ----------
def code_hash(user_id, purpose, code):
    return hmac.new(SECRET, f"{purpose}:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(user_id, purpose, email, name):
    """Create a new 6-digit code (cancelling older ones) and email it."""
    c = cur()
    last = c.execute("SELECT MAX(created_at) FROM email_codes WHERE user_id = ? AND purpose = ?",
                     [user_id, purpose]).fetchall()[0][0]
    if last and (now() - last).total_seconds() < RESEND_SECONDS:
        raise HTTPException(429, f"Please wait a minute before asking for a new code.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    c.execute("UPDATE email_codes SET used = true WHERE user_id = ? AND purpose = ? AND NOT used", [user_id, purpose])
    c.execute("INSERT INTO email_codes VALUES (?, ?, ?, ?, 0, false, ?)",
              [user_id, purpose, code_hash(user_id, purpose, code), now() + timedelta(minutes=CODE_MINUTES), now()])
    what = "confirm your email address" if purpose == "verify" else "reset your password"
    notify.send_email(c, email, f"{code} is your REDZONE code",
        f"Hello {name},\n\nYour REDZONE code to {what} is:\n\n    {code}\n\n"
        f"Enter it on the REDZONE website. It expires in {CODE_MINUTES} minutes and works once.\n\n"
        "If you did not ask for this code, you can ignore this email. Never share this code.\n", purpose)


def check_code(user_id, purpose, code):
    """True if `code` is the current, unexpired code (then it is used up). Max 5 tries per code."""
    code = (code or "").strip().replace(" ", "")
    c = cur()
    rows = c.execute("""SELECT rowid, code_hash, attempts FROM email_codes
        WHERE user_id = ? AND purpose = ? AND NOT used AND expires_at > ?
        ORDER BY created_at DESC LIMIT 1""", [user_id, purpose, now()]).fetchall()
    if not rows or not code.isdigit() or len(code) != 6:
        if rows:
            c.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE rowid = ?", [rows[0][0]])
        return False
    rid, stored, attempts = rows[0]
    if attempts >= CODE_TRIES:
        return False
    if hmac.compare_digest(stored, code_hash(user_id, purpose, code)):
        c.execute("UPDATE email_codes SET used = true WHERE rowid = ?", [rid])
        return True
    c.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE rowid = ?", [rid])
    return False


# ---------- sessions ----------
def start_session(response, user_id, request):
    token = secrets.token_urlsafe(32)
    cur().execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                  [sha256(token), user_id, now(), now() + timedelta(days=SESSION_DAYS),
                   (request.headers.get("user-agent") or "")[:200]])
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure_cookies(), path="/")


def current_user(request: Request):
    """The signed-in user as a dict, or None for a guest."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    rows = cur().execute("""
        SELECT u.id, u.email, u.name, u.email_verified, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at > ?""", [sha256(token), now()]).fetchall()
    if not rows:
        return None
    uid, email, name, verified, expires = rows[0]
    if expires - now() < timedelta(days=SESSION_DAYS - 1):          # renew while in use
        cur().execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                      [now() + timedelta(days=SESSION_DAYS), sha256(token)])
    return {"id": uid, "email": email, "name": name, "verified": verified,
            "admin": email in ADMIN_EMAILS}


def require_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Please sign in to use this feature.")
    return user


def email_enabled():
    return bool(os.environ.get("SMTP_HOST", "").strip())


def require_verified(user=Depends(require_user)):
    """Kept for the routes that use it; email verification is switched off, so any signed-in user passes."""
    return user


def require_admin(user=Depends(require_user)):
    if not user["admin"]:
        raise HTTPException(403, "Only administrators can do this.")
    return user


# ---------- request bodies ----------
class Register(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(max_length=200)
    password: str = Field(max_length=128)


class Login(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=128)


class EmailOnly(BaseModel):
    email: str = Field(max_length=200)


class Reset(BaseModel):
    email: str = Field(max_length=200)
    code: str = Field(max_length=12)
    password: str = Field(max_length=128)


class Code(BaseModel):
    code: str = Field(max_length=12)


class ChangePassword(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class DeleteAccount(BaseModel):
    password: str = Field(max_length=128)


def check_password_rules(password, email):
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {MIN_PASSWORD} characters for your password.")
    pw, local = password.lower(), email.lower().split("@")[0]
    if email.lower() in pw or pw.strip() == local:
        raise HTTPException(400, "Your password must not contain your email address.")
    if len(set(password)) < 4:
        raise HTTPException(400, "This password is too easy to guess.")


# ---------- endpoints ----------
@router.post("/api/auth/register")
def register(body: Register, request: Request, response: Response):
    rate_limit("register", client_ip(request), 5, 3600)
    email = body.email.strip().lower()
    if not notify.valid_email(email):
        raise HTTPException(400, "Please enter a valid email address.")
    check_password_rules(body.password, email)
    if cur().execute("SELECT 1 FROM users WHERE email = ?", [email]).fetchall():
        raise HTTPException(409, "An account with this email already exists. Sign in or reset your password.")
    uid = cur().execute("INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?) RETURNING id",
                        [email, body.name.strip(), PH.hash(body.password)]).fetchall()[0][0]
    start_session(response, uid, request)
    return {"ok": True, "message": "Account created. Welcome to REDZONE!"}


@router.post("/api/auth/login")
def login(body: Login, request: Request, response: Response):
    email = body.email.strip().lower()
    rate_limit("login-ip", client_ip(request), 30, 900)
    rate_limit("login-email", email, 15, 900)
    rows = cur().execute("SELECT id, password_hash, failed_logins, locked_until FROM users WHERE email = ?",
                         [email]).fetchall()
    generic = HTTPException(401, "Wrong email or password.")
    if not rows:
        try:
            PH.verify(DUMMY_HASH, body.password)       # same time as a real check
        except VerificationError:
            pass
        raise generic
    uid, pw_hash, failed, locked_until = rows[0]
    if locked_until and locked_until > now():
        raise HTTPException(423, "Too many wrong passwords. This account is locked for a few minutes.")
    try:
        PH.verify(pw_hash, body.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        failed = (failed or 0) + 1
        lock = now() + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_FAILED else None
        cur().execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                      [0 if lock else failed, lock, uid])
        raise generic
    if PH.check_needs_rehash(pw_hash):                 # parameters raised since this hash was made
        cur().execute("UPDATE users SET password_hash = ? WHERE id = ?", [PH.hash(body.password), uid])
    cur().execute("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login = ? WHERE id = ?",
                  [now(), uid])
    start_session(response, uid, request)
    return {"ok": True}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        cur().execute("DELETE FROM sessions WHERE token_hash = ?", [sha256(token)])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/logout-all")
def logout_all(response: Response, user=Depends(require_user)):
    cur().execute("DELETE FROM sessions WHERE user_id = ?", [user["id"]])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"user": user, "email_enabled": email_enabled()}


@router.post("/api/auth/forgot")
def forgot(body: EmailOnly, request: Request):
    if not email_enabled():
        raise HTTPException(503, "Password reset by email is not available on this server.")
    rate_limit("forgot-ip", client_ip(request), 5, 3600)
    email = body.email.strip().lower()
    rows = cur().execute("SELECT id, name FROM users WHERE email = ?", [email]).fetchall()
    if rows:
        rate_limit("forgot-email", email, 5, 3600)
        try:
            issue_code(rows[0][0], "reset", email, rows[0][1])
        except HTTPException:
            pass                                   # too soon: keep the same answer
    # same answer whether or not the account exists (no account guessing)
    return {"ok": True, "message": "If an account exists for this email, we sent it a 6-digit code."}


@router.post("/api/auth/reset")
def reset(body: Reset, request: Request):
    if not email_enabled():
        raise HTTPException(503, "Password reset by email is not available on this server.")
    rate_limit("reset", client_ip(request), 20, 3600)
    email = body.email.strip().lower()
    rows = cur().execute("SELECT id FROM users WHERE email = ?", [email]).fetchall()
    check_password_rules(body.password, email)              # a weak password does not use up the code
    if not rows or not check_code(rows[0][0], "reset", body.code):
        raise HTTPException(400, "Wrong or expired code. Check the latest email, or ask for a new code.")
    uid = rows[0][0]
    cur().execute("""UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL,
                     email_verified = true WHERE id = ?""", [PH.hash(body.password), uid])
    cur().execute("DELETE FROM sessions WHERE user_id = ?", [uid])      # sign out everywhere
    return {"ok": True, "message": "Password changed. You can now sign in."}


@router.post("/api/auth/change-password")
def change_password(body: ChangePassword, request: Request, response: Response, user=Depends(require_user)):
    rate_limit("change-pw", str(user["id"]), 10, 3600)
    pw_hash = cur().execute("SELECT password_hash FROM users WHERE id = ?", [user["id"]]).fetchall()[0][0]
    try:
        PH.verify(pw_hash, body.current_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(401, "Your current password is wrong.")
    check_password_rules(body.new_password, user["email"])
    cur().execute("UPDATE users SET password_hash = ? WHERE id = ?", [PH.hash(body.new_password), user["id"]])
    cur().execute("DELETE FROM sessions WHERE user_id = ?", [user["id"]])
    start_session(response, user["id"], request)                         # keep this device signed in
    return {"ok": True, "message": "Password changed. Other devices were signed out."}


@router.post("/api/auth/delete-account")
def delete_account(body: DeleteAccount, response: Response, user=Depends(require_user)):
    pw_hash = cur().execute("SELECT password_hash FROM users WHERE id = ?", [user["id"]]).fetchall()[0][0]
    try:
        PH.verify(pw_hash, body.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(401, "Your password is wrong.")
    c = cur()
    uid = user["id"]
    c.execute("DELETE FROM alerts WHERE site_id IN (SELECT id FROM priority_sites WHERE user_id = ?)", [uid])
    c.execute("DELETE FROM priority_sites WHERE user_id = ?", [uid])
    c.execute("DELETE FROM events WHERE user_id = ?", [uid])
    c.execute("DELETE FROM sessions WHERE user_id = ?", [uid])
    c.execute("DELETE FROM email_codes WHERE user_id = ?", [uid])
    c.execute("DELETE FROM users WHERE id = ?", [uid])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
