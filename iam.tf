# 1. CREATES the actual GitHub OpenID Connect Identity Provider in your new account
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # Official thumbprint for GitHub Actions token service
  thumbprint_list = ["1c58a3a8518e8759bf075b76b750d4f2df264fcd", "6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# 2. Creates the IAM Role that GitHub Actions will assume natively via OIDC
resource "aws_iam_role" "github_actions_oidc" {
  name = "github-actions-arma-deployer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          # References the ARN of the provider created right above
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            # FIXED STRINGS: Matches standard GitHub Action token claims exactly
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Scopes the role exclusively to your specific repository path
            "token.actions.githubusercontent.com:sub" = "repo:imdancin/reforger-funhouse:*"
          }
        }
      }
    ]
  })
}

# 3. Least-privilege custom policy for GitHub Actions — replaces PowerUserAccess
resource "aws_iam_policy" "github_actions_least_privilege" {
  name        = "github-actions-arma-least-privilege"
  description = "Least-privilege policy for the GitHub Actions OIDC role managing Arma Reforger infrastructure"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2Management"
        Effect = "Allow"
        Action = [
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
          "ec2:DescribeAddresses"
        ]
        Resource = "arn:aws:ec2:us-west-2:*:*"
      },
      {
        Sid    = "SSMCommands"
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation"
        ]
        Resource = "arn:aws:ec2:us-west-2:*:instance/*"
      },
      {
        Sid    = "TerraformState"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
          "s3:DeleteObject"
        ]
        Resource = [
          "arn:aws:s3:::your-unique-arma-tfstate-bucket",
          "arn:aws:s3:::your-unique-arma-tfstate-bucket/*"
        ]
      },
      {
        Sid    = "TerraformLock"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:DescribeTable"
        ]
        Resource = "arn:aws:dynamodb:us-west-2:*:table/arma-tf-lockstate-table"
      }
    ]
  })
}

# 4. Attach the least-privilege custom policy to the GitHub Actions OIDC role
resource "aws_iam_role_policy_attachment" "github_actions_least_privilege" {
  role       = aws_iam_role.github_actions_oidc.name
  policy_arn = aws_iam_policy.github_actions_least_privilege.arn
}

# 5. Inline policy granting the EC2 SSM role permission to write the bootstrap-status signal
resource "aws_iam_role_policy" "ssm_role_bootstrap_status" {
  name = "arma-server-bootstrap-status-put"
  role = aws_iam_role.ssm_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowBootstrapStatusPut"
        Effect   = "Allow"
        Action   = "ssm:PutParameter"
        Resource = "arn:aws:ssm:us-west-2:*:parameter/arma-reforger/bootstrap-status"
      }
    ]
  })
}

# ─── External Secrets Operator (ESO) IAM User ────────────────────────────────

# 5. Dedicated IAM user for the External Secrets Operator running in K3s.
#    K3s is not EKS, so IRSA is unavailable; a static IAM user with scoped
#    read-only credentials is the simplest secure approach.
resource "aws_iam_user" "eso_reader" {
  name = "eso-reader"
  tags = {
    Purpose = "External Secrets Operator credential bridge for Arma Reforger K3s cluster"
  }
}

# 6. Inline policy granting the ESO reader user read-only access to exactly the
#    secrets and parameters it needs — nothing more.
resource "aws_iam_user_policy" "eso_reader_policy" {
  name = "eso-reader-policy"
  user = aws_iam_user.eso_reader.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SecretsManagerReadRconPassword"
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        # Trailing wildcard covers the random suffix AWS appends to secret ARNs
        Resource = [
          "arn:aws:secretsmanager:us-west-2:*:secret:/arma-reforger/game-rcon-password*",
          "arn:aws:secretsmanager:us-west-2:*:secret:/arma-reforger/game-password*",
          "arn:aws:secretsmanager:us-west-2:*:secret:/arma-reforger/game-admin-password*",
          "arn:aws:secretsmanager:us-west-2:*:secret:/arma-reforger/grafana-admin-password*"
        ]
      },
      {
        Sid      = "SSMReadPublicAddress"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "arn:aws:ssm:us-west-2:*:parameter/arma-reforger/public-address"
      }
    ]
  })
}

# 7. Access key for the ESO reader user.  The secret value is sensitive and is
#    stored in SSM Parameter Store (SecureString) so the bootstrap script can
#    retrieve it without it ever appearing in plaintext in Terraform outputs.
resource "aws_iam_access_key" "eso_reader" {
  user = aws_iam_user.eso_reader.name
}

# 8. Store the ESO access key ID in SSM so the bootstrap script can read it.
resource "aws_ssm_parameter" "eso_access_key_id" {
  name  = "/arma-reforger/eso-access-key-id"
  type  = "String"
  value = aws_iam_access_key.eso_reader.id

  description = "ESO IAM user access key ID — read by the bootstrap script to create the eso-aws-credentials Kubernetes Secret"
}

# 9. Store the ESO secret access key in SSM as a SecureString so it is
#    encrypted at rest and requires explicit --with-decryption to retrieve.
resource "aws_ssm_parameter" "eso_secret_access_key" {
  name  = "/arma-reforger/eso-secret-access-key"
  type  = "SecureString"
  value = aws_iam_access_key.eso_reader.secret

  description = "ESO IAM user secret access key — read by the bootstrap script to create the eso-aws-credentials Kubernetes Secret"
}
