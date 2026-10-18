terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "secure-event-driven-orders-aws"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Used only by the modules of the secondary Region (enable_multi_region).
provider "aws" {
  alias  = "secondary"
  region = var.secondary_region

  default_tags {
    tags = {
      Project     = "secure-event-driven-orders-aws"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
