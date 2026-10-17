variable "rule_name" {
  description = "Rule name"
  type        = string
}

variable "description" {
  description = "Rule description"
  type        = string
  default     = null
}

variable "bus_name" {
  description = "Event bus the rule is attached to"
  type        = string
}

variable "event_pattern" {
  description = "Event pattern as a JSON string"
  type        = string
}

variable "target_arn" {
  description = "ARN of the target (for example an SQS queue or SNS topic) to route matching events to"
  type        = string
}

variable "tags" {
  description = "Tags applied to the rule"
  type        = map(string)
  default     = {}
}

variable "target_id" {
  description = "Target ID within the rule"
  type        = string
  default     = "sqs"
}

variable "input_transformer" {
  description = "Optional input transformer that reshapes the event before delivery; null sends the full event"
  type = object({
    input_paths    = map(string)
    input_template = string
  })
  default = null
}
