import boto3
from utils import * 
from botocore.exceptions import ClientError

org_client = boto3.client('organizations', region_name = "us-east-1")
account_ids, accId_email_map = get_account_list(org_client)


if __name__ == "__main__":

    master_account = check_delegated_admin_for_aws_guard_duty(org_client)

    if not master_account:
        master_account = get_aws_guard_duty_admin_account(org_client)

    member_accounts = account_ids
    member_accounts.remove(master_account)

    session = boto3.Session(profile_name=str(master_account))

    detector_configurations = []
    region_member_email_list = []
    
    for region in get_regions_with_guard_duty_enabled(session):
        DetectorId = check_guard_duty_organization(session, region)

        if DetectorId:
            member_email_list, member_detector_list = list_guard_duty_members(session, region, DetectorId, accId_email_map)

            try:
                admin_session = boto3.Session(profile_name='default')
                client = admin_session.client('guardduty', region_name=region)
                detector = client.list_detectors()
                detector = detector['DetectorIds'][0]
                
                regional_admin_account = client.get_administrator_account(DetectorId=detector)
                
                response = client.list_organization_admin_accounts()
                
                if 'Administrator' in regional_admin_account:
                    regional_admin_account = regional_admin_account['Administrator']['AccountId']
                else:
                    regional_admin_account = master_account

                client.disable_organization_admin_account(AdminAccountId=regional_admin_account)
                print(f'Disabled Organization admin account in {region}')

            except ClientError as e:
                print(str(e))

        region_member_email_list.append({region: member_email_list})
        detector_configurations.append({region: member_detector_list})

    write_data_to_json_file(detector_configurations, 'extracted_data.json')

    deregister_delegated_administrator = org_client.deregister_delegated_administrator(
        AccountId=master_account,
        ServicePrincipal='guardduty.amazonaws.com'
    )

    disable_aws_service_access = org_client.disable_aws_service_access(ServicePrincipal='guardduty.amazonaws.com')

    for item in region_member_email_list:
        for key, value in item.items():
            client = session.client('guardduty', region_name=key)
            detectorId = client.list_detectors()
            detectorId = detectorId['DetectorIds'][0]

            invite_member(session, key, detectorId, value)
            print(f'Added Member Accounts in Administrator Account and Invitated the added accounts in {key}.')

    for member in member_accounts:
        member_session = boto3.Session(profile_name=str(member))
        for region in get_regions_with_guard_duty_enabled(member_session):
            member_client = member_session.client('guardduty', region_name=region)

            invitation_response = member_client.list_invitations()

            InvitationId = ''

            for invitation in invitation_response['Invitations']:
                if invitation['AccountId'] == master_account:
                    InvitationId = invitation['InvitationId']
                    break
            
            detector = member_client.list_detectors()
            detector = detector['DetectorIds'][0]

            if InvitationId:
                accept_response = member_client.accept_administrator_invitation(
                    DetectorId=detector,
                    AdministratorId=master_account,
                    InvitationId=InvitationId
                )
        print(f'Accepted the invitation in member account {member} in {key}.')

    for item in detector_configurations:
        for key, value in item.items():
            client = session.client('guardduty', region_name=key)
            detectorId = client.list_detectors()
            detectorId = detectorId['DetectorIds'][0]
            for member in value:
                try:
                    response = client.update_member_detectors(
                        DetectorId=detectorId,
                        AccountIds=[member['AccountId']],
                        Features=member['Features']
                    )
                except ClientError as e:
                    print(str(e))
        print(f'Applied Guard Duty Configurations to the member accounts in {key}.')
