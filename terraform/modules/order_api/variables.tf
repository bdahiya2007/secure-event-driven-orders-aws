variable "api_name" {
  description = "Name of the REST API"
  type        = string
}

variable "role_name" {
  description = "Name of the IAM role API Gateway assumes to send to the queue"
  type        = string
}

variable "queue_name" {
  description = "Name of the SQS queue orders are sent to"
  type        = string
}

variable "queue_arn" {
  description = "ARN of the SQS queue (scopes the SendMessage permission)"
  type        = string
}

variable "stage_name" {
  description = "API stage name"
  type        = string
  default     = "v1"
}

variable "throttling_rate_limit" {
  description = "Steady requests per second allowed on the stage"
  type        = number
  default     = 10
}

variable "throttling_burst_limit" {
  description = "Burst of requests allowed on the stage"
  type        = number
  default     = 20
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary attached to the API's role; null attaches none"
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to resources"
  type        = map(string)
  default     = {}
}
