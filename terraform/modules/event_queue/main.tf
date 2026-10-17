resource "aws_sqs_queue" "dlq" {
  name                      = "${var.queue_name}-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}

resource "aws_sqs_queue" "this" {
  name                       = var.queue_name
  visibility_timeout_seconds = var.visibility_timeout_seconds
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = var.tags
}

# Only the one EventBridge rule may send to the queue, and only over TLS.
data "aws_iam_policy_document" "queue" {
  # Only the one EventBridge rule may send (for a queue fed by a rule).
  dynamic "statement" {
    for_each = var.allowed_source_arn == null ? [] : [var.allowed_source_arn]

    content {
      sid       = "AllowEventBridgeRule"
      actions   = ["sqs:SendMessage"]
      resources = [aws_sqs_queue.this.arn]

      principals {
        type        = "Service"
        identifiers = ["events.amazonaws.com"]
      }

      condition {
        test     = "ArnEquals"
        variable = "aws:SourceArn"
        values   = [statement.value]
      }
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.this.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # Only the listed roles may send (for a queue fed by an API). An explicit Deny also binds administrators.
  dynamic "statement" {
    for_each = length(var.sender_role_arns) > 0 ? [1] : []

    content {
      sid       = "DenySendExceptSenders"
      effect    = "Deny"
      actions   = ["sqs:SendMessage"]
      resources = [aws_sqs_queue.this.arn]

      principals {
        type        = "*"
        identifiers = ["*"]
      }

      # A role may appear as its role ARN or as an assumed-role session ARN, so allow both forms.
      condition {
        test     = "ArnNotLike"
        variable = "aws:PrincipalArn"
        values = concat(
          var.sender_role_arns,
          [for arn in var.sender_role_arns : "${replace(replace(arn, "arn:aws:iam::", "arn:aws:sts::"), ":role/", ":assumed-role/")}/*"],
        )
      }
    }
  }
}

resource "aws_sqs_queue_policy" "this" {
  queue_url = aws_sqs_queue.this.id
  policy    = data.aws_iam_policy_document.queue.json
}

data "aws_iam_policy_document" "dlq" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sqs:*"]
    resources = [aws_sqs_queue.dlq.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

# Any message in the dead-letter queue means an order failed processing.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.queue_name}-dlq-not-empty"
  alarm_description   = "Messages are in the dead-letter queue: orders failed processing"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }

  alarm_actions = var.alarm_actions
  ok_actions    = var.alarm_actions
  tags          = var.tags
}
