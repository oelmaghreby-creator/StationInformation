from pathlib import Path

import pytest

from crew_customs.models import load_yaml, load_yaml_dir, write_json


def test_load_yaml_and_write_sorted_json(tmp_path: Path):
    source = tmp_path / "record.yaml"
    source.write_text("iataCode: JFK\ncity: New York\n", encoding="utf-8")
    assert load_yaml(source)["iataCode"] == "JFK"

    target = tmp_path / "record.json"
    write_json(target, {"z": 1, "a": 2})
    assert target.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "z": 1\n}\n'


def test_load_yaml_dir_returns_sorted_yaml_mappings(tmp_path: Path):
    (tmp_path / "z.yaml").write_text("name: Zulu\n", encoding="utf-8")
    (tmp_path / "a.yaml").write_text("name: Alpha\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("name: Ignored\n", encoding="utf-8")

    assert load_yaml_dir(tmp_path) == [{"name": "Alpha"}, {"name": "Zulu"}]


def test_load_yaml_rejects_non_mapping(tmp_path: Path):
    source = tmp_path / "record.yaml"
    source.write_text("- item\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Expected mapping"):
        load_yaml(source)
