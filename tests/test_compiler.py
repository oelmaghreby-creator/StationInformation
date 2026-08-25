"""Tests for deterministic, privacy-safe public API compilation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from crew_customs.compiler import _assert_privacy_safe, compile_airport, compile_api
from crew_customs.validate import validate_repository


def _write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    copytree(Path(__file__).parents[1] / "schemas", tmp_path / "schemas")
    airport = {
        "iataCode": "JFK",
        "airportName": "John F. Kennedy International Airport",
        "city": "New York",
        "countryIso2": "US",
        "reviewStatus": "verified",
        "lastVerified": "2026-08-25",
        "nextReviewDue": "2026-11-25",
        "customsOverrides": {
            "prohibitedItems": [
                {"text": "Airport exception", "sourceIds": ["airport-authority"]}
            ]
        },
    }
    country = {
        "iso2": "US",
        "countryName": "United States",
        "customs": {
            "foodAndAgriculture": [],
            "medicines": [],
            "cashDeclaration": {},
            "alcohol": {},
            "tobacco": {},
            "prohibitedItems": [
                {"text": "Country rule", "sourceIds": ["us-customs"]}
            ],
            "restrictedItems": [],
            "declarationRequirements": [],
        },
        "baggageSecurity": {
            "handBaggage": {
                "sharpObjects": [],
                "liquids": [
                    {"text": "Liquids rule", "sourceIds": ["us-customs"]}
                ],
                "batteriesAndElectronics": [],
                "prohibitedItems": [],
            },
            "checkedCargoBag": {
                "knivesAndSharpObjects": [],
                "batteriesAndElectronics": [],
                "prohibitedItems": [],
            },
        },
        "crewNotes": [{"text": "Country note", "sourceIds": ["us-customs"]}],
        "reviewStatus": "verified",
        "lastVerified": "2026-08-20",
        "nextReviewDue": "2026-11-20",
    }
    sources = [
        {
            "id": "us-customs",
            "authorityName": "U.S. Customs and Border Protection",
            "pageTitle": "Travel guidance",
            "url": "https://www.cbp.gov/travel",
            "jurisdiction": "US",
            "accessedAt": "2026-08-20",
            "fingerprint": "sha256:abc123",
            "supportsFields": ["customs.prohibitedItems"],
            "sourceType": "customs",
            "internalComment": "Flight No. EY001; Related_Hotel; Flag_Url; SharePoint",
        },
        {
            "id": "airport-authority",
            "authorityName": "JFK airport authority",
            "pageTitle": "Airport exception",
            "url": "https://www.jfkairport.com/",
            "jurisdiction": "US",
            "accessedAt": "2026-08-25",
            "fingerprint": "sha256:def456",
            "supportsFields": ["customs.prohibitedItems"],
            "sourceType": "airport_authority",
        },
    ]
    _write_yaml(tmp_path / "data/airports/JFK.yaml", airport)
    _write_yaml(tmp_path / "data/countries/US.yaml", country)
    for source in sources:
        _write_yaml(tmp_path / f"data/sources/{source['id']}.yaml", source)
    return tmp_path


def test_compiler_builds_self_contained_airport_with_effective_rules_and_sources(
    sample_repo: Path, tmp_path: Path
):
    output = tmp_path / "api"

    counts = compile_api(
        sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc)
    )

    jfk = json.loads((output / "airports/JFK.json").read_text(encoding="utf-8"))
    assert counts == {"airports": 1, "countries": 1, "sources": 2}
    assert jfk["country"] == {"iso2": "US", "name": "United States"}
    assert jfk["customs"]["prohibitedItems"] == [
        {"sourceIds": ["airport-authority"], "text": "Airport exception"}
    ]
    assert jfk["baggageSecurity"]["handBaggage"]["liquids"] == [
        {"sourceIds": ["us-customs"], "text": "Liquids rule"}
    ]
    assert [source["id"] for source in jfk["sources"]] == [
        "airport-authority",
        "us-customs",
    ]
    assert "fingerprint" not in jfk["sources"][0]
    assert jfk["disclaimer"] == (
        "Verify current requirements with the relevant authority and airline "
        "procedures before travel."
    )
    serialized = json.dumps(jfk)
    for forbidden in ("Flight No.", "Related_Hotel", "Flag_Url", "SharePoint"):
        assert forbidden not in serialized


def test_compiler_build_is_deterministic_and_writes_sorted_metadata(
    sample_repo: Path, tmp_path: Path
):
    built_at = datetime(2026, 8, 25, 9, 30, tzinfo=timezone.utc)
    first = tmp_path / "first"
    second = tmp_path / "second"

    compile_api(sample_repo, first, built_at)
    compile_api(sample_repo, second, built_at)

    assert {
        path.relative_to(first): path.read_bytes()
        for path in sorted(first.rglob("*.json"))
    } == {
        path.relative_to(second): path.read_bytes()
        for path in sorted(second.rglob("*.json"))
    }
    index = json.loads((first / "index.json").read_text(encoding="utf-8"))
    status = json.loads((first / "status.json").read_text(encoding="utf-8"))
    assert index["airports"] == [
        {
            "airportName": "John F. Kennedy International Airport",
            "city": "New York",
            "country": {"iso2": "US", "name": "United States"},
            "endpoint": "/api/v1/airports/JFK.json",
            "iataCode": "JFK",
            "reviewStatus": "verified",
        }
    ]
    assert status == {
        "builtAt": "2026-08-25T09:30:00Z",
        "counts": {"airports": 1, "countries": 1, "sources": 2},
        "datasetVersion": "1.0.0",
        "oldestVerificationDate": "2026-08-20",
        "schemaVersion": "1.0.0",
    }


def test_compiler_leaves_existing_output_untouched_when_build_fails(
    sample_repo: Path, tmp_path: Path
):
    output = tmp_path / "api"
    output.mkdir()
    sentinel = output / "sentinel.json"
    sentinel.write_text('{"existing": true}\n', encoding="utf-8")
    airport = yaml.safe_load((sample_repo / "data/airports/JFK.yaml").read_text())
    airport["crewNotes"] = [{"text": "Broken source", "sourceIds": ["missing"]}]
    _write_yaml(sample_repo / "data/airports/JFK.yaml", airport)

    with pytest.raises(ValueError, match="invalid repository"):
        compile_api(sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc))

    assert sentinel.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_compiler_publishes_null_dates_for_pending_records_and_status(
    sample_repo: Path, tmp_path: Path
):
    airport_path = sample_repo / "data/airports/JFK.yaml"
    airport = yaml.safe_load(airport_path.read_text())
    airport.update(
        reviewStatus="research_pending", lastVerified=None, nextReviewDue=None
    )
    _write_yaml(airport_path, airport)
    country_path = sample_repo / "data/countries/US.yaml"
    country = yaml.safe_load(country_path.read_text())
    country.update(
        reviewStatus="research_pending", lastVerified=None, nextReviewDue=None
    )
    _write_yaml(country_path, country)

    output = tmp_path / "api"
    compile_api(sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc))

    airport_document = json.loads((output / "airports/JFK.json").read_text())
    country_document = json.loads((output / "countries/US.json").read_text())
    status = json.loads((output / "status.json").read_text())
    assert airport_document["lastVerified"] is None
    assert airport_document["nextReviewDue"] is None
    assert country_document["lastVerified"] is None
    assert country_document["nextReviewDue"] is None
    assert status["oldestVerificationDate"] is None


def test_compile_airport_does_not_substitute_missing_verified_review_due(
    sample_repo: Path,
):
    airport = yaml.safe_load((sample_repo / "data/airports/JFK.yaml").read_text())
    country = yaml.safe_load((sample_repo / "data/countries/US.yaml").read_text())
    del airport["nextReviewDue"]
    del country["nextReviewDue"]
    sources = {
        source_path.stem: yaml.safe_load(source_path.read_text())
        for source_path in (sample_repo / "data/sources").glob("*.yaml")
    }

    with pytest.raises(KeyError, match="nextReviewDue"):
        compile_airport(airport, country, sources)


def test_compiler_rejects_flag_url_nested_in_cash_declaration_before_publication(
    sample_repo: Path, tmp_path: Path
):
    output = tmp_path / "api"
    output.mkdir()
    sentinel = output / "sentinel.json"
    sentinel.write_text('{"existing": true}\n', encoding="utf-8")
    country_path = sample_repo / "data/countries/US.yaml"
    country = yaml.safe_load(country_path.read_text(encoding="utf-8"))
    country["customs"]["cashDeclaration"] = {
        "Flag_Url": "https://private.example/flag"
    }
    _write_yaml(country_path, country)

    assert any(
        "customs.cashDeclaration" in error and "Flag_Url" in error
        for error in validate_repository(sample_repo, datetime(2026, 8, 25).date())
    )
    with pytest.raises(ValueError, match="invalid repository"):
        compile_api(sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc))

    assert sentinel.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_compiler_rejects_sharepoint_source_url_without_sensitive_rule_content(
    sample_repo: Path, tmp_path: Path
):
    output = tmp_path / "api"
    output.mkdir()
    sentinel = output / "sentinel.json"
    sentinel.write_text('{"existing": true}\n', encoding="utf-8")
    source_path = sample_repo / "data/sources/us-customs.yaml"
    source = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    source["url"] = "https://contoso.sharepoint.com/sites/internal"
    _write_yaml(source_path, source)

    assert validate_repository(sample_repo, datetime(2026, 8, 25).date()) == []
    with pytest.raises(ValueError, match="Sensitive"):
        compile_api(sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc))

    assert sentinel.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_compiler_preserves_all_policy_object_airport_overrides_and_sources(
    sample_repo: Path, tmp_path: Path
):
    airport_path = sample_repo / "data/airports/JFK.yaml"
    airport = yaml.safe_load(airport_path.read_text(encoding="utf-8"))
    airport["customsOverrides"] = {
        "cashDeclaration": {
            "rules": [{"text": "Declare cash", "sourceIds": ["policy-source"]}]
        },
        "alcohol": {
            "rules": [{"text": "Alcohol rule", "sourceIds": ["policy-source"]}]
        },
        "tobacco": {
            "rules": [{"text": "Tobacco rule", "sourceIds": ["policy-source"]}]
        },
    }
    _write_yaml(airport_path, airport)
    _write_yaml(
        sample_repo / "data/sources/policy-source.yaml",
        {
            "id": "policy-source",
            "authorityName": "Policy authority",
            "pageTitle": "Policy guidance",
            "url": "https://policy.example/guidance",
            "jurisdiction": "US",
            "accessedAt": "2026-08-25",
            "fingerprint": "sha256:policy",
            "supportsFields": [
                "customs.cashDeclaration",
                "customs.alcohol",
                "customs.tobacco",
            ],
            "sourceType": "customs",
        },
    )

    compile_api(sample_repo, tmp_path / "api", datetime(2026, 8, 25, tzinfo=timezone.utc))

    jfk = json.loads((tmp_path / "api/airports/JFK.json").read_text(encoding="utf-8"))
    assert jfk["customs"]["cashDeclaration"] == {
        "rules": [{"sourceIds": ["policy-source"], "text": "Declare cash"}]
    }
    assert jfk["customs"]["alcohol"] == {
        "rules": [{"sourceIds": ["policy-source"], "text": "Alcohol rule"}]
    }
    assert jfk["customs"]["tobacco"] == {
        "rules": [{"sourceIds": ["policy-source"], "text": "Tobacco rule"}]
    }
    assert "policy-source" in [source["id"] for source in jfk["sources"]]


def test_privacy_guard_rejects_case_variant_flag_url_in_public_source_payload():
    payload = {
        "sources": [
            {
                "id": "official-source",
                "authorityName": "Official authority",
                "pageTitle": "Public guidance",
                "url": "https://official.example/fLaG_uRl",
                "jurisdiction": "US",
                "accessedAt": "2026-08-25",
                "supportsFields": ["customs.prohibitedItems"],
                "sourceType": "customs",
            }
        ]
    }

    with pytest.raises(ValueError, match="flag_url"):
        _assert_privacy_safe(payload)


def test_compiler_refuses_schema_invalid_repository_before_replacing_output(
    sample_repo: Path, tmp_path: Path
):
    output = tmp_path / "api"
    output.mkdir()
    sentinel = output / "sentinel.json"
    sentinel.write_text('{"existing": true}\n', encoding="utf-8")
    airport_path = sample_repo / "data/airports/JFK.yaml"
    airport = yaml.safe_load(airport_path.read_text(encoding="utf-8"))
    airport["iataCode"] = "JfK"
    _write_yaml(airport_path, airport)

    with pytest.raises(ValueError, match="invalid repository"):
        compile_api(sample_repo, output, datetime(2026, 8, 25, tzinfo=timezone.utc))

    assert sentinel.read_text(encoding="utf-8") == '{"existing": true}\n'
