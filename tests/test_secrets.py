"""Secret storage tests.

Two properties matter: values survive a round trip, and the file on disk never contains the
value in plain text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from archpilot.security import secrets


@pytest.fixture(autouse=True)
def isolated_secret_store(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own user-data directory, so no real secrets are touched."""
    return isolated_home


def test_set_get_delete_round_trip() -> None:
    secrets.set_secret("api_key", "sk-example-0123456789")
    assert secrets.get_secret("api_key") == "sk-example-0123456789"
    assert secrets.has_secret("api_key") is True
    assert secrets.delete_secret("api_key") is True
    assert secrets.get_secret("api_key") is None
    assert secrets.has_secret("api_key") is False


def test_missing_secret_reads_as_none() -> None:
    assert secrets.get_secret("never_set") is None
    assert secrets.has_secret("never_set") is False
    assert secrets.delete_secret("never_set") is False


def test_setting_an_empty_value_removes_the_secret() -> None:
    secrets.set_secret("k", "value")
    secrets.set_secret("k", "")
    assert secrets.get_secret("k") is None


def test_overwriting_replaces_the_value() -> None:
    secrets.set_secret("k", "first")
    secrets.set_secret("k", "second")
    assert secrets.get_secret("k") == "second"


def test_names_lists_keys_but_no_values() -> None:
    secrets.set_secret("z", "1")
    secrets.set_secret("a", "2")
    names = secrets.secret_names()
    assert names == ["a", "z"]
    assert "2" not in names


def test_clear_all_reports_how_many_were_removed() -> None:
    secrets.set_secret("a", "1")
    secrets.set_secret("b", "2")
    assert secrets.clear_all() == 2
    assert secrets.secret_names() == []
    assert secrets.clear_all() == 0


def test_value_is_never_written_in_plain_text() -> None:
    marker = "SUPER-SECRET-MARKER-9f3a"
    secrets.set_secret("api_key", marker)
    raw = secrets._secrets_path().read_text(encoding="utf-8")
    assert marker not in raw
    assert "api_key" in raw          # the name is stored, the value is not


def test_missing_name_is_refused() -> None:
    with pytest.raises(ValueError):
        secrets.set_secret("", "value")


def test_over_long_value_is_refused() -> None:
    with pytest.raises(ValueError):
        secrets.set_secret("k", "x" * 20_000)


def test_unicode_values_survive_the_round_trip() -> None:
    value = "clé-secrète-£-€-日本語"
    secrets.set_secret("k", value)
    assert secrets.get_secret("k") == value


def test_a_corrupt_store_reads_as_empty_rather_than_crashing() -> None:
    secrets.set_secret("k", "v")
    secrets._secrets_path().write_text("{not json", encoding="utf-8")
    assert secrets.secret_names() == []
    assert secrets.get_secret("k") is None


def test_an_undecryptable_value_reads_as_none() -> None:
    secrets.set_secret("k", "v")
    data = json.loads(secrets._secrets_path().read_text(encoding="utf-8"))
    # Replace the wrapped blob with base64 that cannot be unwrapped.
    data["k"] = "bm90LXJlYWwtY2lwaGVydGV4dA=="
    secrets._secrets_path().write_text(json.dumps(data), encoding="utf-8")
    assert secrets.has_secret("k") is False
    assert secrets.get_secret("k") is None


def test_each_secret_name_is_independent() -> None:
    secrets.set_secret("a", "1")
    secrets.set_secret("b", "2")
    secrets.set_secret("a", "")
    assert secrets.get_secret("a") is None
    assert secrets.get_secret("b") == "2"
