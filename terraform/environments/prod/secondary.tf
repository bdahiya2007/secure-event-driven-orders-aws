# The secondary Region of a multi-region deployment (enable_multi_region). Everything here exists only while the
# switch is on. It mirrors the primary Region: its own API Gateway, queues, Lambda functions, event bus and SNS topics.
# The three tables are global tables, so the secondary Region reads and writes replicas of the same data.
# IAM roles are global, so the secondary functions and roles carry the Region (without dashes) in their names to avoid
# clashing with the primary ones. Every module here runs against the aws.secondary provider.

locals {
  secondary_table_arns = {
    orders     = "arn:aws:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${module.orders_table.table_name}"
    processing = "arn:aws:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${module.processing_table.table_name}"
    fulfilment = "arn:aws:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${module.fulfilments_table.table_name}"
  }
  region_suffix = replace(var.secondary_region, "-", "")
}

module "event_bus_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/eventbridge"
  providers = { aws = aws.secondary }

  # The global endpoint needs both buses to have the same name.
  bus_name = "orders-${var.environment}"

  # Only the two CreateOrder roles may publish.
  publisher_role_arns = [module.create_order.role_arn, module.create_order_secondary[0].role_arn]
}

module "alerts_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/alerts"
  providers = { aws = aws.secondary }

  topic_name  = "orders-alerts-${var.environment}"
  alert_email = var.alert_email
}

module "intake_queue_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/event_queue"
  providers = { aws = aws.secondary }

  queue_name                 = local.intake_queue_name
  sender_role_arns           = [module.order_api_secondary[0].role_arn]
  visibility_timeout_seconds = 180
  alarm_actions              = [module.alerts_secondary[0].topic_arn]
}

module "order_api_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/order_api"
  providers = { aws = aws.secondary }

  api_name                 = "order-intake-api-${var.environment}"
  role_name                = "CreateOrder-api-${local.region_suffix}-${var.environment}-role"
  queue_name               = local.intake_queue_name
  queue_arn                = module.intake_queue_secondary[0].queue_arn
  permissions_boundary_arn = local.permissions_boundary_arn
}

module "create_order_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/create_order"
  providers = { aws = aws.secondary }

  function_name         = "CreateOrder-${local.region_suffix}-${var.environment}"
  event_bus_name        = module.event_bus_secondary[0].bus_name
  event_bus_arn         = module.event_bus_secondary[0].bus_arn
  table_name            = module.orders_table.table_name
  table_arn             = local.secondary_table_arns.orders
  queue_arn             = module.intake_queue_secondary[0].queue_arn
  processing_table_name = module.processing_table.table_name
  processing_table_arn  = local.secondary_table_arns.processing

  # Events go through the global endpoint, which needs the signing library.
  event_endpoint_id     = local.endpoint_id
  publish_resource_arns = [module.global_endpoint[0].endpoint_arn, module.event_bus.bus_arn]
  vendor_awscrt         = true

  permissions_boundary_arn = local.permissions_boundary_arn

  # The replicas must exist before the functions that use them.
  depends_on = [module.orders_table, module.processing_table, module.fulfilments_table]
}

module "order_created_rule_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/event_rule"
  providers = { aws = aws.secondary }

  rule_name   = "order-created-to-fulfilment-${var.environment}"
  description = "Route OrderCreated events to the fulfilment queue"
  bus_name    = module.event_bus_secondary[0].bus_name
  target_arn  = module.fulfilment_queue_secondary[0].queue_arn
  event_pattern = jsonencode({
    source        = ["orders.create-order"]
    "detail-type" = ["OrderCreated"]
  })
}

module "fulfilment_queue_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/event_queue"
  providers = { aws = aws.secondary }

  queue_name         = "order-fulfilment-${var.environment}"
  allowed_source_arn = module.order_created_rule_secondary[0].rule_arn
  alarm_actions      = [module.alerts_secondary[0].topic_arn]
}

module "fulfil_order_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/fulfil_order"
  providers = { aws = aws.secondary }

  function_name = "FulfilOrder-${local.region_suffix}-${var.environment}"
  queue_arn     = module.fulfilment_queue_secondary[0].queue_arn
  table_name    = module.fulfilments_table.table_name
  table_arn     = local.secondary_table_arns.fulfilment

  orders_table_name = module.orders_table.table_name
  orders_table_arn  = local.secondary_table_arns.orders

  permissions_boundary_arn = local.permissions_boundary_arn

  depends_on = [module.orders_table, module.fulfilments_table]
}

module "order_notification_rule_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/event_rule"
  providers = { aws = aws.secondary }

  rule_name   = "order-created-to-notification-${var.environment}"
  description = "Publish a notification for each OrderCreated event"
  bus_name    = module.event_bus_secondary[0].bus_name
  target_id   = "sns"
  target_arn  = module.order_notifications_secondary[0].topic_arn
  event_pattern = jsonencode({
    source        = ["orders.create-order"]
    "detail-type" = ["OrderCreated"]
  })

  input_transformer = {
    input_paths = {
      orderId   = "$.detail.orderId"
      createdAt = "$.detail.createdAt"
    }
    input_template = "\"Order <orderId> was created at <createdAt>.\""
  }
}

module "order_notifications_secondary" {
  count     = var.enable_multi_region ? 1 : 0
  source    = "../../modules/notification_topic"
  providers = { aws = aws.secondary }

  topic_name         = "order-notifications-${var.environment}"
  allowed_source_arn = module.order_notification_rule_secondary[0].rule_arn
  notification_email = var.notification_email
}

# The global endpoint lives in the primary Region and sends events to the primary bus, or to the secondary bus when
# Route 53 reports the primary unhealthy.
module "global_endpoint" {
  count  = var.enable_multi_region ? 1 : 0
  source = "../../modules/global_endpoint"

  name              = "orders-${var.environment}"
  primary_bus_arn   = module.event_bus.bus_arn
  secondary_bus_arn = module.event_bus_secondary[0].bus_arn
  secondary_region  = var.secondary_region
}
