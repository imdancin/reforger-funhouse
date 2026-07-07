"""Deploy all Discord control-plane Lambda functions to AWS.

Packages the discord_control_plane module and its dependencies into a zip,
then updates each Lambda function's code. Run from the project root:

    uv run python deploy_lambdas.py [--profile reforger-admin] [--region us-west-2]

Prerequisites:
    - AWS CLI credentials configured (or use --profile)
    - Lambda functions already created via Terraform
    - Python dependencies installed (uv sync)
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3

# ---------------------------------------------------------------------------
# Lambda function → handler mapping
# ---------------------------------------------------------------------------

LAMBDA_FUNCTIONS = {
    "arma-launch-handler": "discord_control_plane.handlers.launch_handler.lambda_handler",
    "arma-set-preset": "discord_control_plane.handlers.launch_orchestrator_tasks.lambda_handler_set_preset",
    "arma-dispatch-apply": "discord_control_plane.handlers.launch_orchestrator_tasks.lambda_handler_dispatch_apply",
    "arma-check-ready": "discord_control_plane.handlers.launch_orchestrator_tasks.lambda_handler_check_ready",
    "arma-mark-running": "discord_control_plane.handlers.launch_orchestrator_tasks.lambda_handler_mark_running",
    "arma-launch-failed": "discord_control_plane.handlers.launch_orchestrator.lambda_handler_failed",
    "arma-launch-timed-out": "discord_control_plane.handlers.launch_orchestrator.lambda_handler_timed_out",
    "arma-teardown-handler": "discord_control_plane.handlers.teardown.lambda_handler",
}

# Packages to include from the virtualenv (beyond the project itself)
# (Dependencies are now installed via pip targeting the Lambda platform)

PROJECT_ROOT = Path(__file__).resolve().parent


def build_zip() -> bytes:
    """Build a deployment zip containing the control plane package and Linux dependencies.

    Uses pip to download manylinux wheels for Lambda's x86_64 Amazon Linux runtime,
    ensuring compiled extensions (like PyNaCl's _sodium) are compatible.
    """
    print("Building deployment package...")

    with tempfile.TemporaryDirectory() as tmpdir:
        deps_dir = Path(tmpdir) / "deps"
        deps_dir.mkdir()

        # Install runtime deps targeting Lambda's Linux x86_64 platform
        print("  Installing Linux-compatible dependencies...")
        # Packages with compiled extensions — need manylinux wheels
        native_deps = ["pynacl", "cffi", "pyyaml"]
        subprocess.run(
            [
                "uv", "pip", "install",
                "--target", str(deps_dir),
                "--python-platform", "x86_64-manylinux2014",
                "--python-version", "3.12",
                "--no-deps",
            ] + native_deps,
            check=True,
            capture_output=True,
            text=True,
        )

        # Pure-python deps (no platform-specific binaries needed)
        pure_deps = ["pycparser", "boto3", "botocore", "jmespath", "s3transfer", "urllib3", "python-dateutil"]
        subprocess.run(
            [
                "uv", "pip", "install",
                "--target", str(deps_dir),
                "--no-deps",
            ] + pure_deps,
            check=True,
            capture_output=True,
            text=True,
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add the discord_control_plane package
            pkg_root = PROJECT_ROOT / "discord_control_plane"
            for filepath in pkg_root.rglob("*.py"):
                arcname = filepath.relative_to(PROJECT_ROOT)
                if "__pycache__" in str(arcname):
                    continue
                zf.write(filepath, arcname)

            # Add all installed dependencies from the temp dir
            for filepath in deps_dir.rglob("*"):
                if filepath.is_file() and "__pycache__" not in str(filepath):
                    arcname = filepath.relative_to(deps_dir)
                    # Skip .dist-info directories to save space
                    if ".dist-info" in str(arcname):
                        continue
                    zf.write(filepath, arcname)

    zip_bytes = buf.getvalue()
    size_mb = len(zip_bytes) / (1024 * 1024)
    print(f"Package built: {size_mb:.1f} MB")
    return zip_bytes


def _prune_old_versions(client, function_name: str, keep_version: str) -> int:
    """Delete published versions of a function other than $LATEST and keep_version.

    SnapStart caches a snapshot for every published version for as long as that
    version exists, billed continuously regardless of invocation traffic. Since
    only the version behind the 'live' alias is ever invoked, older versions are
    pure cost with no benefit and should be removed after each deploy.
    """
    paginator = client.get_paginator("list_versions_by_function")
    deleted = 0
    for page in paginator.paginate(FunctionName=function_name):
        for v in page["Versions"]:
            version = v["Version"]
            if version in ("$LATEST", keep_version):
                continue
            try:
                client.delete_function(FunctionName=function_name, Qualifier=version)
                deleted += 1
            except Exception as e:
                print(f"\n    Warning: failed to delete version {version}: {e}", end="")
    return deleted


def deploy(profile: str | None, region: str) -> None:
    """Build the zip and deploy to all Lambda functions."""
    zip_bytes = build_zip()

    session_kwargs = {"region_name": region}
    if profile:
        session_kwargs["profile_name"] = profile

    session = boto3.Session(**session_kwargs)
    client = session.client("lambda")

    print(f"\nDeploying to {len(LAMBDA_FUNCTIONS)} Lambda functions in {region}...")
    print("-" * 60)

    successes = 0
    failures = []

    for function_name, handler in LAMBDA_FUNCTIONS.items():
        try:
            print(f"  Updating {function_name}...", end=" ")
            client.update_function_code(
                FunctionName=function_name,
                ZipFile=zip_bytes,
            )
            # Wait for the code update to complete before updating configuration
            waiter = client.get_waiter("function_updated")
            waiter.wait(FunctionName=function_name)
            # Also update the handler path to point to our module
            client.update_function_configuration(
                FunctionName=function_name,
                Handler=handler,
            )
            waiter.wait(FunctionName=function_name)

            # Publish a new version and update the 'live' alias for SnapStart functions
            if function_name == "arma-launch-handler":
                version_resp = client.publish_version(
                    FunctionName=function_name,
                    Description="Deployed via deploy_lambdas.py",
                )
                new_version = version_resp["Version"]
                client.update_alias(
                    FunctionName=function_name,
                    Name="live",
                    FunctionVersion=new_version,
                )
                pruned = _prune_old_versions(client, function_name, keep_version=new_version)
                print(f"OK (v{new_version}, SnapStart, pruned {pruned} old version(s))")
            else:
                print("OK")
            successes += 1
        except client.exceptions.ResourceNotFoundException:
            print("NOT FOUND (run terraform apply first)")
            failures.append(function_name)
        except Exception as e:
            print(f"FAILED: {e}")
            failures.append(function_name)

    print("-" * 60)
    print(f"Done: {successes} updated, {len(failures)} failed")

    if failures:
        print("\nFailed functions:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Deploy Discord control-plane Lambda functions"
    )
    parser.add_argument(
        "--profile",
        default="reforger-admin",
        help="AWS CLI profile name (default: reforger-admin)",
    )
    parser.add_argument(
        "--region",
        default="us-west-2",
        help="AWS region (default: us-west-2)",
    )
    args = parser.parse_args()

    deploy(profile=args.profile, region=args.region)


if __name__ == "__main__":
    main()
