variable "bus_name" {
  description = "Name of the custom event bus"
  type        = string
}

variable "tags" {
  description = "Tags applied to the bus"
  type        = map(string)
  default     = {}
}
