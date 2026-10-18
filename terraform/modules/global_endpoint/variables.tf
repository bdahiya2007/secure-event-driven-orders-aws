variable "name" {
  description = "Endpoint name"
  type        = string
}

variable "primary_bus_arn" {
  description = "ARN of the event bus in the primary Region"
  type        = string
}

variable "secondary_bus_arn" {
  description = "ARN of the event bus in the secondary Region; it must have the same name as the primary bus"
  type        = string
}

variable "secondary_region" {
  description = "The secondary Region, where events are routed after a failover"
  type        = string
}

variable "latency_threshold_ms" {
  description = "Average ingestion-to-invocation latency, in milliseconds, above which the primary Region counts as unhealthy"
  type        = number
  default     = 30000
}

variable "tags" {
  description = "Tags applied to the alarm and the health check"
  type        = map(string)
  default     = {}
}
