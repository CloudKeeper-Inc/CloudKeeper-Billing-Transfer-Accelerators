from botocore.exceptions import ClientError
import json


def get_account_list(client):
    accounts = client.list_accounts()
    account_ids = []
    accId_email_map = {}
    for account in accounts['Accounts']:
        account_ids.append(account['Id'])
        accId_email_map[account['Id']] = account['Email']
    return account_ids, accId_email_map


def get_aws_guard_duty_admin_account(org_client):
    
    try:
        response = org_client.describe_organization()
        
        if 'MasterAccountArn' in response['Organization']:
            master_account_id = response['Organization']['MasterAccountId']
            print(f"The AWS Guard duty Administrator Account ID is: {master_account_id}")
            return master_account_id
        else:
            print("AWS Guard duty is not enabled in this organization.")
            return None
    
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def check_delegated_admin_for_aws_guard_duty(org_client):
    """Check if the current account is a delegated admin for AWS guardduty."""

    try:
        response = org_client.list_delegated_administrators(ServicePrincipal='guardduty.amazonaws.com')
        if response['DelegatedAdministrators']:
            for admin in response['DelegatedAdministrators']:
                print(f"Delegated Admin Account ID for AWS Guard duty: {admin['Id']}")
                return admin['Id']
        else:
            print("No delegated administrator is set for AWS Guard duty.")
            return None
    except org_client.exceptions.AWSOrganizationsNotInUseException:
        print("AWS Organizations is not in use in this account.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    

def get_regions(session):
    ec2_client = session.client('ec2', region_name='us-east-1')
    regions = ec2_client.describe_regions()['Regions']
    return [region['RegionName'] for region in regions]


def check_guard_duty_in_region(region, session):
    client = session.client('guardduty', region_name=region)
    try:
        client = session.client('guardduty', region_name=region)
        
        response = client.list_detectors()
        
        if response['DetectorIds']:
            detector_id = response['DetectorIds'][0]
            detector_status = client.get_detector(DetectorId=detector_id)['Status']
            return True
        else:
            return False

    except ClientError as e:
        print('Error: ',str(e))


def get_regions_with_guard_duty_enabled(session):
    regions = get_regions(session)
    enabled_regions = []

    for region in regions:
        if check_guard_duty_in_region(region, session):
            enabled_regions.append(region)
            
    return enabled_regions


def check_guard_duty_organization(session, region="us-east-1"):
    try:
        client = session.client('guardduty', region_name=region)

        detectors = client.list_detectors()

        if not detectors['DetectorIds']:
            return False

        detector_id = detectors['DetectorIds'][0]

        return detector_id

    except client.exceptions.BadRequestException as e:
        print(str(e))
        print(f'Guard Duty not enabled for your organization in {region}.')
        return False

    except client.exceptions.InternalServerErrorException as e:
        print(str(e))
        return False


def list_guard_duty_members(session, region, DetectorId, accId_email_map):
    try:
        client = session.client('guardduty', region_name=region)

        response = client.list_members(DetectorId=DetectorId)

        member_email_list = []

        for member in response['Members']:
            if member['RelationshipStatus'] == 'Enabled':
                member_email_list.append({'AccountId': member['AccountId'], 'Email': accId_email_map[member['AccountId']]})

        response = client.get_member_detectors(
            DetectorId=DetectorId,
            AccountIds=[member['AccountId'] for member in member_email_list]
        )

        formatted_list = format_member_data(response)

        return member_email_list, formatted_list

    except client.exceptions.BadRequestException as e:
        print(str(e))


def invite_member(session, region, detectorId, member_email_list):
    client = session.client('guardduty', region_name=region)
    try:
        create_response = client.create_members(
            DetectorId=detectorId,
            AccountDetails= member_email_list
        )

    except ClientError as e:
        print(f"Failed to create members: {e}")
        return

    try:
        invite_response = client.invite_members(
            DetectorId=detectorId,
            AccountIds=[member['AccountId'] for member in member_email_list]
        )
    except ClientError as e:
        print(f"Failed to invite members: {e}")
        return


def format_member_data(input_data):
    formatted_list = []

    for member in input_data.get("MemberDataSourceConfigurations", []):
        account_id = member.get("AccountId")
        features = []

        for feature in member.get("Features", []):
            if feature.get("Status") == "DISABLED" or feature.get("Name") == "DNS_LOGS" or feature.get("Name") == "FLOW_LOGS" or feature.get("Name") == "CLOUD_TRAIL":
                continue
            formatted_feature = {
                "Name": feature.get("Name"),
                "Status": feature.get("Status")
            }

            if "AdditionalConfiguration" in feature:
                formatted_feature["AdditionalConfiguration"] = [
                    {
                        "Name": config.get("Name"),
                        "Status": config.get("Status")
                    }
                    for config in feature["AdditionalConfiguration"]
                ]

            features.append(formatted_feature)

        formatted_list.append({
            "AccountId": account_id,
            "Features": features
        })

    return formatted_list


def write_data_to_json_file(data, file_path):
    try:
        with open(file_path, 'w') as file:
            json.dump(data, file, indent=4)  
        print(f"Extracted Data successfully written to {file_path}")
    except Exception as e:
        print(f"An error occurred: {e}")
