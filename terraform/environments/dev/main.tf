data "aws_caller_identity" "current" {}

locals {
  replica_regions   = var.enable_multi_region ? [var.secondary_region] : []
  endpoint_id       = one(module.global_endpoint[*].endpoint_id) # null when multi-region is off
  intake_queue_name = "order-intake-${var.environment}"

  # Created by terraform/bootstrap, which must be applied first. Every application role carries it,
  # so even a compromised deploy role cannot create a role more powerful than the boundary allows.
  permissions_boundary_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/orders-pipeline-app-role-boundary"
}

module "orders_table" {
  source = "../../modules/dynamodb"

  table_name          = "Orders-${var.environment}"
  hash_key            = "orderId"
  range_key           = "itemId"
  deletion_protection = false
  replica_regions     = local.replica_regions
}

module "event_bus" {
  source = "../../modules/eventbridge"

  bus_name = "orders-${var.environment}"

  # Only CreateOrder may publish; see the bus policy in the module.
  publisher_role_arns = concat([module.create_order.role_arn], module.create_order_secondary[*].role_arn)
}

# Orders arrive here from API Gateway and are processed by CreateOrder.
module "intake_queue" {
  source = "../../modules/event_queue"

  queue_name = local.intake_queue_name

  # Only the API's role may send; the visibility timeout is 6x the CreateOrder timeout.
  sender_role_arns           = [module.order_api.role_arn]
  visibility_timeout_seconds = 180
  alarm_actions              = [module.alerts.topic_arn]
}

module "order_api" {
  source = "../../modules/order_api"

  api_name                 = "order-intake-api-${var.environment}"
  role_name                = "CreateOrder-api-${var.environment}-role"
  queue_name               = local.intake_queue_name
  queue_arn                = module.intake_queue.queue_arn
  permissions_boundary_arn = local.permissions_boundary_arn
}

module "create_order" {
  source = "../../modules/create_order"

  function_name         = "CreateOrder-${var.environment}"
  event_bus_name        = module.event_bus.bus_name
  event_bus_arn         = module.event_bus.bus_arn
  table_name            = module.orders_table.table_name
  table_arn             = module.orders_table.table_arn
  queue_arn             = module.intake_queue.queue_arn
  processing_table_name = module.processing_table.table_name
  processing_table_arn  = module.processing_table.table_arn

  # With multi-region on, events go through the global endpoint (which needs the signing library packaged).
  event_endpoint_id     = local.endpoint_id
  publish_resource_arns = var.enable_multi_region ? [module.global_endpoint[0].endpoint_arn, module.event_bus_secondary[0].bus_arn] : []
  vendor_awscrt         = var.enable_multi_region

  permissions_boundary_arn = local.permissions_boundary_arn
}

# One record per orderId that CreateOrder is processing or has processed: its lease, its status and the stored
# result. Records expire after a week. The name starts with Orders- so the existing least-privilege patterns apply.
module "processing_table" {
  source = "../../modules/dynamodb"

  table_name          = "Orders-processing-${var.environment}"
  hash_key            = "orderId"
  ttl_attribute       = "expiresAt"
  deletion_protection = false
  replica_regions     = local.replica_regions
}

module "fulfilments_table" {
  source = "../../modules/dynamodb"

  table_name          = "Fulfilments-${var.environment}"
  hash_key            = "orderId"
  deletion_protection = false
  replica_regions     = local.replica_regions
}

module "order_created_rule" {
  source = "../../modules/event_rule"

  rule_name   = "order-created-to-fulfilment-${var.environment}"
  description = "Route OrderCreated events to the fulfilment queue"
  bus_name    = module.event_bus.bus_name
  target_arn  = module.fulfilment_queue.queue_arn
  event_pattern = jsonencode({
    source        = ["orders.create-order"]
    "detail-type" = ["OrderCreated"]
  })
}

module "fulfilment_queue" {
  source = "../../modules/event_queue"

  queue_name         = "order-fulfilment-${var.environment}"
  allowed_source_arn = module.order_created_rule.rule_arn
  alarm_actions      = [module.alerts.topic_arn]
}

module "fulfil_order" {
  source = "../../modules/fulfil_order"

  function_name = "FulfilOrder-${var.environment}"
  queue_arn     = module.fulfilment_queue.queue_arn
  table_name    = module.fulfilments_table.table_name
  table_arn     = module.fulfilments_table.table_arn

  orders_table_name = module.orders_table.table_name
  orders_table_arn  = module.orders_table.table_arn

  permissions_boundary_arn = local.permissions_boundary_arn
}

module "alerts" {
  source = "../../modules/alerts"

  topic_name  = "orders-alerts-${var.environment}"
  alert_email = var.alert_email
}

module "order_notification_rule" {
  source = "../../modules/event_rule"

  rule_name   = "order-created-to-notification-${var.environment}"
  description = "Publish a notification for each OrderCreated event"
  bus_name    = module.event_bus.bus_name
  target_id   = "sns"
  target_arn  = module.order_notifications.topic_arn
  event_pattern = jsonencode({
    source        = ["orders.create-order"]
    "detail-type" = ["OrderCreated"]
  })

  # Send a short message instead of the raw event; no customer data is included.
  input_transformer = {
    input_paths = {
      orderId   = "$.detail.orderId"
      createdAt = "$.detail.createdAt"
    }
    input_template = "\"Order <orderId> was created at <createdAt>.\""
  }
}

module "order_notifications" {
  source = "../../modules/notification_topic"

  topic_name         = "order-notifications-${var.environment}"
  allowed_source_arn = module.order_notification_rule.rule_arn
  notification_email = var.notification_email
}

module "dashboard" {
  source = "../../modules/dashboard"

  dashboard_name = "orders-${var.environment}"
  bus_name       = module.event_bus.bus_name
  rule_names     = [module.order_created_rule.rule_name, module.order_notification_rule.rule_name]
  queue_names    = [module.intake_queue.queue_name, module.intake_queue.dlq_name, module.fulfilment_queue.queue_name, module.fulfilment_queue.dlq_name]
  topic_names    = [module.order_notifications.topic_name, module.alerts.topic_name]
}
