from pathlib import Path

import pytest
import yaml

from crew_customs.network import normalize_network


def test_network_is_unique_excludes_auh_and_allowlists_fields():
    rows = normalize_network(
        Path("tests/fixtures/network.csv"), {"AUH"}, {"United States": "US"}
    )
    assert rows == [{
        "iataCode": "JFK", "city": "New York", "countryName": "United States",
        "countryIso2": "US", "region": "Americas"
    }]
    assert "Related_Hotel" not in rows[0]


def test_conflicting_duplicate_non_excluded_iata_raises(tmp_path: Path):
    source = tmp_path / "conflict.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,US,Americas\n"
        "JFK,Queens,United States,US,Americas\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Conflicting airport rows: JFK"):
        normalize_network(source, set(), {"United States": "US"})


def test_explicit_country_mapping_resolves_blank_country_code_conflict(tmp_path: Path):
    source = tmp_path / "mapped.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,,Americas\n"
        "JFK,New York,United States,US,Americas\n",
        encoding="utf-8",
    )

    assert normalize_network(source, set(), {"United States": "US"}) == [{
        "iataCode": "JFK", "city": "New York", "countryName": "United States",
        "countryIso2": "US", "region": "Americas"
    }]


def test_network_rejects_nonblank_csv_code_without_explicit_mapping(tmp_path: Path):
    source = tmp_path / "unmapped.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,US,Americas\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unresolved country mapping for United States"):
        normalize_network(source, set(), {})


def test_network_rejects_csv_code_that_disagrees_with_mapping(tmp_path: Path):
    source = tmp_path / "conflicting-code.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,GB,Americas\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Conflicting country mapping for United States: GB"):
        normalize_network(source, set(), {"United States": "US"})


def test_network_rejects_blank_country_name(tmp_path: Path):
    source = tmp_path / "blank-country.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,   ,US,Americas\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid country name"):
        normalize_network(source, set(), {"United States": "US"})


def test_network_rejects_unrelated_whitespace_mapping_key(tmp_path: Path):
    source = tmp_path / "valid-country.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "JFK,New York,United States,US,Americas\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Country mapping keys must be non-empty"):
        normalize_network(source, set(), {"United States": "US", "   ": "ZZ"})


def test_committed_country_mapping_covers_every_committed_destination_country():
    mapping = yaml.safe_load(Path("data/country_mapping.yaml").read_text(encoding="utf-8"))
    countries = [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in Path("data/countries").glob("*.yaml")
    ]

    assert countries
    assert all(mapping[country["countryName"]] == country["iso2"] for country in countries)


def test_excluded_auh_with_anomalous_country_code_is_ignored(tmp_path: Path):
    source = tmp_path / "excluded.csv"
    source.write_text(
        "Route,City,Country,Country_Code,Region\n"
        "AUH,Abu Dhabi,United Arab Emirates,not-an-iso-code,Asia\n"
        "AUH,Abu Dhabi,United Arab Emirates,AE,Asia\n",
        encoding="utf-8",
    )

    assert normalize_network(source, {"auh"}) == []
