import boto3
from utils import *
from kms import *
org_client = boto3.client('organizations', region_name="us-east-1")
account_ids = get_account_list(org_client)

if __name__ == "__main__":

    if check_delegated_admin_for_aws_cloudtrail(org_client):
        master_account = check_delegated_admin_for_aws_cloudtrail(org_client)
    else:
        master_account = get_aws_cloudtrail_admin_account(org_client)

    member_accounts = account_ids
    print("\nMember Accounts:", member_accounts)

    session = boto3.Session(profile_name=str(master_account))
    details = get_details(session, 'us-east-1')
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
            import time
            time.sleep(5)
    
    # Handle SNS topic policy
    if details['SnsTopic'] == True:
        sns_account = extract_account_num_from_arn(details['SnsTopicARN'])
        sns_region = extract_region_from_arn(details['SnsTopicARN'])
        create_sns_topic_policy(details['SnsTopicARN'], 'CloudTrail-ck', master_account, sns_account, member_accounts, sns_region)
    
    # Handle S3 bucket policy
    flag = False
    for member in member_accounts:
        session = boto3.Session(profile_name=str(member))
        s3_client = session.client('s3', region_name=details['HomeRegion'])
        kms_client = session.client('kms', region_name=details['HomeRegion'])
        try:
            response = s3_client.list_buckets()
            for bucket in response['Buckets']:
                if bucket['Name'] == details['S3BucketName']:
                    s3 = create_s3_cloudtrail_bucket_policy(
                        bucket['Name'], 
                        details['HomeRegion'], 
                        'CloudTrail-ck', 
                        details['S3KeyPrefix'], 
                        member_accounts, 
                        True, 
                        session
                    )
                    res = generate_cloudtrail_kms_policy(member, member_accounts, 'CloudTrail-ck', details['HomeRegion'])
                    kms = kms_client.put_key_policy(
                        KeyId=details['KmsKeyId'],
                        PolicyName='default',
                        Policy=json.dumps(res)
                    )
                    print(f"✓ Updated KMS policy for bucket {bucket['Name']} in member {member}")
                    flag = True
                    break
        except Exception as e:
            print(f"Error handling S3 for member {member}: {e}")
    if flag == True:
        print("✓ Updating KMS Policy")
        
    
    # Create individual trails for each member account
    for member in member_accounts:
        try:
            print(f"Creating trail for member account: {member}")
            session = boto3.Session(profile_name=str(member))
            cloudtrail_client = session.client('cloudtrail', region_name=details['HomeRegion'])
            logs_client = session.client('logs', region_name=log_grp_region)
            
            # Verify log group exists before creating trail
            log_group_name = 'CloudTrail-ck'
            try:
                logs_client.describe_log_groups(logGroupNamePrefix=log_group_name)
                print(f"✓ Log group exists for member {member}")
            except Exception as e:
                print(f"❌ Log group does not exist for member {member}: {e}")
                continue
            
            # Construct proper ARNs for the member account
            log_group_arn = f"arn:aws:logs:{log_grp_region}:{member}:log-group:CloudTrail-ck"
            role_arn = f"arn:aws:iam::{member}:role/CloudTrailLoggingRole-1"
            
            print(f"Creating trail for member {member}")
            print(f"Log Group ARN: {log_group_arn}")
            print(f"Role ARN: {role_arn}")
            
            # Verify role exists
            iam_client = session.client('iam')
            try:
                iam_client.get_role(RoleName='CloudTrailLoggingRole-1')
                print(f"✓ Role exists for member {member}")
            except Exception as e:
                print(f"❌ Role does not exist for member {member}: {e}")
                continue
            
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
            if details['S3KeyPrefix']:
                trail_params['S3KeyPrefix'] = details['S3KeyPrefix']
            
            if details['SnsTopicName']:
                trail_params['SnsTopicName'] = details['SnsTopicName']
            
            if details['KmsKeyId']:
                trail_params['KmsKeyId'] = details['KmsKeyId']
            
            # Add CloudWatch Logs parameters if enabled
            if logs_status:
                trail_params['CloudWatchLogsLogGroupArn'] = log_group_arn+":*"
                print(log_group_arn)
                trail_params['CloudWatchLogsRoleArn'] = role_arn
                print(role_arn)
            
            response = cloudtrail_client.create_trail(**trail_params)
            rep = cloudtrail_client.start_logging(
    Name='CloudTrail-ck'
)
            print(f"Started logging {rep}")
            print(f"✓ Trail created successfully for member {member}")
            
        except Exception as e:
            print(f"❌ Error creating trail for member {member}: {str(e)}")
            continue
    session_mas = boto3.Session(profile_name=str(master_account))
    client_mas = session.client('cloudtrail', region_name='us-east-1')
    client_mas.stop_logging(Name=details['org_tail_name'])
    print(f"Multi-account trail setup completed.")