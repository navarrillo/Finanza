import pytest


@pytest.fixture(autouse=True)
def local_demo_auth(monkeypatch):
    """Existing finance/UI tests use the explicit password-only local demo.

    MFA tests opt back in and always use a temporary authentication database.
    """
    monkeypatch.setenv("MFA_REQUIRED", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
