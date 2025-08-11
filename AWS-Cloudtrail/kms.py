import json
from typing import List, Dict, Any, Optional


def generate_cloudtrail_kms_policy(
    admin_account: str,
    member_account_ids: List[str],
    trail_name: str,
    provider_region: str
) -> Dict[str, Any]:
    """
    Generate a KMS key policy for CloudTrail encryption across multiple AWS accounts.
    
    Args:
        admin_account: The admin AWS account ID
        member_account_ids: List of member AWS account IDs
        trail_name: Name of the CloudTrail trail
        provider_region: AWS region where resources are deployed
    
    Returns:
        Dictionary containing the KMS key policy
    """
    member_account_ids.remove(admin_account)  # Ensure admin account is not in member accounts
    
    # Initialize policy statements
    policy_statements = []
    
    # Enable IAM User Permissions for admin account
    policy_statements.append({
        "Sid": "Enable IAM User Permissions",
        "Effect": "Allow",
        "Principal": {
            "AWS": f"arn:aws:iam::{admin_account}:root"
        },
        "Action": "kms:*",
        "Resource": "*"
    })
    
    # Enable IAM User Permissions for each member account
    for account_id in member_account_ids:
        policy_statements.append({
            "Sid": f"Enable IAM User Permissions {account_id}",
            "Effect": "Allow",
            "Principal": {
                "AWS": f"arn:aws:iam::{account_id}:root"
            },
            "Action": "kms:*",
            "Resource": "*"
        })
    
    # Allow CloudTrail to encrypt logs for admin account
    policy_statements.append({
        "Sid": "Allow CloudTrail to encrypt logs",
        "Effect": "Allow",
        "Principal": {
            "Service": "cloudtrail.amazonaws.com"
        },
			"Action": [
				"kms:GenerateDataKey*",
				"kms:CreateGrant",
				"kms:DescribeKey"
			],
        "Resource": "*",
        "Condition": {
            "StringLike": {
                "aws:SourceArn": f"arn:aws:cloudtrail:*:{admin_account}:trail/{trail_name}",
                "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{admin_account}:trail/*"
            }
        }
    })
    
    # Allow CloudTrail to encrypt logs for each member account
    for account_id in member_account_ids:
        policy_statements.append({
            "Sid": f"Allow CloudTrail to encrypt logs {account_id}",
            "Effect": "Allow",
            "Principal": {
                "Service": "cloudtrail.amazonaws.com"
            },
			"Action": [
				"kms:GenerateDataKey*",
				"kms:CreateGrant",
				"kms:DescribeKey"
			],
            "Resource": "*",
            "Condition": {
                "StringLike": {
                    "aws:SourceArn": f"arn:aws:cloudtrail:*:{account_id}:trail/{trail_name}",
                    "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{account_id}:trail/*"
                }
            }
        })
    
    # Allow CloudTrail to describe key
    policy_statements.append({
        "Sid": "Allow CloudTrail to describe key",
        "Effect": "Allow",
        "Principal": {
            "Service": "cloudtrail.amazonaws.com"
        },
		"Action": [
				"kms:DescribeKey",
				"kms:GetKeyPolicy",
				"kms:GetKeyRotationStatus"
			],
        "Resource": "*"
    })
    
    # Allow principals in the admin account to decrypt log files
    policy_statements.append({
        "Sid": "Allow principals in the account to decrypt log files",
        "Effect": "Allow",
        "Principal": {
            "AWS": "*"
        },
        "Action": [
            "kms:Decrypt",
            "kms:ReEncryptFrom",
            "kms:GenerateDataKey*",
            "kms:Encrypt",
            "kms:DescribeKey"
        ],
        "Resource": "*",
        "Condition": {
            "StringEquals": {
                "kms:CallerAccount": admin_account
            },
            "StringLike": {
                "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{admin_account}:trail/*"
            }
        }
    })
    
    # Allow principals in each member account to decrypt log files
    for account_id in member_account_ids:
        policy_statements.append({
            "Sid": f"Allow principals in the account to decrypt log files {account_id}",
            "Effect": "Allow",
            "Principal": {
                "AWS": "*"
            },
            "Action": [
                "kms:Decrypt",
                "kms:ReEncryptFrom",
                "kms:GenerateDataKey*",
                "kms:Encrypt",
                "kms:DescribeKey"
            ],
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "kms:CallerAccount": account_id
                },
                "StringLike": {
                    "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{account_id}:trail/*"
                }
            }
        })
    
    # Allow alias creation during setup for admin account
    policy_statements.append({
        "Sid": "Allow alias creation during setup",
        "Effect": "Allow",
        "Principal": {
            "AWS": "*"
        },
        "Action": "kms:CreateAlias",
        "Resource": "*",
        "Condition": {
            "StringEquals": {
                "kms:CallerAccount": admin_account,
                "kms:ViaService": f"ec2.{provider_region}.amazonaws.com"
            }
        }
    })
    
    # Allow alias creation during setup for each member account
    for account_id in member_account_ids:
        policy_statements.append({
            "Sid": f"Allow alias creation during setup {account_id}",
            "Effect": "Allow",
            "Principal": {
                "AWS": "*"
            },
            "Action": "kms:CreateAlias",
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "kms:CallerAccount": account_id,
                    "kms:ViaService": f"ec2.{provider_region}.amazonaws.com"
                }
            }
        })
    
    # Enable cross account log decryption for each member account
    for account_id in member_account_ids:
        policy_statements.append({
            "Sid": f"Enable cross account log decryption {account_id}",
            "Effect": "Allow",
            "Principal": {
                "AWS": "*"
            },
            "Action": [
                "kms:Decrypt",
                "kms:ReEncryptFrom"
            ],
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "kms:CallerAccount": account_id
                },
                "StringLike": {
                    "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{account_id}:trail/*"
                }
            }
        })
    
    return {
        "Version": "2012-10-17",
        "Statement": policy_statements
    }



