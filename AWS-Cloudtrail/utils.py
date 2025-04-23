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


def is_organization_trail_enabled(client, region):
    # Initialize a boto3 CloudTrail client with the specific region
    client = boto3.client('cloudtrail', region_name=region)
    
    try:
        # Fetch the list of trails
        response = client.describe_trails()
        
        # Check each trail to see if it is an organization trail
        for trail in response['trailList']:
            if trail.get('IsOrganizationTrail', False):
                print(f"Organization trail '{trail['Name']}' is enabled in region: {region}.")
                if "S3KeyPrefix" in trail:
                    S3KeyPrefix = f"/{trail['S3KeyPrefix']}"
                else:
                    S3KeyPrefix = ""
                if "SnsTopicName" in trail:
                    SnsTopicName = True
                else: SnsTopicName = False
                if "CloudWatchLogsLogGroupArn" in trail:
                    CloudWatchLogsLogGroupArn = True
                else: CloudWatchLogsLogGroupArn = False
                if "KmsKeyId" in trail:
                    KmsKeyId = True
                else: KmsKeyId = False
                return trail['Name'], trail['TrailARN'], trail['HomeRegion'], SnsTopicName, CloudWatchLogsLogGroupArn, KmsKeyId, trail['S3BucketName'], S3KeyPrefix

        # If no organization trail is found
        print(f"No organization trail is enabled in region: {region}.")
        return False

    except client.exceptions.ClientError as e:
        print(f"Error occurred in region {region}: {e}")
        return False
    

def create_import_block(import_id, output_file):

    # Open the file for writing
    with open(output_file, "w") as file:
            
        import_block = f"""
import {{
    to = aws_cloudtrail.org_trail
    id = "{import_id}"
}}
    """
        # Write the import block to the file
        file.write(import_block)

    print(f"Import blocks have been written to {output_file}")
    

def create_tfvars_file(filename, regions= None, admin_account= None, member_account_ids= None, bucket_name= None, sns_topic= None, provider_region= None, bucketRegion= None, snsRegion= None):
    with open(filename, 'w') as f:
        if regions:
            f.write(f'regions = {json.dumps(list(regions))}\n\n')
        if admin_account:
            f.write(f'admin_account = "{admin_account}"\n\n')
        if member_account_ids:
            f.write(f'member_account_ids = {json.dumps(member_account_ids)}\n\n')
        if bucket_name:
            f.write(f'bucket_name = "{bucket_name}"\n\n')
        if sns_topic:
            f.write(f'sns_topic = "{sns_topic}"\n\n')
        if provider_region:
            f.write(f'provider_region = "{provider_region}"\n\n')
        if bucketRegion:
            f.write(f'bucketRegion = "{bucketRegion}"\n\n')
        if snsRegion:
            f.write(f'snsRegion = "{snsRegion}"\n')


def update_terraform_file(file_path, outputs=None):
    # Read the file content
    with open(file_path, 'r') as file:
        content = file.read()
    lines = content.splitlines()
    if file_path == 'masterAccount/cloudtrail.tf':
        # Define patterns and replacements for each attribute
        if len(lines) >= 2:
          new_lines = lines.copy()
          new_lines[-2] = new_lines[-2] + "\n" +"depends_on = [aws_s3_bucket_policy.bucket_policy]"
          content = '\n'.join(new_lines)
        updates = [
            (r'cloud_watch_logs_group_arn\s*=\s*".*"', 'cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.logs[0].arn}:*"'),
            (r'cloud_watch_logs_role_arn\s*=\s*".*"', 'cloud_watch_logs_role_arn = aws_iam_role.cloudtrail_logging_role[0].arn'),
            (r'kms_key_id\s*=\s*".*"', 'kms_key_id = aws_kms_key.kms_key[0].arn'),
            (r'\bname\s*=\s*".*?"', 'name = var.trail_name'),
            (r'sns_topic_name\s*=\s*".*"', 'sns_topic_name = aws_sns_topic.test[0].arn'),
            (r'is_organization_trail\s*=\s*(true|false|"true"|"false")', 'is_organization_trail = false'),
        ]
        
        # Update content only if patterns are found
        for pattern, replacement in updates:
            if re.search(pattern, content):
                if pattern == r'\bname\s*=\s*".*?"':
                    # Replace only the first instance of the 'name' attribute
                    content = re.sub(pattern, replacement, content, count=1)
                else:
                    content = re.sub(pattern, replacement, content)   
    elif file_path == 'memberAccount/cloudtrail.tf':
        # Define patterns and replacements for each attribute
        updates = [
            (r'cloud_watch_logs_group_arn\s*=\s*".*"', 'cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.logs[0].arn}:*"'),
            (r'cloud_watch_logs_role_arn\s*=\s*".*"', 'cloud_watch_logs_role_arn = aws_iam_role.cloudtrail_logging_role[0].arn'),
            (r'kms_key_id\s*=\s*".*"', f'kms_key_id = "{outputs["kms_key_id"]["value"]}"'),
            (r'\bname\s*=\s*".*?"', 'name = var.trail_name'),
            (r'sns_topic_name\s*=\s*".*"', 'sns_topic_name = aws_sns_topic.test[0].arn'),
            (r'is_organization_trail\s*=\s*(true|false|"true"|"false")', 'is_organization_trail = false')
        ]
        # Update content only if patterns are found
        for pattern, replacement in updates:
            if re.search(pattern, content):
                if pattern == r'\bname\s*=\s*".*?"':
                    # Replace only the first instance of the 'name' attribute
                    content = re.sub(pattern, replacement, content, count=1)
                else:
                    content = re.sub(pattern, replacement, content)

    # Write the updated content back to the file
    with open(file_path, 'w') as file:
        file.write(content)
    print(f"Updated .tf file saved to {file_path}")


def remove_empty_attributes(tf_file_path, output_file_path):
    # Regex pattern to match attribute definitions with empty lists or null values
    empty_pattern = re.compile(r'\b(\w+)\s*=\s*(\[\s*\]|\bnull\b)')

    with open(tf_file_path, 'r') as file:
        lines = file.readlines()

    # Remove lines that match the empty pattern
    cleaned_lines = [line for line in lines if not empty_pattern.search(line)]

    with open(output_file_path, 'w') as file:
        file.writelines(cleaned_lines)

    print(f"Cleaned .tf file saved to {output_file_path}")


def create_tfvars_file(filename, admin_account= None, provider_region= None, member_account_ids= None, SnsTopicName= None, CloudWatchLogsLogGroupArn= None, KmsKeyId= None, S3KeyPrefix= "", S3BucketName= ""):
    with open(filename, 'w') as f:
        if admin_account:
            f.write(f'admin_account = "{admin_account}"\n\n')
        if member_account_ids:
            f.write(f'member_account_ids = {json.dumps(member_account_ids)}\n\n')
        if provider_region:
            f.write(f'provider_region = "{provider_region}"\n\n')
        if SnsTopicName:
            f.write(f'sns = true\n\n')
        if CloudWatchLogsLogGroupArn:
            f.write(f'cloudwatchLogs = true\n\n')
        if KmsKeyId:
            f.write(f'kms = true\n\n')
        if S3KeyPrefix:
            f.write(f'S3KeyPrefix = "{S3KeyPrefix}"\n\n')
        if S3BucketName:
            f.write(f'S3BucketName = "{S3BucketName}"\n\n')
