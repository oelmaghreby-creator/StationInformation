"""Bounded, human-review-only monitoring of official source pages.

This module treats all fetched page text as untrusted data.  It never writes
source records or rules: it writes only a local workflow cache and a JSON list
of issue drafts for an issue-capable workflow to create or update.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import unified_diff
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Literal, TypedDict
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from crew_customs.models import load_yaml, load_yaml_dir, write_json


USER_AGENT = "crew-customs-source-monitor/1.0 (+https://github.com/)"
FETCH_TIMEOUT_SECONDS = 20
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_NORMALIZED_TEXT_CHARS = 64 * 1024
MAX_DIFF_CHARS = 4 * 1024
MAX_ERROR_CHARS = 500
FAILURE_THRESHOLD = 3
STATE_DIRECTORY = ".crew-customs-monitor"
STATE_FILENAME = "state.json"
ISSUES_FILENAME = "issues.json"
SOURCE_ID_PATTERN = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")

# Keep this byte-for-byte aligned with docs/research-review.md.  The dedicated
# regression test makes the documentation the canonical review contract.
REVIEWER_CHECKLIST = """- [ ] The named authority is the competent official authority for the rule.
- [ ] Every cited URL opened successfully on the recorded access date.
- [ ] The page jurisdiction and any sub-jurisdiction or route scope are explicit.
- [ ] Every numeric threshold preserves its currency, unit, age, duration, and
      other eligibility qualifiers.
- [ ] Operating-crew scope is explicit in the source, or the record says that a
      verified crew-specific allowance is unavailable; passenger allowances are
      never silently reused as crew allowances.
- [ ] Customs rules are separate from aviation-security rules, and hand baggage
      is separate from checked crew baggage (the cargo bag).
- [ ] The rule's effective date is recorded when the authority publishes one.
- [ ] An independent second-person reviewer name and review date are recorded.
- [ ] `lastVerified` and `nextReviewDue` are set for reviewed records; records
      still at `research_pending` keep both values null."""

# These two authority pages include a dynamic traffic counter in otherwise
# relevant content.  Limit exclusions to those sources so a number elsewhere
# remains semantically significant.  A source record may add a similarly
# bounded monitorIgnorePatterns list after review when another volatile widget
# is identified.
SOURCE_IGNORE_PATTERNS: dict[str, tuple[str, ...]] = {
    "in-mumbai-customs-crew-2026": (
        r"\b(?:website\s+)?(?:visitor|visitors|page\s+views?|hits?)\s*"
        r"(?:count|counter|number)?\s*[:\-]?\s*[\d,]+\b",
    ),
    "th-customs-arriving-passengers": (
        r"\b(?:website\s+)?(?:visitor|visitors|page\s+views?|hits?)\s*"
        r"(?:count|counter|number)?\s*[:\-]?\s*[\d,]+\b",
    ),
}

BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "div", "dl", "dt",
    "dd", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
    "p", "pre", "section", "table", "td", "th", "tr", "ul",
})
IGNORED_TAGS = frozenset({"script", "style", "template", "head"})


class IssueDraft(TypedDict):
    """An idempotent issue payload consumed by the monitoring workflow."""

    key: str
    title: str
    labels: list[str]
    body: str
    kind: Literal["change", "outage"]
    sourceId: str
    authority: str
    url: str
    affectedCountries: list[str]
    affectedAirports: list[str]
    priorFingerprint: str | None
    currentFingerprint: str | None
    textDiff: str
    supportsFields: list[str]
    failureCount: int


@dataclass(frozen=True)
class CheckResult:
    """One fetch/check result; source content is normalized and size-bounded."""

    source_id: str
    authority: str
    url: str
    jurisdiction: str
    supports_fields: tuple[str, ...]
    old_fingerprint: str | None
    new_fingerprint: str | None
    status: Literal["unchanged", "changed", "failed"]
    previous_text: str | None = None
    current_text: str | None = None
    error: str | None = None
    failure_count: int = 0

    @property
    def changed(self) -> bool:
        return self.status == "changed"

    @property
    def failed(self) -> bool:
        return self.status == "failed"


class _VisibleTextParser(HTMLParser):
    """Extract visible text without executing or preserving markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in IGNORED_TAGS:
            self.ignored_depth += 1
        elif not self.ignored_depth and lowered in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1
        elif not self.ignored_depth and lowered in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class _HTTPSOnlyRedirectHandler(HTTPRedirectHandler):
    """Follow at most five redirects and never downgrade away from HTTPS."""

    def __init__(self) -> None:
        super().__init__()
        self.redirect_count = 0

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        self.redirect_count += 1
        if self.redirect_count > MAX_REDIRECTS:
            raise URLError(f"redirect limit exceeded ({MAX_REDIRECTS})")
        _require_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def normalize_text(
    content: str | bytes,
    *,
    source_id: str | None = None,
    ignore_patterns: Iterable[str] = (),
) -> str:
    """Return normalized visible text from a bounded HTML/text response."""
    if isinstance(content, bytes):
        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds 5 MiB limit")
        text = content.decode("utf-8", errors="replace")
    elif isinstance(content, str):
        if len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("response exceeds 5 MiB limit")
        text = content
    else:
        raise TypeError("fetched content must be str or bytes")

    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    visible = "".join(parser.parts)
    patterns = (*SOURCE_IGNORE_PATTERNS.get(source_id or "", ()), *ignore_patterns)
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
            raise ValueError("monitor ignore patterns must be non-empty strings of at most 500 characters")
        visible = re.sub(pattern, " ", visible, flags=re.IGNORECASE)
    # This text is already bounded by the 5 MiB response limit.  Do not trim it
    # here: every normalized byte must contribute to the source fingerprint.
    return " ".join(visible.split())


def fingerprint(
    content: str | bytes,
    *,
    source_id: str | None = None,
    ignore_patterns: Iterable[str] = (),
) -> str:
    """Return the stable SHA-256 fingerprint of semantic visible text."""
    normalized = normalize_text(
        content, source_id=source_id, ignore_patterns=ignore_patterns
    )
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fetch_url(url: str) -> bytes:
    """Fetch one HTTPS response with explicit redirect, time, and size limits."""
    _require_https_url(url)
    redirect_handler = _HTTPSOnlyRedirectHandler()
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.9, */*;q=0.1"})
    opener = build_opener(redirect_handler)
    with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        _require_https_url(response.geturl())
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_RESPONSE_BYTES:
                    raise ValueError("response exceeds 5 MiB limit")
            except ValueError as error:
                if str(error) == "response exceeds 5 MiB limit":
                    raise
                # An invalid Content-Length cannot relax the read limit below.
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response exceeds 5 MiB limit")
    return body


def check_source(
    source: dict[str, Any], fetch: Callable[[str], str | bytes] = fetch_url,
    *, previous_text: str | None = None,
) -> CheckResult:
    """Fetch and compare one source without changing its reviewed record."""
    source_id = _bounded_source_id(source)
    authority = _bounded_required_string(source, "authorityName")
    url = _bounded_required_string(source, "url")
    jurisdiction = _bounded_required_string(source, "jurisdiction")
    old_fingerprint = _bounded_required_string(source, "fingerprint")
    supports_fields = _bounded_string_list(source.get("supportsFields", []), "supportsFields")
    try:
        _require_https_url(url)
        patterns = _bounded_string_list(
            source.get("monitorIgnorePatterns", []), "monitorIgnorePatterns", maximum=20
        )
        fetched = fetch(url)
        current_text = normalize_text(
            fetched, source_id=source_id, ignore_patterns=patterns
        )
        current_fingerprint = "sha256:" + hashlib.sha256(
            current_text.encode("utf-8")
        ).hexdigest()
    except Exception as error:
        # A source outage should remain a review signal rather than aborting an
        # entire weekly run.  Do not catch BaseException so interrupts still
        # stop the workflow normally.
        return CheckResult(
            source_id, authority, url, jurisdiction, tuple(supports_fields), old_fingerprint,
            None, "failed", error=_bounded_error(error),
        )
    status: Literal["unchanged", "changed"] = (
        "unchanged" if current_fingerprint == old_fingerprint else "changed"
    )
    return CheckResult(
        source_id, authority, url, jurisdiction, tuple(supports_fields), old_fingerprint,
        current_fingerprint, status, _bounded_snapshot(previous_text), current_text,
    )


def build_issue(result: CheckResult, affected: list[str]) -> IssueDraft:
    """Build a stable, safe issue payload; callers deduplicate by ``key``."""
    if result.status == "changed":
        if result.new_fingerprint is None:
            raise ValueError("changed result is missing a current fingerprint")
        key = f"source-change:{result.source_id}:{result.new_fingerprint}"
        kind: Literal["change", "outage"] = "change"
        title = f"Review official source change: {result.source_id}"
        text_diff = _bounded_diff(result.previous_text, result.current_text)
    elif result.status == "failed" and result.failure_count >= FAILURE_THRESHOLD:
        key = f"source-outage:{result.source_id}"
        kind = "outage"
        title = f"Review official source outage: {result.source_id}"
        text_diff = _bounded_error_text(result.error or "unknown fetch failure")
    else:
        raise ValueError("only changed sources and threshold outages can become issues")

    airports = sorted({code for code in affected if isinstance(code, str) and code})
    countries = [result.jurisdiction] if result.jurisdiction else []
    prior = result.old_fingerprint
    current = result.new_fingerprint
    body = _issue_body(
        key=key, result=result, countries=countries, airports=airports,
        prior=prior, current=current, text_diff=text_diff,
    )
    return {
        "key": key,
        "title": title,
        "labels": ["source-change"],
        "body": body,
        "kind": kind,
        "sourceId": result.source_id,
        "authority": result.authority,
        "url": result.url,
        "affectedCountries": countries,
        "affectedAirports": airports,
        "priorFingerprint": prior,
        "currentFingerprint": current,
        "textDiff": text_diff,
        "supportsFields": list(result.supports_fields),
        "failureCount": result.failure_count,
    }


def run_monitor(
    root: Path, *, fetch: Callable[[str], str | bytes] = fetch_url
) -> list[IssueDraft]:
    """Check all registered sources and write workflow-only state and drafts.

    The returned drafts are deliberately idempotent.  The workflow must search
    open issues by their exact ``key`` before creating one, otherwise comment
    on the existing issue.  No reviewed YAML or published API file is changed.
    """
    root = root.resolve()
    state_path = root / STATE_DIRECTORY / STATE_FILENAME
    state = _load_state(state_path)
    state_sources = state["sources"]
    drafts: list[IssueDraft] = []
    for source in load_yaml_dir(root / "data/sources"):
        source_id = _bounded_source_id(source)
        previous = state_sources.get(source_id, {})
        previous_text = _observed_preview(previous)
        result = check_source(source, fetch, previous_text=previous_text)
        reviewed_fingerprint = _reviewed_dynamic_fingerprint(
            previous, source["fingerprint"]
        )
        if (
            source_id in SOURCE_IGNORE_PATTERNS
            and reviewed_fingerprint is not None
            and result.new_fingerprint == reviewed_fingerprint
            and result.status == "changed"
        ):
            # Only an explicit reviewed semantic fingerprint can suppress a
            # counter-only difference. Observed cache content is never trusted
            # as reviewed baseline data.
            result = replace(result, status="unchanged")
        prior_failures = _failure_count(previous)
        if result.failed:
            result = replace(result, failure_count=min(prior_failures + 1, FAILURE_THRESHOLD))
            state_sources[source_id] = {
                "failureCount": result.failure_count,
                "lastError": _bounded_error_text(result.error or "unknown fetch failure"),
            }
            _copy_observed_state(state_sources[source_id], previous)
            _copy_reviewed_baseline(state_sources[source_id], previous, source["fingerprint"])
            if result.failure_count >= FAILURE_THRESHOLD:
                drafts.append(build_issue(result, _affected_airports(root, result.jurisdiction)))
            continue

        next_state: dict[str, Any] = {
            "failureCount": 0,
            "lastError": None,
            "observedFingerprint": result.new_fingerprint,
            "observedTextPreview": _bounded_snapshot(result.current_text),
        }
        _copy_reviewed_baseline(next_state, previous, source["fingerprint"])
        state_sources[source_id] = next_state
        if result.changed:
            drafts.append(build_issue(result, _affected_airports(root, result.jurisdiction)))

    _write_state(state_path, state)
    write_json(root / STATE_DIRECTORY / ISSUES_FILENAME, drafts)
    return drafts


def _require_https_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("source URL must be an HTTPS URL without embedded credentials")


def _bounded_required_string(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > 4_096:
        raise ValueError(f"source {name} must be a non-empty string of at most 4096 characters")
    return value.strip()


def _bounded_source_id(source: dict[str, Any]) -> str:
    """Return the exact safe slug used unchanged in an issue-key marker."""
    source_id = source.get("id")
    if not isinstance(source_id, str) or not source_id or len(source_id) > 128:
        raise ValueError("source id must be a non-empty string of at most 128 characters")
    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise ValueError("source id must be a lowercase hyphenated slug")
    return source_id


def _bounded_string_list(value: Any, name: str, *, maximum: int = 100) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"source {name} must be a list of at most {maximum} strings")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value):
        raise ValueError(f"source {name} must contain non-empty strings of at most 500 characters")
    return [item.strip() for item in value]


def _bounded_snapshot(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:MAX_NORMALIZED_TEXT_CHARS]


def _bounded_error(error: BaseException) -> str:
    return _bounded_error_text(f"{type(error).__name__}: {error}")


def _bounded_error_text(value: str) -> str:
    return " ".join(value.split())[:MAX_ERROR_CHARS]


def _bounded_diff(previous: str | None, current: str | None) -> str:
    if previous is None:
        value = "Previous normalized text is unavailable; compare the current authority page manually.\n"
    else:
        value = "".join(unified_diff(
            [previous + "\n"], [current or ""], fromfile="previous", tofile="current", lineterm=""
        ))
    if len(value) > MAX_DIFF_CHARS:
        value = value[: MAX_DIFF_CHARS - 15] + "\n[truncated]\n"
    return value


def _safe_issue_text(value: str | None) -> str:
    # Escape raw HTML and prevent fetched text from mentioning GitHub users or
    # terminating the fenced preview.  It remains a bounded, inert preview.
    return escape(value or "(none)", quote=False).replace("@", "@\u200b").replace("~", "\u223c")


def _issue_body(
    *, key: str, result: CheckResult, countries: list[str], airports: list[str],
    prior: str | None, current: str | None, text_diff: str,
) -> str:
    fields = ", ".join(result.supports_fields) or "(none recorded)"
    return "\n".join((
        f"Monitoring key: `{_safe_issue_text(key)}`",
        "",
        f"Authority: {_safe_issue_text(result.authority)}",
        f"URL: {_safe_issue_text(result.url)}",
        f"Affected countries: {_safe_issue_text(', '.join(countries))}",
        f"Affected airports: {_safe_issue_text(', '.join(airports) or '(none identified)')}",
        f"Prior fingerprint: `{_safe_issue_text(prior)}`",
        f"Current fingerprint: `{_safe_issue_text(current)}`",
        f"Potentially supported fields: {_safe_issue_text(fields)}",
        f"Consecutive fetch failures: {result.failure_count}",
        "",
        "Bounded normalized-text preview:",
        "~~~text",
        _safe_issue_text(text_diff),
        "~~~",
        "",
        "Reviewer checklist:",
        REVIEWER_CHECKLIST,
    ))


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {"sources": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid monitor state at {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("sources"), dict):
        raise RuntimeError(f"invalid monitor state at {path}")
    return {"sources": value["sources"]}


def _write_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    write_json(path, state)


def _failure_count(previous: Any) -> int:
    if not isinstance(previous, dict):
        return 0
    value = previous.get("failureCount", 0)
    return min(value, FAILURE_THRESHOLD) if isinstance(value, int) and value >= 0 else 0


def _observed_preview(previous: Any) -> str | None:
    """Return only an explicitly named observed preview for diff context."""
    if not isinstance(previous, dict):
        return None
    value = previous.get("observedTextPreview")
    return value if isinstance(value, str) else None


def _reviewed_dynamic_fingerprint(previous: Any, source_fingerprint: Any) -> str | None:
    """Return an explicit reviewed counter-free fingerprint, never observations."""
    if not isinstance(previous, dict) or previous.get("reviewed") is not True:
        return None
    if previous.get("reviewedSourceFingerprint") != source_fingerprint:
        return None
    value = previous.get("reviewedNormalizedFingerprint")
    return value if _is_fingerprint(value) else None


def _copy_observed_state(target: dict[str, Any], previous: Any) -> None:
    if not isinstance(previous, dict):
        return
    fingerprint_value = previous.get("observedFingerprint")
    preview = _observed_preview(previous)
    if _is_fingerprint(fingerprint_value):
        target["observedFingerprint"] = fingerprint_value
    if preview is not None:
        target["observedTextPreview"] = _bounded_snapshot(preview)


def _copy_reviewed_baseline(
    target: dict[str, Any], previous: Any, source_fingerprint: Any
) -> None:
    reviewed = _reviewed_dynamic_fingerprint(previous, source_fingerprint)
    if reviewed is not None:
        target.update({
            "reviewed": True,
            "reviewedSourceFingerprint": source_fingerprint,
            "reviewedNormalizedFingerprint": reviewed,
        })


def _is_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _affected_airports(root: Path, jurisdiction: str) -> list[str]:
    """List destination airports in the source jurisdiction, without rule edits."""
    airport_dir = root / "data/airports"
    if not airport_dir.exists():
        return []
    airport_codes: set[str] = set()
    for airport in load_yaml_dir(airport_dir):
        if airport.get("countryIso2") == jurisdiction:
            code = airport.get("iataCode")
            if isinstance(code, str) and re.fullmatch(r"[A-Z]{3}", code):
                airport_codes.add(code)
    return sorted(airport_codes)
