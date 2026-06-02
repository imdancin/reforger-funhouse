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
  }

  # UNCOMMENTED: Moving state management to the cloud layer
  # backend "s3" {
  #   bucket         = "your-unique-arma-tfstate-bucket"
  #   key            = "arma-reforger/terraform.tfstate"
  #   region         = "us-west-2"
  #   dynamodb_table = "arma-tf-lockstate-table"
  #   encrypt        = true
  #   profile        = "reforger-admin" # Inherits your active keyless session config
  # }
}

provider "aws" {
  region  = "us-west-2"
  profile = "reforger-admin"

  default_tags {
    tags = {
      Environment = "Gaming"
      ManagedBy   = "Terraform"
      Project     = "ArmaReforger"
    }
  }
}


