import boto3
from utils import * 
from terraform import *

org_client = boto3.client('organizations', region_name = "us-east-1")
account_ids = get_account_list(org_client)


if __name__ == "__main__":
    if check_delegated_admin_for_aws_config(org_client):
        master_account = check_delegated_admin_for_aws_config(org_client)

    else:
        master_account = get_aws_config_admin_account(org_client)

    member_accounts = account_ids
    member_accounts.remove(master_account)

    session = boto3.Session(profile_name=str(master_account))
    
    for region in get_regions_with_config_enabled(session):
        if file_exists(f'masterAccount/generated.{master_account}.{region}.tf'):
            uncomment_terraform_file(f'masterAccount/generated.{master_account}.{region}.tf')

        OrganizationConfigRuleList = []
        OrganizationConformancePacksList = []

        config_client = session.client('config', region_name=region)
        s3_client = session.client('s3')

        aggregator_regions = get_org_aggregator(config_client)

        config_rules = get_all_organization_config_rules(config_client)
        for rule in config_rules:
            OrganizationConfigRuleList.append(f"OrgConfigRule-{rule['OrganizationConfigRuleArn'].split('/')[-1]}")
        create_rules_import_blocks(OrganizationConfigRuleList, f'masterAccount/import_rules.{master_account}.{region}.tf')

        # conformance_packs = get_all_organization_conformance_packs(config_client)
        # for pack in conformance_packs:
        #     OrganizationConformancePacksList.append(f"OrgConfigRule-{rule['OrganizationConformancePackArn'].split('/')[-1]}")
        # create_packs_import_blocks(OrganizationConformancePacksList, f'masterAccount/import_conformance_packs.{master_account}.{region}.tf')

        delivery_channel = config_client.describe_delivery_channels(
            DeliveryChannelNames=[
                'default'
            ]
        )

        s3BucketName = delivery_channel["DeliveryChannels"][0]["s3BucketName"]

        bucketRegion = get_s3_bucket_region(s3BucketName, s3_client)

        if 'snsTopicARN' in delivery_channel["DeliveryChannels"][0]:
            snsTopicARN = delivery_channel["DeliveryChannels"][0]["snsTopicARN"]
            snsRegion = get_sns_region(snsTopicARN)
        else:
            snsTopicARN = ""
            snsRegion = ""

        if aggregator_regions:
            create_tfvars_file(f'masterAccount/terraform.{master_account}.{region}.tfvars', aggregator_regions, master_account, member_accounts, s3BucketName, snsTopicARN.split(':')[-1], region, bucketRegion, snsRegion, None)

            terraform_apply(master_account, region, 'masterAccount/.terraform/terraform.tfstate')

            for member_account in member_accounts:
                member_session = boto3.Session(profile_name=str(member_account))
                for rg in aggregator_regions:
                    if check_config_in_region(rg, member_session):
                        create_tfvars_file(filename= f'alreadyEnabled/terraform.{member_account}.{rg}.tfvars', provider_region= rg, bucket_name= s3BucketName, sns_topic= snsTopicARN, admin_account= master_account, aggregator_region= region)
                        terraform_apply(member_account, rg, 'alreadyEnabled/.terraform/terraform.tfstate', s3BucketName, snsTopicARN)
                        comment_terraform_file(f'alreadyEnabled/generated.{member_account}.{rg}.tf')
                # else:
                #     create_tfvars_file(filename= f'sourceAccounts/terraform.{master_account}.{region}.tfvars', provider_region= region, bucket_name = s3BucketName, sns_topic= snsTopicARN)
                #     terraform_apply(master_account, region, 'sourceAccounts/.terraform/terraform.tfstate')
                #     comment_terraform_file(f'sourceAccounts/generated.{master_account}.{region}.tf')

        comment_terraform_file(f'masterAccount/import_rules.{master_account}.{region}.tf')
        comment_terraform_file(f'masterAccount/generated.{master_account}.{region}.tf')
    
    print('\nExecution complete!\n')
