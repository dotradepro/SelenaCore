"""
tests/test_weather_module.py — pytest tests for weather-module

Run from:
  pytest modules/weather-module/ -v
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_config():
    cfg = Path("/tmp/weather-test.json")
    if cfg.exists():
        cfg.unlink()
    yield
    if cfg.exists():
        cfg.unlink()


@pytest.fixture
async def client():
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Fake domain objects ────────────────────────────────────────────────────────

def _fake_weather():
    from weather_client import WeatherData
    return WeatherData(
        city="Kyiv", country="UA", lat=50.4501, lon=30.5234,
        temperature=12.4, feels_like=9.8, humidity=72,
        wind_speed=5.2, wind_direction=270,
        wmo_code=2, emoji="\u26c5", condition_en="Partly cloudy",
        condition_uk="\u0425\u043c\u0430\u0440\u043d\u043e \u0437 \u043f\u0440\u043e\u044f\u0441\u043d\u0435\u043d\u043d\u044f\u043c\u0438",
        is_day=True, units="celsius", updated_at=time.time(),
    )


def _fake_forecast():
    from weather_client import ForecastData, HourlyEntry, DailyEntry

    hourly = [
        HourlyEntry(
            time=f"2026-03-21T{h:02d}:00",
            temperature=round(10.0 + h * 0.1, 1),
            precip_prob=5 + h,
            wind_speed=round(3.0 + h * 0.1, 1),
            wmo_code=0, emoji="\u2600\ufe0f",
        )
        for h in range(24)
    ]

    daily_10 = [
        DailyEntry(
            date=f"2026-03-{21+i:02d}",
            temp_min=round(5.0 - i * 0.5, 1),
            temp_max=round(15.0 - i * 0.5, 1),
            precip_prob=10 * i,
            wmo_code=i % 5,
            emoji="\u2600\ufe0f",
            condition_en="Clear sky",
            condition_uk="\u042f\u0441\u043d\u0435 \u043d\u0435\u0431\u043e",
        )
        for i in range(10)
    ]

    return ForecastData(hourly_today=hourly, daily_7=daily_10[:7], daily_10=daily_10)


# ── Basic endpoints ────────────────────────────────────────────────────────────

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["name"] == "weather-module"
    assert data["version"] == "1.0.0"


async def test_icon_svg(client):
    r = await client.get("/icon.svg")
    assert r.status_code == 200
    assert "svg" in r.headers.get("content-type", "")


async def test_widget_html(client):
    r = await client.get("/widget.html")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


async def test_settings_html(client):
    r = await client.get("/settings.html")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


# ── Config API ─────────────────────────────────────────────────────────────────

async def test_config_get_returns_defaults(client):
    r = await client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    for key in ("city", "lat", "lon", "units", "lang"):
        assert key in data


async def test_config_post_and_reload(client):
    payload = {"city": "London", "country": "GB", "lat": 51.5074,
               "lon": -0.1278, "units": "fahrenheit", "lang": "en"}
    r = await client.post("/api/config", json=payload)
    assert r.status_code == 200
    assert r.json()["city"] == "London"
    assert r.json()["units"] == "fahrenheit"
    r2 = await client.get("/api/config")
    assert r2.json()["city"] == "London"


async def test_config_partial_update(client):
    await client.post("/api/config", json={"city": "Berlin", "country": "DE",
                                            "lat": 52.52, "lon": 13.40})
    r = await client.post("/api/config", json={"units": "fahrenheit"})
    assert r.status_code == 200
    assert r.json()["city"] == "Berlin"
    assert r.json()["units"] == "fahrenheit"


# ── Weather API ────────────────────────────────────────────────────────────────

async def test_weather_endpoint(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      return_value=_fake_weather()):
        r = await client.get("/api/weather")
    assert r.status_code == 200
    data = r.json()
    assert data["temperature"] == 12.4
    assert data["humidity"] == 72
    assert data["feels_like"] == 9.8
    assert data["condition_en"] == "Partly cloudy"
    assert "emoji" in data


async def test_weather_503_on_error(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      side_effect=Exception("network error")):
        r = await client.get("/api/weather")
    assert r.status_code == 503


# ── Forecast API ───────────────────────────────────────────────────────────────

async def test_forecast_endpoint(client):
    from main import module
    with patch.object(module._client, "get_forecast", new_callable=AsyncMock,
                      return_value=_fake_forecast()):
        r = await client.get("/api/forecast")
    assert r.status_code == 200
    data = r.json()
    assert "hourly_today" in data
    assert "daily_7" in data
    assert "daily_10" in data
    assert len(data["hourly_today"]) == 24
    assert data["hourly_today"][0]["time"] == "2026-03-21T00:00"
    assert len(data["daily_7"]) == 7
    assert len(data["daily_10"]) == 10
    day = data["daily_7"][0]
    for key in ("date", "temp_min", "temp_max", "precip_prob", "emoji", "condition_en", "condition_uk"):
        assert key in day


async def test_forecast_503_on_error(client):
    from main import module
    with patch.object(module._client, "get_forecast", new_callable=AsyncMock,
                      side_effect=Exception("timeout")):
        r = await client.get("/api/forecast")
    assert r.status_code == 503


# ── City search API ────────────────────────────────────────────────────────────

FAKE_SEARCH_RESULTS = [
    {"name": "Kyiv", "country": "UA", "admin1": "Kyiv City",
     "lat": 50.4501, "lon": 30.5234, "display": "Kyiv, Kyiv City, UA"},
    {"name": "Kharkiv", "country": "UA", "admin1": "Kharkiv Oblast",
     "lat": 49.9935, "lon": 36.2304, "display": "Kharkiv, Kharkiv Oblast, UA"},
]


async def test_city_search(client):
    from main import module
    with patch.object(module._client, "search_city", new_callable=AsyncMock,
                      return_value=FAKE_SEARCH_RESULTS):
        r = await client.get("/api/search?q=Ky")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 2
    assert data["results"][0]["name"] == "Kyiv"
    assert data["results"][0]["country"] == "UA"
    assert "display" in data["results"][0]


async def test_city_search_too_short(client):
    r = await client.get("/api/search?q=K")
    assert r.status_code == 422


async def test_city_search_503_on_error(client):
    from main import module
    with patch.object(module._client, "search_city", new_callable=AsyncMock,
                      side_effect=Exception("timeout")):
        r = await client.get("/api/search?q=London")
    assert r.status_code == 503


# ── Intent API ─────────────────────────────────────────────────────────────────

async def test_intent_uk_weather(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      return_value=_fake_weather()):
        r = await client.post("/api/intent", json={
            "text": "\u044f\u043a\u0430 \u043f\u043e\u0433\u043e\u0434\u0430?",
            "lang": "uk", "context": {},
        })
    assert r.status_code == 200
    data = r.json()
    assert data["handled"] is True
    tts = data["tts_text"]
    assert "\u00b0C" in tts or "\u00b0F" in tts


async def test_intent_en_weather(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      return_value=_fake_weather()):
        r = await client.post("/api/intent", json={
            "text": "what is the weather today?", "lang": "en", "context": {},
        })
    assert r.status_code == 200
    data = r.json()
    assert data["handled"] is True
    assert "Currently in" in data["tts_text"]
    assert "temperature" in data["data"]


async def test_intent_forecast_keyword(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      return_value=_fake_weather()):
        r = await client.post("/api/intent", json={
            "text": "\u043f\u043e\u043a\u0430\u0436\u0438 \u043f\u0440\u043e\u0433\u043d\u043e\u0437 \u043f\u043e\u0433\u043e\u0434\u0438",
            "lang": "uk", "context": {},
        })
    assert r.status_code == 200
    assert r.json()["handled"] is True


async def test_intent_no_match(client):
    r = await client.post("/api/intent", json={
        "text": "turn on the lights", "lang": "en", "context": {},
    })
    assert r.status_code == 200
    assert r.json()["handled"] is False


async def test_intent_error_graceful(client):
    from main import module
    with patch.object(module._client, "get_weather", new_callable=AsyncMock,
                      side_effect=Exception("timeout")):
        r = await client.post("/api/intent", json={
            "text": "\u044f\u043a\u0430 \u043f\u043e\u0433\u043e\u0434\u0430?",
            "lang": "uk", "context": {},
        })
    assert r.status_code == 200
    data = r.json()
    assert data["handled"] is True
    assert "tts_text" in data


# ── WMO code mapping ───────────────────────────────────────────────────────────

def test_wmo_codes_coverage():
    from weather_client import wmo_info
    for code in [0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 63, 65,
                 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99]:
        emoji, en, uk = wmo_info(code)
        assert emoji and en and uk, f"Incomplete WMO mapping for code {code}"


def test_wmo_unknown_code():
    from weather_client import wmo_info
    emoji, en, uk = wmo_info(9999)
    assert emoji and en and uk


# ── Default city from TZ ───────────────────────────────────────────────────────

def test_default_city_kyiv(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Kyiv")
    import importlib, weather_client
    importlib.reload(weather_client)
    city = weather_client.get_default_city()
    assert city["name"] == "Kyiv"
    assert city["country"] == "UA"


def test_default_city_fallback(monkeypatch):
    monkeypatch.setenv("TZ", "Unknown/Zone")
    import importlib, weather_client
    importlib.reload(weather_client)
    city = weather_client.get_default_city()
    assert "name" in city
