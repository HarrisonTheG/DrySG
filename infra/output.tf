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
