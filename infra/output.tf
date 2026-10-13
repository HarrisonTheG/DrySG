# Table names, passed to the Lambdas as TABLE_* env vars (see docs/contracts/handlers.md)
output "table_names" {
  value = {
    area_snapshot      = aws_dynamodb_table.area_snapshot.name
    postal_cache       = aws_dynamodb_table.postal_cache.name
    user               = aws_dynamodb_table.user.name
    monitoring_session = aws_dynamodb_table.monitoring_session.name
    alert_log          = aws_dynamodb_table.alert_log.name
  }
}

output "alert_queue_url" {
  value = aws_sqs_queue.alerts.url
}

output "alert_dlq_url" {
  value = aws_sqs_queue.alerts_dlq.url
}

output "ingest_function_name" {
  value = aws_lambda_function.ingest.function_name
}

output "notifier_function_name" {
  value = aws_lambda_function.notifier.function_name
}

output "api_function_name" {
  value = aws_lambda_function.api.function_name
}

output "api_url" {
  value = aws_apigatewayv2_api.api.api_endpoint
}
