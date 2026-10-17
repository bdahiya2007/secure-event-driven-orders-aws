variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (fixed per folder; used in resource names and tags)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "alert_email" {
  description = "Email address for operational alerts; empty means no subscriber"
  type        = string
  sensitive   = true
  default     = ""
}

variable "notification_email" {
  description = "Email address for order notifications; empty means no subscriber"
  type        = string
  sensitive   = true
  default     = ""
}
