"""Regression checks for the repository automation contracts."""

from __future__ import annotations

from pathlib import Path
import json
import re
import subprocess

import yaml


ROOT = Path(__file__).parents[1]
SHA_PIN = re.compile(r".+@[0-9a-f]{40}$")
DEPLOY_PAGES_V4_SHA = "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e"


def load_workflow(name: str) -> tuple[str, dict[str, object]]:
    text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
    # BaseLoader preserves GitHub Actions' YAML 1.2 ``on`` key as a string.
    value = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return text, value


def step_uses(job: dict[str, object]) -> list[str]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [step["uses"] for step in steps if isinstance(step, dict) and "uses" in step]


def test_ci_pages_workflow_tests_generated_output_and_deploys_with_scoped_permissions():
    _, workflow = load_workflow("ci-pages.yml")
    trigger = workflow["on"]
    assert isinstance(trigger, dict)
    assert set(trigger) == {"pull_request", "push"}
    assert trigger["push"]["branches"] == ["main"]
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    build = jobs["build"]
    deploy = jobs["deploy"]
    assert isinstance(build, dict)
    assert isinstance(deploy, dict)
    build_script = "\n".join(
        step.get("run", "") for step in build["steps"] if isinstance(step, dict)
    )
    assert "python -m pytest" in build_script
    assert "crew-customs validate --root ." in build_script
    assert "--built-at \"$API_BUILT_AT\"" in build_script
    assert "SOURCE_DATE_EPOCH" not in workflow.get("env", {})
    assert "crew-customs build --root . --output public/api/v1" in build_script
    assert "crew-customs release-check --root . --snapshot-only" in build_script
    assert "git diff --exit-code public" in build_script
    assert "git status --porcelain --untracked-files=all -- public" in build_script
    upload_steps = [
        step for step in build["steps"]
        if isinstance(step, dict) and "upload-pages-artifact@" in step.get("uses", "")
    ]
    assert len(upload_steps) == 1
    assert upload_steps[0]["if"] == "github.ref == 'refs/heads/main'"
    manifest = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["publicationMode"] == "snapshot-only"
    assert upload_steps[0]["with"]["path"] == manifest["publishRoot"]
    assert deploy["if"] == "github.ref == 'refs/heads/main'"
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert any("deploy-pages@" in use for use in step_uses(deploy))
    assert f"actions/deploy-pages@{DEPLOY_PAGES_V4_SHA}" in step_uses(deploy)
    assert all(SHA_PIN.fullmatch(use) for use in step_uses(build) + step_uses(deploy))


def test_git_porcelain_check_reports_an_untracked_generated_airport_json(tmp_path: Path):
    airport = tmp_path / "public/api/v1/airports/ZZZ.json"
    airport.parent.mkdir(parents=True)
    airport.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    result = subprocess.run(
        [
            "git", "status", "--porcelain", "--untracked-files=all", "--",
            "public",
        ],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )

    assert "?? public/api/v1/airports/ZZZ.json" in result.stdout


def test_monitor_workflow_uses_exact_keys_and_cannot_modify_rule_data():
    text, workflow = load_workflow("monitor-sources.yml")
    trigger = workflow["on"]
    assert isinstance(trigger, dict)
    assert trigger["schedule"] == [{"cron": "17 3 * * 1"}]
    assert trigger["workflow_dispatch"] == {}
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert workflow["concurrency"] == {
        "group": "crew-customs-source-monitor",
        "cancel-in-progress": "false",
    }

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    monitor = jobs["monitor"]
    assert isinstance(monitor, dict)
    script = "\n".join(
        step.get("run", "") for step in monitor["steps"] if isinstance(step, dict)
    )
    assert "crew-customs monitor --root ." in script
    assert "gh issue list" in script
    assert "gh issue create" in script
    assert "gh issue comment" in script
    assert 'Monitoring key: \\`' in script
    assert "contains($marker)" in script
    assert "git commit" not in script
    assert "gh pr" not in script
    assert all(SHA_PIN.fullmatch(use) for use in step_uses(monitor))


def test_workflow_shell_steps_parse_as_bash():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        assert isinstance(workflow, dict)
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        for job in jobs.values():
            assert isinstance(job, dict)
            for step in job.get("steps", []):
                if not isinstance(step, dict) or "run" not in step:
                    continue
                parsed = subprocess.run(
                    ["bash", "-n"], input=step["run"], text=True,
                    capture_output=True, check=False,
                )
                assert parsed.returncode == 0, f"{path}: {parsed.stderr}"
