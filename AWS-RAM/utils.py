import boto3
import os


def get_account_list(client):
    """
    Retrieve a list of all AWS account IDs in the organization.
    
    Args:
        client: AWS Organizations client
        
    Returns:
        list: List of account IDs as strings
    """
    # Get all accounts in the organization
    accounts = client.list_accounts()
    account_ids = []
    
    # Extract account IDs from the response
    for account in accounts["Accounts"]:
        account_ids.append(account["Id"])
    return account_ids


def get_aws_ram_account(org_client):
    """
    Get the master/management account ID that serves as the AWS RAM administrator.
    
    Args:
        org_client: AWS Organizations client
        
    Returns:
        str: Master account ID if successful, None if error occurs
    """
    try:
        # Get organization details
        response = org_client.describe_organization()
        
        # Check if organization has a master account (legacy term, now called management account)
        if "MasterAccountArn" in response["Organization"]:
            master_account_id = response["Organization"]["MasterAccountId"]
            print(f"The AWS Ram Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS RAM is not enabled in this organization.")
            return None

    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def list_regions():
    """
    Get a list of all enabled AWS regions.
    
    Returns:
        list: List of enabled region names
    """
    # Use environment variable or default to us-east-1
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    # Create EC2 client to query available regions
    ec2_client = boto3.client("ec2", region_name=default_region)
    
    # Get only enabled regions (AllRegions=False excludes opt-in regions that aren't enabled)
    response = ec2_client.describe_regions(AllRegions=False)

    # Extract region names from response
    enabled_regions = [region["RegionName"] for region in response["Regions"]]
    return enabled_regions


def disable_organization_ram(master_account, client, org):
    """
    Disable AWS RAM service access at the organization level.
    
    Args:
        master_account: Master account ID (for logging purposes)
        client: RAM client (not used in current implementation)
        org: Organizations client
        
    Returns:
        bool: True if successful, False if error occurs
    """
    try:
        # Disable RAM service access for the entire organization
        org.disable_aws_service_access(ServicePrincipal="ram.amazonaws.com")
        print(f"Organizational service has been disabled {master_account} \n")
        return True

    except client.exceptions.ClientError as e:
        print(f"Error occurred in region {e}")
        return False


def list_resource_share(client):
    """
    List all resource shares owned by the current account.
    
    Args:
        client: AWS RAM client
        
    Returns:
        list: List of dictionaries containing resource share name, ARN, and status
    """
    try:
        # Get resource shares where current account is the owner
        response = client.get_resource_shares(resourceOwner='SELF')
        name_arn_map = []
        
        # Extract relevant information from each resource share
        for resshare in response['resourceShares']:
            name_arn_map.append({
                "name": resshare["name"], 
                "arn": resshare["resourceShareArn"], 
                "status": resshare["status"]
            })
        return name_arn_map
    except client.exceptions.ClientError as e:
        print(f"Error occurred in region {e}")


def list_permissions(client, arn):
    """
    List all permissions associated with a specific resource share.
    
    Args:
        client: AWS RAM client
        arn: Resource share ARN
        
    Returns:
        list: List of permission ARNs
    """
    try:
        # Get permissions for the specified resource share
        response = client.list_resource_share_permissions(resourceShareArn=arn)
        perm_arn = []
        
        # Extract permission ARNs
        for perm in response['permissions']:
            perm_arn.append(perm["arn"])
        return perm_arn
    except client.exceptions.ClientError as e:
        print(f"Error occured in region {e}")


def list_resource(client, arn):
    """
    List all resources associated with a specific resource share, filtering out
    organization-only resources that cannot be shared outside of AWS Organizations.
    
    Args:
        client: AWS RAM client
        arn: Resource share ARN
        
    Returns:
        list: List of resource ARNs that can be shared without organization
    """
    try:
        # Prepare list with the resource share ARN
        list = []
        list.append(arn)
        
        # Get all resources associated with this resource share
        response = client.list_resources(resourceOwner='SELF', resourceShareArns=list)
        resources = []
        
        # Filter out resource types that require AWS Organizations to be enabled
        organization_only_types = [
            "bedrock:CustomModel",
            "billing:billingview", 
            "outposts:Outpost",
            "ec2:LocalGatewayRouteTable",
            "s3-outposts:Outpost",
            "resource-explorer-2:View",
            "servicecatalog:Applications",
            "servicecatalog:AttributeGroups",
            "ec2:CoipPool",
            "ec2:Subnet",
            "ec2:SecurityGroup"
        ]
        
        for res in response['resources']:
            if res['type'] in organization_only_types:
                print("Cannot add this resource in the resource share because it can only be accessible when organization is enabled " + res['type'])
            else:
                # Add resources that can be shared without organization
                resources.append(res['arn'])
        return resources
    except client.exceptions.ClientError as e:
        print(f"Error occured in ressshare {e}")


def list_principals(client, arn):
    """
    List all principals (accounts, OUs, or organization) associated with a resource share.
    
    Args:
        client: AWS RAM client
        arn: Resource share ARN
        
    Returns:
        list: List of principal IDs
    """
    try:
        # Prepare list with the resource share ARN
        list = []
        list.append(arn)
        
        # Get principals associated with this resource share
        response = client.list_principals(resourceOwner='SELF', resourceShareArns=list)
        principals = []
        
        # Extract principal IDs
        for res in response['principals']:
            principals.append(res["id"])

        return principals
    
    except client.exceptions.ClientError as e:
        print(f"Error occured in region {e}")

       
def delete_resource_share(client, arn):
    """
    Delete a resource share.
    
    Args:
        client: AWS RAM client
        arn: Resource share ARN to delete
        
    Returns:
        dict: AWS API response
    """
    try:
        # Delete the specified resource share
        response = client.delete_resource_share(resourceShareArn=arn)
        print("Created new resource share")  # Note: This message seems incorrect for a delete operation
        return response
    except client.exceptions.ClientError as e:
        print(f"Error occured in region {e}")  


def aws_create_resource_share(name, res, permissions, client):
    """
    Create a new AWS RAM resource share with specified resources and permissions.
    
    Args:
        name: Original resource share name
        res: List of resource ARNs to include
        permissions: List of permission ARNs to assign
        client: AWS RAM client
        
    Returns:
        dict: AWS API response containing new resource share details
    """
    try:
        # Add "ck" prefix to the original name to create new resource share name
        new_name = "ck" + name 
        
        # Create new resource share with external principals allowed
        response = client.create_resource_share(
            name=new_name, 
            permissionArns=permissions,
            resourceArns=res, 
            allowExternalPrincipals=True
        )
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
        print("Created a Resource Share " + new_name)
        return response

    except Exception as e:
        print(f"Error in creating member: {e}")

    # Note: There's an unreachable except block here in the original code
    except Exception as e:
        print(f"Error in accepting invitations: {e}")
        return None


def accept_invite(client):
    """
    Accept all pending resource share invitations for the current account.
    
    Args:
        client: AWS RAM client
        
    Returns:
        dict: Response from the last invitation acceptance, or None if error
    """
    try:
        # Get all pending resource share invitations
        response = client.get_resource_share_invitations()
        ans = ''
        
        # Accept each invitation
        for res in response['resourceShareInvitations']:
            invi_arn = res['resourceShareInvitationArn']
            print(invi_arn)
            
            # Accept the invitation
            ans = client.accept_resource_share_invitation(resourceShareInvitationArn=invi_arn)
            print("Invite Accepted")
        return ans
    except Exception as e:
        print(f"Error in sending invitations: {e}")


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