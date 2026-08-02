from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def stable_row_hash(fields: Mapping[str, str]) -> str:
    """Fingerprint a row's contents, independent of key order.

    Used to recognise the same record across republications of a county's list. Keys are
    sorted so that a change in column order does not read as a change in the data, and the
    values are taken exactly as published: normalising them here would let two genuinely
    different records collide.
    """
    encoded = json.dumps(dict(sorted(fields.items())), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
