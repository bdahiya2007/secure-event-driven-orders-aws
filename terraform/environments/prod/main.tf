data "aws_caller_identity" "current" {}

locals {
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
  deletion_protection = true
}

module "event_bus" {
  source = "../../modules/eventbridge"

  bus_name = "orders-${var.environment}"

  # Only CreateOrder may publish; see the bus policy in the module.
  publisher_role_arns = [module.create_order.role_arn]
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

  function_name  = "CreateOrder-${var.environment}"
  event_bus_name = module.event_bus.bus_name
  event_bus_arn  = module.event_bus.bus_arn
  table_name     = module.orders_table.table_name
  table_arn      = module.orders_table.table_arn
  queue_arn      = module.intake_queue.queue_arn

  permissions_boundary_arn = local.permissions_boundary_arn
}

module "fulfilments_table" {
  source = "../../modules/dynamodb"

  table_name          = "Fulfilments-${var.environment}"
  hash_key            = "orderId"
  deletion_protection = true
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
