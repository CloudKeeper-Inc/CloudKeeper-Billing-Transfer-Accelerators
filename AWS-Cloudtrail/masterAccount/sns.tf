resource "aws_sns_topic" "test" {
  count                              = var.sns ? 1 : 0
  name = "${var.trail_name}-sns"
}

resource "aws_sns_topic_policy" "default" {
  count                              = var.sns ? 1 : 0
  arn = aws_sns_topic.test[0].arn

  policy = data.aws_iam_policy_document.sns_topic_policy.json
}

data "aws_iam_policy_document" "sns_topic_policy" {
  policy_id = "__default_policy_ID"

  statement {
    sid    = "__default_statement_ID"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    actions = [
      "SNS:GetTopicAttributes",
      "SNS:SetTopicAttributes",
      "SNS:AddPermission",
      "SNS:RemovePermission",
      "SNS:DeleteTopic",
      "SNS:Subscribe",
      "SNS:ListSubscriptionsByTopic",
      "SNS:Publish"
    ]

    resources = [aws_sns_topic.test[0].arn]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"
      values   = [var.admin_account]
    }
  }

  statement {
    sid    = "AWSCloudTrailSNSPolicy20150319For${var.trail_name}"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions = ["SNS:Publish"]

    resources = [aws_sns_topic.test[0].arn]

    condition {
      test     = "StringLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:cloudtrail:*:${var.admin_account}:trail/${var.trail_name}"]
    }
  }
}
