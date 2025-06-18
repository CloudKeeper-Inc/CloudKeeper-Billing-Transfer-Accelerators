import boto3
from utils import *

org_client = boto3.client("organizations", region_name="us-east-1")

ram_client_1 = boto3.client("ram", region_name="us-east-1")

account_ids  = get_account_list(org_client)
if __name__ == "__main__":
    
    master_account = get_aws_ram_account(org_client)
    principals = []
    member_accounts = account_ids
   
    print("\nMember Accounts:", member_accounts)
    print("-------------------------------------------------------------------------------------------------------------------------------------------")
    map_principals = []
    map =[]
    for members in member_accounts:
        for region in list_regions():
            session = boto3.Session(profile_name=str(members))
            ram_client = session.client("ram", region_name=region)
            map = list_resource_share(ram_client)
            if map != []:
                print("RAM is has resource share in account number "+members+" in the region "+region)
                print("---------------------------------------------------------------------------------------------------------------------------------")        
                for element in map:
                    if element["status"] == "ACTIVE":
                        region = extract_region_from_arn(element["arn"])
                        ram_client = session.client("ram", region_name=region)                
                        resources = list_resource(ram_client,element["arn"])
                        principals = list_principals(ram_client,element["arn"])
                        if resources !=  []:
                            permissions = list_permissions(ram_client,element["arn"])
                            res = aws_create_resource_share(element["name"],resources,permissions,ram_client)
                            new_arn =res["resourceShare"]["resourceShareArn"]
                            map_principals.append({"arn": new_arn , "principals": principals})
                            delete_resource_share(ram_client,element["arn"])
                            print("Resource share deleted" + element["arn"])
                        else:
                            print("This resource share will not be Created arn :" + element['arn'] )
                    else:
                        print("The Reshource share is in " +element["status"]+" state arn: " + element['arn'] )
            else:
                print("RAM is disabled in account number "+members+" in the region "+region)
        print("----------------------------------------------------------------------------------------------------------------------------------------")

    disable_organization_ram(master_account,ram_client_1,org_client)
    princ =[] 
    for element in map_principals:
                try:
                    new_arn = element["arn"]
                    princ = element["principals"]
                    acc = extract_account_num_from_arn(element["arn"])
                    session = boto3.Session(profile_name=str(acc))
                    region = extract_region_from_arn(element["arn"])

                    ram_client = session.client("ram", region_name=region)  
                    
                    response = ram_client.associate_resource_share(resourceShareArn=new_arn, principals=princ)
                    while response['resourceShareAssociations'][0]['external'] == False:                        
                        response = ram_client.associate_resource_share(resourceShareArn=new_arn, principals=princ)
                    print("Associated the accounts to the resource share arn: "+new_arn)
                except Exception as e:
                    print(f"Problem in associating the principals: {e}")
    for element in map_principals:
        for num in element['principals']:
                session = boto3.Session(profile_name=str(num))
                region = extract_region_from_arn(element['arn'])
                ram_client = session.client("ram", region_name=region)  
                accept_invite(ram_client)      

    print("Alternate Setup For RAM is Sucessfully Completed")
