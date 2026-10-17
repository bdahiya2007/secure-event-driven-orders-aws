resource "aws_cloudwatch_event_rule" "this" {
  name           = var.rule_name
  description    = var.description
  event_bus_name = var.bus_name
  event_pattern  = var.event_pattern
  tags           = var.tags
}

resource "aws_cloudwatch_event_target" "this" {
  rule           = aws_cloudwatch_event_rule.this.name
  event_bus_name = var.bus_name
  target_id      = var.target_id
  arn            = var.target_arn

  dynamic "input_transformer" {
    for_each = var.input_transformer == null ? [] : [var.input_transformer]

    content {
      input_paths    = input_transformer.value.input_paths
      input_template = input_transformer.value.input_template
    }
  }

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 8
  }
}
