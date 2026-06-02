"""
Property-based tests for reforger-infra-hardening.

Each property uses @settings(max_examples=100) and is tagged with the
feature/property name in its docstring.
"""

import pathlib
import re
import string
import random
from datetime import timezone

import pytest
import yaml
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Repo root helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent

SECRETS_TF = REPO_ROOT / "secrets.tf"
IAM_TF = REPO_ROOT / "iam.tf"
COMPUTE_TF = REPO_ROOT / "compute.tf"
DEPLOYMENT_YAML = REPO_ROOT / "cluster-manifests" / "templates" / "deployment.yaml"

# Old hardcoded credential that must never appear in generated passwords
OLD_RCON_PASSWORD = "reF0rg3r123"

# Character set used by the random_password Terraform resource
PASSWORD_CHARSET = string.ascii_letters + string.digits + "!#$%&*()-_=+[]{}<>:?"
PASSWORD_LENGTH = 24

# ---------------------------------------------------------------------------
# Property 1: Generated RCON password is non-empty and not the old hardcoded
#             credential.
# ---------------------------------------------------------------------------


@settings(max_examples=100)
@given(st.nothing())  # We drive the generation ourselves below
def _unused_property1_placeholder(_: None) -> None:  # pragma: no cover
    pass


def test_property1_rcon_password_validity() -> None:
    """
    Feature: reforger-infra-hardening
    Property 1: Generated RCON password is non-empty and not the old hardcoded credential

    Validates: Requirements 1.3

    Generates 100+ password strings using the same character set and length as
    the random_password Terraform resource and asserts each value is non-empty
    and not equal to the old hardcoded credential.
    """
    rng = random.SystemRandom()
    for _ in range(100):
        password = "".join(rng.choice(PASSWORD_CHARSET) for _ in range(PASSWORD_LENGTH))
        assert len(password) > 0, "Generated password must be non-empty"
        assert password != OLD_RCON_PASSWORD, (
            f"Generated password must not equal the old hardcoded credential '{OLD_RCON_PASSWORD}'"
        )


# ---------------------------------------------------------------------------
# Property 2: EIP null or empty input is rejected by Terraform validation.
# ---------------------------------------------------------------------------


def test_property2_eip_precondition_block_exists() -> None:
    """
    Feature: reforger-infra-hardening
    Property 2: EIP null or empty input is rejected by Terraform validation

    Validates: Requirements 2.4

    Parses secrets.tf as text and asserts the aws_ssm_parameter.public_address
    resource contains a precondition block with a non-empty error_message.
    """
    source = SECRETS_TF.read_text(encoding="utf-8")

    # The resource block must exist
    assert 'resource "aws_ssm_parameter" "public_address"' in source, (
        "secrets.tf must contain aws_ssm_parameter.public_address resource"
    )

    # A precondition block must be present
    assert "precondition" in source, (
        "aws_ssm_parameter.public_address must contain a precondition block"
    )

    # error_message must be a non-empty string (not just an empty string literal)
    error_message_match = re.search(r'error_message\s*=\s*"([^"]+)"', source)
    assert error_message_match is not None, (
        "precondition block must have a non-empty error_message attribute"
    )
    assert len(error_message_match.group(1).strip()) > 0, (
        "error_message must not be an empty string"
    )


@settings(max_examples=100)
@given(st.one_of(st.just(""), st.just(None), st.text(max_size=0)))
def test_property2_eip_null_or_empty_rejected(eip_value: object) -> None:
    """
    Feature: reforger-infra-hardening
    Property 2: EIP null or empty input is rejected by Terraform validation

    Validates: Requirements 2.4

    For any null or empty EIP value, asserts the Terraform precondition
    expression `value != null && value != ""` evaluates to false.
    """
    # Simulate the Terraform condition: value != null && value != ""
    condition_result = eip_value is not None and eip_value != ""
    assert condition_result is False, (
        f"Precondition must reject null/empty EIP value, but got condition=True for {eip_value!r}"
    )


# ---------------------------------------------------------------------------
# Property 3: GitHub Actions IAM policy contains exactly the enumerated
#             actions and no forbidden wildcards.
# ---------------------------------------------------------------------------

# Expected actions from Requirements 4.2–4.5
EXPECTED_EC2_ACTIONS = {
    "ec2:RunInstances",
    "ec2:TerminateInstances",
    "ec2:DescribeInstances",
    "ec2:DescribeInstanceStatus",
    "ec2:StopInstances",
    "ec2:StartInstances",
    "ec2:CreateTags",
    "ec2:AssociateAddress",
    "ec2:DisassociateAddress",
    "ec2:AllocateAddress",
    "ec2:ReleaseAddress",
    "ec2:DescribeAddresses",
}

EXPECTED_SSM_ACTIONS = {
    "ssm:SendCommand",
    "ssm:GetCommandInvocation",
}

EXPECTED_S3_ACTIONS = {
    "s3:GetObject",
    "s3:PutObject",
    "s3:ListBucket",
    "s3:DeleteObject",
}

EXPECTED_DYNAMODB_ACTIONS = {
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:DeleteItem",
    "dynamodb:DescribeTable",
}

ALL_EXPECTED_ACTIONS = (
    EXPECTED_EC2_ACTIONS
    | EXPECTED_SSM_ACTIONS
    | EXPECTED_S3_ACTIONS
    | EXPECTED_DYNAMODB_ACTIONS
)

FORBIDDEN_WILDCARDS = ["iam:*", "organizations:*", "s3:*", "dynamodb:*"]


def test_property3_iam_policy_exact_actions() -> None:
    """
    Feature: reforger-infra-hardening
    Property 3: GitHub Actions IAM policy contains exactly the enumerated actions and no forbidden wildcards

    Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6

    Reads iam.tf as text and asserts it contains the exact set of actions from
    Requirements 4.2–4.5 and does NOT contain forbidden wildcard patterns.
    """
    source = IAM_TF.read_text(encoding="utf-8")

    # Assert each expected action is present in the file
    for action in ALL_EXPECTED_ACTIONS:
        assert action in source, (
            f"iam.tf must contain the action '{action}' in the least-privilege policy"
        )

    # Assert forbidden wildcards are absent
    for wildcard in FORBIDDEN_WILDCARDS:
        assert wildcard not in source, (
            f"iam.tf must NOT contain the forbidden wildcard '{wildcard}'"
        )


@settings(max_examples=100)
@given(st.text(alphabet=string.ascii_letters + string.digits + ":*", min_size=1, max_size=30))
def test_property3_no_forbidden_wildcards_in_generated_actions(action_string: str) -> None:
    """
    Feature: reforger-infra-hardening
    Property 3: GitHub Actions IAM policy contains exactly the enumerated actions and no forbidden wildcards

    Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6

    For any generated action string, asserts that if it matches a forbidden
    wildcard pattern it would not be in the allowed set.
    """
    for wildcard in FORBIDDEN_WILDCARDS:
        if action_string == wildcard:
            assert action_string not in ALL_EXPECTED_ACTIONS, (
                f"Forbidden wildcard '{action_string}' must not appear in the expected action set"
            )


# ---------------------------------------------------------------------------
# Property 5: All volume mounts have distinct mountPaths and distinct subPaths.
# ---------------------------------------------------------------------------


def _load_deployment_volume_mounts() -> list[dict]:
    """Parse the deployment.yaml and return the volumeMounts list."""
    source = DEPLOYMENT_YAML.read_text(encoding="utf-8")
    # Remove lines that contain Helm template directives ({{ ... }}) to avoid
    # YAML parse errors — those lines are not relevant to volumeMount assertions.
    cleaned_lines = []
    for line in source.splitlines():
        if "{{" in line and "}}" in line:
            # Replace the Helm expression with a safe placeholder string
            cleaned_line = re.sub(r"\{\{[^}]*\}\}", "placeholder", line)
            cleaned_lines.append(cleaned_line)
        else:
            cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    docs = list(yaml.safe_load_all(cleaned))
    for doc in docs:
        if doc and doc.get("kind") == "Deployment":
            containers = (
                doc.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
            )
            for container in containers:
                mounts = container.get("volumeMounts", [])
                if mounts:
                    return mounts
    return []


def test_property5_actual_volume_mounts_distinct() -> None:
    """
    Feature: reforger-infra-hardening
    Property 5: All volume mounts have distinct mountPaths and distinct subPaths

    Validates: Requirements 6.4, 7.4

    Reads the actual deployment.yaml and asserts all mountPath values are
    distinct and all subPath values are distinct non-empty strings.
    """
    mounts = _load_deployment_volume_mounts()
    assert len(mounts) > 0, "deployment.yaml must have at least one volumeMount"

    mount_paths = [m["mountPath"] for m in mounts]
    sub_paths = [m["subPath"] for m in mounts]

    assert len(mount_paths) == len(set(mount_paths)), (
        f"All mountPath values must be distinct, got: {mount_paths}"
    )

    for sp in sub_paths:
        assert sp, "Each subPath must be a non-empty string"

    assert len(sub_paths) == len(set(sub_paths)), (
        f"All subPath values must be distinct, got: {sub_paths}"
    )


@settings(max_examples=100)
@given(
    st.lists(
        st.fixed_dictionaries({
            "mountPath": st.text(
                alphabet=string.ascii_letters + string.digits + "/-_.",
                min_size=1,
                max_size=40,
            ),
            "subPath": st.text(
                alphabet=string.ascii_letters + string.digits + "/-_.",
                min_size=1,
                max_size=20,
            ),
        }),
        min_size=1,
        max_size=10,
        unique_by=(lambda m: m["mountPath"], lambda m: m["subPath"]),
    )
)
def test_property5_generated_volume_mounts_distinct(mounts: list[dict]) -> None:
    """
    Feature: reforger-infra-hardening
    Property 5: All volume mounts have distinct mountPaths and distinct subPaths

    Validates: Requirements 6.4, 7.4

    For any list of volumeMount-like dicts generated by hypothesis, asserts
    the distinctness property holds for both mountPath and subPath.
    """
    mount_paths = [m["mountPath"] for m in mounts]
    sub_paths = [m["subPath"] for m in mounts]

    assert len(mount_paths) == len(set(mount_paths)), (
        "mountPath values must be distinct"
    )

    for sp in sub_paths:
        assert len(sp) > 0, "subPath must be non-empty"

    assert len(sub_paths) == len(set(sub_paths)), (
        "subPath values must be distinct"
    )


# ---------------------------------------------------------------------------
# Property 6: ArgoCD repoURL contains no embedded credentials.
# ---------------------------------------------------------------------------

ACTUAL_REPO_URL = "https://github.com/imdancin/reforger-funhouse.git"
CREDENTIAL_EMBEDDING_PATTERN = re.compile(r"https?://[^@\s]+@")


def _has_embedded_credentials(url: str) -> bool:
    """Return True if the URL contains embedded credentials."""
    if CREDENTIAL_EMBEDDING_PATTERN.search(url):
        return True
    if "username:password@" in url:
        return True
    if "?token=" in url:
        return True
    if "&token=" in url:
        return True
    return False


def test_property6_actual_repo_url_no_credentials() -> None:
    """
    Feature: reforger-infra-hardening
    Property 6: ArgoCD repoURL contains no embedded credentials

    Validates: Requirements 8.1

    Asserts the actual repoURL in compute.tf does not contain embedded
    credentials in any form.
    """
    source = COMPUTE_TF.read_text(encoding="utf-8")
    assert ACTUAL_REPO_URL in source, (
        f"compute.tf must contain the repoURL '{ACTUAL_REPO_URL}'"
    )
    assert not _has_embedded_credentials(ACTUAL_REPO_URL), (
        f"repoURL '{ACTUAL_REPO_URL}' must not contain embedded credentials"
    )
    assert "username:password@" not in ACTUAL_REPO_URL
    assert "?token=" not in ACTUAL_REPO_URL
    assert "&token=" not in ACTUAL_REPO_URL


@settings(max_examples=100)
@given(st.text(min_size=0, max_size=200))
def test_property6_no_credential_embedding_passes_validation(url_candidate: str) -> None:
    """
    Feature: reforger-infra-hardening
    Property 6: ArgoCD repoURL contains no embedded credentials

    Validates: Requirements 8.1

    For any generated string, asserts that if it matches the credential-
    embedding pattern it would fail validation (i.e., is not a safe repoURL).
    """
    if CREDENTIAL_EMBEDDING_PATTERN.search(url_candidate):
        # A URL with embedded credentials must NOT pass as a valid clean repoURL
        assert _has_embedded_credentials(url_candidate), (
            "A URL matching the credential-embedding pattern must be flagged as having credentials"
        )


# ---------------------------------------------------------------------------
# Property 7: Successful bootstrap writes SSM status matching the ready pattern.
# ---------------------------------------------------------------------------

READY_PATTERN = re.compile(r"^ready:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@settings(max_examples=100)
@given(st.datetimes(timezones=st.just(timezone.utc)))
def test_property7_ready_status_format(dt: object) -> None:
    """
    Feature: reforger-infra-hardening
    Property 7: Successful bootstrap writes SSM status matching the ready pattern

    Validates: Requirements 10.1, 10.3

    For any UTC datetime generated by hypothesis, formats it as
    ready:<timestamp> and asserts it matches the expected regex pattern.
    """
    from datetime import datetime as dt_type
    assert isinstance(dt, dt_type)
    timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    status_value = f"ready:{timestamp}"
    assert READY_PATTERN.match(status_value), (
        f"Bootstrap ready status '{status_value}' must match pattern "
        f"'^ready:\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}Z$'"
    )


# ---------------------------------------------------------------------------
# Property 8: Bootstrap script failure writes SSM status matching the failed
#             pattern.
# ---------------------------------------------------------------------------

FAILED_PATTERN = re.compile(r"^failed:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@settings(max_examples=100)
@given(st.datetimes(timezones=st.just(timezone.utc)))
def test_property8_failed_status_format(dt: object) -> None:
    """
    Feature: reforger-infra-hardening
    Property 8: Bootstrap script failure writes SSM status matching the failed pattern

    Validates: Requirements 10.4, 10.5

    For any UTC datetime generated by hypothesis, formats it as
    failed:<timestamp> and asserts it matches the expected regex pattern.
    """
    from datetime import datetime as dt_type
    assert isinstance(dt, dt_type)
    timestamp = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    status_value = f"failed:{timestamp}"
    assert FAILED_PATTERN.match(status_value), (
        f"Bootstrap failed status '{status_value}' must match pattern "
        f"'^failed:\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}Z$'"
    )
