resource "aws_cloudwatch_event_bus" "this" {
  name = var.bus_name
  tags = var.tags
}

# Only the listed roles may put events on this bus. A rule matches on `source` and `detail-type`, which any
# caller of PutEvents can set, so without this any principal in the account could forge an OrderCreated event.
# An explicit Deny also binds administrators. Test events must be sent through the application itself.
data "aws_iam_policy_document" "publishers" {
  count = length(var.publisher_role_arns) > 0 ? 1 : 0

  statement {
    sid       = "DenyPutEventsExceptPublishers"
    effect    = "Deny"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.this.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    # A role may appear as its role ARN or as an assumed-role session ARN, so allow both forms.
    condition {
      test     = "ArnNotLike"
      variable = "aws:PrincipalArn"
      values = concat(
        var.publisher_role_arns,
        [for arn in var.publisher_role_arns : "${replace(replace(arn, "arn:aws:iam::", "arn:aws:sts::"), ":role/", ":assumed-role/")}/*"],
      )
    }
  }
}

resource "aws_cloudwatch_event_bus_policy" "publishers" {
  count          = length(var.publisher_role_arns) > 0 ? 1 : 0
  event_bus_name = aws_cloudwatch_event_bus.this.name
  policy         = data.aws_iam_policy_document.publishers[0].json
}
