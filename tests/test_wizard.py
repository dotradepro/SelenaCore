"""
tests/test_wizard.py — Wizard flow + onboarding tests
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock


class TestWizardState:
    """Test the wizard state management functions."""

    @pytest.fixture
    def wizard_state_file(self, tmp_path):
        return tmp_path / "wizard_state.json"

    def test_load_state_default(self, wizard_state_file):
        with patch("system_modules.ui_core.wizard.WIZARD_STATE_FILE", wizard_state_file):
            from system_modules.ui_core.wizard import _load_state
            state = _load_state()
            assert state["completed"] is False
            assert state["current_step"] == "wifi"

    def test_save_and_load_state(self, wizard_state_file):
        with patch("system_modules.ui_core.wizard.WIZARD_STATE_FILE", wizard_state_file):
            from system_modules.ui_core.wizard import _load_state, _save_state
            state = {"completed": False, "current_step": "language", "data": {"wifi": {"ssid": "test"}}}
            _save_state(state)
            assert wizard_state_file.exists()
            loaded = _load_state()
            assert loaded["current_step"] == "language"

    def test_steps_list(self):
        from system_modules.ui_core.wizard import STEPS
        assert "wifi" in STEPS
        assert "language" in STEPS
        assert "admin_user" in STEPS
        assert "import" in STEPS
        assert STEPS[0] == "wifi"
        assert STEPS[-1] == "import"


class TestWizardAPI:
    """Integration test: wizard via HTTP endpoints."""

    @pytest.fixture
    def ui_client(self, tmp_path):
        import system_modules.ui_core.wizard as wiz_mod
        state_file = tmp_path / "wizard_state.json"
        orig = wiz_mod.WIZARD_STATE_FILE
        wiz_mod.WIZARD_STATE_FILE = state_file
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        test_app = FastAPI()
        test_app.include_router(wiz_mod.router)
        yield TestClient(test_app)
        wiz_mod.WIZARD_STATE_FILE = orig

    def test_get_wizard_status(self, ui_client):
        resp = ui_client.get("/api/ui/wizard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "completed" in data
        assert "current_step" in data
        assert "steps" in data

    def test_submit_wizard_step(self, ui_client):
        resp = ui_client.post(
            "/api/ui/wizard/step",
            json={"step": "wifi", "data": {"ssid": "MyWiFi"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["next_step"] == "language"

    def test_submit_unknown_step_rejected(self, ui_client):
        resp = ui_client.post(
            "/api/ui/wizard/step",
            json={"step": "nonexistent_step", "data": {}},
        )
        assert resp.status_code == 422

    def test_step_progression(self, ui_client):
        """Test progression through steps that don't require DB."""
        simple_steps = ["wifi", "language", "device_name", "timezone", "stt_model"]
        for step in simple_steps:
            resp = ui_client.post(
                "/api/ui/wizard/step",
                json={"step": step, "data": _step_data(step)},
            )
            assert resp.status_code == 200, f"Step {step} failed: {resp.text}"

    def test_complete_wizard(self, ui_client):
        from system_modules.ui_core.wizard import STEPS, _process_step

        async def _mock_process(step, data, state):
            return {}

        with patch("system_modules.ui_core.wizard._process_step", new=_mock_process):
            for step in STEPS:
                resp = ui_client.post(
                    "/api/ui/wizard/step",
                    json={"step": step, "data": _step_data(step)},
                )
                assert resp.status_code == 200, f"Step {step} failed: {resp.text}"

        status = ui_client.get("/api/ui/wizard/status")
        assert status.json()["completed"] is True

    def test_wizard_reset(self, ui_client):
        # Submit a step first
        ui_client.post(
            "/api/ui/wizard/step",
            json={"step": "wifi", "data": {"ssid": "test"}},
        )
        # Reset
        resp = ui_client.post("/api/ui/wizard/reset")
        assert resp.status_code == 200
        # Check state is reset
        status = ui_client.get("/api/ui/wizard/status")
        assert status.json()["completed"] is False
        assert status.json()["current_step"] == "wifi"


def _step_data(step: str) -> dict:
    """Generate minimal valid data for a wizard step."""
    data = {
        "wifi": {"ssid": "TestNet"},
        "language": {"language": "uk"},
        "device_name": {"name": "MyHome"},
        "timezone": {"timezone": "Europe/Kyiv"},
        "stt_model": {"model": "base"},
        "tts_voice": {"voice": "uk_UA-ukrainian_tts-medium"},
        "admin_user": {"username": "admin", "pin": "1234"},
        "home_devices": {"device_name": "Kiosk"},
        "platform": {"device_hash": "abc123"},
        "import": {"source": "manual"},
    }
    return data.get(step, {})
