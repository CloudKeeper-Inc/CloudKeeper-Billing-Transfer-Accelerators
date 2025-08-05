import boto3
import os, re, json

def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    for account in accounts['Accounts']:
        account_ids.append(account['Id'])
    return account_ids


def get_aws_cloudtrail_admin_account(org_client):
    
    try:
        response = org_client.describe_organization()
        # Check if the organization has AWS Cloudtrail enabled
        if 'MasterAccountArn' in response['Organization']:
            master_account_id = response['Organization']['MasterAccountId']
            print(f"The AWS Cloudtrail Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS Cloudtrail is not enabled in this organization.")
            return None
    
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def get_details(session,region):
    """Get the details of the AWS Cloudtrail organization trail."""
    client = session.client('cloudtrail', region_name=region)
    try:
        # List the organization trails
        response = client.list_trails()
        if response['Trails']:
            for rail in response['Trails']:
               tr = client.get_trail(Name=rail['Name'])
               if tr['Trail']['IsOrganizationTrail']:
                    if "S3KeyPrefix" in tr['Trail']:
                        S3KeyPrefix = f"/{tr['Trail']['S3KeyPrefix']}"
                    else:
                        S3KeyPrefix = ""
                    if "SnsTopicName" in tr['Trail']:
                        SnsTopic = True
                        SnsTopicARN = tr['Trail']['SnsTopicARN']
                        SnsTopicName = tr['Trail']['SnsTopicName']
                    else: 
                        SnsTopic = False
                        SnsTopicARN = ""
                        SnsTopicName = ""
                    if "CloudWatchLogsLogGroupArn" in tr['Trail']:
                        CloudWatchLogsLogGroupArn = tr['Trail']['CloudWatchLogsLogGroupArn']
                        Cloudwatchlogsenable = True
                        role = tr['Trail']['CloudWatchLogsRoleArn']
                    else: 
                        CloudWatchLogsLogGroupArn = ''
                        role =''
                        Cloudwatchlogsenable = False
                    if "KmsKeyId" in tr['Trail']:
                        KmsKeyId = tr['Trail']['KmsKeyId']
                    else: KmsKeyId = ''
                    
                    trail = {
                        'Name': 'CloudTrail-ck',
                        'org_trail_name':rail['Name'],
                        'HomeRegion': tr['Trail']['HomeRegion'],
                        'S3BucketName': tr['Trail']['S3BucketName'],
                        'S3KeyPrefix': S3KeyPrefix,
                        'IsMultiRegionTrail':tr['Trail']['IsMultiRegionTrail'],
                        'SnsTopic': SnsTopic,
                        'SnsTopicARN': SnsTopicARN,
                        'SnsTopicName': SnsTopicName,
                        'CloudWatchLogsEnabled': Cloudwatchlogsenable,
                        'CloudWatchLogsLogGroupArn':CloudWatchLogsLogGroupArn,
                        'LogFileValidationEnabled': tr['Trail']['LogFileValidationEnabled'],
                        'CloudWatchLogsRoleArn' : role,
                        'KmsKeyId': KmsKeyId,
                        'IsMultiRegionTrail': tr['Trail']['IsMultiRegionTrail'],
                        'IncludeGlobalServiceEvents': tr['Trail']['IncludeGlobalServiceEvents'],
                        'HasCustomEventSelectors': tr['Trail']['HasCustomEventSelectors'],
                        'HasInsightSelectors': tr['Trail']['IsOrganizationTrail']
                        
                    }
                    return trail
        else:
            print("No organization trails found.")
            return None
    
    except client.exceptions.ClientError as e:
        print(f"An error occurred: {e}")
        return None

def list_enabled_regions():
    # Set a default region if none is set
    default_region = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    
    ec2_client = boto3.client('ec2', region_name=default_region)
    response = ec2_client.describe_regions(AllRegions=False)
    
    # Return the list of regions that are enabled
    enabled_regions = [region['RegionName'] for region in response['Regions']]
    return enabled_regions


def check_delegated_admin_for_aws_cloudtrail(org_client):
    """Check if the current account is a delegated admin for AWS Cloudtrail."""

    # org_client = boto3.client('organizations', region_name = "us-east-1")
    try:
        # List the delegated administrators for AWS Cloudtrail
        response = org_client.list_delegated_administrators(ServicePrincipal='cloudtrail.amazonaws.com')
        
        if response['DelegatedAdministrators']:
            for admin in response['DelegatedAdministrators']:
                print(f"Delegated Admin Account ID for AWS Cloudtrail: {admin['Id']}")
                return admin['Id']
        else:
            print("No delegated administrator is set for AWS Cloudtrail.")
            return None 
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None   

def setup_cloudtrail_logging(
    cloudwatch_logs,
    trail_name,
    provider_region,
    admin_account,
    session
):
    if not cloudwatch_logs:
        return {"message": "CloudWatch logs disabled, no resources created"}
    
    logs_client = session.client('logs', region_name=provider_region)
    created_resources = {}
    
    try:
        # Check if log group already exists
        try:
            response = logs_client.describe_log_groups(logGroupNamePrefix=trail_name)
            existing_groups = [lg['logGroupName'] for lg in response['logGroups']]
            if trail_name in existing_groups:
                print(f"✓ Log group {trail_name} already exists")
                created_resources['log_group'] = {
                    'name': trail_name,
                    'arn': f"arn:aws:logs:{provider_region}:{admin_account}:log-group:{trail_name}",
                    'status': 'already_exists'
                }
                return {
                    "status": "success",
                    "resources": created_resources
                }
        except Exception as e:
            print(f"Error checking existing log groups: {e}")
        
        # Create CloudWatch Log Group
        print(f"Creating CloudWatch log group: {trail_name}")
        logs_client.create_log_group(
            logGroupName=trail_name,
            logGroupClass='STANDARD'
        )
        created_resources['log_group'] = {
            'name': trail_name,
            'arn': f"arn:aws:logs:{provider_region}:{admin_account}:log-group:{trail_name}",
            'status': 'created'
        }
        print(f"✓ Log group created: {trail_name}")
        
        # Verify log group was created
        import time
        time.sleep(2)  # Wait for resource to be available
        
        response = logs_client.describe_log_groups(logGroupNamePrefix=trail_name)
        if not any(lg['logGroupName'] == trail_name for lg in response['logGroups']):
            raise Exception(f"Log group {trail_name} was not created successfully")
        
        return {
            "status": "success",
            "resources": created_resources
        }
        
    except logs_client.exceptions.ResourceAlreadyExistsException:
        print(f"✓ Log group {trail_name} already exists")
        created_resources['log_group'] = {
            'name': trail_name,
            'arn': f"arn:aws:logs:{provider_region}:{admin_account}:log-group:{trail_name}",
            'status': 'already_exists'
        }
        return {
            "status": "success",
            "resources": created_resources
        }
    except Exception as e:
        print(f"❌ Error creating log group: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "resources": created_resources
        }
def setup_role_logging(
    cloudwatch_logs,
    trail_name,
    provider_region,
    admin_account,
    session
):
    if not cloudwatch_logs:
        return {"message": "CloudWatch logs disabled, no resources created"}
    
    iam_client = session.client('iam')
    created_resources = {}
    
    try:  
        # Create IAM Role
        role_name = "CloudTrailLoggingRole-1"
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudtrail.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        
        print(f"Creating IAM role: {role_name}")
        role_response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_role_policy),
            Description="IAM role for CloudTrail to write logs to CloudWatch"
        )
        created_resources['iam_role'] = {
            'name': role_name,
            'arn': role_response['Role']['Arn']
        }
        print(f"✓ IAM role created: {role_name}")
        
        # Create IAM Policy Document with proper permissions
        policy_document =         {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AWSCloudTrailCreateLogGroupAndStream",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogStream"
                    ],
                    "Resource": [
                        f"arn:aws:logs:{provider_region}:{admin_account}:log-group:{trail_name}:log-stream:*"
                    ]
                },
                {
                    "Sid": "AWSCloudTrailPutLogEvents",
                    "Effect": "Allow",
                    "Action": ["logs:PutLogEvents"],
                    "Resource": [
                        f"arn:aws:logs:{provider_region}:{admin_account}:log-group:{trail_name}:log-stream:*"
                    ]
                }
            ]
        }
        
        # Attach policy to role
        policy_name = "CloudTrailLoggingPolicy"
        print(f"Creating and attaching IAM policy: {policy_name}")
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document)
        )
        created_resources['iam_policy'] = {
            'name': policy_name,
            'attached_to_role': role_name
        }
        print(f"✓ IAM policy created and attached: {policy_name}")
        
        # Wait a bit for role to propagate
        import time
        print("Waiting for role to propagate...")
        time.sleep(10)
        
        print("✅ All CloudTrail logging resources created successfully!")
        return {
            "status": "success",
            "resources": created_resources
        }
    except Exception as e:
        print(f"❌ Error creating role: {str(e)}")
        return {
            "status": "error",
            "error": str(e),
            "resources": created_resources
        }
def create_s3_cloudtrail_bucket_policy(s3_bucket_name,provider_region,trail_name,s3_key_prefix,member_account_ids,apply_policy,session): 
    """
    Creates and optionally applies an S3 bucket policy for CloudTrail logging.
    
    Args:
        s3_bucket_name: Name of the S3 bucket
        admin_account: AWS account ID for the admin account
        provider_region: AWS region where the CloudTrail is located
        trail_name: Name of the CloudTrail
        s3_key_prefix: S3 key prefix for CloudTrail logs (optional)
        member_account_ids: List of member account IDs (optional)
        apply_policy: Whether to apply the policy to the bucket (default: True)
    
    Returns:
        dict: The generated IAM policy document
    """
    
    if member_account_ids is None:
        member_account_ids = []
    
    # Initialize the policy document
    policy_document = {
        "Version": "2012-10-17",
        "Id": "__default_policy_ID",
        "Statement": []
    }
    
    # Member accounts ACL check statements (dynamic)
    for member_account in member_account_ids:
        member_acl_statement = {
            "Sid": f"AWSCloudTrailAclCheckMember{member_account}",
            "Effect": "Allow",
            "Principal": {
                "Service": "cloudtrail.amazonaws.com"
            },
            "Action": "s3:GetBucketAcl",
            "Resource": f"arn:aws:s3:::{s3_bucket_name}",
            "Condition": {
                "StringEquals": {
                    "aws:SourceArn": f"arn:aws:cloudtrail:{provider_region}:{member_account}:trail/{trail_name}"
                }
            }
        }
        member_write_statement = {
            "Sid": f"AWSCloudTrailWriteMember{member_account}",
            "Effect": "Allow",
            "Principal": {
                "Service": "cloudtrail.amazonaws.com"
            },
            "Action": "s3:PutObject",
            "Resource": f"arn:aws:s3:::{s3_bucket_name}{s3_key_prefix}/AWSLogs/{member_account}/*",
            "Condition": {
                "StringEquals": {
                    "aws:SourceArn": f"arn:aws:cloudtrail:{provider_region}:{member_account}:trail/{trail_name}",
                    "s3:x-amz-acl": "bucket-owner-full-control"
                }
            }
        }
        policy_document["Statement"].append(member_acl_statement)
        policy_document["Statement"].append(member_write_statement)
    
    # Apply the policy to the bucket if requested
    if apply_policy:
        try:
            s3_client = session.client('s3', region_name=provider_region)
            # s3_client = boto3.client('s3')
            s3_client.put_bucket_policy(
                Bucket=s3_bucket_name,
                Policy=json.dumps(policy_document)
            )
            print(f"Successfully applied policy to bucket: {s3_bucket_name}")
        except Exception as e:
            print(f"Error applying policy to bucket: {e}")
            raise
    
    return policy_document



def create_sns_topic_policy(
    sns_topic_arn,
    trail_name,
    admin_account,
    member,
    member_account_ids,
    region
):
    session = boto3.Session(profile_name=str(member))
    if member_account_ids is None:
        member_account_ids = []
    
    # Initialize SNS client
    sns_client = session.client('sns', region_name=region)
    
    # Build the policy document
    policy_document = {
        "Version": "2012-10-17",
        "Id": "__default_policy_ID",
        "Statement": [
            {
                "Sid": "__default_statement_ID",
                "Effect": "Allow",
                "Principal": {
                    "AWS": "*"
                },
                "Action": [
                    "SNS:GetTopicAttributes",
                    "SNS:SetTopicAttributes",
                    "SNS:AddPermission",
                    "SNS:RemovePermission",
                    "SNS:DeleteTopic",
                    "SNS:Subscribe",
                    "SNS:ListSubscriptionsByTopic",
                    "SNS:Publish"
                ],
                "Resource": sns_topic_arn,
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceOwner": admin_account
                    }
                }
            },
            {
                "Sid": f"AWSCloudTrailSNSPolicy20150319For{trail_name}",
                "Effect": "Allow",
                "Principal": {
                    "Service": "cloudtrail.amazonaws.com"
                },
                "Action": "SNS:Publish",
                "Resource": sns_topic_arn,
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": admin_account
                    }
                }
            }
        ]
    }
    # Add dynamic statements for member accounts
    for member_account_id in member_account_ids:
        member_statement = {
            "Sid": f"AWSCloudTrailWriteMember{member_account_id}",
            "Effect": "Allow",
            "Principal": {
                "Service": "cloudtrail.amazonaws.com"
            },
            "Action": "SNS:Publish",
            "Resource": sns_topic_arn,
            "Condition": {
                "StringEquals": {
                    "aws:SourceAccount": member_account_id
                }
            }
        }
        policy_document["Statement"].append(member_statement)
    
    try:
        # Apply the policy to the SNS topic
        response = sns_client.set_topic_attributes(
            TopicArn=sns_topic_arn,
            AttributeName='Policy',
            AttributeValue=json.dumps(policy_document)
        )
        
        print(f"Successfully applied policy to SNS topic: {sns_topic_arn}")
        return response
        
    except Exception as e:
        print(f"Error applying SNS topic policy: {str(e)}")
        raise

def extract_region_from_arn(arn):
    """
    Extract the AWS region from an ARN string.
    
    Args:
        arn: AWS ARN string
        
    Returns:
        str: Region name, or None if region is empty
        
    Raises:
        ValueError: If ARN format is invalid
    """
    # Validate ARN format
    if not isinstance(arn, str) or not arn.startswith('arn:'):
        raise ValueError('Invalid ARN format')
    
    # Split ARN into components: arn:partition:service:region:account-id:resource
    parts = arn.split(':')
    if len(parts) < 6:
        raise ValueError('ARN does not have enough parts')
    
    # Region is the 4th component (index 3)
    region = parts[3]
    return region if region else None


def extract_account_num_from_arn(arn):
    """
    Extract the AWS account number from an ARN string.
    
    Args:
        arn: AWS ARN string
        
    Returns:
        str: Account number, or None if account is empty
        
    Raises:
        ValueError: If ARN format is invalid
    """
    # Validate ARN format
    if not isinstance(arn, str) or not arn.startswith('arn:'):
        raise ValueError('Invalid ARN format')
    
    # Split ARN into components: arn:partition:service:region:account-id:resource
    parts = arn.split(':')
    if len(parts) < 6:
        raise ValueError('ARN does not have enough parts')
    
    # Account number is the 5th component (index 4)
    region = parts[4]  # Note: Variable name 'region' is misleading, should be 'account'
    return region if region else None