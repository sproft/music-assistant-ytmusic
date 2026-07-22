"""Tests that lock down the provider manifest contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


MANIFEST_PATH = Path(__file__).resolve().parents[2] / "ytmusic_free" / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_required_top_level_fields(manifest):
    for key in ("type", "domain", "name", "description", "codeowners", "requirements"):
        assert key in manifest, f"manifest is missing required key: {key}"


def test_manifest_domain_matches_package_dir(manifest):
    assert manifest["domain"] == "ytmusic_free"
    assert MANIFEST_PATH.parent.name == manifest["domain"]


def test_manifest_type_is_music(manifest):
    assert manifest["type"] == "music"


def test_manifest_codeowners_non_empty(manifest):
    assert isinstance(manifest["codeowners"], list)
    assert manifest["codeowners"], "manifest must list at least one codeowner"


def test_manifest_requirements_pin_known_libs(manifest):
    requirements = manifest["requirements"]
    assert isinstance(requirements, list)
    joined = " ".join(requirements)
    assert "ytmusicapi" in joined
    assert "yt-dlp" in joined
    # duration-parser was dropped once timestamp parsing moved in-house (PR #29);
    # guard against it creeping back as a needless dependency.
    assert "duration-parser" not in joined


def test_manifest_documentation_url_present(manifest):
    assert manifest.get("documentation", "").startswith("https://")


def test_manifest_multi_instance_enabled(manifest):
    assert manifest.get("multi_instance") is True
