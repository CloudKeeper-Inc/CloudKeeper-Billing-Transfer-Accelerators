# Check if the IAM Role exists using AWS CLI
data "external" "role_exists" {
  program = ["bash", "-c", <<EOT
    role_result=$(aws iam get-role --role-name MultiAccountConfigRole --profile ${local.provider_profile} 2>/dev/null)
    if [ -z "$role_result" ]; then
      echo '{"exists": "false"}'
    else
      echo '{"exists": "true"}'
    fi
EOT
  ]
}

resource "aws_iam_role" "my_config_role" {
  count = data.external.role_exists.result.exists == "false" ? 1 : 0

  name = "MultiAccountConfigRole"
  assume_role_policy = jsonencode({
    "Version": "2012-10-17",
    "Statement": [
      {
        "Action": "sts:AssumeRole",
        "Effect": "Allow",
        "Principal": {
          "Service": "config.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "p" {
  count = data.external.role_exists.result.exists == "false" ? 1 : 0

  name   = "AWSServiceRoleForConfigPolicy"
  role   = aws_iam_role.my_config_role[0].name
  policy = data.aws_iam_policy_document.p.json
}

data "aws_iam_policy_document" "p" {
  statement {
    effect  = "Allow"
    actions = ["s3:*"]
    resources = [
      "arn:aws:s3:::${var.bucket_name}",
      "arn:aws:s3:::${var.bucket_name}/*"
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["config:Put*"]
    resources = ["*"]
  }

  dynamic "statement" {
    for_each = var.sns_topic != "" ? [var.sns_topic] : []
    content {
      effect    = "Allow"
      actions   = ["sns:*"]
      resources = [statement.value]
    }
  }
}
