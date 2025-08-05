import boto3
import os, re, json
from typing import List, Dict, Any
def generate_cloudtrail_kms_policy(
    admin_account: str,
    member_account_ids: List[str],
    trail_name: str,
    provider_region: str
) -> Dict[str, Any]:
    """
    Generate a KMS policy document for CloudTrail encryption across multiple AWS accounts.
    
    Args:
        admin_account (str): The admin/master account ID
        member_account_ids (List[str]): List of member account IDs
        trail_name (str): Name of the CloudTrail trail
        provider_region (str): AWS region where resources are deployed
    
    Returns:
        Dict[str, Any]: KMS policy document as a dictionary
    """
    
    statements = []
    
    # Enable IAM User Permissions for admin account
    statements.append({
        "Sid": "Enable IAM User Permissions",
        "Effect": "Allow",
        "Principal": {
            "AWS": f"arn:aws:iam::{admin_account}:root"
        },
        "Action": "kms:*",
        "Resource": "*"
    })
    
    # Enable IAM User Permissions for member accounts
    for account_id in member_account_ids:
        statements.append({
            "Sid": f"Enable IAM User Permissions {account_id}",
            "Effect": "Allow",
            "Principal": {
                "AWS": f"arn:aws:iam::{account_id}:root"
            },
            "Action": "kms:*",
            "Resource": "*"
        })
    
    # Allow CloudTrail to encrypt logs for admin account
    statements.append({
        "Sid": "Allow CloudTrail to encrypt logs",
        "Effect": "Allow",
        "Principal": {
            "Service": "cloudtrail.amazonaws.com"
        },
        "Action": "kms:GenerateDataKey*",
        "Resource": "*",
        "Condition": {
            "StringLike": {
                "aws:SourceArn": f"arn:aws:cloudtrail:*:{admin_account}:trail/{trail_name}",
                "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{admin_account}:trail/*"
            }
        }
    })
    
    # Allow CloudTrail to encrypt logs for member accounts
    for account_id in member_account_ids:
        statements.append({
            "Sid": f"Allow CloudTrail to encrypt logs {account_id}",
            "Effect": "Allow",
            "Principal": {
                "Service": "cloudtrail.amazonaws.com"
            },
            "Action": "kms:GenerateDataKey*",
            "Resource": "*",
            "Condition": {
                "StringLike": {
                    "aws:SourceArn": f"arn:aws:cloudtrail:*:{account_id}:trail/{trail_name}",
                    "kms:EncryptionContext:aws:cloudtrail:arn": f"arn:aws:cloudtrail:*:{account_id}:trail/*"
                }
            }
        })
    
    # Allow CloudTrail to describe key
    statements.append({
        "Sid": "Allow CloudTrail to describe key",
        "Effect": "Allow",
        "Principal": {
            "Service": "cloudtrail.amazonaws.com"
        },
        "Action": "kms:DescribeKey",
        "Resource": "*"
    })
    
    # Allow principals in admin account to decrypt log files
    statements.append({
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
    
    # Allow principals in member accounts to decrypt log files
    for account_id in member_account_ids:
        statements.append({
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
    statements.append({
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
    
    # Allow alias creation during setup for member accounts
    for account_id in member_account_ids:
        statements.append({
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
    
    # Enable cross account log decryption for member accounts
    for account_id in member_account_ids:
        statements.append({
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
    
    # Return the complete policy document
    policy_document = {
        "Version": "2012-10-17",
        "Statement": statements
    }
    
    return policy_document
