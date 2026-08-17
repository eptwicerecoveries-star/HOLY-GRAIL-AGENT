"""Productization P1: local-only read HTTP API around the Holy Grail engine.

UNAUTHENTICATED. Bind to 127.0.0.1 only. Must not be internet-facing until
authentication/authorization is designed.
"""

from __future__ import annotations

from surplus_ai.api.app import app, create_app

__all__ = ["app", "create_app"]
