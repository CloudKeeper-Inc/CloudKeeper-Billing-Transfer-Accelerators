resource "aws_s3_bucket_policy" "bucketpolicy" {
  provider = aws.s3
  bucket = var.bucket_name
  policy = data.aws_iam_policy_document.bucketpolicydocument.json
}

data "aws_iam_policy_document" "bucketpolicydocument" {
  provider = aws.s3

  statement {
    sid    = "AWSConfigBucketPermissionsCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    actions = [
      "s3:GetBucketAcl"
    ]
    resources = ["arn:aws:s3:::${var.bucket_name}"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = concat([var.admin_account], var.member_account_ids)
    }
  }
  statement {
    sid    = "AWSConfigBucketExistenceCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    actions = [
      "s3:ListBucket"
    ]
    resources = ["arn:aws:s3:::${var.bucket_name}"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = concat([var.admin_account], var.member_account_ids)
    }
  }
  statement {
    sid    = "AWSConfigBucketDelivery"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["config.amazonaws.com"]
    }
    actions = [
      "s3:PutObject"
    ]
    resources = ["arn:aws:s3:::${var.bucket_name}/*"]
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.admin_account]
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
      sid    = "AWSConfigBucketDeliveryfor${statement.value}"
      effect = "Allow"
      principals {
        type        = "Service"
        identifiers = ["config.amazonaws.com"]
      }
      actions   = ["s3:PutObject"]
      resources = ["arn:aws:s3:::${var.bucket_name}/AWSLogs/${statement.value}/Config/*"]
      condition {
        test     = "StringEquals"
        variable = "s3:x-amz-acl"
        values   = ["bucket-owner-full-control"]
      }
      condition {
        test     = "StringEquals"
        variable = "AWS:SourceAccount"
        values   = ["${statement.value}"]
      }
    }
  }
}
  