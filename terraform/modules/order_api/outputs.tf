output "invoke_url" {
  description = "Base URL of the stage; POST to <invoke_url>/orders"
  value       = aws_api_gateway_stage.this.invoke_url
}

output "rest_api_id" {
  value = aws_api_gateway_rest_api.this.id
}

output "role_arn" {
  description = "Role API Gateway assumes to send to the queue"
  value       = aws_iam_role.send.arn
}
