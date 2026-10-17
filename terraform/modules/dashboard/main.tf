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

# Each series is wrapped in FILL(metric, 0). These metrics are only published when something happens, and
# CloudWatch draws a straight line between sparse points, which makes a single event look like a gradual
# slope. Filling the gaps with zero turns each event into a spike.
resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.dashboard_name

  dashboard_body = jsonencode({
    widgets = [
      merge(local.widget_defaults, {
        x = 0
        y = 0
        properties = {
          title  = "EventBridge MatchedEvents"
          region = local.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = concat([for i, r in var.rule_names : [
            ["AWS/Events", "MatchedEvents", "EventBusName", var.bus_name, "RuleName", r, { id = "r${i}", visible = false }],
            [{ expression = "FILL(r${i}, 0)", label = r, id = "fr${i}" }],
          ]]...)
          yAxis = { left = { min = 0 } }
        }
      }),
      merge(local.widget_defaults, {
        x = 8
        y = 0
        properties = {
          title  = "SQS NumberOfMessagesSent"
          region = local.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = concat([for i, q in var.queue_names : [
            ["AWS/SQS", "NumberOfMessagesSent", "QueueName", q, { id = "q${i}", visible = false }],
            [{ expression = "FILL(q${i}, 0)", label = q, id = "fq${i}" }],
          ]]...)
          yAxis = { left = { min = 0 } }
        }
      }),
      merge(local.widget_defaults, {
        x = 16
        y = 0
        properties = {
          title  = "SNS NumberOfMessagesPublished"
          region = local.region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = concat([for i, t in var.topic_names : [
            ["AWS/SNS", "NumberOfMessagesPublished", "TopicName", t, { id = "t${i}", visible = false }],
            [{ expression = "FILL(t${i}, 0)", label = t, id = "ft${i}" }],
          ]]...)
          yAxis = { left = { min = 0 } }
        }
      }),
    ]
  })
}
