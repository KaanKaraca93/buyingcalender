"""Denetim kaydi — kim, ne zaman, hangi tabloya ne yazdi.

Heroku'da stdout zaten log akisidir; satirlar JSON olarak basilir ki
Papertrail/Logtail gibi bir eklentiyle aranabilsin. Tarih/deger disinda
veri yazilmaz, token veya credential ASLA loglanmaz.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("audit")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_event(event: str, *, user: str = "", **fields: Any) -> None:
    payload = {"ts": _now(), "event": event, "user": user}
    payload.update({k: v for k, v in fields.items() if v is not None})
    try:
        logger.info(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001  # log asla istegi dusurmemeli
        logger.info("%s %s", event, user)


def log_read(user: str, table: str, keys: dict, count: int, ms: int) -> None:
    log_event("m3.read", user=user, table=table, keys=keys, count=count, ms=ms)


def log_write(user: str, table: str, added: int, changed: int, failed: int,
              remaining: int, ms: int, strategy: str) -> None:
    log_event("m3.write", user=user, table=table, added=added, changed=changed,
              failed=failed, remaining=remaining, ms=ms, strategy=strategy)


def log_denied(user: str, reason: str, **fields: Any) -> None:
    log_event("denied", user=user, reason=reason, **fields)
