output "queue_arn" {
  value = aws_sqs_queue.this.arn
}

output "queue_url" {
  value = aws_sqs_queue.this.id
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}

output "queue_name" {
  value = aws_sqs_queue.this.name
}

output "dlq_name" {
  value = aws_sqs_queue.dlq.name
}
