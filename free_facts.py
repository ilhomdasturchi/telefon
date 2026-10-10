"""Free live facts for Gemini tools (no Google Search / no billing)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

# Central Bank of Uzbekistan — official daily rates, free, no API key
_CBU_JSON = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/"
# Open-Meteo — free weather, no API key
_WEATHER = "https://api.open-meteo.com/v1/forecast"


def _http_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "telefon-ai/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_exchange_rate(currency: str = "USD") -> dict[str, Any]:
    """Official CBU rate: 1 unit of currency → UZS."""
    code = (currency or "USD").strip().upper()
    if code in {"DOLLAR", "DOLLOR", "AQSH", "USDOLLAR", "$"}:
        code = "USD"
    try:
        if code == "UZS":
            return {
                "ok": True,
                "currency": "UZS",
                "rate_uzs": 1.0,
                "nominal": 1,
                "date": None,
                "source": "cbu.uz",
                "note": "O'zbek so'mi",
            }
        data = _http_json(f"{_CBU_JSON}{code}/")
        if not data:
            return {"ok": False, "error": f"unknown currency {code}"}
        row = data[0] if isinstance(data, list) else data
        return {
            "ok": True,
            "currency": row.get("Ccy", code),
            "name_uz": row.get("CcyNm_UZ"),
            "rate_uzs": float(str(row.get("Rate", "0")).replace(",", ".")),
            "nominal": int(row.get("Nominal") or 1),
            "diff": row.get("Diff"),
            "date": row.get("Date"),
            "source": "Markaziy bank (cbu.uz)",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "currency": code}


def get_weather(city: str = "Toshkent") -> dict[str, Any]:
    """Current weather via Open-Meteo (free)."""
    places = {
        "toshkent": (41.3111, 69.2797, "Toshkent"),
        "tashkent": (41.3111, 69.2797, "Toshkent"),
        "samarqand": (39.6542, 66.9597, "Samarqand"),
        "samarkand": (39.6542, 66.9597, "Samarqand"),
        "buxoro": (39.7681, 64.4556, "Buxoro"),
        "bukhara": (39.7681, 64.4556, "Buxoro"),
        "namangan": (40.9983, 71.6726, "Namangan"),
        "andijon": (40.7821, 72.3442, "Andijon"),
        "fargona": (40.3842, 71.7843, "Farg'ona"),
        "fergana": (40.3842, 71.7843, "Farg'ona"),
    }
    key = (city or "Toshkent").strip().lower().replace("'", "").replace("‘", "").replace("ʼ", "")
    lat, lon, label = places.get(key, places["toshkent"])
    url = (
        f"{_WEATHER}?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        f"&timezone=Asia%2FTashkent"
    )
    try:
        data = _http_json(url)
        cur = data.get("current") or {}
        return {
            "ok": True,
            "city": label,
            "temperature_c": cur.get("temperature_2m"),
            "humidity_percent": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "weather_code": cur.get("weather_code"),
            "time": cur.get("time"),
            "source": "open-meteo.com",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "city": city}


def run_tool(name: str, args: dict | None) -> dict[str, Any]:
    args = args or {}
    if name == "get_exchange_rate":
        return get_exchange_rate(str(args.get("currency") or "USD"))
    if name == "get_weather":
        return get_weather(str(args.get("city") or "Toshkent"))
    return {"ok": False, "error": f"unknown tool {name}"}
