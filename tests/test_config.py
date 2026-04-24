"""Tests for configuration loading."""

import tempfile
from pathlib import Path

import yaml

from codewise.config import load_config, find_config_file, generate_default_config, _deep_merge


def test_deep_merge():
    base = {"a": 1, "b": {"c": 2, "d": 3}, "e": [1, 2]}
    override = {"b": {"c": 99}, "f": "new"}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3
    assert result["f"] == "new"


def test_load_config_defaults():
    config, rules = load_config()
    assert config.model == "gpt-4o-mini"
    assert config.temperature == 0.1
    assert config.review_enabled is True


def test_load_config_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.5,
            "rules": {
                "enable_packs": ["security-basics"],
            }
        }, f)
        f.flush()

        config, rules = load_config(config_path=f.name)
        assert config.model == "claude-sonnet-4-20250514"
        assert config.temperature == 0.5
        assert len(rules) > 0

    Path(f.name).unlink()


def test_load_config_with_overrides():
    config, _ = load_config(overrides={"model": "custom-model", "temperature": 0.9})
    assert config.model == "custom-model"
    assert config.temperature == 0.9


def test_generate_default_config():
    template = generate_default_config()
    assert "model:" in template
    assert "rules:" in template
    assert "hooks:" in template
    assert "pre_push:" in template
    # Should be valid YAML
    data = yaml.safe_load(template)
    assert data["model"] == "gpt-4o-mini"
