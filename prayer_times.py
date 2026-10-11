"""Namozga (ibodat.marvar.id) — today + tomorrow prayer times only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

import truststore

truststore.inject_into_ssl()

import config

# Main namoz times only (skip duplicate saharlik/iftor aliases)
_TIME_KEYS = ("bomdod", "quyosh", "peshin", "asr", "shom", "hufton")
_TASHKENT = timezone(timedelta(hours=5))  # Asia/Tashkent (no DST) — works in frozen exe


def _base_url() -> str:
    return (
        os.environ.get("NAMOZGA_BASE_URL")
        or getattr(config, "NAMOZGA_BASE_URL", None)
        or "https://ibodat.marvar.id"
    ).strip().rstrip("/")


def _api_key() -> str:
    # Prefer live env (exe loads .env at startup); config may be stale if imported early
    return (
        os.environ.get("NAMOZGA_API_KEY")
        or getattr(config, "NAMOZGA_API_KEY", None)
        or ""
    ).strip()


def _http_get(path: str, params: dict[str, str]) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    url = f"{_base_url()}{path}?{qs}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "telefon-ai/1.0",
    }
    key = _api_key()
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:300]
        try:
            err = json.loads(raw)
        except json.JSONDecodeError:
            err = {"error": raw or str(exc)}
        raise RuntimeError(err.get("error") or f"HTTP {exc.code}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("unexpected prayer API response")
    return data


def _slim_day(raw: dict[str, Any]) -> dict[str, Any]:
    times_in = raw.get("times") if isinstance(raw.get("times"), dict) else {}
    times = {k: times_in[k] for k in _TIME_KEYS if k in times_in}
    return {
        "date": raw.get("date"),
        "weekday": raw.get("weekday"),
        "times": times,
    }


def get_prayer_times(region: str | None = None) -> dict[str, Any]:
    """Return namoz times for today and tomorrow only (Asia/Tashkent)."""
    city = (
        (region or "").strip()
        or os.environ.get("NAMOZGA_DEFAULT_REGION")
        or getattr(config, "NAMOZGA_DEFAULT_REGION", None)
        or "Toshkent"
    ).strip() or "Toshkent"
    # Common English / ASR variants → API city names
    aliases = {
        "tashkent": "Toshkent",
        "toskent": "Toshkent",
        "samarkand": "Samarqand",
        "bukhara": "Buxoro",
        "fergana": "Farg'ona",
        "fargona": "Farg'ona",
    }
    city = aliases.get(city.lower().replace("'", "").replace("ʻ", ""), city)

    try:
        today_raw = _http_get("/api/today", {"region": city})
        tomorrow_date = (datetime.now(_TASHKENT).date() + timedelta(days=1)).isoformat()
        tomorrow_raw = _http_get(
            "/api/day",
            {"region": city, "date": tomorrow_date},
        )
        return {
            "ok": True,
            "region": today_raw.get("region") or city,
            "timezone": today_raw.get("timezone") or "Asia/Tashkent",
            "today": _slim_day(today_raw),
            "tomorrow": _slim_day(tomorrow_raw),
            "source": "ibodat.marvar.id",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "region": city}
