import boto3
import time
from utils import * 
from botocore.exceptions import ClientError

org_client = boto3.client('organizations', region_name = "us-east-1")

account_ids, accId_email_map = get_account_list(org_client)


if __name__ == "__main__":

    master_account = check_delegated_admin_for_aws_security_hub(org_client)

    if not master_account:
        master_account = get_aws_security_hub_admin_account(org_client)

    member_accounts = account_ids
    member_accounts.remove(master_account)
    accId_email_map = [item for item in accId_email_map if item['AccountId'] != master_account]


    session = boto3.Session(profile_name=str(master_account))

    aggregator_region_map = {}
    
    for region in get_regions_with_security_hub_enabled(session):
        if check_security_hub_organization(session, region):
            client = session.client('securityhub', region_name=region)
            aggregators = client.list_finding_aggregators()
            for aggregator in aggregators['FindingAggregators']:
                agg = client.get_finding_aggregator(
                    FindingAggregatorArn=aggregator['FindingAggregatorArn']
                )
                aggregator_region_map.setdefault(agg['FindingAggregatorArn'], []).append(region)

    extracted_data = {}

    for item in aggregator_region_map:
        home_region = item.split(':')[3]

        extracted_data[home_region] = {"policies": []}
        client = session.client('securityhub', region_name=home_region)

        org_conf = client.describe_organization_configuration()
        if org_conf['OrganizationConfiguration']['Status'] != 'ENABLED' or org_conf['OrganizationConfiguration']['ConfigurationType'] != 'CENTRAL':
            print('\nOrganization Configuration is not enabled for home region', home_region)
            continue

        conf_policies = client.list_configuration_policies()

        for policy in conf_policies['ConfigurationPolicySummaries']:
            desc_policy = client.get_configuration_policy(
                Identifier=policy['Id']
            )

            enabled_standards = []

            if desc_policy['ConfigurationPolicy']['SecurityHub']['ServiceEnabled']:
                for standard in desc_policy['ConfigurationPolicy']['SecurityHub']['EnabledStandardIdentifiers']:
                    enabled_standards.append(standard)

            associations = client.list_configuration_policy_associations(
                Filters={
                    'ConfigurationPolicyId': policy['Id']
                }
            )

            securityControls = desc_policy['ConfigurationPolicy']['SecurityHub']['SecurityControlsConfiguration']

            associated_accounts = []

            for association in associations['ConfigurationPolicyAssociationSummaries']:
                if association['TargetType'] == 'ACCOUNT' and association['AssociationStatus'] != 'FAILED':
                    associated_accounts.append(association['TargetId'])

            extracted_data[home_region]["policies"].append({"name": policy['Name'], "id": policy['Id'], "accounts": associated_accounts, "standards": enabled_standards, "securityControls": securityControls})

            for association in associations['ConfigurationPolicyAssociationSummaries']:
                if association['AssociationType'] == 'APPLIED':
                    if association['TargetType'] == 'ROOT':
                        TargetId = association['TargetId']
                        TargetType = 'RootId'
                    elif association['TargetType'] == 'ACCOUNT':
                        TargetId = association['TargetId']
                        TargetType = 'AccountId'
                    else:
                        TargetId = association['TargetId']
                        TargetType = 'OrganizationalUnitId'

                    disassociate_target = client.start_configuration_policy_disassociation(
                        Target={
                            TargetType: TargetId
                        },
                        ConfigurationPolicyIdentifier=policy['Id']
                    )
        
        print('\nExtracted Configuration Policies data in', home_region,'...')

        create_csv_from_dict(extracted_data, "policies.csv")
        print('\nCreated policies.csv from the extracted data...')

        for policy in conf_policies['ConfigurationPolicySummaries']:
            max_retries=5
            delay=5
            retries = 0
            while retries < max_retries:
                try:
                    delete_policy = client.delete_configuration_policy(
                        Identifier=policy['Id']
                    )
                    print(f"Policy {policy['Name']} deleted successfully.")
                    break 

                except ClientError as e:
                    print(f"Failed to delete policy {policy['Name']}: {e}")
                    retries += 1

                    if retries < max_retries:
                        print(f"Retrying in {delay} seconds... (Attempt {retries}/{max_retries})")
                        time.sleep(delay)
                    else:
                        print(f"Max retries reached for policy {policy['Name']}. Skipping.")

        remove_central_configuration = client.update_organization_configuration(
            AutoEnable=False,
            AutoEnableStandards='NONE',
            OrganizationConfiguration={
                'ConfigurationType':'LOCAL'
            }
        )
        print('\nRemoved Security Hub central configuration.')

    root_session = boto3.Session(profile_name=str("default"))

    for item in aggregator_region_map:
        home_region = item.split(':')[3]
        root_client = root_session.client('securityhub', region_name=home_region)
        
        response = root_client.disable_organization_admin_account(
            AdminAccountId=master_account
        )
        print('\nDisabled Organization admin account for', home_region)

    root_client = root_session.client('organizations', region_name="us-east-1")
    response = root_client.deregister_delegated_administrator(
        AccountId=master_account,
        ServicePrincipal='securityhub.amazonaws.com'
    )
    print('\nDeregistered Delegated Administrator for Security Hub.')

    disable_trusted_access = org_client.disable_aws_service_access(
        ServicePrincipal='securityhub.amazonaws.com'
    )
    print('\nDisabled AWS Organization service access for Security Hub.')

    for item in aggregator_region_map:
        home_region = item.split(':')[3]
        
        admin_session = boto3.Session(profile_name=str(master_account))
        admin_client = admin_session.client('securityhub', region_name=home_region)

        create_members = admin_client.create_members(
            AccountDetails=accId_email_map
        )

        invite_members = admin_client.invite_members(
            AccountIds=member_accounts
        )
        print('\n Invited all the member accounts to Security Hub.')

        for member in member_accounts:
            member_session = boto3.Session(profile_name=str(member))
            member_client = member_session.client('securityhub', region_name=home_region)

            list_invitations = member_client.list_invitations()

            InvitationId= ''

            for invite in list_invitations['Invitations']:
                if invite['AccountId'] == master_account:
                    InvitationId = invite['InvitationId']
                    break
            
            accept_invitation = member_client.accept_administrator_invitation(
                AdministratorId=master_account,
                InvitationId=InvitationId
            )
            print(f'\nAccepted {master_account}\'s invitation in account {member}.')
