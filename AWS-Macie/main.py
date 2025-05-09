import boto3
from utils import *

org_client = boto3.client("organizations", region_name="us-east-1")
account_client = boto3.client("account")
macie_client_1 = boto3.client("macie2", region_name = "us-east-1"))
macie_enabled_regions = []

account_ids, accId_email_map = get_account_list(org_client)

if __name__ == "__main__":
    master_account = check_delegated_admin_for_macie(org_client)
    
    if not master_account:
        master_account = get_aws_macie_admin_account(org_client)
    member_accounts = account_ids
    member_accounts.remove(master_account)
    
    print("\nMember Accounts:", member_accounts)
    print("-------------------------------------------------------------------------------------------------------------------------------------------")

    accId_email_map = [
        item for item in accId_email_map if item["AccountId"] != master_account
    ]
    
    session = boto3.Session(profile_name=str(master_account))

    for region in list_enabled_regions():
        try:
            macie_client = session.client("macie2", region_name=region)
            response = macie_client.get_macie_session()
            if response["status"] == "ENABLED":
                macie_enabled_regions.append(region)
                print(f"Macie Enabled in the region: " + region)
        except Exception as e:
            print(f"Macie is Disabled in the region: {region}")
    
    securityhub_publishClassificationFindings = False
    securityhub_publishPolicyFindings = False
    
    print("-------------------------------------------------------------------------------------------------------------------------------------------")
    for region in macie_enabled_regions:
        macie_client = session.client("macie2", region_name=region)
        all, accId_email_map = get_account_list(org_client)
        bucket_conf = macie_client.get_classification_export_configuration()
        response = macie_client.get_findings_publication_configuration()
        
        securityhub_publishClassificationFindings = response["securityHubConfiguration"]["publishClassificationFindings"]
        securityhub_publishPolicyFindings = response["securityHubConfiguration"]["publishPolicyFindings"]

        if bucket_conf["configuration"] != {}:
            print(
                "The Bucket Used to export sensitive find in Admin Account is: "
                + bucket_conf["configuration"]["s3Destination"]["bucketName"]
            )
            
            print(
                "The KMS ARN Used to export sensitive find in Admin Account is: "
                + bucket_conf["configuration"]["s3Destination"]["kmsKeyArn"]
            )
            key_id = get_key_id_from_arn(bucket_conf["configuration"]["s3Destination"]["kmsKeyArn"])
            kms_policy  = get_kms_policy(key_id,master_account,region)
            updated_kms_policy  = update_kms_key_policy(kms_policy, all)
            flag = update_kms_policy(key_id, updated_kms_policy,master_account,region)
            policy  = get_bucket_policy(bucket_conf["configuration"]["s3Destination"]["bucketName"],master_account,region)
            updated_policy = update_macie_bucket_policy(policy, all)
            if update_bucket_policy(bucket_conf["configuration"]["s3Destination"]["bucketName"], updated_policy, master_account,region):
                print("✅ Bucket policy updated successfully!")
                print("---------------------------------------------------------------------------------------------------------------------------------- \n")
            if flag == True:
                print("✅ KMS policy updated successfully!")
                print("---------------------------------------------------------------------------------------------------------------------------------- \n")

            else:
                print("❌ Unable to update KMS Policy")
                print("---------------------------------------------------------------------------------------------------------------------------------- \n")       
        if securityhub_publishPolicyFindings:
            print("Policy findings are being published to Security Hub for " + master_account)
        if securityhub_publishClassificationFindings:
            print("Sensitive data findings are being published to Security Hub for " + master_account)

    print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
    
    disable_organization_macie(master_account)

    for region in macie_enabled_regions:
        macie_client = session.client("macie2", region_name=region)
        
        for accou, ema in zip(member_accounts, accId_email_map):
            acc = accou
            email = ema["Email"]
            aws_create_member(acc, email, macie_client)

        create_invite(member_accounts, macie_client)
        policy = {}

        for accou, ema in zip(member_accounts, accId_email_map):
            acc = accou
            email = ema["Email"]

            session = boto3.Session(profile_name=str(acc))
            macie_client = session.client("macie2", region_name=region)
            
            bucket_conf = macie_client.get_classification_export_configuration()
            response = macie_client.get_findings_publication_configuration()
            if bucket_conf["configuration"] != {}:
                print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
                print(
                    "The Bucket Used to export sensitive find is "
                    + bucket_conf["configuration"]["s3Destination"]["bucketName"]
                )
                print(
                    "The KMS ARN Used to export sensitive find is "
                    + bucket_conf["configuration"]["s3Destination"]["kmsKeyArn"]
                )
            
            if securityhub_publishPolicyFindings:
                print("------------------------------------------------------------------------------------------------------------------------------------------- \n")
                print("Policy findings are being published to Security Hub for the Account :" + acc)
            if securityhub_publishClassificationFindings:
                print("Publishing sensitive data findings to Security Hub for the Account :" + acc)
            
            response = macie_client.list_invitations()
            InvitationId = ""
            if response["invitations"][0]["accountId"] == master_account:
                InvitationId = response["invitations"][0]["invitationId"]
            accept_invitation(
                response["invitations"][0]["accountId"], InvitationId, macie_client, acc
            )


    print("Alternate Setup For Macie is Sucessfully Completed") 
