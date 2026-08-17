"""Productization P2/P3 local dashboard serving and contract tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from surplus_ai.api.static import STATIC_DIR

FORBIDDEN_TOKENS = (
    "0.0.0.0",
    "postgresql+psycopg",
    "surplus_ai_dev",
    "555-555-0100",
    "+15555550100",
    "secret@example.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "fonts.googleapis.com",
    "bootstrap",
    "tailwind",
)

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _static_text(*, exclude_login: bool = False) -> str:
    parts: list[str] = []
    for path in STATIC_DIR.rglob("*"):
        if not path.is_file():
            continue
        if exclude_login and path.name in {"login.html", "login.js"}:
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_root_requires_auth_redirect(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_root_serves_dashboard_html(api_client: TestClient) -> None:
    response = api_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "Holy Grail" in body
    assert "Local development only" in body
    assert "/static/css/dashboard.css" in body
    assert "/static/js/dashboard.js" in body
    assert "X-Content-Type-Options" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


def test_static_css_and_js_are_served(anonymous_client: TestClient) -> None:
    css = anonymous_client.get("/static/css/dashboard.css")
    js = anonymous_client.get("/static/js/dashboard.js")
    api_js = anonymous_client.get("/static/js/api.js")
    login_js = anonymous_client.get("/static/js/login.js")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert js.status_code == 200
    assert api_js.status_code == 200
    assert login_js.status_code == 200
    assert "apiGet" in api_js.text


def test_p1_routes_still_available(api_client: TestClient, anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/health").status_code == 200
    assert api_client.get("/api/v1/status").status_code == 200
    assert api_client.get("/api/v1/cases").status_code == 200
    docs = anonymous_client.get("/docs")
    assert docs.status_code == 200


def test_dashboard_assets_are_privacy_safe() -> None:
    blob = _static_text().lower()
    for token in FORBIDDEN_TOKENS:
        assert token.lower() not in blob, token
    assert "contact.value" not in blob
    assert "item.value" not in blob
    assert '["value"]' not in blob
    # Login UI may label the password field; secrets must not appear elsewhere.
    non_login = _static_text(exclude_login=True).lower()
    assert "password_hash" not in non_login
    assert "$argon2" not in non_login


def test_dashboard_uses_relative_api_only() -> None:
    js = (STATIC_DIR / "js" / "api.js").read_text(encoding="utf-8")
    dashboard = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    login = (STATIC_DIR / "js" / "login.js").read_text(encoding="utf-8")
    combined = f"{js}\n{dashboard}\n{login}"
    assert 'method: "GET"' in js
    assert 'method: "POST"' in js
    assert "/api/v1/status" in combined
    assert "/api/v1/cases" in combined
    assert "/api/v1/leads" in combined
    assert "/api/v1/research/reviews" in combined
    assert "/api/v1/contacts" in combined
    assert "/api/v1/auth/login" in login
    assert "/api/v1/auth/logout" in dashboard
    assert "http://127.0.0.1" not in combined
    assert "http://localhost" not in combined
    for method in ("PUT", "PATCH", "DELETE"):
        assert f'method: "{method}"' not in combined
        assert f"method: '{method}'" not in combined
    assert "https://" not in combined
    assert "localStorage" not in combined
    assert "sessionStorage" not in combined
    assert "indexedDB" not in combined
    assert "document.cookie" not in combined


def test_dashboard_html_has_no_cdn_or_write_forms() -> None:
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert "<form" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html
    assert "<script src=\"http" not in html.lower()


def test_login_html_is_local_only() -> None:
    html = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    assert "/static/js/login.js" in html
    assert "/static/css/dashboard.css" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn." not in html.lower()


def test_no_user_controlled_static_path(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/static/../app.py")
    assert response.status_code in {404, 400}


def test_static_dir_contains_expected_files() -> None:
    expected = {
        Path("dashboard.html"),
        Path("login.html"),
        Path("css") / "dashboard.css",
        Path("js") / "api.js",
        Path("js") / "dashboard.js",
        Path("js") / "login.js",
    }
    found = {path.relative_to(STATIC_DIR) for path in STATIC_DIR.rglob("*") if path.is_file()}
    assert expected <= found
