# Tables follow docs/contracts/dynamodb.md. Only key and index attributes are
# declared here; DynamoDB is schemaless for everything else.

# Latest conditions and score per NEA forecast area (~47 items), written by ingest every 5 min
resource "aws_dynamodb_table" "area_snapshot" {
  name         = "${local.prefix}-AreaSnapshot"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "area"

  attribute {
    name = "area"
    type = "S"
  }
}

# Cached OneMap lookups so repeat postal codes don't call OneMap
resource "aws_dynamodb_table" "postal_cache" {
  name         = "${local.prefix}-PostalCache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "postal_code"

  attribute {
    name = "postal_code"
    type = "S"
  }
}

# One row per user (email verification state)
resource "aws_dynamodb_table" "user" {
  name         = "${local.prefix}-User"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "email"

  attribute {
    name = "email"
    type = "S"
  }
}

# One row per monitoring session; expires 12 h after start via TTL
resource "aws_dynamodb_table" "monitoring_session" {
  name         = "${local.prefix}-MonitoringSession"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "area"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  # Alert trigger: find active sessions in an area that got worse
  global_secondary_index {
    name            = "area-status-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "area"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "status"
      key_type       = "RANGE"
    }
  }

  # API: stop a user's previous active session when they start a new one
  global_secondary_index {
    name            = "email-index"
    projection_type = "KEYS_ONLY"

    key_schema {
      attribute_name = "email"
      key_type       = "HASH"
    }
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

# Idempotency and audit trail for alert emails; rows expire after 30 days via TTL
resource "aws_dynamodb_table" "alert_log" {
  name         = "${local.prefix}-AlertLog"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"
  range_key    = "alert_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "alert_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}
