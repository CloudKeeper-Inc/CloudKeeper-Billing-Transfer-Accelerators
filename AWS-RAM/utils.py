import boto3
import os


def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    for account in accounts["Accounts"]:
        account_ids.append(account["Id"])
    return account_ids

def get_aws_ram_account(org_client):
    try:
        response = org_client.describe_organization()
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
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    ec2_client = boto3.client("ec2", region_name=default_region)
    response = ec2_client.describe_regions(AllRegions=False)

    enabled_regions = [region["RegionName"] for region in response["Regions"]]
    return enabled_regions

def disable_organization_ram(master_account,client,org):
    try:
        org.disable_aws_service_access(ServicePrincipal="ram.amazonaws.com")
        print(f"Organizational service has been disabled {master_account} \n")
        return True

    except client.exceptions.ClientError as e:
        print(f"Error occurred in region {e}")
        return False

def list_resource_share(client):
    try:
        response = client.get_resource_shares(resourceOwner='SELF')
        name_arn_map =[]
        for resshare in response['resourceShares']:
            name_arn_map.append({"name": resshare["name"], "arn": resshare["resourceShareArn"], "status":resshare["status"]})
        return name_arn_map
    except client.exceptions.ClientError as e :
        print(f"Error occurred in region {e}")

def list_permissions(client, arn):
    try:
        response = client.list_resource_share_permissions(resourceShareArn=arn)
        perm_arn = []
        for perm in response['permissions']:
            perm_arn.append(perm["arn"])
        return perm_arn
    except client.exceptions.ClientError  as e:
        print(f"Error occured in region {e}")

def list_resource(client,arn):
    try:
        list =[]
        list.append(arn)
        response = client.list_resources(resourceOwner='SELF',resourceShareArns=list)
        resources = []
        
        for res in response['resources']:
            if res['type'] == "bedrock:CustomModel" or res['type'] == "billing:billingview" or res['type'] == "outposts:Outpost" or res['type'] == "ec2:LocalGatewayRouteTable" or res['type'] == "s3-outposts:Outpost" or res['type'] == "resource-explorer-2:View" or res['type'] == "servicecatalog:Applications" or res['type'] == "servicecatalog:AttributeGroups" or res['type'] == "ec2:CoipPool" or res['type'] == "ec2:Subnet" or res['type'] == "ec2:SecurityGroup":
                print("Cannot add this resource in the resource share because it can only be accessable when organization is enabled " + res['type'])
            else:
                resources.append(res['arn'])
        return resources
    except client.exceptions.ClientError  as e:
        print(f"Error occured in ressshare {e}")

def list_principals(client, arn):
    try:
        list =[]
        list.append(arn)
        response = client.list_principals(resourceOwner='SELF', resourceShareArns=list)
        principals= []
        for res in response['principals']:
            principals.append(res["id"])

        return principals
    
    except client.exceptions.ClientError  as e:
        print(f"Error occured in region {e}")
       
def delete_resource_share(client,arn):
    try:
        response = client.delete_resource_share(resourceShareArn=arn)
        print ("Created new resource share")
        return response
    except client.exceptions.ClientError  as e:
        print(f"Error occured in region {e}")  
        
def aws_create_resource_share(name, res, permissions, client):
    try:
        new_name = "ck"+name 
        response = client.create_resource_share(name = new_name, permissionArns = permissions ,resourceArns = res, allowExternalPrincipals=True )
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
        print("Created a Resource Share " + new_name)
        return response

    except Exception as e:
        print(f"Error in creating member: {e}")

        
    except Exception as e:
        print(f"Error in accepting invitations: {e}")
        return None
def accept_invite(client):
    try:
            response = client.get_resource_share_invitations()
            ans =''
            for res in response['resourceShareInvitations']:
                invi_arn = res['resourceShareInvitationArn']
                print(invi_arn)
                ans = client.accept_resource_share_invitation(resourceShareInvitationArn=invi_arn)
                print("Invite Accepted")
            return ans
    except Exception as e:
        print(f"Error in sending invitations: {e}")

def extract_region_from_arn(arn):
    if not isinstance(arn, str) or not arn.startswith('arn:'):
        raise ValueError('Invalid ARN format')
    
    parts = arn.split(':')
    if len(parts) < 6:
        raise ValueError('ARN does not have enough parts')
    
    region = parts[3]
    return region if region else None


def extract_account_num_from_arn(arn):
    if not isinstance(arn, str) or not arn.startswith('arn:'):
        raise ValueError('Invalid ARN format')
    
    parts = arn.split(':')
    if len(parts) < 6:
        raise ValueError('ARN does not have enough parts')
    
    region = parts[4]
    return region if region else None