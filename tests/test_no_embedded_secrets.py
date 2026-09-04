import os
from pathlib import Path


def test_no_legacy_secret_constants():
    root = Path(__file__).parents[1] / "src"
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))
    assert "LOGIN_PSW" not in text
    assert "LOGIN_CPF" not in text


def test_dotenv_is_loaded_and_never_overrides_the_real_environment(tmp_path, monkeypatch):
    """.env is what the README and the config error message tell you to use."""
    from pulsar_research.config import load_dotenv

    (tmp_path / ".env").write_text(
        "# comment\n"
        "\n"
        "UFAL_SIGAA_USERNAME=123.456.789-00\n"
        'PULSAR_SMTP_PASSWORD="quoted secret"\n'
        "export PULSAR_SMTP_USER=levi@famed.ufal.br\n"
        "ALREADY_SET=from_file\n"
        "malformed line without equals\n",
        encoding="utf-8")
    monkeypatch.delenv("UFAL_SIGAA_USERNAME", raising=False)
    monkeypatch.delenv("PULSAR_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("PULSAR_SMTP_USER", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from_environment")

    applied = load_dotenv(tmp_path)

    assert os.environ["UFAL_SIGAA_USERNAME"] == "123.456.789-00"
    assert os.environ["PULSAR_SMTP_PASSWORD"] == "quoted secret", "quotes are stripped"
    assert os.environ["PULSAR_SMTP_USER"] == "levi@famed.ufal.br", "`export ` prefix is tolerated"
    assert os.environ["ALREADY_SET"] == "from_environment", "the real environment wins"
    assert "ALREADY_SET" not in applied
    assert set(applied.values()) == {"set"}, "the report must never carry a value"


def test_dotenv_is_optional(tmp_path):
    from pulsar_research.config import load_dotenv

    assert load_dotenv(tmp_path) == {}
