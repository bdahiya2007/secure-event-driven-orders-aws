variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (fixed per folder; used in resource names and tags)"
  type        = string
  default     = "prod"

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

variable "enable_multi_region" {
  description = "Run a second copy of the application in secondary_region (API, queues, Lambda functions, event bus, SNS topics), make the three tables global tables, and publish events through an EventBridge global endpoint. Billed items: replicated writes and a Route 53 health check. Applied from a workstation, not by the pipeline."
  type        = bool
  default     = false
}

variable "secondary_region" {
  description = "The secondary Region used when enable_multi_region is on"
  type        = string
  default     = "us-west-2"

  validation {
    condition     = var.secondary_region != var.aws_region
    error_message = "secondary_region must differ from aws_region."
  }
}
