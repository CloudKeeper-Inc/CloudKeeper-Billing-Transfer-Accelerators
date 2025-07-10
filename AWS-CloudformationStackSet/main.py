import boto3
from utils import *
from botocore.exceptions import ClientError

ORG_REGION = "us-east-1"
ADMIN_ROLE_NAME = "AWSCloudFormationStackSetAdministrationRole"
EXEC_ROLE_NAME = "AWSCloudFormationStackSetExecutionRole"
def get_stackset_account_ids(stack_set_name, region) :
    """
    Returns a list of account IDs for all stack instances in a given stack set.
    
    Args:
        stack_set_name (str): The name of the stack set
        region (str): AWS region (default: us-east-1)
    
    Returns:
        List[str]: List of account IDs that have stack instances
    
    Raises:
        ClientError: If there's an error accessing AWS CloudFormation
    """
    
    # Initialize CloudFormation client
    cf_client = boto3.client('cloudformation', region_name=region)
    
    account_ids = []
    next_token = None
    
    try:
        while True:
            # Prepare parameters for the API call
            params = {
                'StackSetName': stack_set_name
            }
            
            # Add pagination token if available
            if next_token:
                params['NextToken'] = next_token
            
            # List stack instances
            response = cf_client.list_stack_instances(**params)
            
            # Extract account IDs from the response
            for instance in response.get('Summaries', []):
                account_id = instance.get('Account')
                if account_id and account_id not in account_ids:
                    account_ids.append(account_id)
            
            # Check if there are more results
            next_token = response.get('NextToken')
            if not next_token:
                break
                
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'StackSetNotFoundException':
            print(f"Stack set '{stack_set_name}' not found")
            return []
        else:
            print(f"Error accessing stack set: {e}")
            raise
    
    return sorted(account_ids)

def get_delegated_admin_account_id(org_client):
    try:
        response = org_client.list_delegated_administrators(ServicePrincipal='member.org.stacksets.cloudformation.amazonaws.com')
        if response['DelegatedAdministrators']:
            return response['DelegatedAdministrators'][0]['Id']
        else:
            print("No delegated admin found for CloudFormation StackSets.")
            return None
    except ClientError as e:
        print(f"[ERROR] Unable to fetch delegated admin: {str(e)}")
        return None

def main():
    org_client = boto3.client("organizations", region_name=ORG_REGION)

    print("Fetching accounts in the organization...")
    account_ids = list_organization_accounts(org_client)
    account_ids.remove('726629337836')
    account_ids.remove('038964340427')
    account_ids.remove('700083210961')
    account_ids.remove('131881834230')
    account_ids.remove('636461996335')
    account_ids.remove('665499488902')
    account_ids.remove('822619186276')
    delegated_admin_id = get_delegated_admin_account_id(org_client)
    if not delegated_admin_id:
        print("Exiting: No delegated admin set up for CloudFormation StackSets.")
        return

    delegated_session = get_boto3_session_for_account(delegated_admin_id,'us-east-1')
    if not delegated_session:
        print(f"Failed to create session for delegated admin account {delegated_admin_id}")
        return

    print("Creating self-managed admin role in delegated admin account...")
    for account in account_ids:
        delegated_sion = get_boto3_session_for_account(account,'us-east-1')
        create_stackset_roles_admin(delegated_sion,admin_role_name=ADMIN_ROLE_NAME)
    for account in account_ids:
        delegated_sion = get_boto3_session_for_account(account,'us-east-1')       
        create_stackset_roles_child(delegated_sion,account_ids)
        

    stacksets_by_region = {}

    for region in list_regions():
        print(f"Discovering StackSets in {region}...")
        stacksets = list_service_managed_stacksets(delegated_session, region)
        stacksets_by_region[region] = stacksets
    print("Creating execution roles and deploying self-managed StackSets...")
    for region, stacksets in stacksets_by_region.items():
        for stackset_name in stacksets:
            details = extract_stackset_details_with_fallback(delegated_session, region, stackset_name)
            acct =  get_stackset_account_ids(stackset_name,'us-east-1')
            arn = details['ARN']
            res = extract_account_and_region(arn)
            account_id = res['account']
            regi = res['region']                
            child_session = get_boto3_session_for_account(account_id,regi)
            print("Making all the Stack Set for the account no. =>" + account_id)
            create_self_managed_stackset(
                child_session,
                stackset_name,
                details,
                account_ids=acct,
                regions=regi,
                admin_role_name=ADMIN_ROLE_NAME,
                exec_role_name=EXEC_ROLE_NAME)

    print("Deleting original service-managed StackSets...")
    for region, stacksets in stacksets_by_region.items():
        for stackset_name in stacksets:
            delete_stackset(delegated_session, region, stackset_name)
            print(f"Deleted service-managed StackSet {stackset_name} in {region}.")
    print("Disassociating delegated admin...")
    disassociate_delegated_admin(org_client, delegated_admin_id)
if __name__ == "__main__":
    main()
