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
    

# def get_trail_event_configurations(client, trail_name):

#     try:
#         # Describe the trail to get details
#         trail_info = client.get_trail(Name=trail_name)
        
#         # Initialize configuration dictionary
#         event_configuration = {
#             "ManagementEvents": None,
#             "DataEvents": None,
#             "AdvancedEventSelectors": None,
#             "InsightSelectors": None,
#             "NetworkActivityEvents": False
#         }
        
#         # Get Management and Data event selectors
#         try:
#             event_selectors_response = client.get_event_selectors(TrailName=trail_name)
#             event_selectors = event_selectors_response.get('EventSelectors', [])
#             advanced_event_selectors = event_selectors_response.get('AdvancedEventSelectors', [])

#             # Populate configuration dictionary with retrieved selectors
#             event_configuration["ManagementEvents"] = [event for event in event_selectors if 'ManagementEvent' in event]
#             event_configuration["DataEvents"] = [event for event in event_selectors if 'DataResources' in event]
#             event_configuration["AdvancedEventSelectors"] = advanced_event_selectors
#             event_configuration["NetworkActivityEvents"] = any(
#                 'AWS::EC2::Network' in res['ResourceType'] 
#                 for event in event_selectors for res in event.get('DataResources', [])
#             )
#         except client.exceptions.ClientError as e:
#             print(f"No event selectors found for trail '{trail_name}': {e}")
        
#         # Get Insight selectors configuration
#         try:
#             insight_selectors_response = client.get_insight_selectors(TrailName=trail_name)
#             insight_selectors = insight_selectors_response.get('InsightSelectors', [])
#             event_configuration["InsightSelectors"] = insight_selectors
#         except client.exceptions.ClientError as e:
#             print(f"No insight selectors found for trail '{trail_name}': {e}")

#         print(f"Configurations for trail '{trail_name}':")
#         print(event_configuration)
#         return event_configuration

#     except client.exceptions.TrailNotFoundException:
#         print(f"Trail '{trail_name}' not found.")
#     except client.exceptions.ClientError as e:
#         print(f"An error occurred: {e}")


# def get_trail_home_region(client, org_trail):
#     # Initialize a boto3 CloudTrail client with a default region
    
#     try:
#         # Fetch the list of trails
#         response = client.describe_trails(trailNameList=[org_trail])
#         print(response)

#         # Iterate through the trails and find their home region
#         for trail in response['trailList']:
#             print(trail)
#             trail_name = trail['Name']
#             home_region = trail.get('HomeRegion', 'Unknown')

#             # Display the trail and its home region
#             print(f"Trail Name: {trail_name}, Home Region: {home_region}")
#             return home_region

#     except client.exceptions.ClientError as e:
#         print(f"Error occurred: {e}")


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
                # print(f"Organization trail '{trail['Name']}' is enabled in region: {region}.")
                if "SnsTopicName" in trail:
                    SnsTopicName = True
                else: SnsTopicName = False
                if "CloudWatchLogsLogGroupArn" in trail:
                    CloudWatchLogsLogGroupArn = True
                else: CloudWatchLogsLogGroupArn = False
                if "KmsKeyId" in trail:
                    KmsKeyId = True
                else: KmsKeyId = False
                return trail['Name'], trail['TrailARN'], trail['HomeRegion'], SnsTopicName, CloudWatchLogsLogGroupArn, KmsKeyId

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


# def update_terraform_file(file_path):
#     # Read the file content
#     with open(file_path, 'r') as file:
#         content = file.read()
    
#     content = re.sub(r'cloud_watch_logs_group_arn\s*=\s*".*"', f'cloud_watch_logs_group_arn = aws_cloudwatch_log_group.logs.arn', content)
    
#     content = re.sub(r'cloud_watch_logs_role_arn\s*=\s*".*"', f'cloud_watch_logs_role_arn = aws_iam_role.cloudtrail_logging_role.arn', content)

#     content = re.sub(r'kms_key_id\s*=\s*".*"', f'kms_key_id = aws_kms_key.kms_key.arn', content)

#     content = re.sub(r'name\s*=\s*".*"', f'name = var.trail_name', content)

#     content = re.sub(r's3_bucket_name\s*=\s*".*"', f's3_bucket_name = aws_s3_bucket.bucket.name', content)

#     content = re.sub(r'sns_topic_name\s*=\s*".*"', f'sns_topic_name = aws_sns_topic.test.arn', content)

#     content = re.sub(r'is_organization_trail\s*=\s*".*"', f'is_organization_trail = false', content)

#     # Write the updated content back to the file
#     with open(file_path, 'w') as file:
#         file.write(content)


def update_terraform_file(file_path, outputs=None):
    # Read the file content
    with open(file_path, 'r') as file:
        content = file.read()

    if file_path == 'masterAccount/cloudtrail.tf':
        # Define patterns and replacements for each attribute
        updates = [
            (r'cloud_watch_logs_group_arn\s*=\s*".*"', 'cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.logs[0].arn}:*"'),
            (r'cloud_watch_logs_role_arn\s*=\s*".*"', 'cloud_watch_logs_role_arn = aws_iam_role.cloudtrail_logging_role[0].arn'),
            (r'kms_key_id\s*=\s*".*"', 'kms_key_id = aws_kms_key.kms_key[0].arn'),
            (r'\bname\s*=\s*".*?"', 'name = var.trail_name'),
            (r's3_bucket_name\s*=\s*".*"', 's3_bucket_name = aws_s3_bucket.bucket.id'),
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
    
    elif file_path == 'memberAccount/cloudtrail.tf':
        # Define patterns and replacements for each attribute
        updates = [
            (r'cloud_watch_logs_group_arn\s*=\s*".*"', 'cloud_watch_logs_group_arn = "${aws_cloudwatch_log_group.logs[0].arn}:*"'),
            (r'cloud_watch_logs_role_arn\s*=\s*".*"', 'cloud_watch_logs_role_arn = aws_iam_role.cloudtrail_logging_role[0].arn'),
            (r'kms_key_id\s*=\s*".*"', f'kms_key_id = "{outputs["kms_key_id"]["value"]}"'),
            (r'\bname\s*=\s*".*?"', 'name = var.trail_name'),
            (r's3_bucket_name\s*=\s*".*"', f's3_bucket_name = "{outputs["s3_bucket_name"]["value"]}"'),
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


def create_tfvars_file(filename, admin_account= None, provider_region= None, member_account_ids= None, SnsTopicName= None, CloudWatchLogsLogGroupArn= None, KmsKeyId= None):
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
