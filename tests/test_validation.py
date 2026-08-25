"""Tests for repository-level data validation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest
import yaml

from crew_customs.validate import validate_repository


@dataclass
class SampleRepository:
    root: Path
    airport: dict[str, Any]
    country: dict[str, Any]
    source: dict[str, Any]

    def write(self) -> None:
        _write_yaml(self.root / "data/airports/JFK.yaml", self.airport)
        _write_yaml(self.root / "data/countries/US.yaml", self.country)
        _write_yaml(self.root / "data/sources/us-customs.yaml", self.source)


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


@pytest.fixture
def sample_repo(tmp_path: Path) -> SampleRepository:
    copytree(Path(__file__).parents[1] / "schemas", tmp_path / "schemas")
    airport = {
        "iataCode": "JFK",
        "airportName": "John F. Kennedy International Airport",
        "city": "New York",
        "countryIso2": "US",
        "reviewStatus": "verified",
        "lastVerified": "2026-08-25",
        "nextReviewDue": "2026-11-25",
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
            "prohibitedItems": [{"text": "Item X", "sourceIds": ["us-customs"]}],
            "restrictedItems": [],
            "declarationRequirements": [],
        },
        "baggageSecurity": {
            "handBaggage": {
                "sharpObjects": [],
                "liquids": [],
                "batteriesAndElectronics": [],
                "prohibitedItems": [],
            },
            "checkedCargoBag": {
                "knivesAndSharpObjects": [],
                "batteriesAndElectronics": [],
                "prohibitedItems": [],
            },
        },
        "crewNotes": [],
        "reviewStatus": "verified",
        "lastVerified": "2026-08-25",
        "nextReviewDue": "2026-11-25",
    }
    source = {
        "id": "us-customs",
        "authorityName": "U.S. Customs and Border Protection",
        "pageTitle": "Travel guidance",
        "url": "https://www.cbp.gov/travel",
        "jurisdiction": "US",
        "accessedAt": "2026-08-25",
        "fingerprint": "sha256:abc123",
        "supportsFields": ["customs.prohibitedItems"],
        "sourceType": "customs",
    }
    repository = SampleRepository(tmp_path, airport, country, source)
    repository.write()
    return repository


def test_valid_repository_has_no_errors(sample_repo: SampleRepository):
    assert validate_repository(sample_repo.root, date(2026, 8, 25)) == []


def test_verified_rule_requires_known_source(sample_repo: SampleRepository):
    sample_repo.country["customs"]["prohibitedItems"] = [
        {"text": "Item X", "sourceIds": []}
    ]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "US: verified rule has no sourceIds" in errors


def test_unverified_rule_can_await_source_research(sample_repo: SampleRepository):
    sample_repo.country.update(
        reviewStatus="research_pending", lastVerified=None, nextReviewDue=None
    )
    sample_repo.country["customs"]["prohibitedItems"] = [
        {"text": "Item X", "sourceIds": []}
    ]
    sample_repo.write()

    assert validate_repository(sample_repo.root, date(2026, 8, 25)) == []


def test_verified_airport_note_requires_source_ids(sample_repo: SampleRepository):
    sample_repo.airport["crewNotes"] = [{}]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "JFK: verified rule has no sourceIds" in errors


def test_pending_airport_note_can_await_source_research(sample_repo: SampleRepository):
    sample_repo.airport.update(
        reviewStatus="research_pending", lastVerified=None, nextReviewDue=None
    )
    sample_repo.airport["crewNotes"] = [{"text": "Note X", "sourceIds": []}]
    sample_repo.write()

    assert validate_repository(sample_repo.root, date(2026, 8, 25)) == []


def test_research_pending_records_cannot_claim_verification_dates(
    sample_repo: SampleRepository,
):
    sample_repo.airport["reviewStatus"] = "research_pending"
    sample_repo.country["reviewStatus"] = "research_pending"
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "JFK: lastVerified: '2026-08-25' is not of type 'null'" in errors
    assert "US: lastVerified: '2026-08-25' is not of type 'null'" in errors


def test_verified_records_require_next_review_due(sample_repo: SampleRepository):
    del sample_repo.airport["nextReviewDue"]
    del sample_repo.country["nextReviewDue"]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "JFK: record: 'nextReviewDue' is a required property" in errors
    assert "US: record: 'nextReviewDue' is a required property" in errors


def test_rejects_non_string_airport_override_rule_text(sample_repo: SampleRepository):
    sample_repo.airport["customsOverrides"] = {
        "prohibitedItems": [{"text": 42, "sourceIds": ["us-customs"]}]
    }
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("customsOverrides.prohibitedItems.0.text" in error for error in errors)


def test_rejects_duplicate_source_ids_in_airport_baggage_override(
    sample_repo: SampleRepository,
):
    sample_repo.airport["baggageSecurityOverrides"] = {
        "handBaggage": {
            "liquids": [{"text": "Liquid rule", "sourceIds": ["us-customs", "us-customs"]}]
        }
    }
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("baggageSecurityOverrides.handBaggage.liquids.0.sourceIds" in error for error in errors)


def test_rejects_malformed_airport_note_keys(sample_repo: SampleRepository):
    sample_repo.airport["crewNotes"] = [
        {"text": "Note X", "sourceIds": ["us-customs"], "citation": "unexpected"}
    ]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("crewNotes.0" in error and "citation" in error for error in errors)


def test_rejects_unapproved_cash_declaration_keys(sample_repo: SampleRepository):
    sample_repo.country["customs"]["cashDeclaration"] = {
        "Flag_Url": "https://private.example/flag"
    }
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any(
        "customs.cashDeclaration" in error and "Flag_Url" in error
        for error in errors
    )


def test_review_due_cannot_precede_verified_date(sample_repo: SampleRepository):
    sample_repo.country.update(lastVerified="2026-08-25", nextReviewDue="2026-08-24")
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "US: nextReviewDue precedes lastVerified" in errors


def test_rejects_invalid_iata_and_iso2_formats(sample_repo: SampleRepository):
    sample_repo.airport.update(iataCode="JfK", countryIso2="USA")
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("iataCode" in error for error in errors)
    assert any("countryIso2" in error for error in errors)


def test_rejects_trailing_newlines_in_iata_and_iso2_codes(sample_repo: SampleRepository):
    sample_repo.airport.update(iataCode="JFK\n", countryIso2="US\n")
    sample_repo.country["iso2"] = "US\n"
    sample_repo.source["jurisdiction"] = "US\n"
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("iataCode" in error for error in errors)
    assert sum("countryIso2" in error or "iso2" in error or "jurisdiction" in error for error in errors) >= 3


def test_rejects_duplicate_identifiers(sample_repo: SampleRepository):
    duplicate_airport = deepcopy(sample_repo.airport)
    duplicate_airport["city"] = "Duplicate city"
    _write_yaml(sample_repo.root / "data/airports/DUP.yaml", duplicate_airport)
    duplicate_country = deepcopy(sample_repo.country)
    duplicate_country["countryName"] = "Duplicate country"
    _write_yaml(sample_repo.root / "data/countries/DUP.yaml", duplicate_country)
    duplicate_source = deepcopy(sample_repo.source)
    duplicate_source["authorityName"] = "Duplicate authority"
    _write_yaml(sample_repo.root / "data/sources/DUP.yaml", duplicate_source)

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "duplicate airport IATA code: JFK" in errors
    assert "duplicate country ISO2 code: US" in errors
    assert "duplicate source ID: us-customs" in errors


def test_rejects_unresolved_country_and_source_references(sample_repo: SampleRepository):
    sample_repo.airport["countryIso2"] = "CA"
    sample_repo.country["customs"]["prohibitedItems"][0]["sourceIds"] = ["missing"]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "JFK: countryIso2 does not resolve: CA" in errors
    assert "US: unknown sourceId: missing" in errors


def test_rejects_unapproved_source_type(sample_repo: SampleRepository):
    sample_repo.source["sourceType"] = "travel_blog"
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "us-customs: unapproved sourceType: travel_blog" in errors


def test_rejects_source_id_with_issue_marker_characters(sample_repo: SampleRepository):
    sample_repo.source["id"] = "us-customs`$(inject)"
    sample_repo.country["customs"]["prohibitedItems"][0]["sourceIds"] = [
        "us-customs`$(inject)"
    ]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("id" in error and "does not match" in error for error in errors)


@pytest.mark.parametrize("source_id", ["abc\n", "abc\r", "abc\r\n"])
def test_rejects_source_ids_with_all_trailing_newline_forms(
    sample_repo: SampleRepository, source_id: str
):
    sample_repo.source["id"] = source_id
    sample_repo.country["customs"]["prohibitedItems"][0]["sourceIds"] = [source_id]
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert any("id" in error for error in errors)


def test_rejects_invalid_dates_and_sorts_errors(sample_repo: SampleRepository):
    sample_repo.airport["lastVerified"] = "not-a-date"
    sample_repo.source["accessedAt"] = "2026-13-01"
    sample_repo.write()

    errors = validate_repository(sample_repo.root, date(2026, 8, 25))

    assert "JFK: invalid lastVerified date: not-a-date" in errors
    assert "us-customs: invalid accessedAt date: 2026-13-01" in errors
    assert errors == sorted(errors)
