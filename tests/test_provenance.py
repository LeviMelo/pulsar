from __future__ import annotations

import pytest

from pulsar_research.semantics.provenance import (ProfileRunIdentity, SemanticSpaceIdentity,
                                                  check_staleness, record_space)


def _identity(fingerprint="abc", architecture=None, embedding=None):
    return SemanticSpaceIdentity(
        corpus_fingerprint=fingerprint,
        architecture=architecture or {"latent": {"kind": "sppmi_svd", "n_components": 300}},
        embedding_identity=embedding or {},
        versions={"numpy": "2.0.0"},
    )


def test_editing_the_operator_profile_does_not_change_the_space():
    space = _identity()
    a = ProfileRunIdentity(space.space_id, {"queries": {"domain": "epidemiologia"}})
    b = ProfileRunIdentity(space.space_id, {"queries": {"domain": "biologia celular"}})
    assert a.run_id != b.run_id, "a different profile must be a different run"
    assert a.space_id == b.space_id, "a different profile must NOT be a different space"


def test_space_id_changes_with_corpus_architecture_or_embedding_model():
    base = _identity().space_id
    assert _identity(fingerprint="xyz").space_id != base
    assert _identity(architecture={"latent": {"kind": "lsa", "n_components": 384}}).space_id != base
    assert _identity(embedding={"model": "qwen3-4b"}).space_id != base


def test_staleness_is_detected_when_the_corpus_moves(db):
    identity = _identity(fingerprint="corpus-v1")
    record_space(db, identity, {"opportunities": 3})
    assert not check_staleness(db, "corpus-v1").is_stale
    stale = check_staleness(db, "corpus-v2")
    assert stale.is_stale
    assert "pulsar semantics build" in stale.describe()


def test_no_space_reads_as_stale(db):
    assert check_staleness(db, "anything").is_stale


def test_local_config_overrides_defaults_without_touching_the_tracked_file(tmp_path, monkeypatch):
    """Personal settings live in a git-ignored overlay, merged key by key."""
    import tomllib

    from pulsar_research.config import AppConfig

    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    tracked = root / "config" / "default.toml"
    tracked.write_text(
        '[smtp]\nhost = ""\nport = 587\nstarttls = true\n'
        '[semantics]\nlsa_components = 384\n', encoding="utf-8")

    plain = AppConfig.load(root)
    assert plain.smtp["host"] == "" and plain.semantics["lsa_components"] == 384

    (root / "config" / "local.toml").write_text(
        '[smtp]\nhost = "smtp.gmail.com"\nfrom_address = "levi@famed.ufal.br"\n', encoding="utf-8")
    merged = AppConfig.load(root)
    assert merged.smtp["host"] == "smtp.gmail.com"
    assert merged.smtp["from_address"] == "levi@famed.ufal.br"
    assert merged.smtp["port"] == 587, "keys the overlay omits must survive the merge"
    assert merged.semantics["lsa_components"] == 384, "untouched sections must survive"
    assert tomllib.loads(tracked.read_text(encoding="utf-8"))["smtp"]["host"] == "", \
        "the tracked file must not be modified"
