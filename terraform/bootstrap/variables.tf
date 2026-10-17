variable "aws_region" {
  description = "Region the application is deployed in"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for the pipeline's own IAM resources (deploy role, boundary policy)"
  type        = string
  default     = "orders-pipeline"
}

variable "github_sub_prefix" {
  description = <<-EOT
    The repository's OIDC subject prefix, exactly as GitHub reports it, for example
    repo:<owner>@<owner-id>/<repo>@<repo-id>. Get it with:
      gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix
    (after enabling immutable subjects, see terraform/README.md). It pins the trust to this exact repository.
  EOT
  type        = string

  validation {
    condition     = can(regex("^repo:[^:]+@[0-9]+/[^:]+@[0-9]+$", var.github_sub_prefix))
    error_message = "github_sub_prefix must look like repo:<owner>@<id>/<repo>@<id> (immutable IDs, no branch or environment)."
  }
}

variable "github_environments" {
  description = "GitHub environments whose jobs may assume the deploy role"
  type        = list(string)
  default     = ["dev", "prod"]
}

variable "state_bucket_name" {
  description = "S3 bucket holding the Terraform state; null derives it from the account ID (the shared bucket)"
  type        = string
  default     = null
}

variable "state_key_prefix" {
  description = "Key prefix of this project's state objects in the bucket"
  type        = string
  default     = "secure-event-driven-orders-aws/"
}

variable "app_role_prefixes" {
  description = "Name prefixes of the application roles the pipeline may create (each must carry the boundary)"
  type        = list(string)
  default     = ["CreateOrder-", "FulfilOrder-"]
}
