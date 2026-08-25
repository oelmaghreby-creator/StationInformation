"""Validate the privacy boundary for the static Pages release snapshot."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile


def validate_publish_snapshot(root: Path) -> list[str]:
    """Return privacy failures found in the manifest-defined publish snapshot.

    The raw network spreadsheet is intentionally outside the public repository.
    This check protects both that repository boundary and the Pages artifact.
    """
    manifest = _load_manifest(root)
    errors: list[str] = []
    forbidden_directories = set(manifest["forbiddenDirectories"])
    publish_root = root / manifest["publishRoot"]

    for directory in sorted(forbidden_directories):
        if (root / directory).exists():
            errors.append(f"Private directory must not exist in release checkout: {directory}/")

    if not publish_root.is_dir():
        return [*errors, f"Publish root is missing: {manifest['publishRoot']}/"]

    terms = tuple(term.casefold() for term in manifest["forbiddenOperationalTerms"])
    for path in sorted(publish_root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root)
        if forbidden_directories.intersection(relative.parts):
            errors.append(f"Forbidden directory in publish snapshot: {relative}")
            continue
        content = path.read_text(encoding="utf-8")
        normalized = content.casefold()
        for term in terms:
            if term in normalized:
                errors.append(
                    f"Forbidden operational content {term!r} in publish snapshot: {relative}"
                )
    return errors


def validate_history_privacy(root: Path) -> list[str]:
    """Return failures when reachable Git history contains private material.

    A safe HEAD tree does not make historical blobs safe to publish.  Any
    failure means the public repository must start from a new, parentless
    commit created from :func:`create_release_snapshot`.
    """
    manifest = _load_manifest(root)
    forbidden_directories = set(manifest["forbiddenDirectories"])
    terms = tuple(term.casefold() for term in manifest["historyForbiddenOperationalTerms"])
    errors: list[str] = []
    seen_blobs: set[str] = set()

    for commit in _git_lines(root, "rev-list", "HEAD"):
        for blob, path in _tree_blobs(root, commit):
            if forbidden_directories.intersection(Path(path).parts):
                errors.append(
                    "History privacy gate failed: snapshot-only publication required; "
                    f"forbidden path {path!r} is reachable from {commit}"
                )
            if blob in seen_blobs:
                continue
            seen_blobs.add(blob)
            content = _git_bytes(root, "cat-file", "blob", blob).decode(
                "utf-8", errors="replace"
            ).casefold()
            for term in terms:
                if term in content:
                    errors.append(
                        "History privacy gate failed: snapshot-only publication required; "
                        f"forbidden operational term {term!r} is reachable in blob {blob}"
                    )
    return errors


def create_release_snapshot(root: Path, output: Path) -> None:
    """Write a deterministic tar export of tracked ``HEAD`` using ``git archive``.

    This intentionally exports only the current tree.  It never packages the
    working directory, so ignored artifacts such as ``.superpowers/`` cannot
    enter the snapshot.  The resulting archive is for a new parentless public
    repository, not a normal-history push.
    """
    errors = validate_publish_snapshot(root)
    if errors:
        raise ValueError("Cannot create release snapshot:\n" + "\n".join(errors))
    manifest = _load_manifest(root)
    prefix = manifest["snapshotPrefix"]
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as archive:
            _git_archive(root, f"--prefix={prefix}/", archive)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_manifest(root: Path) -> dict[str, object]:
    path = root / "release-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    publish_root = manifest.get("publishRoot")
    forbidden_directories = manifest.get("forbiddenDirectories")
    forbidden_terms = manifest.get("forbiddenOperationalTerms")
    history_forbidden_terms = manifest.get("historyForbiddenOperationalTerms")
    publication_mode = manifest.get("publicationMode")
    snapshot_prefix = manifest.get("snapshotPrefix")
    if not isinstance(publish_root, str) or not publish_root:
        raise ValueError("release manifest publishRoot must be a non-empty string")
    if not _string_list(forbidden_directories) or not _string_list(forbidden_terms):
        raise ValueError("release manifest privacy lists must contain non-empty strings")
    if not _string_list(history_forbidden_terms):
        raise ValueError("release manifest history privacy terms must be non-empty strings")
    if publication_mode != "snapshot-only":
        raise ValueError("release manifest publicationMode must be snapshot-only")
    if not isinstance(snapshot_prefix, str) or not snapshot_prefix:
        raise ValueError("release manifest snapshotPrefix must be a non-empty string")
    return {
        "publishRoot": publish_root,
        "forbiddenDirectories": forbidden_directories,
        "forbiddenOperationalTerms": forbidden_terms,
        "historyForbiddenOperationalTerms": history_forbidden_terms,
        "publicationMode": publication_mode,
        "snapshotPrefix": snapshot_prefix,
    }


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def _git_lines(root: Path, *arguments: str) -> list[str]:
    return _git_bytes(root, *arguments).decode("ascii").splitlines()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as error:
        raise ValueError("Git is required for release privacy checks") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git release privacy check failed: {detail}") from error
    return result.stdout


def _tree_blobs(root: Path, commit: str) -> list[tuple[str, str]]:
    entries = _git_bytes(root, "ls-tree", "-r", "-z", "--full-tree", commit)
    blobs: list[tuple[str, str]] = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", maxsplit=1)
        mode, object_type, object_id = metadata.split()
        if mode and object_type == b"blob":
            blobs.append((object_id.decode("ascii"), raw_path.decode("utf-8")))
    return blobs


def _git_archive(root: Path, prefix: str, destination: object) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", prefix, "HEAD"],
            check=True,
            stdout=destination,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise ValueError("Git is required to create a release snapshot") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git release snapshot failed: {detail}") from error
