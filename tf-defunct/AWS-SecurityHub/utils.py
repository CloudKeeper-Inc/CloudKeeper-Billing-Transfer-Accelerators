import csv
from botocore.exceptions import ClientError


def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    accId_email_map = []
    for account in accounts['Accounts']:
        account_ids.append(account['Id'])
        accId_email_map.append({'AccountId': account['Id'], 'Email': account['Email']})
    return account_ids, accId_email_map


def get_all_organization_conformance_packs(client):
    conformance_packs = []
    paginator = client.get_paginator('describe_organization_conformance_packs')
    
    for page in paginator.paginate():
        conformance_packs.extend(page['OrganizationConformancePacks'])
    
    return conformance_packs


def get_aws_security_hub_admin_account(org_client):
    
    try:
        response = org_client.describe_organization()
        
        if 'MasterAccountArn' in response['Organization']:
            master_account_id = response['Organization']['MasterAccountId']
            print(f"The AWS Security Hub Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS Security Hub is not enabled in this organization.")
            return None
    
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def check_delegated_admin_for_aws_security_hub(org_client):
    """Check if the current account is a delegated admin for AWS securityhub."""

    try:
        response = org_client.list_delegated_administrators(ServicePrincipal='securityhub.amazonaws.com')
        if response['DelegatedAdministrators']:
            for admin in response['DelegatedAdministrators']:
                print(f"Delegated Admin Account ID for AWS Security Hub: {admin['Id']}")
                return admin['Id']
        else:
            print("No delegated administrator is set for AWS Security Hub.")
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


def check_security_hub_in_region(region, session):
    try:
        client = session.client('securityhub', region_name=region)
        
        response = client.get_enabled_standards()
        
        if response['StandardsSubscriptions']:
            return True
        else:
            return False

    except ClientError as e:
        if e.response['Error']['Code'] == 'InvalidAccessException':
            print(f"Security Hub is not enabled in region {region}.")
            return False
        else:
            print(f"An error occurred: {str(e)}")
            return False


def get_regions_with_security_hub_enabled(session):
    regions = get_regions(session)
    enabled_regions = []

    for region in regions:
        if check_security_hub_in_region(region, session):
            enabled_regions.append(region)
            
    return enabled_regions


def check_security_hub_organization(session, region="us-east-1"):
    try:
        client = session.client('securityhub', region_name=region)

        response = client.describe_organization_configuration()
        if response['OrganizationConfiguration']['Status'] == 'ENABLED':
            return True
        else:
            return False

    except:
        return False


def create_csv_from_dict(data, output_file):
    headers = [
        "Region", 
        "Policy Name", 
        "Policy ID", 
        "Accounts", 
        "Standards", 
        "Disabled Security Controls", 
        "Enabled Security Controls", 
        "Custom Parameters"
    ]
    
    with open(output_file, mode='w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        writer.writeheader() 

        for region, region_data in data.items():
            for policy in region_data.get('policies', []):
                writer.writerow({
                    "Region": region,
                    "Policy Name": policy.get('name', ''),
                    "Policy ID": policy.get('id', ''),
                    "Accounts": ", ".join(policy.get('accounts', [])),
                    "Standards": ", ".join(policy.get('standards', [])),
                    "Disabled Security Controls": ", ".join(
                        policy.get('securityControls', {}).get('DisabledSecurityControlIdentifiers', [])
                    ),
                    "Enabled Security Controls": ", ".join(
                        policy.get('securityControls', {}).get('EnabledSecurityControlIdentifiers', [])
                    ),
                    "Custom Parameters": ", ".join(
                        str(param) for param in policy.get('securityControls', {}).get('SecurityControlCustomParameters', [])
                    )
                })
