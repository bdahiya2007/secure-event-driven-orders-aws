variable "function_name" {
  description = "Lambda function name"
  type        = string
}

variable "event_bus_name" {
  description = "Custom event bus the OrderCreated event is published to"
  type        = string
}

variable "event_bus_arn" {
  description = "ARN of the custom event bus (scopes the PutEvents permission)"
  type        = string
}

variable "table_name" {
  description = "DynamoDB table the order items are written to"
  type        = string
}

variable "table_arn" {
  description = "ARN of the DynamoDB table (scopes the PutItem permission)"
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}

variable "tags" {
  description = "Tags applied to resources"
  type        = map(string)
  default     = {}
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary attached to the execution role; null attaches none"
  type        = string
  default     = null
}
