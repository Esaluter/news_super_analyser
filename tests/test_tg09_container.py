from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_python_311_and_unified_entrypoint():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in text
    assert 'CMD ["python", "run.py"]' in text
    assert "PYTHONUNBUFFERED=1" in text
    assert "TOKEN=" not in text
    assert "SECRET=" not in text


def test_dockerignore_excludes_secrets_logs_tests_and_caches():
    lines = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {".env", "logs/", "tests/", ".pytest_cache", "**/__pycache__"} <= lines
