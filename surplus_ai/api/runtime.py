"""Production ASGI launcher with explicit Uvicorn proxy-header settings.

Uvicorn 0.52.3 defaults ``proxy_headers=True`` and trusts ``127.0.0.1`` when
``forwarded_allow_ips`` is unset. Holy Grail disables proxy headers unless
``SURPLUS_AI_TRUST_PROXY_HEADERS`` is explicitly enabled with a validated
allowlist. Login throttling continues to use ``request.client.host`` only.
"""

from __future__ import annotations

from typing import Any

import uvicorn

from surplus_ai.utils.config import get_settings


def build_uvicorn_run_kwargs() -> dict[str, Any]:
    """Return kwargs for ``uvicorn.run`` with fail-closed proxy defaults."""
    settings = get_settings()
    kwargs: dict[str, Any] = {
        "app": "surplus_ai.api.app:app",
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        "reload": False,
        "proxy_headers": False,
        "forwarded_allow_ips": [],
    }
    if settings.trust_proxy_headers:
        kwargs["proxy_headers"] = True
        kwargs["forwarded_allow_ips"] = list(settings.forwarded_allow_ip_allowlist)
    return kwargs


def main() -> None:
    uvicorn.run(**build_uvicorn_run_kwargs())


if __name__ == "__main__":
    main()
