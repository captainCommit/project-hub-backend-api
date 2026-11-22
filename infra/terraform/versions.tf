terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State holds the database admin password and the Cognito client secret. For anything beyond a one-person
  # setup, keep it in an encrypted S3 bucket instead of on disk:
  #
  # backend "s3" {
  #   bucket       = "<state-bucket>"
  #   key          = "project-hub/dev/foundation.tfstate"
  #   region       = "ca-central-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = "project-hub"
      env        = var.stage
      managed-by = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
