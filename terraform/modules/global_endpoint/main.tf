data "aws_region" "current" {}

# EventBridge decides where to send events by asking Route 53 whether the primary Region is healthy. The health check
# watches a CloudWatch alarm on how long events wait before their targets start (AWS's recommended signal).
resource "aws_cloudwatch_metric_alarm" "primary_unhealthy" {
  alarm_name          = "${var.name}-primary-region-unhealthy"
  alarm_description   = "Event ingestion in the primary Region is slow; the global endpoint fails over to the secondary Region"
  namespace           = "AWS/Events"
  metric_name         = "IngestionToInvocationStartLatency"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = var.latency_threshold_ms
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # no events means nothing is slow
  tags                = var.tags
}

resource "aws_route53_health_check" "primary" {
  type                            = "CLOUDWATCH_METRIC"
  cloudwatch_alarm_name           = aws_cloudwatch_metric_alarm.primary_unhealthy.alarm_name
  cloudwatch_alarm_region         = data.aws_region.current.region
  insufficient_data_health_status = "Healthy"
  tags                            = merge(var.tags, { Name = "${var.name}-primary" })
}

resource "aws_cloudwatch_event_endpoint" "this" {
  name        = var.name
  description = "Routes order events to the primary event bus, or to the secondary one if the primary Region is unhealthy"

  event_bus {
    event_bus_arn = var.primary_bus_arn
  }

  event_bus {
    event_bus_arn = var.secondary_bus_arn
  }

  # Events are not copied to the secondary bus while the primary is healthy, so the secondary rules do not
  # process every event a second time. After a failover the secondary bus receives new events directly.
  replication_config {
    state = "DISABLED"
  }

  routing_config {
    failover_config {
      primary {
        health_check = aws_route53_health_check.primary.arn
      }

      secondary {
        route = var.secondary_region
      }
    }
  }
}
