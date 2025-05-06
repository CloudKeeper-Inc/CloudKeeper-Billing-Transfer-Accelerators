import re
import os
import fnmatch
import json

def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    for account in accounts['Accounts']:
        account_ids.append(account['Id'])
    return account_ids


def get_s3_bucket_region(bucket_name, s3_client):
    response = s3_client.get_bucket_location(Bucket=bucket_name)

    region = response['LocationConstraint']

    # 'None' means the bucket is in the 'us-east-1' region
    if region is None:
        region = 'us-east-1'

    return region


def get_sns_region(sns_arn):
    try:
        arn_parts = sns_arn.split(':')
        if len(arn_parts) > 3 and arn_parts[2] == 'sns':
            region = arn_parts[3]
            return region
    except:
        return


def file_exists(file_path):
    return os.path.isfile(file_path)


def create_rules_import_blocks(ids, output_file):

    with open(output_file, "w") as file:
        # Loop through each ID and create an import block
        for import_id in ids:
            import_block = f"""
import {{
    to = aws_config_config_rule.{import_id}
    id = "{import_id}"
}}
    """
            file.write(import_block)

    print(f"Import blocks have been written to {output_file}")


def create_packs_import_blocks(ids, output_file):

    with open(output_file, "w") as file:
        # Loop through each ID and create an import block
        for import_id in ids:
            import_block = f"""
import {{
    to = aws_config_conformance_pack.{import_id}
    id = "{import_id}"
}}
    """
            # Write the import block to the file
            file.write(import_block)

    print(f"Import blocks have been written to {output_file}")


def get_all_organization_config_rules(client):
    config_rules = []
    paginator = client.get_paginator('describe_organization_config_rules')
    
    for page in paginator.paginate():
        config_rules.extend(page['OrganizationConfigRules'])
    
    return config_rules


def get_all_organization_conformance_packs(client):
    conformance_packs = []
    paginator = client.get_paginator('describe_organization_conformance_packs')
    
    for page in paginator.paginate():
        conformance_packs.extend(page['OrganizationConformancePacks'])
    
    return conformance_packs


def get_aws_config_admin_account(org_client):
    
    try:
        response = org_client.describe_organization()
        
        if 'MasterAccountArn' in response['Organization']:
            master_account_id = response['Organization']['MasterAccountId']
            print(f"The AWS Config Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS Config is not enabled in this organization.")
            return None
    
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def check_delegated_admin_for_aws_config(org_client):
    """Check if the current account is a delegated admin for AWS Config."""

    try:
        response = org_client.list_delegated_administrators(ServicePrincipal='config.amazonaws.com')
        if response['DelegatedAdministrators']:
            for admin in response['DelegatedAdministrators']:
                print(f"Delegated Admin Account ID for AWS Config: {admin['Id']}")
                return admin['Id']
        else:
            print("No delegated administrator is set for AWS Config.")
            return None
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    

def get_regions(session):
    ec2_client = session.client('ec2', region_name='us-east-1')
    regions = ec2_client.describe_regions()['Regions']
    return [region['RegionName'] for region in regions]


def check_config_in_region(region, session):
    client = session.client('config', region_name=region)
    try:
        recorders = client.describe_configuration_recorders()['ConfigurationRecorders']
        return len(recorders) > 0
    except client.exceptions.NoSuchConfigurationRecorderException:
        return False
    except client.exceptions.InvalidAccessException:
        # Handle cases where access is denied
        print(f"Access denied for region: {region}")
        return False


def get_regions_with_config_enabled(session):
    regions = get_regions(session)
    enabled_regions = []

    for region in regions:
        if check_config_in_region(region, session):
            enabled_regions.append(region)
            
    return enabled_regions


def get_org_aggregator(client, session):
    agg_region = set()
    next_token = None

    while True:
        # Describe configuration aggregators with pagination
        if next_token:
            response = client.describe_configuration_aggregators(NextToken=next_token)
        else:
            response = client.describe_configuration_aggregators()

        # Filter and print organization-level aggregators
        for aggregator in response['ConfigurationAggregators']:
            if 'OrganizationAggregationSource' in aggregator:
                if aggregator['OrganizationAggregationSource']['AllAwsRegions'] == True:
                    agg_region = get_regions(session)
                else:
                    for region in aggregator['OrganizationAggregationSource']['AwsRegions']:
                        agg_region.add(region)

        # Check if there's more data to fetch
        next_token = response.get('NextToken')
        if not next_token:
            break
        
    return agg_region


def create_tfvars_file(filename, regions= None, admin_account= None, member_account_ids= None, bucket_name= None, sns_topic= None, provider_region= None, bucketRegion= None, snsRegion= None, aggregator_region= None):
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
        if aggregator_region:
            f.write(f'aggregator_region = "{aggregator_region}"\n')


def comment_terraform_file(file_path):
    if file_exists(file_path):
        with open(file_path, 'r') as file:
            content = file.read()
        
        commented_content = re.sub(r'^(.*)$', r'# \1', content, flags=re.MULTILINE)
        
        with open(file_path, 'w') as file:
            file.write(commented_content)


def uncomment_terraform_file(file_path):
    if file_exists(file_path):
        with open(file_path, 'r') as file:
            content = file.read()
        
        uncommented_content = re.sub(r'^#\s?', '', content, flags=re.MULTILINE)
        
        with open(file_path, 'w') as file:
            file.write(uncommented_content)


def find_files_with_pattern(directory, account_number, region):
    pattern = f'import_rules.{account_number}.{region}.tf'
    
    matching_files = []

    for root, dirs, files in os.walk(directory):
        for filename in fnmatch.filter(files, pattern):
            matching_files.append(os.path.join(root, filename))
    
    return matching_files


def select_file(files, account_number, region):
    specific_pattern = f'import_rules.{account_number}.{region}.tf'
    
    for file in files:
        if specific_pattern in file:
            return file
    
    return None


def update_terraform_file(file_path, new_s3_bucket_name, new_sns_topic_arn, account_id):
    with open(file_path, 'r') as file:
        content = file.read()
    
    # Update s3_bucket_name
    content = re.sub(r's3_bucket_name\s*=\s*".*"', f's3_bucket_name = "{new_s3_bucket_name}"', content)
    
    # Update sns_topic_arn
    content = re.sub(r'sns_topic_arn\s*=\s*".*"', f'sns_topic_arn = "{new_sns_topic_arn}"', content)

    # Update role_arn
    content = re.sub(r'role_arn\s*=\s*".*"', f'role_arn = "arn:aws:iam::{account_id}:role/MultiAccountConfigRole"', content)

    # Write the updated content back to the file
    with open(file_path, 'w') as file:
        file.write(content)
