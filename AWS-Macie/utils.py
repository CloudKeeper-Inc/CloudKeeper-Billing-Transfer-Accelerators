import boto3
import os, re, json


def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    accId_email_map = []

    for account in accounts["Accounts"]:
        account_ids.append(account["Id"])
        accId_email_map.append({"AccountId": account["Id"], "Email": account["Email"]})
    return account_ids, accId_email_map


def get_aws_macie_admin_account(org_client):
    try:
        response = org_client.describe_organization()
        
        if "MasterAccountArn" in response["Organization"]:
            master_account_id = response["Organization"]["MasterAccountId"]
            print(f"The AWS Macie Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS Macie is not enabled in this organization.")
            return None

    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def list_enabled_regions():
    default_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    ec2_client = boto3.client("ec2", region_name=default_region)
    response = ec2_client.describe_regions(AllRegions=False)

    enabled_regions = [region["RegionName"] for region in response["Regions"]]
    return enabled_regions


def check_delegated_admin_for_macie(org_client):
    """Check if the current account is a delegated admin for AWS Macie."""

    try:
        response = org_client.list_delegated_administrators(
            ServicePrincipal="macie.amazonaws.com"
        )
        if response["DelegatedAdministrators"]:
            for admin in response["DelegatedAdministrators"]:
                print(f"Delegated Admin Account ID for AWS macie: {admin['Id']}")
                return admin["Id"]
        else:
            print("No delegated administrator is set for AWS macie.")
            return None
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def disable_organization_macie(master_account):
    client = boto3.client("macie2")
    org = boto3.client("organizations")
    try:
        response = org.deregister_delegated_administrator(
            AccountId=master_account, ServicePrincipal="macie.amazonaws.com"
        )
        org.disable_aws_service_access(ServicePrincipal="macie.amazonaws.com")
        print(f"Dissasociated the Delegated Admin Account {master_account} \n")
        return True

    except client.exceptions.ClientError as e:
        print(f"Error occurred in region {e}")
        return False


def aws_create_member(member_account, Email, client):
    try:
        response = (
            client.create_member(account={"accountId": member_account, "email": Email}),
        )
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
        print("Added the Member Account in the Admin account: " + member_account)

    except Exception as e:
        print(f"Error in creating member: {e}")


def create_invite(member_accounts, client):
    try:
        response = client.create_invitations(accountIds=member_accounts)
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
        print("Invited all the member Accounts")
        return response

    except Exception as e:
        print(f"Error in sending invitations: {e}")


def accept_invitation(master_account_id, invi_arn, client, member_account):
    try:
        response = client.accept_invitation(
            administratorAccountId=master_account_id, invitationId=invi_arn
        )
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
        print("Invite Accepted by account: " + member_account)
        print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
       
    except Exception as e:
        print(f"Error in accepting invitations: {e}")
def get_key_id_from_arn(key_arn):
    try:
        if not key_arn.startswith('arn:'):
            print("h1"+key_arn)
            return key_arn
        
        parts = key_arn.split(':')
        if len(parts) >= 6 and parts[2] == 'kms':
            key_part = parts[5]
            if key_part.startswith('key/'):
                return key_part.split('/')[-1]
            elif key_part.startswith('alias/'):
                return key_part
            else:
                return key_part
        else:
            return key_arn
    except Exception as e:
        print(f"Error extracting key ID from ARN: {e}")
        return key_arn

def get_bucket_policy(bucket_name,master_account,region):
    session = boto3.Session(profile_name=str(master_account))
    s3_client = session.client("s3", region_name=region)
            
    try:
        response = s3_client.get_bucket_policy(Bucket=bucket_name)
        
        policy = json.loads(response['Policy'])
        return policy
    except s3_client.exceptions.NoSuchBucketPolicy:
        print(f"No policy exists for bucket {bucket_name}")
        return None
    except Exception as e:
        print(f"Error fetching bucket policy: {e}")
        return None
    
def update_bucket_policy(bucket_name, policy_document, master_account, region):
    session = boto3.Session(profile_name=str(master_account))
    s3_client = session.client("s3", region_name=region)
    
    try:
        if isinstance(policy_document, dict):
            policy_document = json.dumps(policy_document)
        
        s3_client.put_bucket_policy(
            Bucket=bucket_name,
            Policy=policy_document
        )
        print(f"Successfully updated policy for bucket {bucket_name}")
        return True
    except Exception as e:
        print(f"Error updating bucket policy: {e}")
        return False

def update_macie_bucket_policy(policy, child_accounts):
    if not policy or "Statement" not in policy:
        print("Invalid policy document")
        return policy
    
    for statement in policy["Statement"]:
     
        if "Action" in statement and statement["Action"] ==  "s3:PutObject":  

            if "Condition" in statement and "StringEquals" in statement["Condition"] and "aws:SourceAccount" in statement["Condition"]["StringEquals"]:
                statement["Condition"]["StringEquals"]["aws:SourceAccount"] = child_accounts
                if "Condition" in statement and "ArnLike" in statement["Condition"] and "aws:SourceArn" in statement["Condition"]["ArnLike"]:
                    updated_arns = []

                    for account in child_accounts:
                        arn = "arn:aws:macie2:*:"+account+":export-configuration:*"
                        arn_1  = "arn:aws:macie2:*:"+account+":classification-job/*"
                        updated_arns.append(arn)
                        updated_arns.append(arn_1)
               
            
                    statement["Condition"]["ArnLike"]["aws:SourceArn"] = updated_arns
        
            
    return policy

def get_kms_policy(key_id,master_account,region):
    session = boto3.Session(profile_name=str(master_account))
    kms_client = session.client("kms", region_name=region)
    
    try:
        response = kms_client.get_key_policy(
            KeyId=key_id,
        )
        if isinstance(response['Policy'], str):
            policy = json.loads(response['Policy'])
        else:
            
            policy = response['Policy']
            
        return policy
    except Exception as e:
        print(f"Error fetching KMS policy: {e}")
        return None

def update_kms_policy(key_id, policy_document,master_account,region):
    session = boto3.Session(profile_name=str(master_account))
    kms_client = session.client("kms", region_name=region)
    
    try:
        if isinstance(policy_document, dict):
            policy_document = json.dumps(policy_document)
        
        kms_client.put_key_policy(
            KeyId=key_id,
            Policy=policy_document
        )
        print(f"Successfully updated KMS key policy for key {key_id}")
        return True
    except Exception as e:
        print(f"Error updating KMS policy: {e}")
        return False    

def update_kms_key_policy(policy, child_accounts):
    if not policy or "Statement" not in policy:
        print("Invalid policy document")
        return policy
    
    for statement in policy["Statement"]:
        if "Condition" in statement:
            if "StringEquals" in statement["Condition"] and "aws:SourceAccount" in statement["Condition"]["StringEquals"]:
                statement["Condition"]["StringEquals"]["aws:SourceAccount"] = child_accounts
            
            if "ArnLike" in statement["Condition"] and "aws:SourceArn" in statement["Condition"]["ArnLike"]:
                    updated_arns = []
                    for account in child_accounts:
                        arn = "arn:aws:macie2:*:"+account+":export-configuration:*"
                        arn_1  = "arn:aws:macie2:*:"+account+":classification-job/*"
                        updated_arns.append(arn)
                        updated_arns.append(arn_1)
                    statement["Condition"]["ArnLike"]["aws:SourceArn"] = updated_arns
            
    return policy
