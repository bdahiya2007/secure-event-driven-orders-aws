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
