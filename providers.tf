terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  backend "s3" {
    bucket         = "your-unique-arma-tfstate-bucket"
    key            = "arma-reforger/terraform.tfstate"
    region         = "us-west-2"
    dynamodb_table = "arma-tf-lockstate-table"
    encrypt        = true
  }
}

provider "aws" {
  region  = "us-west-2"
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Environment = "Gaming"
      ManagedBy   = "Terraform"
      Project     = "ArmaReforger"
    }
  }
}


