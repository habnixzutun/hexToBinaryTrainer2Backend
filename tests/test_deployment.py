"""Tests for deployment configuration (US-06).

Verifies that deployment files are properly configured.
"""

import pytest
import yaml
from pathlib import Path


class TestDeploymentConfig:
    """Test deployment configuration files."""

    def test_dockerfile_exists(self):
        """Verify Dockerfile exists."""
        dockerfile = Path("Dockerfile")
        assert dockerfile.exists()

    def test_dockerfile_uses_pinned_uv_version(self):
        """Verify Dockerfile pins uv version (not :latest)."""
        dockerfile = Path("Dockerfile")
        content = dockerfile.read_text()

        # Should NOT use :latest
        assert "ghcr.io/astral-sh/uv:latest" not in content

        # Should use a specific version
        assert "ghcr.io/astral-sh/uv:0." in content

    def test_dockerfile_runs_as_non_root(self):
        """Verify Dockerfile creates and uses non-root user."""
        dockerfile = Path("Dockerfile")
        content = dockerfile.read_text()

        # Should create non-root user
        assert "adduser" in content or "useradd" in content

        # Should switch to non-root user
        assert "USER appuser" in content or "USER nobody" in content

    def test_docker_compose_exists(self):
        """Verify docker-compose.yaml exists."""
        compose_file = Path("docker-compose.yaml")
        assert compose_file.exists()

    def test_docker_compose_valid_yaml(self):
        """Verify docker-compose.yaml is valid YAML."""
        compose_file = Path("docker-compose.yaml")
        with open(compose_file) as f:
            config = yaml.safe_load(f)

        assert "services" in config
        assert "api" in config["services"]
        assert "db" in config["services"]

    def test_docker_compose_api_healthcheck(self):
        """Verify API service has healthcheck configured."""
        compose_file = Path("docker-compose.yaml")
        with open(compose_file) as f:
            config = yaml.safe_load(f)

        api_service = config["services"]["api"]
        assert "healthcheck" in api_service
        assert "test" in api_service["healthcheck"]
        assert "interval" in api_service["healthcheck"]
        assert "timeout" in api_service["healthcheck"]
        assert "retries" in api_service["healthcheck"]

    def test_docker_compose_resource_limits(self):
        """Verify services have resource limits configured."""
        compose_file = Path("docker-compose.yaml")
        with open(compose_file) as f:
            config = yaml.safe_load(f)

        # Check API service
        api_service = config["services"]["api"]
        assert "deploy" in api_service
        assert "resources" in api_service["deploy"]
        assert "limits" in api_service["deploy"]["resources"]
        assert "memory" in api_service["deploy"]["resources"]["limits"]

        # Check DB service
        db_service = config["services"]["db"]
        assert "deploy" in db_service
        assert "resources" in db_service["deploy"]
        assert "limits" in db_service["deploy"]["resources"]
        assert "memory" in db_service["deploy"]["resources"]["limits"]

    def test_github_workflow_exists(self):
        """Verify GitHub workflow exists."""
        workflow_file = Path(".github/workflows/deploy.yml")
        assert workflow_file.exists()

    def test_github_workflow_has_test_gate(self):
        """Verify GitHub workflow has test job before deploy."""
        workflow_file = Path(".github/workflows/deploy.yml")
        with open(workflow_file) as f:
            config = yaml.safe_load(f)

        assert "jobs" in config
        assert "test" in config["jobs"]
        assert "deploy" in config["jobs"]

        # Deploy should depend on test
        deploy_job = config["jobs"]["deploy"]
        assert "needs" in deploy_job
        assert "test" in deploy_job["needs"]

    def test_github_workflow_runs_tests_with_coverage(self):
        """Verify GitHub workflow runs pytest with coverage."""
        workflow_file = Path(".github/workflows/deploy.yml")
        content = workflow_file.read_text()

        # Should run pytest with coverage
        assert "pytest" in content
        assert "--cov" in content
        assert "--cov-fail-under" in content

    def test_env_example_exists(self):
        """Verify .env.example exists for documentation."""
        env_example = Path(".env.example")
        assert env_example.exists()

    def test_env_example_documents_required_vars(self):
        """Verify .env.example documents all required variables."""
        env_example = Path(".env.example")
        content = env_example.read_text()

        required_vars = ["DB_USER", "DB_PW", "DB_NAME", "DB_HOST", "DB_PORT"]
        for var in required_vars:
            assert var in content
