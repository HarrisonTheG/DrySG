# Notifier Lambda: reads alerts from SQS, emails them via SES, records them in AlertLog.
# See docs/contracts/handlers.md. Shares build/lambda.zip and the assume-role policy with ingest.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

resource "aws_iam_role" "notifier" {
  name               = "${local.prefix}-notifier"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "notifier_logs" {
  role       = aws_iam_role.notifier.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "notifier" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.alerts.arn]
  }

  # Dedupe claim + delivery status
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.alert_log.arn]
  }

  # Move the session baseline after alerting
  statement {
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.monitoring_session.arn]
  }

  # In sandbox SES also authorizes against recipient identities, so allow any identity
  # but only when sending from our sender address
  statement {
    actions   = ["ses:SendEmail"]
    resources = ["arn:aws:ses:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:identity/*"]
    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = [var.ses_sender]
    }
  }
}

resource "aws_iam_role_policy" "notifier" {
  name   = "notifier"
  role   = aws_iam_role.notifier.id
  policy = data.aws_iam_policy_document.notifier.json
}

resource "aws_cloudwatch_log_group" "notifier" {
  name              = "/aws/lambda/${local.prefix}-notifier"
  retention_in_days = 14
}

resource "aws_lambda_function" "notifier" {
  function_name    = "${local.prefix}-notifier"
  role             = aws_iam_role.notifier.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handlers.notifier.handler"
  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      TABLE_ALERT_LOG          = aws_dynamodb_table.alert_log.name
      TABLE_MONITORING_SESSION = aws_dynamodb_table.monitoring_session.name
      SES_SENDER               = var.ses_sender
      APP_BASE_URL             = local.app_base_url
      LOG_LEVEL                = "INFO"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.notifier,
    aws_iam_role_policy_attachment.notifier_logs,
  ]
}

# SQS trigger: Lambda polls the queue and invokes the notifier with up to 10 messages.
# ReportBatchItemFailures lets the handler retry only the messages that failed.
resource "aws_lambda_event_source_mapping" "notifier" {
  event_source_arn        = aws_sqs_queue.alerts.arn
  function_name           = aws_lambda_function.notifier.arn
  batch_size              = 10
  function_response_types = ["ReportBatchItemFailures"]

  # The role needs its SQS permissions before the mapping can be created
  depends_on = [aws_iam_role_policy.notifier]
}
