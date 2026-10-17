# Note: this topic is not KMS-encrypted. EventBridge can only publish to an
# encrypted topic that uses a customer-managed key, not the AWS-managed aws/sns key.
resource "aws_sns_topic" "this" {
  name = var.topic_name
  tags = var.tags
}

# Only the one EventBridge rule may publish, and only over TLS.
data "aws_iam_policy_document" "topic" {
  statement {
    sid       = "AllowEventBridgeRule"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.this.arn]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.allowed_source_arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.this.arn]

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

resource "aws_sns_topic_policy" "this" {
  arn    = aws_sns_topic.this.arn
  policy = data.aws_iam_policy_document.topic.json
}

# Email subscriptions must be confirmed by clicking the link AWS sends.
resource "aws_sns_topic_subscription" "email" {
  count     = var.notification_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.this.arn
  protocol  = "email"
  endpoint  = var.notification_email
}
