from __future__ import annotations

import json
from pathlib import Path
from shutil import copytree
import subprocess
import tarfile

import yaml

from crew_customs.cli import main
from crew_customs.release import validate_history_privacy, validate_publish_snapshot


def runner(arguments: list[str]) -> int:
    return main(arguments)


def _copy_schemas(root: Path) -> None:
    copytree(Path(__file__).parents[1] / "schemas", root / "schemas")


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_validate_returns_nonzero_for_invalid_repo(tmp_path: Path, capsys):
    _copy_schemas(tmp_path)
    _write_yaml(tmp_path / "data/airports/JFK.yaml", {"iataCode": "not-an-iata"})

    result = runner(["validate", "--root", str(tmp_path)])

    assert result == 1
    assert "iataCode" in capsys.readouterr().err


def test_build_refuses_invalid_data(tmp_path: Path, capsys):
    _copy_schemas(tmp_path)
    _write_yaml(tmp_path / "data/airports/JFK.yaml", {"iataCode": "not-an-iata"})

    result = runner(["build", "--root", str(tmp_path)])

    assert result == 1
    assert "iataCode" in capsys.readouterr().err


def test_init_network_requires_explicit_mapping_even_when_csv_has_country_code(
    tmp_path: Path, capsys
):
    _copy_schemas(tmp_path)
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "TST,Test City,Testland,TL,Test Region\n",
        encoding="utf-8",
    )

    result = runner(["init-network", "--csv", str(csv_path), "--root", str(tmp_path)])

    assert result == 1
    assert "Unresolved country mapping" in capsys.readouterr().err
    assert not (tmp_path / "data/airports/TST.yaml").exists()


def test_init_network_rejects_mapping_missing_a_csv_country_name(tmp_path: Path, capsys):
    _copy_schemas(tmp_path)
    _write_yaml(tmp_path / "data/country_mapping.yaml", {"Otherland": "OL"})
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "TST,Test City,Testland,TL,Test Region\n",
        encoding="utf-8",
    )

    result = runner(["init-network", "--csv", str(csv_path), "--root", str(tmp_path)])

    assert result == 1
    assert "Unresolved country mapping" in capsys.readouterr().err


def test_init_network_rejects_blank_country_mapping_key(tmp_path: Path, capsys):
    _copy_schemas(tmp_path)
    _write_yaml(
        tmp_path / "data/country_mapping.yaml",
        {"": "US", "United States": "US"},
    )
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,US,Americas\n",
        encoding="utf-8",
    )

    result = runner(["init-network", "--csv", str(csv_path), "--root", str(tmp_path)])

    assert result == 1
    assert "Country mapping keys must be non-empty" in capsys.readouterr().err


def test_init_network_creates_safe_pending_airport_and_country_records(
    tmp_path: Path,
):
    _copy_schemas(tmp_path)
    _write_yaml(tmp_path / "data/country_mapping.yaml", {"Testland": "TL"})
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region,Flight No.,Related_Hotel,Flag_Url\n"
        "TST,Test City,Testland,,Test Region,EY001,Private Hotel,https://x.sharepoint.example\n"
        "TST,Test City,Testland,TL,Test Region,EY002,Private Hotel,https://x.sharepoint.example\n"
        "AUH,Abu Dhabi,United Arab Emirates,AE,Asia,EY003,Private Hotel,https://x.sharepoint.example\n",
        encoding="utf-8",
    )

    result = runner(
        [
            "init-network",
            "--csv",
            str(csv_path),
            "--exclude",
            "AUH",
            "--root",
            str(tmp_path),
        ]
    )

    assert result == 0
    airport = yaml.safe_load((tmp_path / "data/airports/TST.yaml").read_text())
    country = yaml.safe_load((tmp_path / "data/countries/TL.yaml").read_text())
    assert airport["countryIso2"] == "TL"
    assert airport["reviewStatus"] == "research_pending"
    assert airport["lastVerified"] is None
    assert airport["nextReviewDue"] is None
    assert country["reviewStatus"] == "research_pending"
    assert country["lastVerified"] is None
    assert country["nextReviewDue"] is None
    assert not (tmp_path / "data/airports/AUH.yaml").exists()
    serialized = (tmp_path / "data/airports/TST.yaml").read_text() + (
        tmp_path / "data/countries/TL.yaml"
    ).read_text()
    for forbidden in ("EY001", "Private Hotel", "sharepoint", "Flight No."):
        assert forbidden not in serialized
    assert runner(["validate", "--root", str(tmp_path)]) == 0


def test_build_uses_root_relative_default_output_and_truthful_pending_metadata(
    tmp_path: Path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    root = Path("repository")
    _copy_schemas(root)
    _write_yaml(root / "data/country_mapping.yaml", {"Testland": "TL"})
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "TST,Test City,Testland,TL,Test Region\n",
        encoding="utf-8",
    )
    assert runner(["init-network", "--csv", str(csv_path), "--root", str(root)]) == 0

    assert runner(["build", "--root", str(root)]) == 0

    output = root / "public/api/v1"
    assert output.is_dir()
    assert not (root / "repository/public/api/v1").exists()
    airport = json.loads((output / "airports/TST.json").read_text(encoding="utf-8"))
    country = json.loads((output / "countries/TL.json").read_text(encoding="utf-8"))
    status = json.loads((output / "status.json").read_text(encoding="utf-8"))
    assert airport["lastVerified"] is None
    assert airport["nextReviewDue"] is None
    assert country["lastVerified"] is None
    assert country["nextReviewDue"] is None
    assert status["oldestVerificationDate"] is None


def test_build_uses_explicit_or_environment_utc_timestamp_reproducibly(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "repository"
    _copy_schemas(root)
    _write_yaml(root / "data/country_mapping.yaml", {"Testland": "TL"})
    csv_path = tmp_path / "network.csv"
    csv_path.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "TST,Test City,Testland,TL,Test Region\n",
        encoding="utf-8",
    )
    assert runner(["init-network", "--csv", str(csv_path), "--root", str(root)]) == 0

    built_at = "2026-08-25T00:00:00Z"
    explicit = root / "first"
    environment = root / "second"
    assert runner([
        "build", "--root", str(root), "--output", str(explicit),
        "--built-at", built_at,
    ]) == 0
    monkeypatch.setenv("SOURCE_DATE_EPOCH", built_at)
    assert runner(["build", "--root", str(root), "--output", str(environment)]) == 0

    assert json.loads((explicit / "status.json").read_text())["builtAt"] == built_at
    assert {
        path.relative_to(explicit): path.read_bytes()
        for path in explicit.rglob("*.json")
    } == {
        path.relative_to(environment): path.read_bytes()
        for path in environment.rglob("*.json")
    }


def test_build_rejects_non_utc_reproducible_timestamp(tmp_path: Path, capsys):
    _copy_schemas(tmp_path)

    result = runner([
        "build", "--root", str(tmp_path), "--built-at", "2026-08-25T01:00:00+01:00",
    ])

    assert result == 1
    assert "UTC ISO 8601" in capsys.readouterr().err


def test_release_check_rejects_private_inputs_and_operational_content(
    tmp_path: Path, capsys
):
    (tmp_path / "release-manifest.json").write_text(
        (Path(__file__).parents[1] / "release-manifest.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/Final_Flight_Numbers_DIL.csv").write_text(
        "Route,Flight No.,Related_Hotel\nJFK,EY001,Private Hotel\n",
        encoding="utf-8",
    )
    public_file = tmp_path / "public/api/v1/status.json"
    public_file.parent.mkdir(parents=True)
    public_file.write_text('{"source": "SharePoint"}\n', encoding="utf-8")

    errors = validate_publish_snapshot(tmp_path)

    assert any("inputs" in error for error in errors)
    assert any("sharepoint" in error.casefold() for error in errors)
    assert runner(["release-check", "--root", str(tmp_path)]) == 1
    assert "Publish snapshot rejected" in capsys.readouterr().err


def _write_release_manifest(root: Path) -> None:
    (root / "release-manifest.json").write_text(
        (Path(__file__).parents[1] / "release-manifest.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True)


def _private_history_repo(tmp_path: Path) -> Path:
    root = tmp_path / "history-repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    _write_release_manifest(root)
    public = root / "public/api/v1/status.json"
    public.parent.mkdir(parents=True)
    public.write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").write_text(".superpowers/\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "safe snapshot")

    sensitive = root / "inputs/network.csv"
    sensitive.parent.mkdir()
    sensitive.write_text("Key_Flight,Related_Hotel\nEY001,Private Hotel\n", encoding="utf-8")
    _git(root, "add", "inputs/network.csv")
    _git(root, "commit", "-qm", "add private input")
    _git(root, "rm", "-q", "inputs/network.csv")
    _git(root, "commit", "-qm", "remove private input")
    (root / ".superpowers/private-note.md").parent.mkdir()
    (root / ".superpowers/private-note.md").write_text("do not publish\n", encoding="utf-8")
    return root


def test_history_privacy_gate_requires_snapshot_only_publication(
    tmp_path: Path, capsys
):
    root = _private_history_repo(tmp_path)

    errors = validate_history_privacy(root)

    assert any("snapshot-only publication required" in error for error in errors)
    assert any("inputs/network.csv" in error for error in errors)
    assert any("key_flight" in error.casefold() for error in errors)
    assert runner(["release-check", "--root", str(root)]) == 1
    assert "snapshot-only publication required" in capsys.readouterr().err


def test_release_snapshot_uses_tracked_head_and_excludes_ignored_superpowers(
    tmp_path: Path,
):
    root = _private_history_repo(tmp_path)
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"

    assert runner(["release-snapshot", "--root", str(root), "--output", str(first)]) == 0
    assert runner(["release-snapshot", "--root", str(root), "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        names = archive.getnames()

    assert "StationInformation/release-manifest.json" in names
    assert "StationInformation/public/api/v1/status.json" in names
    assert not any(".superpowers" in name for name in names)
    assert not any("inputs/" in name for name in names)
