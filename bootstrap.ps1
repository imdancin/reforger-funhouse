# Ensure the script halts immediately on any execution failure
$ErrorActionPreference = "Stop"

Write-Host "=== Step 1: Temporarily Disabling Cloud S3 Backend for Local Bootstrap ===" -ForegroundColor Cyan
$ProvidersPath = ".\providers.tf"
$ProvidersContent = Get-Content $ProvidersPath -Raw

# Comment out the backend "s3" resource block using a regular expression match
$CommentedContent = $ProvidersContent -replace '(?s)backend\s+"s3"\s+\{.*\}', '# Backend temporarily disabled by bootstrap script'
Set-Content $ProvidersPath $CommentedContent

Write-Host "=== Step 2: Initializing Terraform Locally ===" -ForegroundColor Cyan
terraform init

Write-Host "=== Step 3: Provisioning S3 State Bucket and DynamoDB Lock Table ===" -ForegroundColor Cyan
# This only deploys your storage metadata; your game instance count stays at zero
# FIXED: Added the resource names (.terraform_state and .terraform_locks) to the target parameters
terraform apply -target=aws_s3_bucket.terraform_state -target=aws_dynamodb_table.terraform_locks -auto-approve

Write-Host "=== Step 4: Re-enabling Cloud S3 Backend Layout ===" -ForegroundColor Cyan
# Restore the original providers.tf code layout
Set-Content $ProvidersPath $ProvidersContent

Write-Host "=== Step 5: Migrating Local State up to AWS Cloud Backend ===" -ForegroundColor Cyan
# Forces terraform to copy its local tracking file into your new S3 bucket
terraform init -migrate-state -force-copy

Write-Host "=====================================================" -ForegroundColor Green
Write-Host " Bootstrap Complete! Remote state is now active.    " -ForegroundColor Green
Write-Host " You can safely run your initial Git commit and push." -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green