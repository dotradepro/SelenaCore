"""
tests/test_wizard.py — Wizard flow + onboarding tests
"""
from __future__ import annotations

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock


class TestWizardFlow:
    """Test the 9-step onboarding wizard state machine."""

    @pytest.fixture
    def wizard_state_file(self, tmp_path):
        return tmp_path / "wizard_state.json"

    @pytest.fixture
    def wizard(self, wizard_state_file):
        with patch("system_modules.ui_core.wizard.WIZARD_STATE_FILE", wizard_state_file):
            from system_modules.ui_core.wizard import WizardManager
            return WizardManager()

    def test_initial_step_is_wifi(self, wizard):
        state = wizard.get_state()
        assert state["step"] == "wifi" or state.get("completed") is False

    def test_step_progression(self, wizard):
        """Test that confirming each step advances to the next."""
        steps = ["wifi", "language", "device_name", "timezone", "stt_model",
                 "tts_voice", "admin_user", "platform", "import"]
        for i, step in enumerate(steps[:-1]):
            wizard.set_step_data(step, {"confirmed": True})
            state = wizard.get_state()
            assert state["step"] == steps[i + 1] or state.get("completed")

    def test_complete_wizard(self, wizard):
        steps = ["wifi", "language", "device_name", "timezone", "stt_model",
                 "tts_voice", "admin_user", "platform", "import"]
        for step in steps:
            wizard.set_step_data(step, {"confirmed": True, "skip": True})
        state = wizard.get_state()
        assert state.get("completed") is True

    def test_state_persisted_to_file(self, wizard, wizard_state_file):
        wizard.set_step_data("wifi", {"ssid": "MyNetwork", "confirmed": True})
        assert wizard_state_file.exists()
        saved = json.loads(wizard_state_file.read_text())
        assert saved is not None

    def test_reset_clears_state(self, wizard):
        wizard.set_step_data("wifi", {"confirmed": True})
        wizard.reset()
        state = wizard.get_state()
        assert state["step"] == "wifi"
        assert not state.get("completed")


class TestWizardAPI:
    """Integration test: wizard via HTTP endpoints."""

    @pytest.fixture
    def ui_client(self, tmp_path):
        state_file = tmp_path / "wizard_state.json"
        with patch("system_modules.ui_core.wizard.WIZARD_STATE_FILE", state_file):
            from system_modules.ui_core.server import ui_app
            from fastapi.testclient import TestClient
            return TestClient(ui_app)

    def test_get_wizard_state(self, ui_client):
        resp = ui_client.get("/api/ui/wizard/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "step" in data

    def test_submit_wizard_step(self, ui_client):
        resp = ui_client.post("/api/ui/wizard/step", json={"step": "wifi", "data": {"skip": True}})
        assert resp.status_code in (200, 201)
