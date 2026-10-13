# API Lambda: serves the website's requests behind API Gateway.
# See docs/contracts/handlers.md. Shares build/lambda.zip and the assume-role policy with ingest.

resource "aws_iam_role" "api" {
  name               = "${local.prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "api" {
  # Scan lists the areas; BatchWriteItem is the cold-start refresh
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:BatchWriteItem"]
    resources = [aws_dynamodb_table.area_snapshot.arn]
  }

  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.postal_cache.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.prefix}-api"
  retention_in_days = 14
}

resource "aws_lambda_function" "api" {
  function_name    = "${local.prefix}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = "handlers.api.handler"
  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)
  timeout          = 10
  memory_size      = 512

  environment {
    variables = {
      TABLE_AREA_SNAPSHOT = aws_dynamodb_table.area_snapshot.name
      TABLE_POSTAL_CACHE  = aws_dynamodb_table.postal_cache.name
      APP_BASE_URL        = var.app_base_url
      LOG_LEVEL           = "INFO"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy_attachment.api_logs,
  ]
}
