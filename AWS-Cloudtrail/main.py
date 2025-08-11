import boto3
import json
from utils import *
from kms import *
import time


org_client = boto3.client('organizations', region_name="us-east-1")
account_ids = get_account_list(org_client)

if __name__ == "__main__":

    if check_delegated_admin_for_aws_cloudtrail(org_client):
        master_account = check_delegated_admin_for_aws_cloudtrail(org_client)
    else:
        master_account = get_aws_cloudtrail_admin_account(org_client)

    member_accounts = account_ids
    print("\nMember Accounts:", member_accounts)

    mas_session = boto3.Session(profile_name=str(master_account))
    details = get_details(mas_session, 'us-east-1')
    logs_status = details['CloudWatchLogsEnabled']
    print(f"Logs status: {logs_status}")
    
    # Create roles and log groups
    if logs_status == True:
        log_grp_arn = details['CloudWatchLogsLogGroupArn']
        print(f"Original log group ARN: {log_grp_arn}")
        log_grp_region = log_grp_arn.split(':')[3]
        
        for member in member_accounts:
            session = boto3.Session(profile_name=str(member))
            print(f"Setting up logging for member account: {member}")
            
            # Create log group in member account
            log_result = setup_cloudtrail_logging(logs_status, 'CloudTrail-ck', log_grp_region, member, session)
            if log_result.get('status') == 'error':
                print(f"❌ Failed to create log group for {member}: {log_result.get('error')}")
                continue
                
            # Create role in member account  
            role_result = setup_role_logging(logs_status, 'CloudTrail-ck', log_grp_region, member, session)
            if role_result.get('status') == 'error':
                print(f"❌ Failed to create role for {member}: {role_result.get('error')}")
                continue
                
            print(f"✓ Successfully set up logging resources for {member}")
            
            # Wait for resources to propagate
            time.sleep(5)
    
    # Generate and apply KMS policy FIRST (before S3 policy)
    if details['KmsKeyId'] != "":
        try:
            kms_client = mas_session.client('kms', region_name=details['HomeRegion'])
            res = generate_cloudtrail_kms_policy(master_account, member_accounts, 'CloudTrail-ck', details['HomeRegion'])
            kms = kms_client.put_key_policy(
                KeyId=details['KmsKeyId'],
                Policy=json.dumps(res)
            )
            print("✓ Updated KMS Policy")
        except Exception as e:
            print(f"Error updating KMS policy: {e}")
    
    # Handle S3 bucket policy AFTER KMS policy
    print("Setting up S3 bucket policies...")
    s3_bucket_owner = None
    
    # Find which account owns the S3 bucket
    for member in member_accounts:
        session = boto3.Session(profile_name=str(member))
        s3_client = session.client('s3', region_name=details['HomeRegion'])
        try:
            response = s3_client.list_buckets()
            for bucket in response['Buckets']:
                if bucket['Name'] == details['S3BucketName']:
                    s3_bucket_owner = member
                    print(f"Found S3 bucket {details['S3BucketName']} in account {member}")
                    
                    # Create S3 policy for the bucket owner
                    s3_policy = create_s3_cloudtrail_bucket_policy(
                        bucket['Name'], 
                        master_account,
                        details['HomeRegion'], 
                        'CloudTrail-ck', 
                        details['S3KeyPrefix'], 
                        member_accounts, 
                        True, 
                        session
                    )
                    print(f"✓ Updated S3 bucket policy for {bucket['Name']}")
                    break
        except Exception as e:
            print(f"Error checking S3 buckets for member {member}: {e}")
        
        if s3_bucket_owner:
            break
    
    if not s3_bucket_owner:
        print("❌ Could not find S3 bucket owner. S3 policy not updated.")
    
    # Handle SNS topic policy
    if details['SnsTopic'] == True:
        try:
            sns_account = extract_account_num_from_arn(details['SnsTopicARN'])
            sns_region = extract_region_from_arn(details['SnsTopicARN'])
            create_sns_topic_policy(details['SnsTopicARN'], 'CloudTrail-ck', master_account, sns_account, member_accounts, sns_region)
            
            print("✓ Updated SNS topic policy")
        except Exception as e:
            print(f"Error updating SNS topic policy: {e}")
    
    # Wait for policies to propagate before creating trails
    print("Waiting for policies to propagate...")
    time.sleep(30)  # Increased wait time for policy propagation
    
    # Create individual trails for each member account
    member_accounts.append(master_account)  # Include master account in trail creation
    for member in member_accounts:
        try:
            print(f"Creating trail for member account: {member}")
            session = boto3.Session(profile_name=str(member))
            cloudtrail_client = session.client('cloudtrail', region_name=details['HomeRegion'])
            # Prepare create_trail parameters
            trail_params = {
                'Name': 'CloudTrail-ck',
                'S3BucketName': details['S3BucketName'],
                'IncludeGlobalServiceEvents': details['IncludeGlobalServiceEvents'],
                'EnableLogFileValidation': details['LogFileValidationEnabled'],
                'IsMultiRegionTrail': details['IsMultiRegionTrail'],
                'IsOrganizationTrail': False
            }
            
            # Add optional parameters only if they exist
            if details['SnsTopic'] == True :
                trail_params['SnsTopicName'] = details['SnsTopicARN']
            if details['S3KeyPrefix']:
                trail_params['S3KeyPrefix'] = details['S3KeyPrefix'].lstrip('/')
                print(f"Using S3 Key Prefix: {trail_params['S3KeyPrefix']}")
            
            # if details['SnsTopicName']:
            #     trail_params['SnsTopicName'] = details['SnsTopicName']
            
            if details['KmsKeyId']:
                trail_params['KmsKeyId'] = details['KmsKeyId']
            
            # Add CloudWatch Logs parameters if enabled
            if logs_status:
                log_group_arn = f"arn:aws:logs:{log_grp_region}:{member}:log-group:CloudTrail-ck:*"
                role_arn = f"arn:aws:iam::{member}:role/CloudTrailLoggingRole-1"
                
                trail_params['CloudWatchLogsLogGroupArn'] = log_group_arn
                trail_params['CloudWatchLogsRoleArn'] = role_arn
                
                print(f"Log Group ARN: {log_group_arn}")
                print(f"Role ARN: {role_arn}")
            
            # Create the trail
            response = cloudtrail_client.create_trail(**trail_params)
            
            # Start logging
            rep = cloudtrail_client.start_logging(Name='CloudTrail-ck')
            print(f"Started logging {rep}")
            print(f"✓ Trail created successfully for member {member}")
            
        except Exception as e:
            print(f"❌ Error creating trail for member {member}: {str(e)}")
            # Print more detailed error information

            continue
    
    # Stop the original organization trail
    try:
        session_mas = boto3.Session(profile_name=str(master_account))
        client_mas = session_mas.client('cloudtrail', region_name='us-east-1')
        client_mas.stop_logging(Name=details['org_trail_name'])
        print(f"✓ Stopped organization trail: {details['org_trail_name']}")
    except Exception as e:
        print(f"Error stopping organization trail: {e}")
    
    print(f"Multi-account trail setup completed.")