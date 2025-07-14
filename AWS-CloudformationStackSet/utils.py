import boto3
from botocore.exceptions import ClientError
import json
import time

def list_organization_accounts(org_client):
    """
    Retrieve all active AWS accounts from an AWS Organization.
    
    Args:
        org_client: Boto3 Organizations client
        
    Returns:
        list: List of active account IDs
    """
    accounts = []
    # Use paginator to handle large numbers of accounts
    paginator = org_client.get_paginator('list_accounts')
    
    # Iterate through all pages of accounts
    for page in paginator.paginate():
        for acct in page['Accounts']:
            # Only include active accounts (exclude suspended/closed accounts)
            if acct['Status'] == 'ACTIVE':
                accounts.append(acct['Id'])
    return accounts

def get_boto3_session_for_account(account_id, region):
    """
    Create a boto3 session for a specific AWS account using profile-based authentication.
    
    Args:
        account_id: AWS account ID
        region: AWS region name
        
    Returns:
        boto3.Session or None: Session object if successful, None if failed
    """
    try:
        # Create session using account ID as profile name
        return boto3.Session(profile_name=str(account_id), region_name=region)
    except Exception as e:
        print(f"[ERROR] Failed to create session for account {account_id}: {str(e)}")
        return None

def list_regions():
    """
    Get list of all available AWS regions that are enabled for the account.
    
    Returns:
        list: List of region names that are available for use
    """
    # Use us-east-1 as default region to query all regions
    ec2 = boto3.client('ec2', region_name='us-east-1')
    regions = ec2.describe_regions(AllRegions=True)['Regions']
    
    # Return only regions that are enabled (not requiring opt-in or already opted-in)
    return [r['RegionName'] for r in regions if r['OptInStatus'] in ('opt-in-not-required', 'opted-in')]

def create_stackset_roles_admin(session, admin_role_name=""):
    """
    Create the CloudFormation StackSet administration role in the management account.
    This role allows CloudFormation to assume the execution role in target accounts.
    
    Args:
        session: Boto3 session for the management account
        admin_role_name: Name of the administration role to create
    """
    iam = session.client('iam')
    
    # Check if role already exists
    try:
        iam.get_role(RoleName=admin_role_name)
        print(f"Role {admin_role_name} already exists.")
        return
    except iam.exceptions.NoSuchEntityException:
        # Role doesn't exist, continue with creation
        pass

    # Define trust policy allowing CloudFormation service to assume this role
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
        # Create the IAM role
        iam.create_role(
            RoleName=admin_role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        ),
        
        # Attach administrator access policy to the role
        iam.attach_role_policy(
            RoleName=admin_role_name,
            PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        print(f"Created role {admin_role_name}.")
    except ClientError as e:
        print(f"[ERROR] Creating role {admin_role_name}: {str(e)}")
        
def create_stackset_roles_child(session, account_ids):
    """
    Create the CloudFormation StackSet execution role in target accounts.
    This role is assumed by the administration role to deploy stacks.
    
    Args:
        session: Boto3 session for the target account
        account_ids: List of account IDs that contain the administration role
    """
    iam = session.client("iam")
    role_name = 'AWSCloudFormationStackSetExecutionRole'
    role_arn = []
    
    # Check if execution role already exists
    try:
        iam.get_role(RoleName=role_name)
        print(f"Role {role_name} already exists.")
        return
    except iam.exceptions.NoSuchEntityException:
        # Role doesn't exist, continue with creation
        pass
    
    # Build ARNs for all administration roles that can assume this execution role
    for account in account_ids:
       ans = f"arn:aws:iam::{account}:role/AWSCloudFormationStackSetAdministrationRole"
       role_arn.append(ans)
    
    # Define trust policy allowing CloudFormation service and administration roles to assume this role
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "cloudformation.amazonaws.com",
                    "AWS": role_arn  # Allow administration roles from specified accounts
                },
                "Action": "sts:AssumeRole"
            }
        ]
    }
    
    try:
        # Create the execution role
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        
        # Attach administrator access policy
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        print(f"Created role {role_name}.")
    except ClientError as e:
        print(f"[ERROR] Creating execution role{role_name}: {str(e)}")

def create_self_managed_stackset(session, stackset_name, details, account_ids, regions, admin_role_name, exec_role_name):
    """
    Create and deploy a self-managed CloudFormation StackSet.
    Self-managed StackSets require explicit IAM roles for administration and execution.
    
    Args:
        session: Boto3 session for the management account
        stackset_name: Name of the StackSet to create
        details: Dictionary containing template body, parameters, capabilities, and tags
        account_ids: List of target account IDs
        regions: AWS region for the StackSet (used for client initialization)
        admin_role_name: Name of the administration role
        exec_role_name: Name of the execution role
    """
    cf = session.client('cloudformation', region_name=regions)
    
    # First, try to update existing StackSet (this will fail if it doesn't exist)
    try:
        response = cf.update_stack_set(
            StackSetName=stackset_name,
            TemplateBody=details['TemplateBody'],
            Parameters=details['Parameters'],
            Capabilities=details['Capabilities'],
            Tags=details['Tags'],
            AutoDeployment={
                'Enabled': False,  # Disable auto-deployment for self-managed StackSets
            }
        )
    except ClientError:
        # Update failed, likely because StackSet doesn't exist
        pass
    
    # Create new StackSet with modified name to avoid conflicts
    name = stackset_name + "-ck"
    try:
        # Create the StackSet
        r = cf.create_stack_set(
            StackSetName=name,
            TemplateBody=details['TemplateBody'],
            Parameters=details['Parameters'],
            Capabilities=details['Capabilities'],
            Tags=details['Tags'],
            PermissionModel='SELF_MANAGED',  # Use self-managed permission model
            AdministrationRoleARN=f"arn:aws:iam::{session.client('sts').get_caller_identity()['Account']}:role/{admin_role_name}",
            ExecutionRoleName=exec_role_name
        )
        
        print(f"Created self-managed StackSet {stackset_name}, now launching instances...")
        
        # Deploy StackSet instances to target accounts and regions
        cf.create_stack_instances(
            StackSetName=name,
            Accounts=account_ids,
            Regions=details['Regions']
        )
        print(f"Successfully deployed instances for StackSet {stackset_name}.")
        
    except ClientError as e:
        print(f"[ERROR] Creating or deploying self-managed stackset {stackset_name}: {str(e)}")

def disassociate_delegated_admin(org_client, account_id):
    """
    Remove an account's delegated administrator privileges for CloudFormation StackSets.
    
    Args:
        org_client: Boto3 Organizations client
        account_id: Account ID to remove delegated admin privileges from
    """
    try:
        org_client.deregister_delegated_administrator(
            AccountId=account_id,
            ServicePrincipal='member.org.stacksets.cloudformation.amazonaws.com'
        )
        print(f"Successfully disassociated account {account_id} as delegated admin.")
        
        org_client.disable_aws_service_access(
    ServicePrincipal='member.org.stacksets.cloudformation.amazonaws.com')
        print('Disabled Cloudformation Stackset from Organization')
    except ClientError as e:
        print(f"[ERROR] Disassociating delegated admin {account_id}: {str(e)}")

def list_service_managed_stacksets(session, region):
    """
    List all active service-managed StackSets in a specific region.
    Service-managed StackSets use AWS Organizations for permissions.
    
    Args:
        session: Boto3 session
        region: AWS region to query
        
    Returns:
        list: List of service-managed StackSet names
    """
    cf = session.client('cloudformation', region_name=region)
    stacksets = []
    
    try:
        # List StackSets with delegated admin permissions
        response = cf.list_stack_sets(Status='ACTIVE', CallAs='DELEGATED_ADMIN')
        
        # Filter for service-managed StackSets only
        for stackset in response.get('Summaries', []):
            if stackset.get('PermissionModel') == 'SERVICE_MANAGED':
                stacksets.append(stackset['StackSetName'])
                
    except ClientError as e:
        print(f"[ERROR] Listing stacksets in {region}: {str(e)}")
        
    return stacksets

def extract_stackset_details_with_fallback(session, region, stackset_name):
    """
    Extract detailed information about a StackSet including template, parameters, and configuration.
    
    Args:
        session: Boto3 session
        region: AWS region where the StackSet exists
        stackset_name: Name of the StackSet to describe
        
    Returns:
        dict: Dictionary containing StackSet details (template, parameters, capabilities, tags, ARN, regions)
    """
    cf = session.client('cloudformation', region_name=region)
    
    try:
        # Get detailed information about the StackSet
        response = cf.describe_stack_set(StackSetName=stackset_name, CallAs='DELEGATED_ADMIN')
        
        # Extract relevant details from the response
        parameters = response['StackSet'].get('Parameters', [])
        capabilities = response['StackSet'].get('Capabilities', [])
        tags = response['StackSet'].get('Tags', [])
        template_body = response['StackSet'].get('TemplateBody', '{}')
        arn = response['StackSet'].get('StackSetARN', '')
        region = response['StackSet'].get('Regions', [])
        
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

def delete_stackset(session, region, stackset_name, details):
    """
    Delete a CloudFormation StackSet and all its instances.
    This function handles the proper sequence: delete instances first, then delete the StackSet.
    
    Args:
        session: Boto3 session
        region: AWS region where the StackSet exists
        stackset_name: Name of the StackSet to delete
        details: Dictionary containing StackSet details including regions
        
    Returns:
        bool: True if deletion was successful, False otherwise
    """
    cf = session.client('cloudformation', region_name=region)
   
    try:
        # Step 1: Get all stack instances that need to be deleted
        instances = cf.list_stack_instances(StackSetName=stackset_name, CallAs='DELEGATED_ADMIN')
        summaries = instances.get('Summaries', [])
        
        if not summaries:
            print(f"No instances found for StackSet {stackset_name}")
        else:
            # Extract unique organizational unit IDs from instances
            ous = list(set(i['OrganizationalUnitId'] for i in summaries if 'OrganizationalUnitId' in i))
            
            if ous:
                print(f"Deleting stack instances from OUs: {ous}")
                
                # Step 2: Delete all stack instances
                response = cf.delete_stack_instances(
                    StackSetName=stackset_name,
                    DeploymentTargets={"OrganizationalUnitIds": ous},
                    Regions=details['Regions'],
                    RetainStacks=False,  # Don't retain stacks after deletion
                    CallAs='DELEGATED_ADMIN'
                )
                
                operation_id = response['OperationId']
                print(f"Started deletion operation: {operation_id}")
                
                # Step 3: Wait for the deletion operation to complete
                while True:
                    try:
                        # Check operation status
                        op_status = cf.describe_stack_set_operation(
                            StackSetName=stackset_name,
                            OperationId=operation_id,
                            CallAs='DELEGATED_ADMIN'
                        )
                        
                        status = op_status['StackSetOperation']['Status']
                        print(f"Operation status: {status}")
                        
                        if status == 'SUCCEEDED':
                            print("Stack instances deleted successfully")
                            break
                        elif status == 'FAILED':
                            print(f"Operation failed: {op_status['StackSetOperation'].get('StatusReason', 'Unknown reason')}")
                            return False
                        elif status in ['STOPPED', 'STOPPING']:
                            print(f"Operation was stopped: {op_status['StackSetOperation'].get('StatusReason', 'Unknown reason')}")
                            return False
                        else:
                            # Operation still in progress, wait and check again
                            print("Waiting for operation to complete...")
                            time.sleep(10)
                            
                    except ClientError as e:
                        print(f"Error checking operation status: {str(e)}")
                        time.sleep(10)
        
        # Step 4: Delete the StackSet itself (only after all instances are deleted)
        print(f"Deleting StackSet: {stackset_name}")
        cf.delete_stack_set(StackSetName=stackset_name, CallAs='DELEGATED_ADMIN')
        print(f"StackSet {stackset_name} deleted successfully")
        return True
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        
        if error_code == 'StackSetNotFoundException':
            print(f"StackSet {stackset_name} not found in region {region}")
            return True  # Consider this a success since the goal is achieved
        else:
            print(f"[ERROR] Deleting stackset {stackset_name} in {region}: {error_message}")
            return False

def extract_account_and_region(arn):
    """
    Extract account number and region from an AWS ARN.
    
    AWS ARN format: arn:partition:service:region:account-id:resource
    
    Args:
        arn (str): AWS ARN string
        
    Returns:
        dict: Dictionary containing 'account' and 'region' keys
        
    Raises:
        ValueError: If ARN format is invalid
    """
    # Input validation
    if not arn or not isinstance(arn, str):
        raise ValueError("ARN must be a non-empty string")
    
    # Split ARN by colons to extract components
    arn_parts = arn.split(':')
    
    # Validate ARN has minimum required parts
    if len(arn_parts) < 6:
        raise ValueError("Invalid ARN format. Expected at least 6 parts separated by colons")
    
    # Validate ARN starts with 'arn'
    if arn_parts[0] != 'arn':
        raise ValueError("ARN must start with 'arn:'")
    
    # Extract region and account ID from their respective positions
    region = arn_parts[3]
    account_id = arn_parts[4]
    
    # Validate account ID format (must be 12 digits)
    if not account_id.isdigit() or len(account_id) != 12:
        raise ValueError("Invalid account ID. Must be 12 digits")
    
    return {
        'account': account_id,
        'region': region
    }
