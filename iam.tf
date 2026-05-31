# 1. CREATES the actual GitHub OpenID Connect Identity Provider in your new account
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
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

# 3. Attach policies required to manage your gaming resources seamlessly
resource "aws_iam_role_policy_attachment" "ssm_and_infra" {
  role       = aws_iam_role.github_actions_oidc.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}