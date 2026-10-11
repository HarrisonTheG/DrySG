# Sender identity for alert emails. Apply sends a verification link to this address;
# nothing can be sent until it's clicked. SES starts in sandbox: recipients must be
# verified too (SES console -> Identities -> Create identity) and ~200 emails/day.
resource "aws_sesv2_email_identity" "sender" {
  email_identity = var.ses_sender
}
