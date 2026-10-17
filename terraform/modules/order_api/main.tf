data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.region

  # Builds the queue message from validated fields only. $context.requestId (a UUID) becomes the orderId,
  # so the caller gets it in the response without waiting for any processing. $input.json() emits valid JSON,
  # and the whole message is URL-encoded for the SQS SendMessage form body.
  request_template = <<-EOT
    #set($customer = $input.json('$.customerId'))
    #set($items = $input.json('$.items'))
    #set($message = "{""orderId"":""$context.requestId"",""receivedAt"":$context.requestTimeEpoch,""customerId"":$customer,""items"":$items}")
    Action=SendMessage&MessageBody=$util.urlEncode($message)
  EOT
}

resource "aws_api_gateway_rest_api" "this" {
  name = var.api_name

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = var.tags

  # If the API ever has to be replaced, create the new one first so the endpoint is not left without one.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_resource" "orders" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  parent_id   = aws_api_gateway_rest_api.this.root_resource_id
  path_part   = "orders"
}

# The request body must match this schema or API Gateway answers 400 and nothing is queued.
resource "aws_api_gateway_model" "order" {
  rest_api_id  = aws_api_gateway_rest_api.this.id
  name         = "Order"
  content_type = "application/json"

  schema = jsonencode({
    "$schema"            = "http://json-schema.org/draft-04/schema#"
    title                = "Order"
    type                 = "object"
    additionalProperties = false
    required             = ["customerId", "items"]
    properties = {
      customerId = { type = "string", minLength = 1, maxLength = 64 }
      items = {
        type     = "array"
        minItems = 1
        maxItems = 100
        items = {
          type                 = "object"
          additionalProperties = false
          required             = ["itemId"]
          properties = {
            itemId   = { type = "string", minLength = 1, maxLength = 64 }
            quantity = { type = "integer", minimum = 1, maximum = 1000 }
          }
        }
      }
    }
  })
}

resource "aws_api_gateway_request_validator" "body" {
  rest_api_id           = aws_api_gateway_rest_api.this.id
  name                  = "validate-body"
  validate_request_body = true
}

# Callers must sign requests with AWS credentials (SigV4) and be allowed execute-api:Invoke.
resource "aws_api_gateway_method" "post" {
  rest_api_id          = aws_api_gateway_rest_api.this.id
  resource_id          = aws_api_gateway_resource.orders.id
  http_method          = "POST"
  authorization        = "AWS_IAM"
  request_validator_id = aws_api_gateway_request_validator.body.id

  request_models = {
    "application/json" = aws_api_gateway_model.order.name
  }
}

# A role API Gateway assumes to send to the one queue, nothing else.
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "send" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
  tags                 = var.tags
}

data "aws_iam_policy_document" "send" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arn]
  }
}

resource "aws_iam_role_policy" "send" {
  name   = "${var.role_name}-policy"
  role   = aws_iam_role.send.id
  policy = data.aws_iam_policy_document.send.json
}

# Direct integration with SQS: no Lambda sits in the request path.
resource "aws_api_gateway_integration" "sqs" {
  rest_api_id             = aws_api_gateway_rest_api.this.id
  resource_id             = aws_api_gateway_resource.orders.id
  http_method             = aws_api_gateway_method.post.http_method
  type                    = "AWS"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:${local.region}:sqs:path/${local.account_id}/${var.queue_name}"
  credentials             = aws_iam_role.send.arn
  passthrough_behavior    = "NEVER" # any content type other than JSON is rejected with 415

  request_parameters = {
    "integration.request.header.Content-Type" = "'application/x-www-form-urlencoded'"
  }

  request_templates = {
    "application/json" = local.request_template
  }

  depends_on = [aws_iam_role_policy.send]
}

resource "aws_api_gateway_method_response" "accepted" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.orders.id
  http_method = aws_api_gateway_method.post.http_method
  status_code = "202"

  response_models = {
    "application/json" = "Empty"
  }
}

resource "aws_api_gateway_method_response" "error" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  resource_id = aws_api_gateway_resource.orders.id
  http_method = aws_api_gateway_method.post.http_method
  status_code = "500"

  response_models = {
    "application/json" = "Empty"
  }
}

# 202 Accepted: the order was queued, not processed. The orderId is returned immediately.
resource "aws_api_gateway_integration_response" "accepted" {
  rest_api_id       = aws_api_gateway_rest_api.this.id
  resource_id       = aws_api_gateway_resource.orders.id
  http_method       = aws_api_gateway_method.post.http_method
  status_code       = aws_api_gateway_method_response.accepted.status_code
  selection_pattern = "2\\d{2}"

  response_templates = {
    "application/json" = "{\"orderId\":\"$context.requestId\",\"status\":\"ACCEPTED\"}"
  }

  depends_on = [aws_api_gateway_integration.sqs]
}

# Any error from SQS becomes a fixed 500, so internal details never reach the caller.
resource "aws_api_gateway_integration_response" "error" {
  rest_api_id       = aws_api_gateway_rest_api.this.id
  resource_id       = aws_api_gateway_resource.orders.id
  http_method       = aws_api_gateway_method.post.http_method
  status_code       = aws_api_gateway_method_response.error.status_code
  selection_pattern = "[45]\\d{2}"

  response_templates = {
    "application/json" = "{\"message\":\"Internal error\"}"
  }

  depends_on = [aws_api_gateway_integration.sqs]
}

# Fixed messages for validation failures, so the response does not echo what the caller sent.
resource "aws_api_gateway_gateway_response" "bad_request_body" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  response_type = "BAD_REQUEST_BODY"
  status_code   = "400"

  response_templates = {
    "application/json" = "{\"message\":\"Invalid request\"}"
  }
}

resource "aws_api_gateway_gateway_response" "bad_request_parameters" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  response_type = "BAD_REQUEST_PARAMETERS"
  status_code   = "400"

  response_templates = {
    "application/json" = "{\"message\":\"Invalid request\"}"
  }
}

resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  # Redeploy whenever the API definition changes.
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.orders.id,
      aws_api_gateway_model.order.schema,
      aws_api_gateway_request_validator.body.id,
      aws_api_gateway_method.post.id,
      aws_api_gateway_method.post.authorization,
      aws_api_gateway_integration.sqs.id,
      aws_api_gateway_integration.sqs.request_templates,
      aws_api_gateway_integration.sqs.uri,
      aws_api_gateway_integration_response.accepted.response_templates,
      aws_api_gateway_integration_response.error.response_templates,
      aws_api_gateway_gateway_response.bad_request_body.response_templates,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration_response.accepted,
    aws_api_gateway_integration_response.error,
  ]
}

resource "aws_api_gateway_stage" "this" {
  rest_api_id   = aws_api_gateway_rest_api.this.id
  deployment_id = aws_api_gateway_deployment.this.id
  stage_name    = var.stage_name
  tags          = var.tags
}

# Throttling protects the queue and the account from a flood of requests.
resource "aws_api_gateway_method_settings" "all" {
  rest_api_id = aws_api_gateway_rest_api.this.id
  stage_name  = aws_api_gateway_stage.this.stage_name
  method_path = "*/*"

  settings {
    throttling_rate_limit  = var.throttling_rate_limit
    throttling_burst_limit = var.throttling_burst_limit
  }
}
