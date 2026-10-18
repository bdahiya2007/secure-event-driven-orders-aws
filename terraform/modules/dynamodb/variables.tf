variable "table_name" {
  description = "Name of the DynamoDB table"
  type        = string
}

variable "hash_key" {
  description = "Partition key attribute name (string)"
  type        = string
}

variable "range_key" {
  description = "Sort key attribute name (string); null for a partition-key-only table"
  type        = string
  default     = null
}

variable "deletion_protection" {
  description = "Block table deletion until this is turned off"
  type        = bool
  default     = true
}

variable "enable_point_in_time_recovery" {
  description = "Enable point-in-time recovery"
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Customer-managed KMS key ARN; null uses the AWS-managed aws/dynamodb key"
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to the table"
  type        = map(string)
  default     = {}
}

variable "ttl_attribute" {
  description = "Name of a number attribute holding an expiry time in epoch seconds; null disables time-to-live"
  type        = string
  default     = null
}

variable "replica_regions" {
  description = "Regions that get a replica of this table, making it a global table; empty keeps it in one region"
  type        = list(string)
  default     = []
}
