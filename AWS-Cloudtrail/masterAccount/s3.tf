data "aws_iam_policy_document" "s3_bucket_policy" {
  policy_id = "__default_policy_ID"

  statement {
    sid    = "AWSCloudTrailAclCheckAdmin${var.admin_account}"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions = ["s3:GetBucketAcl"]

    resources = ["arn:aws:s3:::${var.S3BucketName}"]

    condition {
      test     = "StringLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:cloudtrail:${var.provider_region}:${var.admin_account}:trail/${var.trail_name}"]
    }
  }

  statement {
    sid    = "AWSCloudTrailWriteAdmin${var.admin_account}"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    actions = ["s3:PutObject"]

    resources = ["arn:aws:s3:::${var.S3BucketName}${var.S3KeyPrefix}/AWSLogs/${var.admin_account}/*"]

    condition {
      test     = "StringLike"
      variable = "AWS:SourceArn"
      values   = ["arn:aws:cloudtrail:${var.provider_region}:${var.admin_account}:trail/${var.trail_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids
    content {
      sid    = "AWSCloudTrailAclCheckMember${statement.value}"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["cloudtrail.amazonaws.com"]
      }

      actions = ["s3:GetBucketAcl"]

      resources = ["arn:aws:s3:::${var.S3BucketName}"]

      condition {
        test     = "StringLike"
        variable = "AWS:SourceArn"
        values   = ["arn:aws:cloudtrail:${var.provider_region}:${statement.value}:trail/${var.trail_name}"]
      }
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids
    content {
      sid    = "AWSCloudTrailWriteMember${statement.value}"
      effect = "Allow"

      principals {
        type        = "Service"
        identifiers = ["cloudtrail.amazonaws.com"]
      }

      actions = ["s3:PutObject"]

      resources = ["arn:aws:s3:::${var.S3BucketName}${var.S3KeyPrefix}/AWSLogs/${statement.value}/*"]

      condition {
        test     = "StringLike"
        variable = "AWS:SourceArn"
        values   = ["arn:aws:cloudtrail:${var.provider_region}:${statement.value}:trail/${var.trail_name}"]
      }

      condition {
        test     = "StringEquals"
        variable = "s3:x-amz-acl"
        values   = ["bucket-owner-full-control"]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "bucket_policy" {
  bucket = var.S3BucketName
  policy = data.aws_iam_policy_document.s3_bucket_policy.json
}