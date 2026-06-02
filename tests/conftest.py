"""
Shared pytest fixtures for the reforger-infra-hardening test suite.
"""

import pathlib
import pytest


# Repo root is one level up from this tests/ directory
REPO_ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def cluster_manifests_dir() -> pathlib.Path:
    """Path to the cluster-manifests/ directory."""
    return REPO_ROOT / "cluster-manifests"


@pytest.fixture
def compute_tf() -> pathlib.Path:
    """Path to compute.tf."""
    return REPO_ROOT / "compute.tf"


@pytest.fixture
def iam_tf() -> pathlib.Path:
    """Path to iam.tf."""
    return REPO_ROOT / "iam.tf"


@pytest.fixture
def gitignore() -> pathlib.Path:
    """Path to .gitignore."""
    return REPO_ROOT / ".gitignore"
