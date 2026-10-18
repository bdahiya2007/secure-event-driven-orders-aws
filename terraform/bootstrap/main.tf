data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.aws_region

  # The account's existing GitHub OIDC provider (one per account). It is referenced, not created.
  oidc_provider_arn = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"

  state_bucket = coalesce(var.state_bucket_name, "secure-serverless-api-tfstate-${local.account_id}")

  deploy_role_name = "${var.name_prefix}-deploy-role"
  deploy_role_arn  = "arn:aws:iam::${local.account_id}:role/${local.deploy_role_name}"
  boundary_name    = "${var.name_prefix}-app-role-boundary"
  boundary_arn     = "arn:aws:iam::${local.account_id}:policy/${local.boundary_name}"

  app_role_arns = [for p in var.app_role_prefixes : "arn:aws:iam::${local.account_id}:role/${p}*"]
  log_groups    = [for p in var.app_role_prefixes : "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${p}*"]
  function_arns = [for p in var.app_role_prefixes : "arn:aws:lambda:${local.region}:${local.account_id}:function:${p}*"]

  table_arns = [
    "arn:aws:dynamodb:${local.region}:${local.account_id}:table/Orders-*",
    "arn:aws:dynamodb:${local.region}:${local.account_id}:table/Fulfilments-*",
  ]
  bus_arn  = "arn:aws:events:${local.region}:${local.account_id}:event-bus/orders-*"
  rule_arn = "arn:aws:events:${local.region}:${local.account_id}:rule/orders-*/*"
  queue_arns = [
    "arn:aws:sqs:${local.region}:${local.account_id}:order-fulfilment-*",
    "arn:aws:sqs:${local.region}:${local.account_id}:order-intake-*",
  ]
  intake_queue_arn = "arn:aws:sqs:${local.region}:${local.account_id}:order-intake-*"
  apigw_arns = [
    "arn:aws:apigateway:${local.region}::/restapis",
    "arn:aws:apigateway:${local.region}::/restapis/*",
    "arn:aws:apigateway:${local.region}::/tags/*",
  ]
  # The boundary also covers the secondary Region (multi-region), so the application roles created there stay inside it.
  # The deploy role's own permissions above are NOT extended: the second Region is applied from a workstation.
  boundary_regions = [local.region, var.secondary_region]
  boundary_log_groups = flatten([
    for r in local.boundary_regions : [for p in var.app_role_prefixes : "arn:aws:logs:${r}:${local.account_id}:log-group:/aws/lambda/${p}*:*"]
  ])
  boundary_bus_arns = concat(
    [for r in local.boundary_regions : "arn:aws:events:${r}:${local.account_id}:event-bus/orders-*"],
    ["arn:aws:events:${local.region}:${local.account_id}:endpoint/orders-*"], # the global endpoint lives in the primary Region
  )
  boundary_table_arns = flatten([
    for r in local.boundary_regions : [
      "arn:aws:dynamodb:${r}:${local.account_id}:table/Orders-*",
      "arn:aws:dynamodb:${r}:${local.account_id}:table/Fulfilments-*",
    ]
  ])
  boundary_orders_table_arns = [for r in local.boundary_regions : "arn:aws:dynamodb:${r}:${local.account_id}:table/Orders-*"]
  boundary_intake_queue_arns = [for r in local.boundary_regions : "arn:aws:sqs:${r}:${local.account_id}:order-intake-*"]
  boundary_queue_arns = flatten([
    for r in local.boundary_regions : [
      "arn:aws:sqs:${r}:${local.account_id}:order-fulfilment-*",
      "arn:aws:sqs:${r}:${local.account_id}:order-intake-*",
    ]
  ])

  topic_arns = [
    "arn:aws:sns:${local.region}:${local.account_id}:order-notifications-*",
    "arn:aws:sns:${local.region}:${local.account_id}:orders-alerts-*",
  ]
}

# ---------------------------------------------------------------------------
# Permissions boundary: the ceiling for every application role the pipeline creates.
# Even an admin-level inline policy on such a role yields no more than this.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "boundary" {
  statement {
    sid       = "LambdaLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = local.boundary_log_groups
  }

  statement {
    sid       = "PublishOrderEvents"
    actions   = ["events:PutEvents"]
    resources = local.boundary_bus_arns
  }

  statement {
    sid       = "WriteOrderTables"
    actions   = ["dynamodb:PutItem"]
    resources = local.boundary_table_arns
  }

  statement {
    sid       = "ReadOrdersForFulfilmentChecks"
    actions   = ["dynamodb:Query"]
    resources = local.boundary_orders_table_arns
  }

  # The API's role sends accepted orders to the intake queue.
  statement {
    sid       = "SendOrdersToIntakeQueue"
    actions   = ["sqs:SendMessage"]
    resources = local.boundary_intake_queue_arns
  }

  statement {
    sid       = "ConsumeFulfilmentQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = local.boundary_queue_arns
  }
}

resource "aws_iam_policy" "boundary" {
  name        = local.boundary_name
  description = "Permissions boundary for roles created by the orders pipeline"
  policy      = data.aws_iam_policy_document.boundary.json
}

# ---------------------------------------------------------------------------
# GitHub Actions deploy role (assumed through the existing OIDC provider)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    sid     = "GitHubOidc"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # The deploy job always runs in a GitHub environment, whose sub claim ends in :environment:<name>.
    # Pull requests, other branches and other repositories cannot match.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [for e in var.github_environments : "${var.github_sub_prefix}:environment:${e}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = local.deploy_role_name
  description          = "Assumed by GitHub Actions to deploy the orders stack (Terraform)"
  assume_role_policy   = data.aws_iam_policy_document.deploy_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "StateBucketList"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${local.state_bucket}"]
  }

  statement {
    sid       = "StateObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${local.state_bucket}/${var.state_key_prefix}*"]
  }

  statement {
    sid       = "OrderTables"
    actions   = ["dynamodb:*"]
    resources = local.table_arns
  }

  statement {
    sid       = "EventBridgeBusAndRules"
    actions   = ["events:*"]
    resources = [local.bus_arn, local.rule_arn]
  }

  statement {
    sid       = "FulfilmentQueues"
    actions   = ["sqs:*"]
    resources = local.queue_arns
  }

  statement {
    sid       = "NotificationTopics"
    actions   = ["sns:*"]
    resources = local.topic_arns
  }

  statement {
    sid       = "Functions"
    actions   = ["lambda:*"]
    resources = local.function_arns
  }

  # REST APIs get an ID when they are created, so they cannot be scoped by name. The role can manage REST APIs in
  # this account and region; it cannot touch other services. Narrowing this with tags is a possible follow-up.
  statement {
    sid       = "RestApis"
    actions   = ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"]
    resources = local.apigw_arns
  }

  # The ARN of an event source mapping is not known before it is created.
  statement {
    sid = "EventSourceMappings"
    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:ListEventSourceMappings",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "FunctionLogGroups"
    actions   = ["logs:*"]
    resources = concat(local.log_groups, [for g in local.log_groups : "${g}:*"])
  }

  statement {
    sid       = "LogGroupDiscovery"
    actions   = ["logs:DescribeLogGroups", "logs:ListTagsForResource"]
    resources = ["*"]
  }

  statement {
    sid = "Alarms"
    actions = [
      "cloudwatch:PutMetricAlarm",
      "cloudwatch:DeleteAlarms",
      "cloudwatch:TagResource",
      "cloudwatch:UntagResource",
      "cloudwatch:ListTagsForResource",
    ]
    resources = [
      "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:order-fulfilment-*",
      "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:order-intake-*",
    ]
  }

  statement {
    sid       = "AlarmDiscovery"
    actions   = ["cloudwatch:DescribeAlarms", "cloudwatch:ListDashboards"]
    resources = ["*"]
  }

  statement {
    sid       = "Dashboards"
    actions   = ["cloudwatch:PutDashboard", "cloudwatch:GetDashboard", "cloudwatch:DeleteDashboards"]
    resources = ["arn:aws:cloudwatch::${local.account_id}:dashboard/orders-*"]
  }

  # Non-escalating IAM on application roles: read, tag, delete, pass to Lambda.
  statement {
    sid = "AppRoleLifecycle"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRoleTags",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = local.app_role_arns
  }

  statement {
    sid       = "PassAppRolesToLambdaAndApiGateway"
    actions   = ["iam:PassRole"]
    resources = local.app_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "apigateway.amazonaws.com"]
    }
  }

  # Anything that grants permissions only works on a role carrying the boundary.
  statement {
    sid = "IamGrantsOnlyWithinBoundary"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = local.app_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  # Explicit denies win over every allow above.
  statement {
    sid       = "DenyPipelineSelfModification"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = [local.deploy_role_arn, local.boundary_arn]
  }

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "DeployOrdersStack"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
