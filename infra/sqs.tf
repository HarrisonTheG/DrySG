# Alert queue between the ingest Lambda (producer) and the notifier Lambda (consumer).
# See docs/contracts/handlers.md "SQS".

# Messages that failed 3 deliveries land here for inspection
resource "aws_sqs_queue" "alerts_dlq" {
  name                      = "${local.prefix}-alerts-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "alerts" {
  name = "${local.prefix}-alerts"

  # >= 6x the notifier timeout (30 s)
  visibility_timeout_seconds = 180

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.alerts_dlq.arn
    maxReceiveCount     = 3
  })
}
