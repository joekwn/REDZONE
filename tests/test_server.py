"""The website: pages, health check, security rules, accounts and scheduled calls."""


def test_pages(client):
    for path in ("/", "/app", "/privacy", "/terms", "/robots.txt", "/favicon.ico"):
        assert client.get(path).status_code == 200, path


def test_legal_pages_show_contact(client):
    assert "team@example.com" in client.get("/privacy").text
    assert "{{CONTACT}}" not in client.get("/terms").text


def test_health_and_meta(client):
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/api/meta").status_code == 200
    assert client.get("/api/status").json()["serverless"] is True


def test_security_headers(client):
    r = client.get("/")
    assert r.headers.get("x-content-type-options") == "nosniff"


def test_post_without_csrf_header_is_refused(client):
    r = client.post("/api/auth/logout", headers={"X-RedZone": ""})
    assert r.status_code == 403


def test_register_login_and_admin(client):
    r = client.post("/api/auth/register", json={"name": "Admin", "email": "admin@example.com",
                                                "password": "a long test password 1"})
    assert r.status_code == 200
    me = client.get("/api/auth/me").json()["user"]
    assert me["email"] == "admin@example.com" and me["admin"] is True
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    bad = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "wrong password!!"})
    assert bad.status_code in (400, 401)
    ok = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "a long test password 1"})
    assert ok.status_code == 200


def test_weak_password_refused(client):
    r = client.post("/api/auth/register", json={"name": "W", "email": "weak@example.com", "password": "123"})
    assert r.status_code in (400, 422)


def test_cron_needs_the_secret(client):
    assert client.get("/api/cron/update").status_code == 401
    assert client.get("/api/cron/update", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/cron/update", headers={"Authorization": "Bearer test-cron-secret"})
    assert r.status_code == 200 and r.json()["error"] is None
    assert client.get("/api/cron/events", headers={"Authorization": "Bearer test-cron-secret"}).status_code == 200


def test_meta_dates_are_real_dates(client):
    from datetime import date
    m = client.get("/api/meta").json()
    date.fromisoformat(m["min_date"]) and date.fromisoformat(m["max_date"])
