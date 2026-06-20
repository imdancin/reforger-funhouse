"""Adapter for writing the active scenario preset to AWS SSM Parameter Store."""

import boto3


class ScenarioStoreError(Exception):
    """Raised when writing the active scenario to SSM fails."""

    pass


def set_active_scenario(
    values_file: str,
    ssm_client=None,
    parameter_name: str = "/arma-reforger/active-scenario",
) -> None:
    """Write the resolved preset values file name to SSM.

    Args:
        values_file: The Helm values file name (e.g. "values-freedomfighters.yaml")
        ssm_client: Optional boto3 SSM client (created if not provided)
        parameter_name: SSM parameter path

    Raises:
        ScenarioStoreError: If the SSM put_parameter call fails for any reason.
    """
    if ssm_client is None:
        ssm_client = boto3.client("ssm")

    try:
        ssm_client.put_parameter(
            Name=parameter_name,
            Value=values_file,
            Type="String",
            Overwrite=True,
        )
    except Exception as exc:
        raise ScenarioStoreError(
            f"Failed to write active scenario to {parameter_name}: {exc}"
        ) from exc
