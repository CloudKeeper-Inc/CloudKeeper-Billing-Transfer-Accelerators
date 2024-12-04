import boto3
from utils import *
from terraform import *

org_client = boto3.client('organizations', region_name = "us-east-1")
account_ids = get_account_list(org_client)

if __name__ == "__main__":

    if check_delegated_admin_for_aws_cloudtrail(org_client):
        master_account = check_delegated_admin_for_aws_cloudtrail(org_client)

    else:
        master_account = get_aws_cloudtrail_admin_account(org_client)

    member_accounts = account_ids
    member_accounts.remove(master_account)
    print("\nMember Accounts:", member_accounts)

    session = boto3.Session(profile_name=str(master_account))
    
    for region in list_enabled_regions():
        cloudtrail_client = session.client('cloudtrail', region_name=region)

        org_trail_name, org_trail_arn, home_region, SnsTopicName, CloudWatchLogsLogGroupArn, KmsKeyId = is_organization_trail_enabled(cloudtrail_client, region)

        if org_trail_name:

            create_import_block(org_trail_name, f'import/main.tf')
            create_tfvars_file(f'import/terraform.{master_account}.{home_region}.tfvars', master_account, home_region)
            terraform_apply(master_account, home_region, 'import/.terraform/terraform.tfstate')

            #Master Account
            update_terraform_file('masterAccount/cloudtrail.tf')
            remove_empty_attributes('masterAccount/cloudtrail.tf', 'masterAccount/cloudtrail.tf')
            create_tfvars_file(f'masterAccount/terraform.{master_account}.{home_region}.tfvars', master_account, home_region, member_accounts, SnsTopicName, CloudWatchLogsLogGroupArn, KmsKeyId)
            outputs = terraform_apply(master_account, home_region, 'masterAccount/.terraform/terraform.tfstate')

            break

    #Member Accounts
    for account in member_accounts:
        update_terraform_file('memberAccount/cloudtrail.tf', outputs)
        remove_empty_attributes('memberAccount/cloudtrail.tf', 'memberAccount/cloudtrail.tf')
        create_tfvars_file(f'memberAccount/terraform.{account}.{home_region}.tfvars', provider_region= home_region, CloudWatchLogsLogGroupArn=CloudWatchLogsLogGroupArn, SnsTopicName=SnsTopicName)
        terraform_apply(account, home_region, 'memberAccount/.terraform/terraform.tfstate')
