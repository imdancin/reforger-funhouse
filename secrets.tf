# Random RCON password — replaces the previously hardcoded "reF0rg3r123" credential
resource "random_password" "rcon_password" {
  length           = 24
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Secrets Manager secret container for the RCON password
resource "aws_secretsmanager_secret" "rcon_password" {
  name                    = "/arma-reforger/rcon-password"
  recovery_window_in_days = 0 # Allow immediate deletion for dev lifecycle
}

# Secrets Manager secret version — stores the generated random password value
resource "aws_secretsmanager_secret_version" "rcon_password" {
  secret_id     = aws_secretsmanager_secret.rcon_password.id
  secret_string = random_password.rcon_password.result
}

# SSM Parameter: EIP public address — written after the Elastic IP is allocated
resource "aws_ssm_parameter" "public_address" {
  name  = "/arma-reforger/public-address"
  type  = "String"
  value = aws_eip.arma_static_ip.public_ip

  lifecycle {
    precondition {
      condition     = aws_eip.arma_static_ip.public_ip != null && aws_eip.arma_static_ip.public_ip != ""
      error_message = "EIP public_ip is null or empty. Ensure the Elastic IP has been allocated before writing the SSM parameter."
    }
  }
}

# SSM Parameter: active scenario values file name — determines which Helm values file ArgoCD uses
resource "aws_ssm_parameter" "active_scenario" {
  name  = "/arma-reforger/active-scenario"
  type  = "String"
  value = "values-freedomfighters.yaml"
}
