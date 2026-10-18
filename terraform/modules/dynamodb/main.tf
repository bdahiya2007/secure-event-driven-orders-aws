resource "aws_dynamodb_table" "this" {
  name                        = var.table_name
  billing_mode                = "PAY_PER_REQUEST" # on-demand capacity
  hash_key                    = var.hash_key
  range_key                   = var.range_key
  deletion_protection_enabled = var.deletion_protection

  dynamic "attribute" {
    for_each = toset(compact([var.hash_key, var.range_key]))

    content {
      name = attribute.value
      type = "S"
    }
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  # Encrypts with the AWS-managed key (aws/dynamodb). Set kms_key_arn to use a
  # customer-managed key instead.
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }

  # A global table: replicas in other regions. Replication needs a stream with both old and new images.
  stream_enabled   = length(var.replica_regions) > 0
  stream_view_type = length(var.replica_regions) > 0 ? "NEW_AND_OLD_IMAGES" : null

  dynamic "replica" {
    for_each = toset(var.replica_regions)

    content {
      region_name            = replica.value
      point_in_time_recovery = var.enable_point_in_time_recovery
    }
  }

  # Expire rows by a timestamp attribute (used for idempotency records, which are only needed for a while).
  dynamic "ttl" {
    for_each = var.ttl_attribute == null ? [] : [var.ttl_attribute]

    content {
      attribute_name = ttl.value
      enabled        = true
    }
  }

  tags = var.tags
}
