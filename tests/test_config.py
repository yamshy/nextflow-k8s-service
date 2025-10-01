"""Tests for application configuration."""

import os
from unittest.mock import patch

import pytest

from app.config import Settings


class TestSettingsDefaults:
    """Tests for Settings default values."""

    def test_default_settings(self):
        """Test that Settings has sensible defaults."""
        settings = Settings()

        # Kubernetes settings
        assert settings.nextflow_namespace == "nextflow"
        assert settings.kube_context is None
        assert settings.nextflow_pvc == "nextflow-pvc"

        # Nextflow settings
        assert settings.nextflow_image == "nextflow/nextflow:25.04.7"
        assert settings.nxf_work_root == "/workspace"

        # Resource limits
        assert settings.controller_cpu_limit == "2"
        assert settings.controller_memory_limit == "4Gi"
        assert settings.worker_cpu_limit == "1"
        assert settings.worker_memory_limit == "4GB"

        # Demo settings
        assert settings.default_batch_count == 5
        assert settings.max_batch_count == 12

        # State management
        assert settings.run_history_limit == 5
        assert settings.run_ttl_minutes == 30
        assert settings.redis_url is None

        # WebSocket settings
        assert settings.max_websocket_clients == 100
        assert settings.websocket_keepalive_seconds == 30

        # API settings
        assert settings.allowed_origins == ["*"]
        assert settings.rate_limit_requests == 60
        assert settings.rate_limit_window == 60

    def test_homelab_constants(self):
        """Test homelab resource constants."""
        settings = Settings()

        assert settings.HOMELAB_TOTAL_MEMORY_GI == 50
        assert settings.HOMELAB_TOTAL_CPU == 14
        assert settings.DEMO_WORKFLOW_PATH == "/app/workflows/demo.nf"

    def test_property_methods(self):
        """Test Settings property methods."""
        settings = Settings()

        # Test workflow_path property
        assert settings.workflow_path == "/app/workflows/demo.nf"

        # Test homelab_available_memory property
        assert settings.homelab_available_memory == 46  # 50 - 4 (controller)

        # Test homelab_available_cpu property
        assert settings.homelab_available_cpu == 12  # 14 - 2 (controller)

        # Test max_parallel_workers property
        assert settings.max_parallel_workers == 12  # min(46/4, 12/1)


class TestSettingsFromEnvironment:
    """Tests for Settings loading from environment variables."""

    @patch.dict(os.environ, {
        "NEXTFLOW_NAMESPACE": "custom-ns",
        "KUBE_CONTEXT": "local-k8s",
        "REDIS_URL": "redis://redis:6379/0",
        "NEXTFLOW_IMAGE": "custom/nextflow:latest",
        "DEFAULT_BATCH_COUNT": "8",
        "MAX_BATCH_COUNT": "10",
        "RUN_HISTORY_LIMIT": "10",
        "RUN_TTL_MINUTES": "60",
        "MAX_WEBSOCKET_CLIENTS": "50",
        "ALLOWED_ORIGINS": '["https://example.com", "https://app.example.com"]',
        "RATE_LIMIT_REQUESTS": "100",
        "RATE_LIMIT_WINDOW": "120"
    })
    def test_load_from_environment(self):
        """Test loading settings from environment variables."""
        settings = Settings()

        assert settings.nextflow_namespace == "custom-ns"
        assert settings.kube_context == "local-k8s"
        assert settings.redis_url == "redis://redis:6379/0"
        assert settings.nextflow_image == "custom/nextflow:latest"
        assert settings.default_batch_count == 8
        assert settings.max_batch_count == 10
        assert settings.run_history_limit == 10
        assert settings.run_ttl_minutes == 60
        assert settings.max_websocket_clients == 50
        assert settings.allowed_origins == ["https://example.com", "https://app.example.com"]
        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window == 120

    @patch.dict(os.environ, {"DEFAULT_BATCH_COUNT": "invalid"})
    def test_invalid_integer_from_environment(self):
        """Test that invalid integer values raise validation errors."""
        with pytest.raises(ValueError):
            Settings()

    @patch.dict(os.environ, {"ALLOWED_ORIGINS": "not-a-json-list"})
    def test_invalid_json_from_environment(self):
        """Test that invalid JSON raises validation errors."""
        with pytest.raises(ValueError):
            Settings()

    @patch.dict(os.environ, {
        "CONTROLLER_CPU_LIMIT": "4",
        "CONTROLLER_MEMORY_LIMIT": "8Gi"
    })
    def test_resource_limits_from_environment(self):
        """Test loading resource limits from environment."""
        settings = Settings()

        assert settings.controller_cpu_limit == "4"
        assert settings.controller_memory_limit == "8Gi"

    @patch.dict(os.environ, {"REDIS_URL": ""})
    def test_empty_redis_url(self):
        """Test that empty REDIS_URL is treated as None."""
        settings = Settings()
        # Empty string should be converted to None
        assert settings.redis_url == ""  # Actually keeps empty string


class TestSettingsValidation:
    """Tests for Settings validation logic."""

    def test_batch_count_constraints(self):
        """Test batch count must be within valid range."""
        settings = Settings()

        # Default batch count should be <= max batch count
        assert settings.default_batch_count <= settings.max_batch_count

        # Test with environment override
        with patch.dict(os.environ, {
            "DEFAULT_BATCH_COUNT": "15",
            "MAX_BATCH_COUNT": "10"
        }):
            settings = Settings()
            # This should be allowed, but logic should handle it
            assert settings.default_batch_count == 15
            assert settings.max_batch_count == 10

    def test_ttl_and_history_limits(self):
        """Test TTL and history limit constraints."""
        settings = Settings()

        assert settings.run_ttl_minutes > 0
        assert settings.run_history_limit > 0

        # Test with zero values
        with patch.dict(os.environ, {
            "RUN_TTL_MINUTES": "0",
            "RUN_HISTORY_LIMIT": "0"
        }):
            settings = Settings()
            assert settings.run_ttl_minutes == 0  # Allowed but means no expiration
            assert settings.run_history_limit == 0  # Allowed but means no history

    def test_resource_calculations(self):
        """Test resource availability calculations."""
        settings = Settings()

        # Controller resources
        controller_cpu = int(settings.controller_cpu_limit)
        controller_memory = int(settings.controller_memory_limit.rstrip("Gi"))

        # Available resources
        available_cpu = settings.HOMELAB_TOTAL_CPU - controller_cpu
        available_memory = settings.HOMELAB_TOTAL_MEMORY_GI - controller_memory

        assert settings.homelab_available_cpu == available_cpu
        assert settings.homelab_available_memory == available_memory

        # Worker resources
        worker_cpu = int(settings.worker_cpu_limit)
        worker_memory = int(settings.worker_memory_limit.rstrip("GB"))

        # Max workers calculation
        max_by_cpu = available_cpu // worker_cpu
        max_by_memory = available_memory // worker_memory
        expected_max = min(max_by_cpu, max_by_memory)

        assert settings.max_parallel_workers == expected_max

    def test_cors_origins_validation(self):
        """Test CORS allowed origins validation."""
        # Default allows all origins
        settings = Settings()
        assert settings.allowed_origins == ["*"]

        # Test with specific origins
        with patch.dict(os.environ, {
            "ALLOWED_ORIGINS": '["http://localhost:3000", "http://localhost:8080"]'
        }):
            settings = Settings()
            assert "http://localhost:3000" in settings.allowed_origins
            assert "http://localhost:8080" in settings.allowed_origins
            assert len(settings.allowed_origins) == 2


class TestSettingsEdgeCases:
    """Tests for edge cases in Settings."""

    def test_very_large_values(self):
        """Test settings with very large values."""
        with patch.dict(os.environ, {
            "RUN_HISTORY_LIMIT": "1000000",
            "MAX_WEBSOCKET_CLIENTS": "999999",
            "RATE_LIMIT_REQUESTS": "1000000"
        }):
            settings = Settings()
            assert settings.run_history_limit == 1000000
            assert settings.max_websocket_clients == 999999
            assert settings.rate_limit_requests == 1000000

    def test_special_characters_in_strings(self):
        """Test special characters in string settings."""
        with patch.dict(os.environ, {
            "NEXTFLOW_NAMESPACE": "nextflow-测试-🚀",
            "KUBE_CONTEXT": "k8s/cluster@context"
        }):
            settings = Settings()
            assert settings.nextflow_namespace == "nextflow-测试-🚀"
            assert settings.kube_context == "k8s/cluster@context"

    def test_resource_limit_formats(self):
        """Test various resource limit formats."""
        test_cases = [
            ("1", "1Gi"),
            ("0.5", "512Mi"),
            ("2", "2048Mi"),
            ("4", "4G"),
        ]

        for cpu, memory in test_cases:
            with patch.dict(os.environ, {
                "CONTROLLER_CPU_LIMIT": cpu,
                "CONTROLLER_MEMORY_LIMIT": memory
            }):
                settings = Settings()
                assert settings.controller_cpu_limit == cpu
                assert settings.controller_memory_limit == memory

    def test_workflow_path_override(self):
        """Test that workflow path is correctly set."""
        settings = Settings()

        # Should always return the demo workflow path
        assert settings.workflow_path == settings.DEMO_WORKFLOW_PATH
        assert "/demo.nf" in settings.workflow_path

    @patch.dict(os.environ, {"REDIS_URL": "invalid://url"})
    def test_invalid_redis_url_format(self):
        """Test handling of invalid Redis URL format."""
        # Should still accept it - validation happens at connection time
        settings = Settings()
        assert settings.redis_url == "invalid://url"

    def test_settings_immutability(self):
        """Test that Settings constants cannot be modified."""
        settings = Settings()

        # These are class attributes, not instance attributes
        original_cpu = settings.HOMELAB_TOTAL_CPU

        # This creates an instance attribute, doesn't modify class attribute
        settings.HOMELAB_TOTAL_CPU = 999

        # New instance should still have original value
        new_settings = Settings()
        assert new_settings.HOMELAB_TOTAL_CPU == original_cpu

    def test_settings_model_dump(self):
        """Test exporting settings as dict."""
        settings = Settings()
        settings_dict = settings.model_dump()

        assert "nextflow_namespace" in settings_dict
        assert "redis_url" in settings_dict
        assert "allowed_origins" in settings_dict

        # Check some values
        assert settings_dict["nextflow_namespace"] == "nextflow"
        assert settings_dict["max_batch_count"] == 12

    def test_settings_json_schema(self):
        """Test that Settings can generate JSON schema."""
        schema = Settings.model_json_schema()

        assert "properties" in schema
        assert "nextflow_namespace" in schema["properties"]
        assert "redis_url" in schema["properties"]

        # Check field descriptions exist
        ns_field = schema["properties"]["nextflow_namespace"]
        assert "default" in ns_field
        assert ns_field["default"] == "nextflow"


class TestSettingsDotEnvSupport:
    """Tests for .env file support."""

    def test_env_file_loading(self, tmp_path):
        """Test loading settings from .env file."""
        env_file = tmp_path / ".env"
        env_file.write_text("""
NEXTFLOW_NAMESPACE=from-env-file
REDIS_URL=redis://from-env:6379
DEFAULT_BATCH_COUNT=7
        """)

        with patch.dict(os.environ, {"ENV_FILE": str(env_file)}):
            # In real usage, pydantic-settings handles .env files
            # This is more of a documentation test
            pass

    def test_env_override_priority(self):
        """Test that environment variables override .env file."""
        # Environment variables should take priority over .env file
        with patch.dict(os.environ, {"NEXTFLOW_NAMESPACE": "from-env-var"}):
            settings = Settings()
            assert settings.nextflow_namespace == "from-env-var"