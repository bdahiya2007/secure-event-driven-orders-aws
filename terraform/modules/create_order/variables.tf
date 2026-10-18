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

variable "processing_table_name" {
  description = "DynamoDB table holding one idempotency record per orderId (lease, status and result)"
  type        = string
}

variable "processing_table_arn" {
  description = "ARN of the processing table (scopes the PutItem and Query permissions)"
  type        = string
}

variable "queue_arn" {
  description = "SQS queue the function consumes orders from"
  type        = string
}

variable "timeout" {
  description = "Function timeout in seconds; the queue visibility timeout must be at least 6x this"
  type        = number
  default     = 30
}

variable "batch_size" {
  description = "Maximum messages per invocation"
  type        = number
  default     = 10
}

variable "max_concurrency" {
  description = "Maximum concurrent invocations from the queue (2-1000)"
  type        = number
  default     = 5
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

variable "event_endpoint_id" {
  description = "EventBridge global endpoint ID; when set, events are published through it instead of straight to the regional bus"
  type        = string
  default     = null
}

variable "publish_resource_arns" {
  description = "Extra ARNs the role may PutEvents to: the global endpoint and the other region's bus"
  type        = list(string)
  default     = []
}

variable "vendor_awscrt" {
  description = "Package the pinned awscrt library with the function (needed to sign requests to a global endpoint)"
  type        = bool
  default     = false
}
