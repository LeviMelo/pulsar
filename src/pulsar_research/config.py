from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    data_dir: Path
    database: Path
    public_dataset_dir: Path
    legacy_ledger_json: Path
    legacy_ledger_csv: Path
    professors_csv: Path
    profile_yaml: Path


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
            legacy_ledger_json=self._path(p.get("legacy_ledger_json", "projects_ledger.json")),
            legacy_ledger_csv=self._path(p.get("legacy_ledger_csv", "projects_ledger.csv")),
            professors_csv=self._path(p.get("professors_csv", "professores_ufal.csv")),
            profile_yaml=self._path(p.get("profile_yaml", "config/profile.yaml")),
        )

    def _path(self, value: str | Path) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    @classmethod
    def load(cls, root: Path | str | None = None) -> "AppConfig":
        root_path = discover_root(root)
        cfg_path = root_path / "config" / "default.toml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"Configuration not found: {cfg_path}. Run `pulsar init` from the project root.")
        with cfg_path.open("rb") as fh:
            raw = tomllib.load(fh)
        return cls(root_path, raw)

    @property
    def sigaa(self) -> dict[str, Any]:
        return self.raw.get("sigaa", {})

    @property
    def analysis(self) -> dict[str, Any]:
        return self.raw.get("analysis", {})

    @property
    def smtp(self) -> dict[str, Any]:
        return self.raw.get("smtp", {})

    def load_profile(self) -> dict[str, Any]:
        if not self.paths.profile_yaml.exists():
            return {"name": "default", "semantic_profile": "", "qualifications_text": "", "campaign_signature": ""}
        with self.paths.profile_yaml.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def ensure_dirs(self) -> None:
        for p in [
            self.paths.data_dir,
            self.paths.public_dataset_dir,
            self.paths.data_dir / "raw",
            self.paths.data_dir / "cache",
            self.paths.data_dir / "exports",
        ]:
            p.mkdir(parents=True, exist_ok=True)


def discover_root(root: Path | str | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    env = os.getenv("PULSAR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "config" / "default.toml").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return cur


def env_secret(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Required secret environment variable is not set: {name}")
    return value
