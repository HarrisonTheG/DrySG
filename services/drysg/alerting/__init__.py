"""Background alert pipeline (Stream B): trigger (ingest -> SQS) and notifier (SQS -> SES).

The decision rules live in drysg.core.alerts; this package does the AWS I/O.
"""
