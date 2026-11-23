"""Load config.yaml + companies.yaml (shared by the CLI and the web app)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    cfg_path = ROOT / "config.yaml"
    cfg = (yaml.safe_load(cfg_path.read_text()) or {}) if cfg_path.exists() else {}
    companies = ROOT / "companies.yaml"
    if companies.exists():
        cfg.update(yaml.safe_load(companies.read_text()) or {})
    return cfg
