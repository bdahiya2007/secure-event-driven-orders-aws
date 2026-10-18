data "archive_file" "this" {
  type        = "zip"
  source_file = "${path.module}/src/handler.py"
  output_path = "${path.module}/build/create_order.zip"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = "${var.function_name}-role"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
  tags                 = var.tags
}

# Least privilege: write to its own log group, publish to the one event bus,
# write and read the one orders table, consume the one intake queue.
data "aws_iam_policy_document" "permissions" {
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }

  statement {
    actions   = ["events:PutEvents"]
    resources = [var.event_bus_arn]
  }

  statement {
    actions   = ["dynamodb:PutItem"]
    resources = [var.table_arn, var.processing_table_arn]
  }

  # Reads: confirm a redelivered message is the order already stored, and read the processing record
  # (the lease and, once finished, the stored result).
  statement {
    actions   = ["dynamodb:Query"]
    resources = [var.table_arn, var.processing_table_arn]
  }

  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.queue_arn]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "${var.function_name}-policy"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.permissions.json
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  role             = aws_iam_role.this.arn
  runtime          = "python3.14"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256
  timeout          = var.timeout
  memory_size      = 128

  environment {
    variables = {
      EVENT_BUS_NAME        = var.event_bus_name
      TABLE_NAME            = var.table_name
      PROCESSING_TABLE_NAME = var.processing_table_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
  tags       = var.tags
}

resource "aws_lambda_event_source_mapping" "sqs" {
  event_source_arn = var.queue_arn
  function_name    = aws_lambda_function.this.arn
  batch_size       = var.batch_size

  # Only failed messages in a batch return to the queue.
  function_response_types = ["ReportBatchItemFailures"]

  # Caps parallel invocations so a burst of orders cannot flood DynamoDB or the bus.
  scaling_config {
    maximum_concurrency = var.max_concurrency
  }
}
