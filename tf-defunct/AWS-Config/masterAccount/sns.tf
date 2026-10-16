data "aws_sns_topic" "topic" {
  provider = aws.sns
  count = length(var.sns_topic) > 0 ? 1 : 0
  name  = var.sns_topic
}

resource "aws_sns_topic_policy" "default" {
  provider = aws.sns
  count = length(var.sns_topic) > 0 ? 1 : 0
  arn   = data.aws_sns_topic.topic[count.index].arn

  policy = data.aws_iam_policy_document.sns_topic_policy[count.index].json
}

data "aws_iam_policy_document" "sns_topic_policy" {
  provider = aws.sns
  count     = length(var.sns_topic) > 0 ? 1 : 0
  policy_id = "__default_policy_ID"

  statement {
    actions = [
      "SNS:Subscribe",
      "SNS:SetTopicAttributes",
      "SNS:RemovePermission",
      "SNS:Publish",
      "SNS:ListSubscriptionsByTopic",
      "SNS:GetTopicAttributes",
      "SNS:DeleteTopic",
      "SNS:AddPermission",
    ]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceOwner"

      values = concat([var.admin_account], var.member_account_ids)
    }

    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    resources = [
      data.aws_sns_topic.topic[count.index].arn,
    ]

    sid = "__default_statement_ID"
  }

  statement {
    actions = [
      "SNS:Publish"
    ]

    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.admin_account}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"]
    }

    resources = [
      data.aws_sns_topic.topic[count.index].arn,
    ]

    sid = "AWSConfigSNSPolicy${var.admin_account}"
  }

  dynamic "statement" {
    for_each = var.member_account_ids
    content {
      actions = [
        "SNS:Publish"
      ]

      effect = "Allow"

      principals {
        type        = "AWS"
        identifiers = ["arn:aws:iam::${statement.value}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig"]
      }

      resources = [
        data.aws_sns_topic.topic[count.index].arn,
      ]

      sid = "AWSConfigSNSPolicy${statement.value}"
    }
  }
}
