data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region

  widget_defaults = {
    type = "metric"
    # 24 columns wide; three widgets of 8 sit side by side.
    width  = 8
    height = 7
  }
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      merge(local.widget_defaults, {
        x = 0
        y = 0
        properties = {
          title   = "EventBridge MatchedEvents"
          region  = local.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 60
          metrics = [for r in var.rule_names : ["AWS/Events", "MatchedEvents", "EventBusName", var.bus_name, "RuleName", r]]
          yAxis   = { left = { min = 0 } }
        }
      }),
      merge(local.widget_defaults, {
        x = 8
        y = 0
        properties = {
          title   = "SQS NumberOfMessagesSent"
          region  = local.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 60
          metrics = [for q in var.queue_names : ["AWS/SQS", "NumberOfMessagesSent", "QueueName", q]]
          yAxis   = { left = { min = 0 } }
        }
      }),
      merge(local.widget_defaults, {
        x = 16
        y = 0
        properties = {
          title   = "SNS NumberOfMessagesPublished"
          region  = local.region
          view    = "timeSeries"
          stat    = "Sum"
          period  = 60
          metrics = [for t in var.topic_names : ["AWS/SNS", "NumberOfMessagesPublished", "TopicName", t]]
          yAxis   = { left = { min = 0 } }
        }
      }),
    ]
  })
}
