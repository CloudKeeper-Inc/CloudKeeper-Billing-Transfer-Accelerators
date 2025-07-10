import boto3
from botocore.exceptions import ClientError
import json

def list_organization_accounts(org_client):
    accounts = []
    paginator = org_client.get_paginator('list_accounts')
    for page in paginator.paginate():
        for acct in page['Accounts']:
            if acct['Status'] == 'ACTIVE':
                accounts.append(acct['Id'])
    return accounts

def get_boto3_session_for_account(account_id,region):
    try:
        return boto3.Session(profile_name=str(account_id),region_name=region)
    except Exception as e:
        print(f"[ERROR] Failed to create session for account {account_id}: {str(e)}")
        return None

def list_regions():
    ec2 = boto3.client('ec2', region_name='us-east-1')
    regions = ec2.describe_regions(AllRegions=True)['Regions']
    return [r['RegionName'] for r in regions if r['OptInStatus'] in ('opt-in-not-required', 'opted-in')]

def create_stackset_roles_admin(session ,admin_role_name=""  ):
    iam = session.client('iam')
    try:
        iam.get_role(RoleName=admin_role_name)
        print(f"Role {admin_role_name} already exists.")
        return
    except iam.exceptions.NoSuchEntityException:
        pass

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "cloudformation.amazonaws.com"
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    try:
        iam.create_role(
            RoleName=admin_role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        ),
        iam.attach_role_policy(
            RoleName=admin_role_name,
            PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        print(f"Created role {admin_role_name}.")
    except ClientError as e:
        print(f"[ERROR] Creating role {admin_role_name}: {str(e)}")
        
def create_stackset_roles_child(session, account_ids):
    iam = session.client("iam")
    role_name = 'AWSCloudFormationStackSetExecutionRole'
    role_arn = []
    try:
        iam.get_role(RoleName=role_name)
        print(f"Role {role_name} already exists.")
        return
    except iam.exceptions.NoSuchEntityException:
        pass
    for account in account_ids:
       ans = f"arn:aws:iam::{account}:role/AWSCloudFormationStackSetAdministrationRole"
       role_arn.append(ans)
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "cloudformation.amazonaws.com",
                    "AWS": role_arn
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        print(f"Created role {role_name}.")
    except ClientError as e:
        print(f"[ERROR] Creating execution role{role_name}: {str(e)}")

def create_self_managed_stackset(session, stackset_name, details, account_ids, regions, admin_role_name, exec_role_name):
    cf = session.client('cloudformation',region_name = regions)
    name  = stackset_name+"-ck-2"
    try:
        r = cf.create_stack_set(
            StackSetName=name,
            TemplateBody=details['TemplateBody'],
            Parameters=details['Parameters'],
            Capabilities=details['Capabilities'],
            Tags=details['Tags'],
            PermissionModel='SELF_MANAGED',
            AdministrationRoleARN=f"arn:aws:iam::{session.client('sts').get_caller_identity()['Account']}:role/{admin_role_name}",
            ExecutionRoleName=exec_role_name,
    #         AutoDeployment={
    #     'Enabled': False,
    #     'RetainStacksOnAccountRemoval': False
    # }
        )
        print(f"Created self-managed StackSet {stackset_name}, now launching instances...")
        cf.create_stack_instances(
            StackSetName=name,
            Accounts=account_ids,
            Regions=details['Regions']
        )
        print(f"Successfully deployed instances for StackSet {stackset_name}.")
    except ClientError as e:
        print(f"[ERROR] Creating or deploying self-managed stackset {stackset_name}: {str(e)}")

def disassociate_delegated_admin(org_client, account_id):
    try:
        org_client.deregister_delegated_administrator(
            AccountId=account_id,
            ServicePrincipal='member.org.stacksets.cloudformation.amazonaws.com'
        )
        print(f"Successfully disassociated account {account_id} as delegated admin.")
    except ClientError as e:
        print(f"[ERROR] Disassociating delegated admin {account_id}: {str(e)}")

def list_service_managed_stacksets(session, region):
    cf = session.client('cloudformation', region_name=region)
    stacksets = []
    try:
        response = cf.list_stack_sets(Status='ACTIVE', CallAs='DELEGATED_ADMIN')
        for stackset in response.get('Summaries', []):
            if stackset.get('PermissionModel') == 'SERVICE_MANAGED':
                stacksets.append(stackset['StackSetName'])
    except ClientError as e:
        print(f"[ERROR] Listing stacksets in {region}: {str(e)}")
    return stacksets

def extract_stackset_details_with_fallback(session, region, stackset_name):
    cf = session.client('cloudformation', region_name=region)
    try:
        response = cf.describe_stack_set(StackSetName=stackset_name, CallAs='DELEGATED_ADMIN')
        parameters = response['StackSet'].get('Parameters', [])
        capabilities = response['StackSet'].get('Capabilities', [])
        tags = response['StackSet'].get('Tags', [])
        template_body = response['StackSet'].get('TemplateBody', '{}')
        arn = response['StackSet'].get('StackSetARN','')
        region = response['StackSet'].get('Regions',[])
        return {
            'TemplateBody': template_body,
            'Parameters': parameters,
            'Capabilities': capabilities,
            'Tags': tags,
            'ARN': arn,
            'Regions': region
        }
    except ClientError as e:
        print(f"[ERROR] Extracting details from {stackset_name} in {region}: {str(e)}")
        return {}

def delete_stackset(session, region, stackset_name):
    cf = session.client('cloudformation', region_name=region)
    try:
        instances = cf.list_stack_instances(StackSetName=stackset_name, CallAs='DELEGATED_ADMIN')
        ous = list(set(i['OrganizationalUnitId'] for i in instances.get('Summaries', []) if 'OrganizationalUnitId' in i))
        while True:

          if ous:
            print('hi')
            r = cf.delete_stack_instances(
                StackSetName=stackset_name,
                DeploymentTargets={"OrganizationalUnitIds": ous},
                Regions=[region],
                RetainStacks=False,
                CallAs='DELEGATED_ADMIN'
            )
          op_status = cf.describe_stack_set_operation(StackSetName=stackset_name,OperationId=r['OperationId'],CallAs='DELEGATED_ADMIN')
          
          if op_status['StackSetOperation']['Status'] == 'SUCCEEDED':
            try:
              cf.delete_stack_set(StackSetName=stackset_name,CallAs='DELEGATED_ADMIN')       
              print(op_status['StackSetOperation']['Status'])
              break
            except ClientError as e:
                print('Trying again')  
          else:
            import time;time.sleep(30)
    # Poll until deletion is complete
    except ClientError as e:
        print(f"[ERROR] Deleting stackset {stackset_name} in {region}: {str(e)}")

def extract_account_and_region(arn):
    """
    Extract account number and region from an AWS ARN.
    
    Args:
        arn (str): AWS ARN string
        
    Returns:
        dict: Dictionary containing 'account' and 'region' keys
        
    Raises:
        ValueError: If ARN format is invalid
    """
    if not arn or not isinstance(arn, str):
        raise ValueError("ARN must be a non-empty string")
    
    # Split ARN by colons
    arn_parts = arn.split(':')
    
    # AWS ARN format: arn:partition:service:region:account-id:resource
    if len(arn_parts) < 6:
        raise ValueError("Invalid ARN format. Expected at least 6 parts separated by colons")
    
    if arn_parts[0] != 'arn':
        raise ValueError("ARN must start with 'arn:'")
    
    region = arn_parts[3]
    account_id = arn_parts[4]
    
    # Basic validation
    if not account_id.isdigit() or len(account_id) != 12:
        raise ValueError("Invalid account ID. Must be 12 digits")
    
    return {
        'account': account_id,
        'region': region
    }