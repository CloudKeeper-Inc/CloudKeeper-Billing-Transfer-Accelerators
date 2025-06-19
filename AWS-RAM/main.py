import boto3
from utils import *
import time

# Initialize AWS clients for Organizations and RAM services in us-east-1 region
org_client = boto3.client("organizations", region_name="us-east-1")
ram_client_1 = boto3.client("ram", region_name="us-east-1")

# Get list of all AWS account IDs in the organization
account_ids = get_account_list(org_client)

if __name__ == "__main__":
    
    # Get the master/management account for the organization
    master_account = get_aws_ram_account(org_client)
    
    # Initialize variables for storing principals and member accounts
    principals = []
    member_accounts = account_ids
   
    print("\nMember Accounts:", member_accounts)
    print("-------------------------------------------------------------------------------------------------------------------------------------------")
    
    # Initialize lists to store mapping of principals and resource shares
    map_principals = []
    map = []
    
    # PHASE 1: Iterate through all member accounts and regions to find existing resource shares
    for members in member_accounts:
        for region in list_regions():
            # Create a session for each member account using profile-based authentication
            session = boto3.Session(profile_name=str(members))
            ram_client = session.client("ram", region_name=region)
            
            # Get list of resource shares in the current account and region
            map = list_resource_share(ram_client)
            
            if map != []:
                print("RAM is has resource share in account number "+members+" in the region "+region)
                print("---------------------------------------------------------------------------------------------------------------------------------")        
                
                # Process each resource share found
                for element in map:
                    if element["status"] == "ACTIVE":
                        # Extract region from the resource share ARN
                        region = extract_region_from_arn(element["arn"])
                        ram_client = session.client("ram", region_name=region)                
                        
                        # Get resources, principals, and permissions associated with this resource share
                        resources = list_resource(ram_client, element["arn"])
                        principals = list_principals(ram_client, element["arn"])
                        
                        if resources != []:
                            # Get permissions for the resource share
                            permissions = list_permissions(ram_client, element["arn"])
                        
                            # Create a new resource share with the same configuration
                            res = aws_create_resource_share(element["name"], resources, permissions, ram_client)
                            new_arn = res["resourceShare"]["resourceShareArn"]
                            
                            # Store the mapping between new ARN and its principals for later association
                            map_principals.append({"arn": new_arn, "principals": principals})
                            
                            # Delete the old resource share
                            delete_resource_share(ram_client, element["arn"])
                            print("Resource share deleted" + element["arn"])
                        else:
                            print("This resource share will not be Created arn :" + element['arn'])
                    else:
                        # Resource share is not in ACTIVE state
                        print("The Resource share is in " + element["status"] + " state arn: " + element['arn'])
            else:
                print("RAM is disabled in account number "+members+" in the region "+region)
        print("----------------------------------------------------------------------------------------------------------------------------------------")
    
    # PHASE 2: Disable organization-wide RAM sharing
    disable_organization_ram(master_account, ram_client_1, org_client)
    
    #PHASE 3: Associate principals (accounts) with the newly created resource shares
    principal = [] 
    for element in map_principals:
        try:
            print(map_principals)
            new_arn = element["arn"]
            principal = element["principals"]
            
            # Extract account number from the resource share ARN
            acc = extract_account_num_from_arn(element["arn"])
            session = boto3.Session(profile_name=str(acc))
            region = extract_region_from_arn(element["arn"])
            ram_client = session.client("ram", region_name=region)  
            
            # Associate the resource share with principals (accounts)
            response = ram_client.associate_resource_share(resourceShareArn=new_arn, principals=principal)
            new_response = ram_client.get_resource_share_associations(associationType='PRINCIPAL',associationStatus='ASSOCIATING')
            # Wait until the association becomes external (cross-account)
            while True:
                if response['resourceShareAssociations'][0]['external'] == True :                     
                    break
                else:    
                    print("Sleeping for 15 secs")
                    time.sleep(15)
                    response = ram_client.associate_resource_share(resourceShareArn=new_arn, principals=principal)

                

            print("Associated the accounts to the resource share arn: "+new_arn)
        except Exception as e:
            print(f"Problem in associating the principals: {e}")
    
    # PHASE 4: Accept resource share invitations from each principal account
    for element in map_principals:
        for num in element['principals']:
            # Switch to each principal account to accept the invitation
            session = boto3.Session(profile_name=str(num))
            region = extract_region_from_arn(element['arn'])
            ram_client = session.client("ram", region_name=region)  
            
            # Accept the resource share invitation
            accept_invite(ram_client)      
    
    print("Alternate Setup For RAM is Successfully Completed")