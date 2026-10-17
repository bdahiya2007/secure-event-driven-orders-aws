variable "topic_name" {
  description = "SNS topic name"
  type        = string
}

variable "allowed_source_arn" {
  description = "ARN of the EventBridge rule allowed to publish to the topic"
  type        = string
}

variable "notification_email" {
  description = "Email address to notify; empty creates the topic with no subscriber"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to the topic"
  type        = map(string)
  default     = {}
}
