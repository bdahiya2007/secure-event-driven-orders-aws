variable "queue_name" {
  description = "Name of the main queue; the dead-letter queue is <name>-dlq"
  type        = string
}

variable "allowed_source_arn" {
  description = "ARN of the EventBridge rule allowed to send messages to the queue; null for a queue not fed by a rule"
  type        = string
  default     = null
}

variable "sender_role_arns" {
  description = "IAM role ARNs allowed to send messages; everyone else is denied. Empty applies no restriction"
  type        = list(string)
  default     = []
}

variable "visibility_timeout_seconds" {
  description = "Should be at least 6x the consumer Lambda timeout"
  type        = number
  default     = 180
}

variable "max_receive_count" {
  description = "Receives before a message moves to the dead-letter queue"
  type        = number
  default     = 3
}

variable "tags" {
  description = "Tags applied to the queues"
  type        = map(string)
  default     = {}
}

variable "alarm_actions" {
  description = "ARNs (for example an SNS topic) notified when the dead-letter queue alarm changes state"
  type        = list(string)
  default     = []
}
