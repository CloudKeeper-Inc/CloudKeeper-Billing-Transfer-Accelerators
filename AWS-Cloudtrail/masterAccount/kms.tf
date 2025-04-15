data "aws_iam_policy_document" "cloudtrail_kms_policy" {
  statement {
    sid = "Enable IAM User Permissions"
    actions = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = [
        "arn:aws:iam::${var.admin_account}:root"
      ]
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids

    content {
      sid       = "Enable IAM User Permissions ${statement.value}"
      actions = ["kms:*"]
      resources = ["*"]

      principals {
        type        = "AWS"
        identifiers = [
          "arn:aws:iam::${statement.value}:root"
        ]
      }
    }
  }

  statement {
    sid = "Allow CloudTrail to encrypt logs"
    actions = ["kms:GenerateDataKey*"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:cloudtrail:*:${var.admin_account}:trail/${var.trail_name}"]
    }

    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:cloudtrail:arn"
      values   = ["arn:aws:cloudtrail:*:${var.admin_account}:trail/*"]
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids

    content {
      sid = "Allow CloudTrail to encrypt logs ${statement.value}"
      actions = ["kms:GenerateDataKey*"]
      resources = ["*"]

      principals {
        type        = "Service"
        identifiers = ["cloudtrail.amazonaws.com"]
      }

      condition {
        test     = "StringLike"
        variable = "aws:SourceArn"
        values   = ["arn:aws:cloudtrail:*:${statement.value}:trail/${var.trail_name}"]
      }

      condition {
        test     = "StringLike"
        variable = "kms:EncryptionContext:aws:cloudtrail:arn"
        values   = ["arn:aws:cloudtrail:*:${statement.value}:trail/*"]
      }
    }
  }

  statement {
    sid = "Allow CloudTrail to describe key"
    actions = ["kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }

  statement {
    sid = "Allow principals in the account to decrypt log files"
    actions = ["kms:Decrypt", "kms:ReEncryptFrom", "kms:GenerateDataKey*", "kms:Encrypt", "kms:DescribeKey"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [var.admin_account]
    }

    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:cloudtrail:arn"
      values   = ["arn:aws:cloudtrail:*:${var.admin_account}:trail/*"]
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids

    content {
      sid = "Allow principals in the account to decrypt log files ${statement.value}"
      actions = ["kms:Decrypt", "kms:ReEncryptFrom", "kms:GenerateDataKey*", "kms:Encrypt", "kms:DescribeKey"]
      resources = ["*"]

      principals {
        type        = "AWS"
        identifiers = ["*"]
      }

      condition {
        test     = "StringEquals"
        variable = "kms:CallerAccount"
        values   = [statement.value]
      }

      condition {
        test     = "StringLike"
        variable = "kms:EncryptionContext:aws:cloudtrail:arn"
        values   = ["arn:aws:cloudtrail:*:${statement.value}:trail/*"]
      }
    }
  }

  statement {
    sid = "Allow alias creation during setup"
    actions = ["kms:CreateAlias"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["*"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [var.admin_account]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.provider_region}.amazonaws.com"]
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids

    content {
      sid = "Allow alias creation during setup ${statement.value}"
      actions = ["kms:CreateAlias"]
      resources = ["*"]

      principals {
        type        = "AWS"
        identifiers = ["*"]
      }

      condition {
        test     = "StringEquals"
        variable = "kms:CallerAccount"
        values   = [statement.value]
      }

      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["ec2.${var.provider_region}.amazonaws.com"]
      }
    }
  }

  dynamic "statement" {
    for_each = var.member_account_ids

    content {
      sid = "Enable cross account log decryption ${statement.value}"
      actions = ["kms:Decrypt", "kms:ReEncryptFrom"]
      resources = ["*"]

      principals {
        type        = "AWS"
        identifiers = ["*"]
      }

      condition {
        test     = "StringEquals"
        variable = "kms:CallerAccount"
        values   = [statement.value]
      }

      condition {
        test     = "StringLike"
        variable = "kms:EncryptionContext:aws:cloudtrail:arn"
        values   = ["arn:aws:cloudtrail:*:${statement.value}:trail/*"]
      }
    }
  }
}



resource "aws_kms_key" "kms_key" {
  count                              = var.kms ? 1 : 0
  bypass_policy_lockout_safety_check = null
  custom_key_store_id                = null
  customer_master_key_spec           = "SYMMETRIC_DEFAULT"
  deletion_window_in_days            = null
  description                        = null
  enable_key_rotation                = false
  is_enabled                         = true
  key_usage                          = "ENCRYPT_DECRYPT"
  multi_region                       = false
  policy = data.aws_iam_policy_document.cloudtrail_kms_policy.json
  tags       = {}
  tags_all   = {}
  xks_key_id = null
}
