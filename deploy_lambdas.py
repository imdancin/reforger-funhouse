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
RUNTIME_DEPS = [
    "boto3",
    "botocore",
    "pynacl",
    "nacl",
    "cffi",
    "pycparser",
    "jmespath",
    "s3transfer",
    "dateutil",
    "urllib3",
    "yaml",
]

PROJECT_ROOT = Path(__file__).resolve().parent


def build_zip() -> bytes:
    """Build a deployment zip containing the control plane package and dependencies."""
    print("Building deployment package...")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add the discord_control_plane package
        pkg_root = PROJECT_ROOT / "discord_control_plane"
        for filepath in pkg_root.rglob("*.py"):
            arcname = filepath.relative_to(PROJECT_ROOT)
            if "__pycache__" in str(arcname):
                continue
            zf.write(filepath, arcname)

        # Add runtime dependencies from the virtual environment
        venv_lib = _find_site_packages()
        if venv_lib:
            for dep in RUNTIME_DEPS:
                dep_path = venv_lib / dep
                if dep_path.is_dir():
                    for filepath in dep_path.rglob("*"):
                        if filepath.is_file() and "__pycache__" not in str(filepath):
                            arcname = filepath.relative_to(venv_lib)
                            zf.write(filepath, arcname)
                # Also check for .dist-info or single-file modules
                for item in venv_lib.glob(f"{dep}*"):
                    if item.is_file() and item.suffix in (".py", ".so", ".pyd"):
                        zf.write(item, item.relative_to(venv_lib))

            # Include _cffi_backend (compiled extension needed by PyNaCl)
            for ext in venv_lib.glob("_cffi_backend*"):
                if ext.is_file():
                    zf.write(ext, ext.relative_to(venv_lib))

    zip_bytes = buf.getvalue()
    size_mb = len(zip_bytes) / (1024 * 1024)
    print(f"Package built: {size_mb:.1f} MB")
    return zip_bytes


def _find_site_packages() -> Path | None:
    """Locate the site-packages directory in the current environment."""
    # Check for .venv first (uv standard)
    venv = PROJECT_ROOT / ".venv"
    if venv.exists():
        # Windows
        win_path = venv / "Lib" / "site-packages"
        if win_path.exists():
            return win_path
        # Linux/Mac
        for lib_dir in (venv / "lib").glob("python*"):
            sp = lib_dir / "site-packages"
            if sp.exists():
                return sp

    # Fallback: use the running interpreter's site-packages
    import site
    paths = site.getsitepackages()
    for p in paths:
        if Path(p).exists():
            return Path(p)
    return None


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
            # Also update the handler path to point to our module
            client.update_function_configuration(
                FunctionName=function_name,
                Handler=handler,
            )
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
