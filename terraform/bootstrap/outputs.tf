output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository secret"
  value       = aws_iam_role.deploy.arn
}

output "boundary_policy_arn" {
  description = "Attached to every application role by the environments"
  value       = aws_iam_policy.boundary.arn
}
