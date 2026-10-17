variable "topic_name" {
  description = "SNS topic name"
  type        = string
}

variable "alert_email" {
  description = "Email address to notify; empty creates the topic with no subscriber"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to the topic"
  type        = map(string)
  default     = {}
}
