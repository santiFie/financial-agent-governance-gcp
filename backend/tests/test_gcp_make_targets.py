"""Offline tests for the GCP Makefile targets and deploy/gcp-destroy.sh (fake gcloud / python on PATH, no docker)."""

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_gcp_bootstrap_script import FAKE_GCLOUD

ROOT = Path(__file__).resolve().parent.parent.parent
DESTROY = ROOT / "deploy" / "gcp-destroy.sh"


def _make(*args, env=None):
    return subprocess.run(["make", *args], capture_output=True, text=True, cwd=ROOT, env={**os.environ, **(env or {})})


# --- Makefile ----------------------------------------------------------------------------------------------------


def test_attack_gcp_requires_the_env_file_before_touching_docker():
    result = _make("attack-gcp", "GCP_ENV_FILE=/nonexistent/.env.gcp")

    assert result.returncode != 0
    assert "make build-gcp" in result.stdout + result.stderr
    assert "docker" not in (result.stdout + result.stderr).lower()


def test_attack_targets_load_the_env_file_and_run_the_demo():
    plan = _make("-n", "attack-gcp", "N=3").stdout
    assert 'set -a; . "' in plan and ".env.gcp" in plan
    assert "scripts/demo_attack.py" in plan and "--runs" not in plan

    many = _make("-n", "attack-gcp-n", "N=3").stdout
    assert "scripts/demo_attack.py --runs 3" in many

    assert "USE_GCS_INGESTION=false" in _make("-n", "attack-gcp", "GCS=false").stdout
    assert "USE_GCS_INGESTION" not in plan


def test_build_and_smoke_pass_the_project():
    assert (
        "deploy/gcp-bootstrap.sh --project mi-proy --env-file .env.gcp"
        in _make("-n", "build-gcp", "PROJECT=mi-proy").stdout
    )
    assert "--project mi-proy" in _make("-n", "smoke-gcp", "PROJECT=mi-proy").stdout
    assert "deploy/gcp-destroy.sh --project mi-proy" in _make("-n", "destroy-gcp", "PROJECT=mi-proy").stdout


def test_env_file_takes_precedence_over_the_inherited_environment_and_gcs_overrides_it(tmp_path):
    env_file = tmp_path / "gcp.env"
    env_file.write_text("SECURITY_MODE=gcp_live\nGCP_PROJECT_ID=desde-archivo\nUSE_GCS_INGESTION=true\nLLM_MODEL=\n")
    # Run the exact shell prefix make would execute, but print the environment instead of starting the attack.
    command = _make("-n", "attack-gcp", f"GCP_ENV_FILE={env_file}", "GCS=false").stdout
    snippet = [line for line in command.splitlines() if line.startswith("set -a;")][0]
    snippet = re.sub(r"exec .*demo_attack\.py.*$", "exec env", snippet)

    out = subprocess.run(
        ["sh", "-c", snippet],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "SECURITY_MODE": "local_mock", "GCP_PROJECT_ID": "heredado"},
    ).stdout.splitlines()

    assert "SECURITY_MODE=gcp_live" in out and "GCP_PROJECT_ID=desde-archivo" in out
    assert "USE_GCS_INGESTION=false" in out


# --- deploy/gcp-destroy.sh ---------------------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("gcloud", FAKE_GCLOUD),
        ("fake-python", '#!/usr/bin/env bash\necho "python $*" >> "$GCLOUD_LOG"\n'),
    ):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    env_file = tmp_path / ".env.gcp"
    env_file.write_text(
        "GCP_PROJECT_ID=demo-proj\nGCP_LOCATION=us-central1\nMODEL_ARMOR_TEMPLATE_ID=tpl-x\n"
        "FIRESTORE_COLLECTION=mis_checkpoints\nGCS_BUCKET_NAME=bucket-x\n"
    )
    log = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GCLOUD_LOG": str(log),
        "PYTHON_BIN": str(bin_dir / "fake-python"),
    }

    def run(*args, existing=""):
        return subprocess.run(
            ["bash", str(DESTROY), "--yes", "--env-file", str(env_file), *args],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={**env, "STUB_EXISTING": existing},
        )

    def calls():
        return log.read_text().splitlines() if log.exists() else []

    return run, calls


def test_destroy_script_has_valid_bash_syntax():
    assert subprocess.run(["bash", "-n", str(DESTROY)]).returncode == 0


def test_destroy_dry_run_lists_everything_without_calling_gcloud(sandbox):
    run, calls = sandbox
    result = run("--dry-run")

    assert result.returncode == 0, result.stderr
    assert "[dry-run] gcloud storage rm --recursive gs://bucket-x" in result.stdout
    assert "[dry-run] gcloud model-armor templates delete tpl-x" in result.stdout
    assert calls() == []


def test_destroy_deletes_existing_resources_using_values_from_the_env_file(sandbox):
    run, calls = sandbox
    result = run(existing="bucket-x tpl-x sa-financial-assistant")

    assert result.returncode == 0, result.stdout + result.stderr
    joined = "\n".join(calls())
    assert "storage rm --recursive gs://bucket-x --project=demo-proj" in joined
    assert "model-armor templates delete tpl-x --location=us-central1 --project=demo-proj" in joined
    assert "iam service-accounts delete sa-financial-assistant@demo-proj.iam.gserviceaccount.com" in joined
    assert "python - demo-proj mis_checkpoints" in joined  # Firestore collections emptied for that project


def test_destroy_skips_resources_that_do_not_exist(sandbox):
    run, calls = sandbox
    result = run(existing="")

    assert result.returncode == 0, result.stdout + result.stderr
    joined = "\n".join(calls())
    assert " rm " not in joined and "delete" not in joined.replace("databases", "")
