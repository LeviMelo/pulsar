"""Project configuration and filesystem layout.

One rule, enforced by structure: **secrets are never values in config**, only
environment-variable *names*. `config/default.toml` is safe to commit and safe to
paste into a bug report.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path
    data_dir: Path
    database: Path
    public_dataset_dir: Path
    state_dir: Path
    opportunity_ledger_json: Path
    opportunity_ledger_csv: Path
    professors_csv: Path
    profile_yaml: Path

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


class AppConfig:
    def __init__(self, root: Path, raw: dict[str, Any]):
        self.root = root.resolve()
        self.raw = raw
        p = raw.get("paths", {})
        self.paths = ProjectPaths(
            root=self.root,
            data_dir=self._path(p.get("data_dir", "data")),
            database=self._path(p.get("database", "data/pulsar.duckdb")),
            public_dataset_dir=self._path(p.get("public_dataset_dir", "data/public_sigaa")),
            state_dir=self._path(p.get("state_dir", "data/state")),
            opportunity_ledger_json=self._path(p.get("opportunity_ledger_json", "data/state/opportunity_ledger.json")),
            opportunity_ledger_csv=self._path(p.get("opportunity_ledger_csv", "data/state/opportunity_ledger.csv")),
            professors_csv=self._path(p.get("professors_csv", "data/state/professores_ufal.csv")),
            profile_yaml=self._path(p.get("profile_yaml", "config/profile.yaml")),
        )

    def _path(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    @classmethod
    def load(cls, root: Path | str | None = None) -> "AppConfig":
        root_path = discover_root(root)
        cfg_path = root_path / "config" / "default.toml"
        if not cfg_path.exists():
            raise FileNotFoundError(
                f"Configuration not found: {cfg_path}. Run `pulsar init` from the project root."
            )
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)
        # `config/local.toml` is git-ignored and overrides the tracked defaults.
        # Personal, machine-specific settings (the address you send from, an
        # alternate embedding host) belong there rather than in a shared file.
        # It holds settings, never secrets: those stay in the environment.
        local_path = root_path / "config" / "local.toml"
        if local_path.exists():
            with local_path.open("rb") as fh:
                raw = _deep_merge(raw, tomllib.load(fh))
        return cls(root_path, raw)

    # -- sections ------------------------------------------------------------

    @property
    def sigaa(self) -> dict[str, Any]:
        return self.raw.get("sigaa", {})

    @property
    def semantics(self) -> dict[str, Any]:
        return self.raw.get("semantics", {})

    @property
    def embedding(self) -> dict[str, Any]:
        return self.raw.get("embedding", {})

    @property
    def smtp(self) -> dict[str, Any]:
        return self.raw.get("smtp", {})

    def load_profile(self) -> dict[str, Any]:
        if not self.paths.profile_yaml.exists():
            return {"name": "default", "semantic_profile": "", "qualifications_text": "",
                    "campaign_signature": ""}
        with self.paths.profile_yaml.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def ensure_dirs(self) -> None:
        for path in (
            self.paths.data_dir,
            self.paths.public_dataset_dir,
            self.paths.state_dir,
            self.paths.cache_dir,
            self.paths.exports_dir,
            self.paths.data_dir / "raw",
        ):
            path.mkdir(parents=True, exist_ok=True)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; `override` wins at the leaves."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def discover_root(root: Path | str | None = None) -> Path:
    """Locate the project root from an explicit path, `$PULSAR_HOME`, or the cwd."""
    if root:
        return Path(root).expanduser().resolve()
    env = os.getenv("PULSAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "default.toml").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return current


def env_secret(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(
            f"Required secret environment variable is not set: {name}. "
            f"See .env.example; never put credentials in config/default.toml."
        )
    return value
