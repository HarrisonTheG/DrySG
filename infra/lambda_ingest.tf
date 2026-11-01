# Ingest Lambda: every 5 min fetch NEA weather, write AreaSnapshot, queue alerts to SQS.
# See docs/contracts/handlers.md. Build the zip first: python scripts/build_lambda.py

locals {
  lambda_zip = "${path.module}/../build/lambda.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${local.prefix}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "ingest_logs" {
  role       = aws_iam_role.ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ingest" {
  statement {
    actions = [
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]
    resources = [aws_dynamodb_table.area_snapshot.arn]
  }

  # Alert trigger: active sessions per area
  statement {
    actions   = ["dynamodb:Query"]
    resources = ["${aws_dynamodb_table.monitoring_session.arn}/index/area-status-index"]
  }

  # Alert trigger: move a session's baseline up once conditions have improved
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.monitoring_session.arn]
  }

  # Also covers SendMessageBatch
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.alerts.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.prefix}-ingest"
  retention_in_days = 14
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.prefix}-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handlers.ingest.handler"
  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)
  timeout          = 60
  memory_size      = 512

  # 1 = overlapping runs can't double-alert; -1 = unreserved (if account limit is only 10)
  reserved_concurrent_executions = var.ingest_reserved_concurrency

  environment {
    variables = {
      TABLE_AREA_SNAPSHOT      = aws_dynamodb_table.area_snapshot.name
      TABLE_MONITORING_SESSION = aws_dynamodb_table.monitoring_session.name
      ALERT_QUEUE_URL          = aws_sqs_queue.alerts.url
      LOG_LEVEL                = "INFO"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.ingest,
    aws_iam_role_policy_attachment.ingest_logs,
  ]
}

# EventBridge rule: the 5-minute "alarm clock" that invokes ingest
resource "aws_cloudwatch_event_rule" "ingest_schedule" {
  name                = "${local.prefix}-ingest-schedule"
  schedule_expression = "rate(5 minutes)"
  state               = var.ingest_schedule_enabled ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule = aws_cloudwatch_event_rule.ingest_schedule.name
  arn  = aws_lambda_function.ingest.arn
}

resource "aws_lambda_permission" "ingest_schedule" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest_schedule.arn
}
