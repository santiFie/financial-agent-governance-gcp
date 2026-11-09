"""Offline tests for deploy/gcp-bootstrap.sh: a fake `gcloud` on PATH records every call (no network, no credentials)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "deploy" / "gcp-bootstrap.sh"

# Fake gcloud: logs its arguments; `describe` succeeds only for resources listed in $STUB_EXISTING.
FAKE_GCLOUD = r"""#!/usr/bin/env bash
echo "$*" >> "$GCLOUD_LOG"
case "$*" in
  "auth list"*) echo "tester@example.com" ;;
  "billing projects describe"*) echo "True" ;;
  *describe*)
    for resource in $STUB_EXISTING; do
      case "$*" in *"$resource"*) exit 0 ;; esac
    done
    exit 1 ;;
esac
exit 0
"""


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gcloud = bin_dir / "gcloud"
    gcloud.write_text(FAKE_GCLOUD)
    gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "gcloud.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GCLOUD_LOG": str(log),
        "STUB_EXISTING": "",
    }
    env.pop("GCP_PROJECT_ID", None)
    return SimpleSandbox(tmp_path, env, log)


class SimpleSandbox:
    def __init__(self, tmp_path, env, log):
        self.env_file = tmp_path / ".env.gcp"
        self.env = env
        self.log = log

    def run(self, *args, existing=""):
        env = {**self.env, "STUB_EXISTING": existing}
        return subprocess.run(
            ["bash", str(SCRIPT), "--yes", "--skip-smoke", "--env-file", str(self.env_file), *args],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


def test_scripts_have_valid_bash_syntax():
    for name in ("gcp-bootstrap.sh", "iam-setup.sh"):
        assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / name)]).returncode == 0


def test_dry_run_prints_the_plan_without_calling_gcloud_or_writing_files(sandbox):
    result = sandbox.run("--dry-run", "--project", "demo-proj")

    assert result.returncode == 0, result.stderr
    assert "[dry-run] gcloud firestore databases create" in result.stdout
    assert "[dry-run] gcloud model-armor templates create aegisbank-template" in result.stdout
    assert sandbox.calls() == [] and not sandbox.env_file.exists()


def test_fresh_project_creates_every_resource_and_writes_env_file(sandbox):
    result = sandbox.run("--project", "demo-proj")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = "\n".join(sandbox.calls())
    for expected in (
        "services enable dlp.googleapis.com aiplatform.googleapis.com firestore.googleapis.com modelarmor.googleapis.com storage.googleapis.com --project=demo-proj",
        "firestore databases create --database=(default) --location=us-central1 --type=firestore-native",
        "model-armor templates create aegisbank-template --location=us-central1 --project=demo-proj",
        "--pi-and-jailbreak-filter-settings-enforcement=enabled",
        "storage buckets create gs://demo-proj-aegisbank-docs",
        "storage cp backend/local_storage/comprobante.txt gs://demo-proj-aegisbank-docs/comprobante.txt",
    ):
        assert expected in calls, expected

    env = dict(line.split("=", 1) for line in sandbox.env_file.read_text().splitlines() if "=" in line)
    assert env["GCP_PROJECT_ID"] == "demo-proj" and env["SECURITY_MODE"] == "gcp_live"
    assert env["LLM_PROVIDER"] == "vertexai" and env["CHECKPOINTER_TYPE"] == "firestore"
    assert env["USE_GCP_MODEL_ARMOR"] == "true" and env["MODEL_ARMOR_TEMPLATE_ID"] == "aegisbank-template"
    assert env["USE_GCS_INGESTION"] == "true" and env["GCS_BUCKET_NAME"] == "demo-proj-aegisbank-docs"
    assert len(env["TOKEN_VAULT_SECRET"]) == 64
    assert oct(sandbox.env_file.stat().st_mode & 0o777) == "0o600"


def test_second_run_is_idempotent_and_keeps_secret_and_user_edits(sandbox):
    sandbox.run("--project", "demo-proj")
    first = sandbox.env_file.read_text()
    secret = [line for line in first.splitlines() if line.startswith("TOKEN_VAULT_SECRET=")][0]
    sandbox.env_file.write_text(first.replace("VERTEX_LOCATION=global", "VERTEX_LOCATION=us-central1"))
    sandbox.log.write_text("")

    result = sandbox.run("--project", "demo-proj", existing="(default) aegisbank-template demo-proj-aegisbank-docs")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = "\n".join(sandbox.calls())
    assert "firestore databases create" not in calls
    assert "model-armor templates create" not in calls
    assert "buckets create" not in calls
    second = sandbox.env_file.read_text()
    assert secret in second.splitlines()
    assert "VERTEX_LOCATION=us-central1" in second  # ensure_env_var does not overwrite a value that is already set


def test_no_gcs_skips_the_bucket_and_disables_gcs_ingestion(sandbox):
    result = sandbox.run("--project", "demo-proj", "--no-gcs")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "buckets create" not in "\n".join(sandbox.calls())
    text = sandbox.env_file.read_text()
    assert "USE_GCS_INGESTION=false" in text and "GCS_BUCKET_NAME=\n" in text


def test_project_is_required_and_unknown_options_are_rejected(sandbox):
    sandbox.env["PATH"] = sandbox.env["PATH"]
    missing = sandbox.run("--dry-run")
    assert missing.returncode != 0 and "--project" in missing.stderr

    unknown = sandbox.run("--nope")
    assert unknown.returncode == 2


def test_billing_disabled_aborts_before_creating_anything(sandbox):
    # Replace the fake so billing reports False.
    gcloud = Path(sandbox.env["PATH"].split(os.pathsep)[0]) / "gcloud"
    gcloud.write_text(FAKE_GCLOUD.replace('echo "True"', 'echo "False"'))

    result = sandbox.run("--project", "demo-proj")

    assert result.returncode != 0 and "facturación" in result.stderr
    assert not any("databases create" in c or "enable" in c for c in sandbox.calls())
