variable "bus_name" {
  description = "Name of the custom event bus"
  type        = string
}

variable "tags" {
  description = "Tags applied to the bus"
  type        = map(string)
  default     = {}
}

variable "publisher_role_arns" {
  description = "IAM role ARNs allowed to put events on the bus; everyone else is denied. Empty applies no restriction"
  type        = list(string)
  default     = []
}
