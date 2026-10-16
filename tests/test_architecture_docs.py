"""Tests for architecture documentation (US-08).

Verifies that architecture documentation exists and is comprehensive.
"""

import pytest
from pathlib import Path


class TestArchitectureDocumentation:
    """Test architecture documentation files."""

    def test_architecture_doc_exists(self):
        """Verify docs/architecture.md exists."""
        arch_doc = Path("docs/architecture.md")
        assert arch_doc.exists()

    def test_architecture_doc_describes_communities(self):
        """Verify architecture doc describes community structure."""
        arch_doc = Path("docs/architecture.md")
        content = arch_doc.read_text()

        # Should describe at least 4 significant communities
        assert "Community" in content
        assert "API Endpoints" in content or "API" in content
        assert "Repository" in content or "User Data Model" in content
        assert "Configuration" in content or "Settings" in content

    def test_architecture_doc_documents_isolated_nodes(self):
        """Verify architecture doc documents isolated nodes."""
        arch_doc = Path("docs/architecture.md")
        content = arch_doc.read_text()

        # Should mention postgres_data volume
        assert "postgres_data" in content

        # Should mention package root
        assert "__init__.py" in content or "Package-Root" in content

    def test_architecture_doc_documents_thin_communities(self):
        """Verify architecture doc documents thin communities."""
        arch_doc = Path("docs/architecture.md")
        content = arch_doc.read_text()

        # Should have table of thin communities
        assert "Community" in content
        assert "Inhalt" in content or "Entscheidung" in content

    def test_architecture_doc_marks_resolved_gaps(self):
        """Verify architecture doc marks resolved code gaps."""
        arch_doc = Path("docs/architecture.md")
        content = arch_doc.read_text()

        # Should mention resolved gaps
        assert "BEHOBEN" in content or "behoben" in content

        # Should reference US-03, US-07
        assert "US-03" in content
        assert "US-07" in content

    def test_readme_links_to_architecture(self):
        """Verify README.md links to architecture documentation."""
        readme = Path("README.md")

        if readme.exists():
            content = readme.read_text()
            # Should link to architecture docs
            assert "docs/architecture.md" in content or "architecture" in content.lower()
