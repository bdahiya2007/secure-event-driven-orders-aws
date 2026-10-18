output "orders_table_name" {
  value = module.orders_table.table_name
}

output "orders_table_arn" {
  value = module.orders_table.table_arn
}

output "event_bus_name" {
  value = module.event_bus.bus_name
}

output "event_bus_arn" {
  value = module.event_bus.bus_arn
}

output "create_order_function_name" {
  value = module.create_order.function_name
}

output "fulfilments_table_name" {
  value = module.fulfilments_table.table_name
}

output "fulfilment_queue_url" {
  value = module.fulfilment_queue.queue_url
}

output "fulfilment_dlq_url" {
  value = module.fulfilment_queue.dlq_url
}

output "fulfil_order_function_name" {
  value = module.fulfil_order.function_name
}

output "alerts_topic_arn" {
  value = module.alerts.topic_arn
}

output "order_notifications_topic_arn" {
  value = module.order_notifications.topic_arn
}

output "dashboard_url" {
  value = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards/dashboard/${module.dashboard.dashboard_name}"
}

output "order_api_url" {
  description = "POST <url>/orders with a SigV4-signed request"
  value       = "${module.order_api.invoke_url}/orders"
}

output "intake_queue_url" {
  value = module.intake_queue.queue_url
}

output "intake_dlq_url" {
  value = module.intake_queue.dlq_url
}

output "global_endpoint_id" {
  description = "EndpointId used to publish events (multi-region only)"
  value       = local.endpoint_id
}

output "global_endpoint_health_check_id" {
  description = "Route 53 health check behind the endpoint's failover. Rehearse a failover with: aws route53 update-health-check --health-check-id <id> --inverted ; fail back with --no-inverted (multi-region only)"
  value       = one(module.global_endpoint[*].health_check_id)
}

output "global_endpoint_failover_alarm" {
  description = "The alarm the health check follows; its metric, not its state, drives the failover (multi-region only)"
  value       = one(module.global_endpoint[*].alarm_name)
}

output "order_api_url_secondary" {
  description = "POST <url> with a SigV4-signed request, signing for the secondary Region (multi-region only)"
  value       = var.enable_multi_region ? "${module.order_api_secondary[0].invoke_url}/orders" : null
}

output "intake_dlq_url_secondary" {
  value = var.enable_multi_region ? module.intake_queue_secondary[0].dlq_url : null
}

output "fulfilment_dlq_url_secondary" {
  value = var.enable_multi_region ? module.fulfilment_queue_secondary[0].dlq_url : null
}
