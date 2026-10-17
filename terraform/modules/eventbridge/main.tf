resource "aws_cloudwatch_event_bus" "this" {
  name = var.bus_name
  tags = var.tags
}
