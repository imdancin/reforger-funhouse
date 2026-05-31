# The DynamoDB Table used strictly for state locking
resource "aws_dynamodb_table" "terraform_locks" {
  name         = "arma-tf-lockstate-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# The S3 Bucket used to store your terraform.tfstate file
resource "aws_s3_bucket" "terraform_state" {
  bucket        = "your-unique-arma-tfstate-bucket" # Must be globally unique across AWS
  force_destroy = false                             # Prevents accidental deletion of your state histories

  tags = {
    Name        = "Terraform State Storage"
    Environment = "Gaming"
  }
}

# Enforce Object Versioning to protect against accidental state corruption or deletion
resource "aws_s3_bucket_versioning" "state_versioning" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block all public read/write access to your infrastructure metadata
resource "aws_s3_bucket_public_access_block" "state_privacy" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Force data-at-rest encryption using AWS-managed keys
resource "aws_s3_bucket_server_side_encryption_configuration" "state_encryption" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
