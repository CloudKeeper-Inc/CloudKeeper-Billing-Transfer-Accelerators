resource "aws_cloudwatch_log_group" "logs" {
  count                              = var.cloudwatchLogs ? 1 : 0
  kms_key_id        = null
  log_group_class   = "STANDARD"
  name              = var.trail_name
  name_prefix       = null
  retention_in_days = 0
  skip_destroy      = false
  tags              = {}
  tags_all          = {}
}

resource "aws_iam_role" "cloudtrail_logging_role" {
  count                              = var.cloudwatchLogs ? 1 : 0
  name               = "CloudTrailLoggingRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = flatten([
      [
        {
          Effect    = "Allow",
          Principal = { Service = "cloudtrail.amazonaws.com" },
          Action    = "sts:AssumeRole"
        }
      ]
    ])
  })
}

resource "aws_iam_role_policy" "cloudtrail_logging_policy" {
  count                              = var.cloudwatchLogs ? 1 : 0
  name   = "CloudTrailLoggingPolicy"
  role   = aws_iam_role.cloudtrail_logging_role[0].name
  policy = data.aws_iam_policy_document.cloudtrail_log_policy.json
}

data "aws_iam_policy_document" "cloudtrail_log_policy" {
  statement {
    sid    = "AWSCloudTrailCreateLogStreamAdmin"
    effect = "Allow"
    actions = ["logs:CreateLogStream"]

    resources = [
      "arn:aws:logs:${var.provider_region}:${var.admin_account}:log-group:${aws_cloudwatch_log_group.logs[0].name}:log-stream:*"
    ]
  }

  statement {
    sid    = "AWSCloudTrailPutLogEventsAdmin"
    effect = "Allow"
    actions = ["logs:PutLogEvents"]

    resources = [
      "arn:aws:logs:${var.provider_region}:${var.admin_account}:log-group:${aws_cloudwatch_log_group.logs[0].name}:log-stream:*"
    ]
  }
}
