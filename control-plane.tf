# ─── Discord Control Plane Infrastructure ─────────────────────────────────────
# Serverless backend for Discord slash-command-driven server launches.
# Components: API Gateway HTTP API, Lambda functions, Step Functions orchestrator,
# DynamoDB state table, SSM parameters, Secrets Manager entries, and IAM roles.

# ─── Data Sources ─────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ─── Variables ────────────────────────────────────────────────────────────────

variable "discord_app_public_key" {
  type        = string
  description = "Discord application Ed25519 public key for interaction signature verification"
  sensitive   = true
}

variable "discord_app_id" {
  type        = string
  description = "Discord application ID for API follow-up messages"
  default     = "1518838964323352637"
}

variable "launch_timeout_seconds" {
  type        = number
  default     = 600
  description = "Maximum seconds to wait for the server to reach RUNNING before timing out"
}

variable "idle_threshold_minutes" {
  type        = number
  default     = 30
  description = "Minutes of zero players before auto-teardown is triggered"
}

# ─── DynamoDB: Server State Store ─────────────────────────────────────────────

resource "aws_dynamodb_table" "arma_server_state" {
  name         = "arma-server-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  tags = {
    Name = "arma-server-state"
  }
}

# ─── SSM Parameters ──────────────────────────────────────────────────────────

resource "aws_ssm_parameter" "discord_allowlist" {
  name        = "/arma-reforger/discord-allowlist"
  type        = "String"
  value       = jsonencode({ user_ids = [], role_ids = [] })
  description = "JSON allowlist of Discord user IDs and role IDs permitted to launch the server"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "bootstrap_status" {
  name        = "/arma-reforger/bootstrap-status"
  type        = "String"
  value       = "unknown"
  description = "Bootstrap status signal written by EC2 user_data on startup"

  lifecycle {
    ignore_changes = [value]
  }
}

# ─── Secrets Manager: Control Plane Secrets ───────────────────────────────────

resource "aws_secretsmanager_secret" "github_dispatch_token" {
  name                    = "/arma-reforger/github-dispatch-token"
  recovery_window_in_days = 0
  description             = "Fine-grained GitHub PAT for triggering repository_dispatch workflows"
}

resource "aws_secretsmanager_secret" "discord_channel_webhook_url" {
  name                    = "/arma-reforger/discord-channel-webhook-url"
  recovery_window_in_days = 0
  description             = "Discord channel webhook URL for teardown and idle notifications"
}

# ─── IAM: Lambda Execution Role ───────────────────────────────────────────────

resource "aws_iam_role" "control_plane_lambda" {
  name = "arma-control-plane-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# CloudWatch Logs — all Lambdas can write logs
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.control_plane_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Least-privilege policy for the control-plane Lambdas
resource "aws_iam_role_policy" "control_plane_lambda_policy" {
  name = "arma-control-plane-lambda-policy"
  role = aws_iam_role.control_plane_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBStateAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = aws_dynamodb_table.arma_server_state.arn
      },
      {
        Sid    = "SSMParameterRead"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/arma-reforger/*"
        ]
      },
      {
        Sid    = "SSMParameterWrite"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter"
        ]
        Resource = [
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/arma-reforger/active-scenario",
          "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/arma-reforger/bootstrap-status"
        ]
      },
      {
        Sid    = "SecretsManagerRead"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "${aws_secretsmanager_secret.github_dispatch_token.arn}",
          "${aws_secretsmanager_secret.discord_channel_webhook_url.arn}"
        ]
      },
      {
        Sid    = "StepFunctionsStart"
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = aws_sfn_state_machine.launch_orchestrator.arn
      }
    ]
  })
}

# ─── Lambda Functions ─────────────────────────────────────────────────────────

# Placeholder zip for initial deployment — replaced by CI/CD pipeline
data "archive_file" "lambda_placeholder" {
  type        = "zip"
  output_path = "${path.module}/.terraform/tmp/lambda_placeholder.zip"

  source {
    content  = "def handler(event, context): return {'statusCode': 200}"
    filename = "handler.py"
  }
}

resource "aws_lambda_function" "launch_handler" {
  function_name = "arma-launch-handler"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 10
  memory_size   = 256

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  snap_start {
    apply_on = "PublishedVersions"
  }

  environment {
    variables = {
      DISCORD_PUBLIC_KEY      = var.discord_app_public_key
      DISCORD_APPLICATION_ID  = var.discord_app_id
      STATE_TABLE_NAME        = aws_dynamodb_table.arma_server_state.name
      ORCHESTRATOR_ARN        = aws_sfn_state_machine.launch_orchestrator.arn
      LAUNCH_TIMEOUT_SECONDS  = tostring(var.launch_timeout_seconds)
    }
  }

  timeouts {
    create = "5m"
    update = "5m"
  }

  tags = {
    Name = "arma-launch-handler"
  }
}

# The 'live' alias is created and managed by deploy_lambdas.py after deploying
# real code and publishing a SnapStart-optimized version.
# Terraform only manages the alias if it already exists.
resource "aws_lambda_alias" "launch_handler_live" {
  name             = "live"
  function_name    = aws_lambda_function.launch_handler.function_name
  function_version = "$LATEST"

  lifecycle {
    ignore_changes = [function_version]
  }
}

resource "aws_lambda_function" "set_preset" {
  function_name = "arma-set-preset"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-set-preset"
  }
}

resource "aws_lambda_function" "dispatch_apply" {
  function_name = "arma-dispatch-apply"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-dispatch-apply"
  }
}

resource "aws_lambda_function" "check_ready" {
  function_name = "arma-check-ready"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-check-ready"
  }
}

resource "aws_lambda_function" "mark_running" {
  function_name = "arma-mark-running"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-mark-running"
  }
}

resource "aws_lambda_function" "failed" {
  function_name = "arma-launch-failed"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-launch-failed"
  }
}

resource "aws_lambda_function" "timed_out" {
  function_name = "arma-launch-timed-out"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-launch-timed-out"
  }
}

resource "aws_lambda_function" "teardown_handler" {
  function_name = "arma-teardown-handler"
  role          = aws_iam_role.control_plane_lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 128

  filename         = data.archive_file.lambda_placeholder.output_path
  source_code_hash = data.archive_file.lambda_placeholder.output_base64sha256

  environment {
    variables = {
      STATE_TABLE_NAME = aws_dynamodb_table.arma_server_state.name
    }
  }

  tags = {
    Name = "arma-teardown-handler"
  }
}

# ─── Step Functions: LaunchOrchestrator ───────────────────────────────────────

resource "aws_iam_role" "launch_orchestrator" {
  name = "arma-launch-orchestrator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "launch_orchestrator_policy" {
  name = "arma-launch-orchestrator-policy"
  role = aws_iam_role.launch_orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeLambdas"
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          aws_lambda_function.set_preset.arn,
          aws_lambda_function.dispatch_apply.arn,
          aws_lambda_function.check_ready.arn,
          aws_lambda_function.mark_running.arn,
          aws_lambda_function.failed.arn,
          aws_lambda_function.timed_out.arn
        ]
      }
    ]
  })
}

resource "aws_sfn_state_machine" "launch_orchestrator" {
  name     = "arma-launch-orchestrator"
  role_arn = aws_iam_role.launch_orchestrator.arn

  definition = jsonencode({
    Comment        = "Orchestrates Arma Reforger server launch: preset -> dispatch -> poll readiness -> mark running"
    StartAt        = "SetPreset"
    TimeoutSeconds = var.launch_timeout_seconds
    States = {
      SetPreset = {
        Type     = "Task"
        Resource = aws_lambda_function.set_preset.arn
        Next     = "DispatchApply"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "Failed"
          }
        ]
      }
      DispatchApply = {
        Type     = "Task"
        Resource = aws_lambda_function.dispatch_apply.arn
        Next     = "WaitForReady"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "Failed"
          }
        ]
      }
      WaitForReady = {
        Type    = "Wait"
        Seconds = 30
        Next    = "CheckReady"
      }
      CheckReady = {
        Type     = "Task"
        Resource = aws_lambda_function.check_ready.arn
        Next     = "IsReady"
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "Failed"
          }
        ]
      }
      IsReady = {
        Type = "Choice"
        Choices = [
          {
            Variable      = "$.ready"
            BooleanEquals = true
            Next          = "MarkRunning"
          },
          {
            Variable      = "$.timed_out"
            BooleanEquals = true
            Next          = "TimedOut"
          }
        ]
        Default = "WaitForReady"
      }
      MarkRunning = {
        Type     = "Task"
        Resource = aws_lambda_function.mark_running.arn
        End      = true
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "Failed"
          }
        ]
      }
      Failed = {
        Type     = "Task"
        Resource = aws_lambda_function.failed.arn
        End      = true
      }
      TimedOut = {
        Type     = "Task"
        Resource = aws_lambda_function.timed_out.arn
        End      = true
      }
    }
  })

  tags = {
    Name = "arma-launch-orchestrator"
  }
}

# ─── API Gateway HTTP API ─────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "control_plane" {
  name          = "arma-control-plane"
  protocol_type = "HTTP"
  description   = "Discord interaction endpoint for Arma Reforger server control"

  tags = {
    Name = "arma-control-plane-api"
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.control_plane.id
  name        = "$default"
  auto_deploy = true

  tags = {
    Name = "arma-control-plane-api-default-stage"
  }
}

resource "aws_apigatewayv2_integration" "launch_handler" {
  api_id                 = aws_apigatewayv2_api.control_plane.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.launch_handler_live.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "post_interactions" {
  api_id    = aws_apigatewayv2_api.control_plane.id
  route_key = "POST /interactions"
  target    = "integrations/${aws_apigatewayv2_integration.launch_handler.id}"
}

# Grant API Gateway permission to invoke the Launch_Handler Lambda alias
resource "aws_lambda_permission" "apigw_invoke_launch_handler" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.launch_handler.function_name
  qualifier     = aws_lambda_alias.launch_handler_live.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.control_plane.execution_arn}/*/*"
}

# ─── IAM: EC2 Instance Role — Teardown_Handler Invoke Permission ──────────────
# The Idle_Monitor on the EC2 instance needs to invoke exactly the Teardown_Handler.
# This is scoped to only that single function ARN (least-privilege).

resource "aws_iam_role_policy" "ssm_role_invoke_teardown" {
  name = "arma-server-invoke-teardown-handler"
  role = aws_iam_role.ssm_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowInvokeTeardownHandler"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.teardown_handler.arn
      }
    ]
  })
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "control_plane_api_endpoint" {
  description = "The HTTPS endpoint URL for the Discord interaction webhook"
  value       = "${aws_apigatewayv2_api.control_plane.api_endpoint}/interactions"
}

output "launch_orchestrator_arn" {
  description = "ARN of the LaunchOrchestrator Step Functions state machine"
  value       = aws_sfn_state_machine.launch_orchestrator.arn
}

output "teardown_handler_arn" {
  description = "ARN of the Teardown_Handler Lambda (referenced by Idle_Monitor)"
  value       = aws_lambda_function.teardown_handler.arn
}
