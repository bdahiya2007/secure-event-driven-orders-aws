terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Applied MANUALLY (never by the pipeline) with local state, so the pipeline cannot change its own
# permissions. The state file is git-ignored; keep it safe, or re-import the resources if it is lost.
provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "secure-event-driven-orders-aws"
      ManagedBy = "terraform"
      Purpose   = "pipeline-identity"
    }
  }
}
