from pathlib import Path


def test_no_legacy_secret_constants():
    root = Path(__file__).parents[1] / "src"
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))
    assert "LOGIN_PSW" not in text
    assert "LOGIN_CPF" not in text
