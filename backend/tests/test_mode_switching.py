"""Local <-> GCP switching by environment variables, with the GCP clients mocked (no network, no credentials)."""
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.core.dlp_sanitizer import DLPSanitizer
from app.core.model_armor import HybridModelArmorGuard


def _fake_dlp_module():
    module = MagicMock()
    module.DlpServiceClient.return_value = MagicMock(name="dlp_client")
    return module


def test_dlp_uses_local_mock_in_local_mock_mode():
    with patch.object(settings, "security_mode", "local_mock"):
        sanitizer = DLPSanitizer()
        assert sanitizer._dlp_client is None
        with patch.object(sanitizer, "_sanitize_with_gcp_dlp") as gcp:
            text, _ = sanitizer.sanitize("CBU 0170099900000012345678", "s_mode_local")
        gcp.assert_not_called()
        assert "0170099900000012345678" not in text


def test_dlp_uses_cloud_dlp_in_gcp_live_mode():
    fake = _fake_dlp_module()
    with patch.object(settings, "security_mode", "gcp_live"), \
         patch.dict(sys.modules, {"google.cloud.dlp_v2": fake}), \
         patch("google.cloud.dlp_v2", fake, create=True):
        sanitizer = DLPSanitizer()
        assert sanitizer._dlp_client is not None
        with patch.object(sanitizer, "_sanitize_with_gcp_dlp", return_value=("x", [])) as gcp:
            sanitizer.sanitize("hola", "s_mode_gcp")
        gcp.assert_called_once()


def test_model_armor_uses_local_guard_unless_gcp_is_enabled():
    with patch.object(settings, "use_gcp_model_armor", False):
        assert HybridModelArmorGuard()._get_runnable() is None


def test_model_armor_uses_gcp_runnable_when_enabled_and_templated():
    runnable = MagicMock()
    runnable.invoke.return_value = {"is_threat": True, "threat_category": "GCP_CAT", "threat_reason": "r"}
    fake_module = MagicMock()
    fake_module.ModelArmorSanitizePromptRunnable.return_value = runnable
    with patch.object(settings, "use_gcp_model_armor", True), \
         patch.object(settings, "model_armor_template_id", "tpl-1"), \
         patch.dict(sys.modules, {"langchain_google_community.model_armor": fake_module}):
        guard = HybridModelArmorGuard()
        assert guard._get_runnable() is runnable
        assert guard.inspect("texto") == (True, "GCP_CAT", "r")


def test_production_refuses_the_default_vault_secret():
    with pytest.raises(ValidationError):
        Settings(environment="production", _env_file=None)
    assert Settings(environment="production", token_vault_secret="x" * 32, _env_file=None).environment == "production"
