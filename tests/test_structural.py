"""
Structural assertion tests for reforger-infra-hardening.

These tests verify static structural properties of files in the repo:
  - .gitignore contains required ignore patterns (Requirements 3.1, 3.2)
  - Bootstrap script in compute.tf has correct content (Requirements 5.3, 8.2, 9.1, 10.1, 10.5)
  - Helm templates and values files are hardened (Requirements 1.5, 1.6, 6.1–6.4, 7.1–7.4)
"""

import pathlib
import re

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo root helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parent.parent

GITIGNORE = REPO_ROOT / ".gitignore"
COMPUTE_TF = REPO_ROOT / "compute.tf"
VALUES_FILE = REPO_ROOT / "cluster-manifests" / "values-freedomfighters.yaml"
DEPLOYMENT_YAML = REPO_ROOT / "cluster-manifests" / "templates" / "deployment.yaml"


# ---------------------------------------------------------------------------
# Helper: extract the user_data bootstrap script text from compute.tf
# ---------------------------------------------------------------------------

def _get_bootstrap_script() -> str:
    """
    Extract the heredoc content of user_data from compute.tf.

    Returns the raw text between `user_data = <<-EOF` and the closing `EOF`.
    """
    source = COMPUTE_TF.read_text(encoding="utf-8")
    match = re.search(r"user_data\s*=\s*<<-EOF\s*\n(.*?)\n\s*EOF", source, re.DOTALL)
    assert match is not None, "compute.tf must contain a user_data heredoc (<<-EOF ... EOF)"
    return match.group(1)


# ---------------------------------------------------------------------------
# Helper: load deployment.yaml, stripping Helm template directives
# ---------------------------------------------------------------------------

def _load_deployment_yaml() -> dict:
    """
    Load and parse cluster-manifests/templates/deployment.yaml.

    Helm template directives ({{ ... }}) are replaced with placeholder strings
    so yaml.safe_load can parse the document.
    """
    source = DEPLOYMENT_YAML.read_text(encoding="utf-8")
    cleaned_lines = []
    for line in source.splitlines():
        if "{{" in line and "}}" in line:
            cleaned_line = re.sub(r"\{\{[^}]*\}\}", "placeholder", line)
            cleaned_lines.append(cleaned_line)
        else:
            cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    docs = list(yaml.safe_load_all(cleaned))
    for doc in docs:
        if doc and doc.get("kind") == "Deployment":
            return doc
    raise AssertionError("deployment.yaml does not contain a Deployment resource")


# ===========================================================================
# Group 1: .gitignore assertions
# Requirements: 3.1, 3.2
# ===========================================================================


def test_gitignore_contains_tfvars_pattern() -> None:
    """
    Validates: Requirements 3.1

    Assert .gitignore contains a line matching *.tfvars so that Terraform
    variable files with sensitive values are never committed to the repo.
    """
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines()
    pattern_lines = [line.strip() for line in lines]
    assert "*.tfvars" in pattern_lines, (
        ".gitignore must contain a '*.tfvars' line to prevent committing sensitive tfvars files"
    )


def test_gitignore_contains_tfvars_json_pattern() -> None:
    """
    Validates: Requirements 3.2

    Assert .gitignore contains a line matching *.tfvars.json so that JSON
    format Terraform variable files are also excluded from version control.
    """
    lines = GITIGNORE.read_text(encoding="utf-8").splitlines()
    pattern_lines = [line.strip() for line in lines]
    assert "*.tfvars.json" in pattern_lines, (
        ".gitignore must contain a '*.tfvars.json' line to prevent committing sensitive tfvars.json files"
    )


# ===========================================================================
# Group 2: Bootstrap script content assertions (task 7.7)
# Requirements: 5.3, 8.2, 9.1, 10.1, 10.5
# ===========================================================================


def test_bootstrap_trap_immediately_after_set_euo() -> None:
    """
    Validates: Requirements 10.5

    Assert the bootstrap script contains a trap '...' ERR handler on the line
    immediately after `set -euo pipefail` so bootstrap failures are always
    signalled to SSM.
    """
    script = _get_bootstrap_script()
    lines = script.splitlines()

    set_euo_index = None
    for i, line in enumerate(lines):
        if "set -euo pipefail" in line:
            set_euo_index = i
            break

    assert set_euo_index is not None, (
        "Bootstrap script must contain 'set -euo pipefail'"
    )

    # The very next non-empty line after set -euo pipefail must be the trap
    next_nonempty_index = None
    for i in range(set_euo_index + 1, len(lines)):
        if lines[i].strip():
            next_nonempty_index = i
            break

    assert next_nonempty_index is not None, (
        "Expected a line after 'set -euo pipefail' but found none"
    )
    next_line = lines[next_nonempty_index].strip()
    assert next_line.startswith("trap ") and next_line.endswith("ERR"), (
        f"The line immediately after 'set -euo pipefail' must be a 'trap ... ERR' handler, "
        f"got: {next_line!r}"
    )


def test_bootstrap_no_kubectl_wait_for_argocd_application_controller_deployment() -> None:
    """
    Validates: Requirements 9.1

    Assert the bootstrap script does NOT use the incorrect
    `kubectl wait --for=condition=Available deployment/argocd-application-controller`
    command. The application controller is a StatefulSet, not a Deployment,
    so this command would hang indefinitely.
    """
    script = _get_bootstrap_script()
    assert "kubectl wait --for=condition=Available deployment/argocd-application-controller" not in script, (
        "Bootstrap script must NOT contain "
        "'kubectl wait --for=condition=Available deployment/argocd-application-controller' "
        "because argocd-application-controller is a StatefulSet, not a Deployment"
    )


def test_bootstrap_contains_kubectl_rollout_status_argocd_application_controller() -> None:
    """
    Validates: Requirements 9.1

    Assert the bootstrap script uses `kubectl rollout status statefulset/argocd-application-controller`
    to wait for the ArgoCD application controller, which is the correct command
    for a StatefulSet resource.
    """
    script = _get_bootstrap_script()
    assert "kubectl rollout status statefulset/argocd-application-controller" in script, (
        "Bootstrap script must contain "
        "'kubectl rollout status statefulset/argocd-application-controller' "
        "to correctly wait for the ArgoCD application controller StatefulSet"
    )


def test_bootstrap_no_argocd_repo_add_or_argocd_login() -> None:
    """
    Validates: Requirements 8.2

    Assert the bootstrap script does NOT contain `argocd repo add` or
    `argocd login` commands. These CLI commands require credentials and
    are replaced by the GitOps-native ArgoCD Application manifest approach.
    """
    script = _get_bootstrap_script()
    assert "argocd repo add" not in script, (
        "Bootstrap script must NOT contain 'argocd repo add' — "
        "use a declarative ArgoCD Application manifest instead"
    )
    assert "argocd login" not in script, (
        "Bootstrap script must NOT contain 'argocd login' — "
        "the bootstrap applies the Application manifest directly via kubectl"
    )


def test_bootstrap_contains_ssm_get_active_scenario() -> None:
    """
    Validates: Requirements 5.3

    Assert the bootstrap script reads the active scenario from SSM Parameter
    Store via `aws ssm get-parameter --name /arma-reforger/active-scenario`
    rather than relying on a hardcoded Terraform variable.
    """
    script = _get_bootstrap_script()
    assert "aws ssm get-parameter --name /arma-reforger/active-scenario" in script, (
        "Bootstrap script must contain "
        "'aws ssm get-parameter --name /arma-reforger/active-scenario' "
        "to read the active scenario config from SSM at runtime"
    )


def test_bootstrap_contains_ssm_put_bootstrap_status_ready() -> None:
    """
    Validates: Requirements 10.1, 10.5

    Assert the bootstrap script writes a `ready:` completion signal to SSM
    via `aws ssm put-parameter --name /arma-reforger/bootstrap-status --value "ready:`
    so that external systems can detect successful bootstrap completion.
    """
    script = _get_bootstrap_script()
    assert 'aws ssm put-parameter --name /arma-reforger/bootstrap-status --value "ready:' in script, (
        "Bootstrap script must contain "
        "'aws ssm put-parameter --name /arma-reforger/bootstrap-status --value \"ready:' "
        "as the final completion signal written to SSM"
    )


# ===========================================================================
# Group 3: Helm template assertions
# Requirements: 1.5, 1.6, 6.1–6.4, 7.1–7.4
# ===========================================================================


def test_values_file_no_rcon_password_key() -> None:
    """
    Validates: Requirements 1.5, 1.6

    Assert values-freedomfighters.yaml does NOT contain a `rconPassword` key.
    The RCON password must be sourced from Secrets Manager via ESO, not from
    a plaintext values file.
    """
    source = VALUES_FILE.read_text(encoding="utf-8")
    assert "rconPassword" not in source, (
        "values-freedomfighters.yaml must NOT contain a 'rconPassword' key — "
        "the RCON password is managed via External Secrets Operator"
    )


def test_values_file_no_public_address_key() -> None:
    """
    Validates: Requirements 1.5, 1.6

    Assert values-freedomfighters.yaml does NOT contain a `publicAddress` key.
    The public address must be sourced from SSM Parameter Store via ESO, not
    from a plaintext values file.
    """
    source = VALUES_FILE.read_text(encoding="utf-8")
    assert "publicAddress" not in source, (
        "values-freedomfighters.yaml must NOT contain a 'publicAddress' key — "
        "the public address is managed via External Secrets Operator"
    )


def test_deployment_rcon_password_uses_secret_key_ref() -> None:
    """
    Validates: Requirements 1.5, 6.1

    Assert the RCON_PASSWORD env var in deployment.yaml uses a `secretKeyRef`
    (sourcing the value from a Kubernetes Secret) rather than a plain `value:`
    which would expose it in plaintext.
    """
    doc = _load_deployment_yaml()
    containers = (
        doc.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    assert containers, "deployment.yaml must define at least one container"

    rcon_env = None
    for container in containers:
        for env in container.get("env", []):
            if env.get("name") == "RCON_PASSWORD":
                rcon_env = env
                break

    assert rcon_env is not None, "deployment.yaml container must define a RCON_PASSWORD env var"
    assert "valueFrom" in rcon_env, (
        "RCON_PASSWORD env var must use 'valueFrom' (secretKeyRef), not a plain 'value:'"
    )
    assert "secretKeyRef" in rcon_env.get("valueFrom", {}), (
        "RCON_PASSWORD env var must use 'secretKeyRef' to source the value from a Kubernetes Secret"
    )
    # Ensure there is no plain 'value:' key on the env entry
    assert "value" not in rcon_env, (
        "RCON_PASSWORD env var must NOT have a plain 'value:' field"
    )


def test_deployment_server_public_address_uses_secret_key_ref() -> None:
    """
    Validates: Requirements 1.6, 6.1

    Assert the SERVER_PUBLIC_ADDRESS env var in deployment.yaml uses a
    `secretKeyRef` rather than a plain `value:` which would embed the IP
    address in a committed manifest.
    """
    doc = _load_deployment_yaml()
    containers = (
        doc.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    assert containers, "deployment.yaml must define at least one container"

    addr_env = None
    for container in containers:
        for env in container.get("env", []):
            if env.get("name") == "SERVER_PUBLIC_ADDRESS":
                addr_env = env
                break

    assert addr_env is not None, "deployment.yaml container must define a SERVER_PUBLIC_ADDRESS env var"
    assert "valueFrom" in addr_env, (
        "SERVER_PUBLIC_ADDRESS env var must use 'valueFrom' (secretKeyRef), not a plain 'value:'"
    )
    assert "secretKeyRef" in addr_env.get("valueFrom", {}), (
        "SERVER_PUBLIC_ADDRESS env var must use 'secretKeyRef' to source the value from a Kubernetes Secret"
    )
    assert "value" not in addr_env, (
        "SERVER_PUBLIC_ADDRESS env var must NOT have a plain 'value:' field"
    )


def test_deployment_all_volume_mounts_have_non_empty_mount_path_and_sub_path() -> None:
    """
    Validates: Requirements 6.1–6.4, 7.1–7.4

    Assert every volumeMount in deployment.yaml has both a non-empty `mountPath`
    and a non-empty `subPath`. A missing mountPath means the container has no
    idea where to find the data; a missing subPath causes different mounts on
    the same PVC to collide.
    """
    doc = _load_deployment_yaml()
    containers = (
        doc.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    assert containers, "deployment.yaml must define at least one container"

    all_mounts: list[dict] = []
    for container in containers:
        all_mounts.extend(container.get("volumeMounts", []))

    assert len(all_mounts) >= 3, (
        f"deployment.yaml must have at least 3 volumeMounts, found {len(all_mounts)}"
    )

    for mount in all_mounts:
        mount_path = mount.get("mountPath", "")
        sub_path = mount.get("subPath", "")
        assert mount_path, (
            f"volumeMount '{mount.get('name')}' must have a non-empty 'mountPath', got: {mount_path!r}"
        )
        assert sub_path, (
            f"volumeMount '{mount.get('name')}' must have a non-empty 'subPath', got: {sub_path!r}"
        )


def test_deployment_all_volume_mount_paths_are_distinct() -> None:
    """
    Validates: Requirements 6.4, 7.4

    Assert all three volumeMount mountPath values in deployment.yaml are
    distinct strings. Duplicate mountPaths would cause containers to see the
    same directory via different volume names, masking data-layout bugs.
    """
    doc = _load_deployment_yaml()
    containers = (
        doc.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    assert containers, "deployment.yaml must define at least one container"

    all_mounts: list[dict] = []
    for container in containers:
        all_mounts.extend(container.get("volumeMounts", []))

    assert len(all_mounts) >= 3, (
        f"deployment.yaml must have at least 3 volumeMounts, found {len(all_mounts)}"
    )

    mount_paths = [m["mountPath"] for m in all_mounts]
    assert len(mount_paths) == len(set(mount_paths)), (
        f"All volumeMount 'mountPath' values must be distinct, got duplicates in: {mount_paths}"
    )
