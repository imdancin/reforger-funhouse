# Manually set game join password — stored in Secrets Manager, set in terraform.tfvars
variable "game_password" {
  type        = string
  sensitive   = true
  description = "Password players must enter to join the server"
}

# Manually set admin password — stored in Secrets Manager, set in terraform.tfvars
variable "game_admin_password" {
  type        = string
  sensitive   = true
  description = "Admin password for in-game server administration"
}

# Manually set rcon password — stored in Secrets Manager, set in terraform.tfvars
variable "rcon_password" {
  type        = string
  sensitive   = true
  description = "Admin password for external server administration"
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

# Secrets Manager secret container for the game join password
resource "aws_secretsmanager_secret" "game_password" {
  name                    = "/arma-reforger/game-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "game_password" {
  secret_id     = aws_secretsmanager_secret.game_password.id
  secret_string = var.game_password
}

# Secrets Manager secret container for the admin password
resource "aws_secretsmanager_secret" "game_admin_password" {
  name                    = "/arma-reforger/game-admin-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "game_admin_password" {
  secret_id     = aws_secretsmanager_secret.game_admin_password.id
  secret_string = var.game_admin_password
}

# Secrets Manager secret container for the rcon password
resource "aws_secretsmanager_secret" "rcon_password" {
  name                    = "/arma-reforger/game-rcon-password"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "rcon_password" {
  secret_id     = aws_secretsmanager_secret.rcon_password.id
  secret_string = var.rcon_password
}
