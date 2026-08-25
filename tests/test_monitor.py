"""Tests for bounded, human-review-only source monitoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import crew_customs.monitor as monitor
from crew_customs.monitor import (
    FAILURE_THRESHOLD,
    MAX_DIFF_CHARS,
    MAX_NORMALIZED_TEXT_CHARS,
    REVIEWER_CHECKLIST,
    build_issue,
    check_source,
    fingerprint,
    run_monitor,
)


def source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "us-cbp",
        "authorityName": "U.S. Customs and Border Protection",
        "url": "https://example.test/customs",
        "jurisdiction": "US",
        "fingerprint": fingerprint("<p>Old allowance</p>"),
        "supportsFields": ["crewNotes"],
    }
    value.update(overrides)
    return value


def changed_result():
    return check_source(source(), lambda _: "<p>New allowance</p>")


def test_markup_only_change_does_not_alert():
    old = "<main><p>Allowance: 2 items</p></main>"
    new = "<main class='red'><p>Allowance: 2 items</p></main>"
    assert fingerprint(old) == fingerprint(new)


def test_scripts_styles_entities_and_whitespace_do_not_change_fingerprint():
    old = "<p>Allowance &amp; declaration</p>"
    new = "<style>bad</style><p> Allowance & declaration </p><script>bad()</script>"
    assert fingerprint(old) == fingerprint(new)


def test_known_traffic_counter_is_ignored_only_for_affected_source():
    old = "<p>Allowance: 2 items</p><p>Visitor counter: 1,234</p>"
    new = "<p>Allowance: 2 items</p><p>Visitor counter: 9,999</p>"
    assert fingerprint(old, source_id="in-mumbai-customs-crew-2026") == fingerprint(
        new, source_id="in-mumbai-customs-crew-2026"
    )
    assert fingerprint(old) != fingerprint(new)


def test_fingerprint_hashes_text_beyond_the_stored_snapshot_limit():
    prefix = "A" * MAX_NORMALIZED_TEXT_CHARS
    assert fingerprint(prefix + "first substantive ending") != fingerprint(
        prefix + "second substantive ending"
    )


def test_check_source_identifies_changed_visible_text():
    result = changed_result()
    assert result.status == "changed"
    assert result.old_fingerprint == source()["fingerprint"]
    assert result.new_fingerprint == fingerprint("<p>New allowance</p>")


def test_changed_text_has_stable_issue_key():
    result = changed_result()
    issue = build_issue(result, ["JFK"])
    assert issue["key"].startswith("source-change:us-cbp:")
    assert f"Monitoring key: `{issue['key']}`" in issue["body"]


def test_monitor_rejects_source_ids_that_cannot_be_exact_issue_markers():
    with pytest.raises(ValueError, match="lowercase hyphenated slug"):
        check_source(source(id="us-cbp`$(inject)"), lambda _: "unchanged")


@pytest.mark.parametrize("source_id", ["abc\n", "abc\r", "abc\r\n"])
def test_monitor_boundary_rejects_newline_ids_without_trimming(source_id: str):
    """Do not turn malformed IDs into the colliding slug ``abc``."""
    with pytest.raises(ValueError, match="lowercase hyphenated slug"):
        check_source(source(id=source_id), lambda _: "unchanged")


@pytest.mark.parametrize("source_id", ["a", "abc", "abc-123", "a1-b2-c3"])
def test_accepted_source_id_is_preserved_in_monitor_state_key_and_marker(
    tmp_path, source_id: str
):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    (tmp_path / "data/sources/source.yaml").write_text(
        "id: " + source_id + "\nauthorityName: Test authority\n"
        "url: https://example.test/customs\njurisdiction: US\nfingerprint: "
        + fingerprint("old")
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    (tmp_path / "data/airports/JFK.yaml").write_text(
        "iataCode: JFK\ncountryIso2: US\n", encoding="utf-8"
    )

    drafts = run_monitor(tmp_path, fetch=lambda _: "new")
    state = json.loads((tmp_path / ".crew-customs-monitor/state.json").read_text())
    key = f"source-change:{source_id}:{fingerprint('new')}"

    assert list(state["sources"]) == [source_id]
    assert drafts[0]["key"] == key
    assert f"Monitoring key: `{key}`" in drafts[0]["body"]


def test_change_issue_has_only_bounded_sanitized_preview_and_review_checklist():
    result = check_source(
        source(),
        lambda _: "<p>New @everyone allowance</p>" + "x" * (MAX_DIFF_CHARS * 2),
        previous_text="Old allowance",
    )
    issue = build_issue(result, ["JFK", "JFK"])
    assert issue["affectedCountries"] == ["US"]
    assert issue["affectedAirports"] == ["JFK"]
    assert issue["supportsFields"] == ["crewNotes"]
    assert len(issue["textDiff"]) <= MAX_DIFF_CHARS
    assert "@\u200beveryone" in issue["body"]
    assert REVIEWER_CHECKLIST in issue["body"]


def test_issue_embeds_the_exact_canonical_reviewer_checklist():
    docs = (Path(__file__).parents[1] / "docs/research-review.md").read_text(encoding="utf-8")
    canonical = docs.split("- [ ] The named authority", 1)[1].split("\n\n## Country batch", 1)[0]
    assert REVIEWER_CHECKLIST == "- [ ] The named authority" + canonical
    assert REVIEWER_CHECKLIST in build_issue(changed_result(), ["JFK"])["body"]


def test_invalid_fetch_content_becomes_a_failure_not_an_unbounded_parse():
    result = check_source(source(), lambda _: object())
    assert result.status == "failed"
    assert result.error is not None


def test_fetch_url_uses_bounded_https_request(monkeypatch):
    class Response:
        headers = {"Content-Length": "2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://example.test/final"

        def read(self, amount):
            assert amount == monitor.MAX_RESPONSE_BYTES + 1
            return b"ok"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://example.test/customs"
            assert request.headers["User-agent"] == monitor.USER_AGENT
            assert timeout == monitor.FETCH_TIMEOUT_SECONDS
            return Response()

    monkeypatch.setattr(monitor, "build_opener", lambda handler: Opener())
    assert monitor.fetch_url("https://example.test/customs") == b"ok"


def test_fetch_url_rejects_an_oversized_response(monkeypatch):
    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return "https://example.test/final"

        def read(self, amount):
            return b"x" * amount

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(monitor, "build_opener", lambda handler: Opener())
    with pytest.raises(ValueError, match="5 MiB"):
        monitor.fetch_url("https://example.test/customs")


def test_run_monitor_waits_for_three_consecutive_failures_and_resets(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    (tmp_path / "data/sources/us-cbp.yaml").write_text(
        "id: us-cbp\nauthorityName: CBP\nurl: https://example.test/customs\n"
        "jurisdiction: US\nfingerprint: " + fingerprint("old") + "\n"
        "supportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    (tmp_path / "data/airports/JFK.yaml").write_text(
        "iataCode: JFK\ncountryIso2: US\n", encoding="utf-8"
    )

    def unavailable(_: str) -> str:
        raise OSError("temporary outage")

    for expected in range(1, FAILURE_THRESHOLD):
        assert run_monitor(tmp_path, fetch=unavailable) == []
        state = json.loads((tmp_path / ".crew-customs-monitor/state.json").read_text())
        assert state["sources"]["us-cbp"]["failureCount"] == expected

    issues = run_monitor(tmp_path, fetch=unavailable)
    assert issues[0]["key"] == "source-outage:us-cbp"
    assert issues[0]["failureCount"] == FAILURE_THRESHOLD

    for _ in range(2):
        repeated = run_monitor(tmp_path, fetch=unavailable)
        assert repeated[0]["key"] == "source-outage:us-cbp"
        assert repeated[0]["failureCount"] == FAILURE_THRESHOLD
        state = json.loads((tmp_path / ".crew-customs-monitor/state.json").read_text())
        assert state["sources"]["us-cbp"]["failureCount"] == FAILURE_THRESHOLD

    assert run_monitor(tmp_path, fetch=lambda _: "old") == []
    state = json.loads((tmp_path / ".crew-customs-monitor/state.json").read_text())
    assert state["sources"]["us-cbp"]["failureCount"] == 0


def test_fresh_substantive_change_drafts_on_first_and_second_identical_runs(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    source_path = tmp_path / "data/sources/us-cbp.yaml"
    source_path.write_text(
        "id: us-cbp\nauthorityName: CBP\nurl: https://example.test/customs\n"
        "jurisdiction: US\nfingerprint: " + fingerprint("old") + "\n"
        "supportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    original = source_path.read_text(encoding="utf-8")
    (tmp_path / "data/airports/JFK.yaml").write_text(
        "iataCode: JFK\ncountryIso2: US\n", encoding="utf-8"
    )

    first = run_monitor(tmp_path, fetch=lambda _: "new")
    second = run_monitor(tmp_path, fetch=lambda _: "new")
    assert first[0]["key"].startswith("source-change:us-cbp:")
    assert second[0]["key"] == first[0]["key"]
    assert source_path.read_text(encoding="utf-8") == original
    state = json.loads((tmp_path / ".crew-customs-monitor/state.json").read_text())
    observed = state["sources"]["us-cbp"]
    assert observed["observedFingerprint"] == fingerprint("new")
    assert "reviewed" not in observed


def test_legacy_observed_text_never_suppresses_a_dynamic_source_change(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    source_record = (
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + fingerprint("Allowance: 2 items Visitor counter: 1,234")
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n"
    )
    (tmp_path / "data/sources/mumbai.yaml").write_text(source_record, encoding="utf-8")
    state_dir = tmp_path / ".crew-customs-monitor"
    state_dir.mkdir()
    state_dir.joinpath("state.json").write_text(json.dumps({"sources": {
        "in-mumbai-customs-crew-2026": {"failureCount": 0, "text": "Allowance: 3 items"}
    }}), encoding="utf-8")

    issues = run_monitor(tmp_path, fetch=lambda _: "Allowance: 3 items Visitor counter: 9,999")
    assert issues[0]["kind"] == "change"


def test_counter_only_change_in_a_reviewed_snapshot_does_not_alert(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    (tmp_path / "data/sources/mumbai.yaml").write_text(
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + fingerprint("Allowance: 2 items Visitor counter: 1,234")
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )

    state_dir = tmp_path / ".crew-customs-monitor"
    state_dir.mkdir()
    state_dir.joinpath("state.json").write_text(json.dumps({"sources": {
        "in-mumbai-customs-crew-2026": {
            "failureCount": 0,
            "reviewed": True,
            "reviewedSourceFingerprint": fingerprint("Allowance: 2 items Visitor counter: 1,234"),
            "reviewedNormalizedFingerprint": fingerprint(
                "Allowance: 2 items", source_id="in-mumbai-customs-crew-2026"
            ),
            "reviewedText": "Allowance: 2 items",
        }
    }}), encoding="utf-8")
    assert run_monitor(tmp_path, fetch=lambda _: "Allowance: 2 items Visitor counter: 9,999") == []


def test_counter_page_substantive_change_alerts_on_first_run_against_reviewed_snapshot(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    (tmp_path / "data/sources/mumbai.yaml").write_text(
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + fingerprint("Allowance: 2 items Visitor counter: 1,234")
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".crew-customs-monitor"
    state_dir.mkdir()
    state_dir.joinpath("state.json").write_text(json.dumps({"sources": {
        "in-mumbai-customs-crew-2026": {
            "failureCount": 0,
            "reviewed": True,
            "reviewedSourceFingerprint": fingerprint("Allowance: 2 items Visitor counter: 1,234"),
            "reviewedNormalizedFingerprint": fingerprint(
                "Allowance: 2 items", source_id="in-mumbai-customs-crew-2026"
            ),
            "reviewedText": "Allowance: 2 items",
        }
    }}), encoding="utf-8")

    first = run_monitor(
        tmp_path, fetch=lambda _: "Allowance: 3 items Visitor counter: 9,999"
    )
    assert first[0]["kind"] == "change"


def test_dynamic_source_lifecycle_repeats_drafts_until_an_explicit_reviewed_baseline(tmp_path):
    """A changing widget is suppressed only after review records its baseline."""
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    source_path = tmp_path / "data/sources/mumbai.yaml"
    source_path.write_text(
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + fingerprint("Allowance: 2 items Visitor counter: 1")
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    (tmp_path / "data/airports/BOM.yaml").write_text(
        "iataCode: BOM\ncountryIso2: IN\n", encoding="utf-8"
    )

    revised = "Allowance: 3 items Visitor counter: 2"
    first = run_monitor(tmp_path, fetch=lambda _: revised)
    second = run_monitor(tmp_path, fetch=lambda _: revised)
    assert first[0]["key"] == second[0]["key"]

    reviewed_source_fingerprint = fingerprint(revised)
    source_path.write_text(
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + reviewed_source_fingerprint
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    state_path = tmp_path / ".crew-customs-monitor/state.json"
    state_path.write_text(json.dumps({"sources": {
        "in-mumbai-customs-crew-2026": {
            "failureCount": 0,
            "reviewed": True,
            "reviewedSourceFingerprint": reviewed_source_fingerprint,
            "reviewedNormalizedFingerprint": fingerprint(
                revised, source_id="in-mumbai-customs-crew-2026"
            ),
        }
    }}), encoding="utf-8")

    assert run_monitor(
        tmp_path, fetch=lambda _: "Allowance: 3 items Visitor counter: 999"
    ) == []


def test_reviewed_dynamic_fingerprint_handles_content_beyond_the_preview_limit(tmp_path):
    (tmp_path / "data/sources").mkdir(parents=True)
    (tmp_path / "data/airports").mkdir()
    (tmp_path / "data/countries").mkdir()
    source_fingerprint = fingerprint("legacy counter 1")
    (tmp_path / "data/sources/mumbai.yaml").write_text(
        "id: in-mumbai-customs-crew-2026\nauthorityName: Mumbai Customs\n"
        "url: https://example.test/customs\njurisdiction: IN\nfingerprint: "
        + source_fingerprint
        + "\nsupportsFields:\n  - crewNotes\nsourceType: customs\n",
        encoding="utf-8",
    )
    content = "Allowance: 2 items " + "x" * (MAX_NORMALIZED_TEXT_CHARS + 100)
    state_dir = tmp_path / ".crew-customs-monitor"
    state_dir.mkdir()
    state_dir.joinpath("state.json").write_text(json.dumps({"sources": {
        "in-mumbai-customs-crew-2026": {
            "failureCount": 0,
            "reviewed": True,
            "reviewedSourceFingerprint": source_fingerprint,
            "reviewedNormalizedFingerprint": fingerprint(
                content, source_id="in-mumbai-customs-crew-2026"
            ),
            "reviewedText": content[:MAX_NORMALIZED_TEXT_CHARS],
        }
    }}), encoding="utf-8")

    assert run_monitor(tmp_path, fetch=lambda _: content) == []
    assert run_monitor(tmp_path, fetch=lambda _: content) == []
    state = json.loads((state_dir / "state.json").read_text())
    assert state["sources"]["in-mumbai-customs-crew-2026"]["observedFingerprint"] == fingerprint(
        content, source_id="in-mumbai-customs-crew-2026"
    )


@pytest.mark.parametrize("url", ["http://example.test", "https://", "not a url"])
def test_sources_must_be_https(url):
    result = check_source(source(url=url), lambda _: "unused")
    assert result.status == "failed"
