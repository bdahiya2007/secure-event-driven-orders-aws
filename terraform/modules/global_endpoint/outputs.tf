output "endpoint_id" {
  description = "The value PutEvents takes as EndpointId: the endpoint URL's host without .endpoint.events.amazonaws.com"
  value       = trimsuffix(trimprefix(aws_cloudwatch_event_endpoint.this.endpoint_url, "https://"), ".endpoint.events.amazonaws.com")
}

output "endpoint_arn" {
  value = aws_cloudwatch_event_endpoint.this.arn
}

output "endpoint_url" {
  value = aws_cloudwatch_event_endpoint.this.endpoint_url
}

output "health_check_id" {
  value = aws_route53_health_check.primary.id
}

output "alarm_name" {
  description = "Set this alarm to ALARM to rehearse a failover, and back to OK to fail back"
  value       = aws_cloudwatch_metric_alarm.primary_unhealthy.alarm_name
}
