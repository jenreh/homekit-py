from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from homekit.cli.main import app


def test_cli_help_lists_subcommands(tmp_homekit_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("discover", "pair", "entities", "diagnose", "raw"):
        assert command in result.stdout


def test_diagnose_storage_passes(tmp_homekit_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["diagnose", "storage"])
    assert result.exit_code == 0
    assert "storage" in result.stdout


def test_raw_write_blocked_by_default(tmp_homekit_config: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["raw", "write", "AA", "1", "2", "true"])
    assert result.exit_code != 0


def test_pairings_export_without_pairings_returns_not_paired(
    tmp_homekit_config: Path,
) -> None:
    runner = CliRunner()
    out = tmp_homekit_config / "backup.json"
    result = runner.invoke(app, ["pairings", "export", "--out", str(out)])
    assert result.exit_code == 11
