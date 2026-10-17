variable "dashboard_name" {
  description = "CloudWatch dashboard name"
  type        = string
}

variable "bus_name" {
  description = "EventBridge bus the rules belong to"
  type        = string
}

variable "rule_names" {
  description = "EventBridge rule names to chart (MatchedEvents)"
  type        = list(string)
}

variable "queue_names" {
  description = "SQS queue names to chart (NumberOfMessagesSent)"
  type        = list(string)
}

variable "topic_names" {
  description = "SNS topic names to chart (NumberOfMessagesPublished)"
  type        = list(string)
}
