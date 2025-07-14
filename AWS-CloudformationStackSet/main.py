import boto3
from utils import *
from botocore.exceptions import ClientError

# Configuration constants
ORG_REGION = "us-east-1"  # Primary region for AWS Organizations operations
ADMIN_ROLE_NAME = "AWSCloudFormationStackSetAdministrationRole"  # Role name for StackSet administration
EXEC_ROLE_NAME = "AWSCloudFormationStackSetExecutionRole"  # Role name for StackSet execution

def get_stackset_account_ids(stack_set_name, region):
    """
    Returns a list of account IDs for all stack instances in a given stack set.
    
    This function retrieves all accounts that currently have stack instances
    deployed from the specified StackSet, handling pagination automatically.
    
    Args:
        stack_set_name (str): The name of the stack set
        region (str): AWS region where the StackSet is located
    
    Returns:
        List[str]: Sorted list of unique account IDs that have stack instances
    
    Raises:
        ClientError: If there's an error accessing AWS CloudFormation
    """
    
    # Initialize CloudFormation client for the specified region
    cf_client = boto3.client('cloudformation', region_name=region)
    
    account_ids = []  # List to store unique account IDs
    next_token = None  # Token for pagination
    
    try:
        # Loop through all pages of stack instances
        while True:
            # Prepare parameters for the API call
            params = {
                'StackSetName': stack_set_name
            }
            
            # Add pagination token if available (for subsequent pages)
            if next_token:
                params['NextToken'] = next_token
            
            # List stack instances for the current page
            response = cf_client.list_stack_instances(**params)
            
            # Extract account IDs from the response
            for instance in response.get('Summaries', []):
                account_id = instance.get('Account')
                # Only add unique account IDs to avoid duplicates
                if account_id and account_id not in account_ids:
                    account_ids.append(account_id)
            
            # Check if there are more results to fetch
            next_token = response.get('NextToken')
            if not next_token:
                break  # No more pages, exit the loop
                
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'StackSetNotFoundException':
            print(f"Stack set '{stack_set_name}' not found")
            return []  # Return empty list if StackSet doesn't exist
        else:
            print(f"Error accessing stack set: {e}")
            raise  # Re-raise other errors
    
    return sorted(account_ids)  # Return sorted list for consistency

def get_delegated_admin_account_id(org_client):
    """
    Retrieves the account ID of the delegated administrator for CloudFormation StackSets.
    
    Args:
        org_client: Boto3 Organizations client
        
    Returns:
        str or None: Account ID of the delegated admin, or None if not found
    """
    try:
        # Query for delegated administrators for the CloudFormation StackSets service
        response = org_client.list_delegated_administrators(
            ServicePrincipal='member.org.stacksets.cloudformation.amazonaws.com'
        )
        
        # Return the first delegated administrator's ID if any exist
        if response['DelegatedAdministrators']:
            return response['DelegatedAdministrators'][0]['Id']
        else:
            print("No delegated admin found for CloudFormation StackSets.")
            return None
            
    except ClientError as e:
        print(f"[ERROR] Unable to fetch delegated admin: {str(e)}")
        return None

def main():
    """
    Main function that orchestrates the migration from service-managed to self-managed StackSets.
    
    This function performs the following steps:
    1. Fetches all accounts in the organization
    2. Identifies the delegated admin account
    3. Creates necessary IAM roles in all accounts
    4. Discovers all service-managed StackSets across regions
    5. Creates equivalent self-managed StackSets
    6. Deletes the original service-managed StackSets
    7. Disassociates the delegated admin
    """
    
    # Initialize Organizations client for the primary region
    org_client = boto3.client("organizations", region_name=ORG_REGION)

    print("Fetching accounts in the organization...")
    # Get list of all accounts in the AWS Organization
    account_ids = list_organization_accounts(org_client)
    # Find the delegated administrator account for StackSets
    delegated_admin_id = get_delegated_admin_account_id(org_client)
    if not delegated_admin_id:
        print("Exiting: No delegated admin set up for CloudFormation StackSets.")
        return

    # Create a session for the delegated admin account
    delegated_session = get_boto3_session_for_account(delegated_admin_id, 'us-east-1')
    if not delegated_session:
        print(f"Failed to create session for delegated admin account {delegated_admin_id}")
        return

    print("Creating self-managed admin role in delegated admin account...")
    # Create administration roles in all accounts
    for account in account_ids:
        # Create session for each account
        delegated_sion = get_boto3_session_for_account(account, 'us-east-1')
        # Create the StackSet administration role
        create_stackset_roles_admin(delegated_sion, admin_role_name=ADMIN_ROLE_NAME)
    
    # Create execution roles in all accounts
    for account in account_ids:
        # Create session for each account
        delegated_sion = get_boto3_session_for_account(account, 'us-east-1')
        # Create the StackSet execution role with trust to all account IDs
        create_stackset_roles_child(delegated_sion, account_ids)

    # Dictionary to store StackSets organized by region
    stacksets_by_region = {}

    # Discover all service-managed StackSets across all regions
    for region in list_regions():
        print(f"Discovering StackSets in {region}...")
        # Get list of service-managed StackSets in this region
        stacksets = list_service_managed_stacksets(delegated_session, region)
        stacksets_by_region[region] = stacksets
    
    print("Creating execution roles and deploying self-managed StackSets...")
    # Process each StackSet and create self-managed equivalent
    for region, stacksets in stacksets_by_region.items():
        for stackset_name in stacksets:
            print(stackset_name)
            
            # Extract detailed information about the StackSet
            details = extract_stackset_details_with_fallback(delegated_session, region, stackset_name)
            
            # Get all accounts that have instances of this StackSet
            acct = get_stackset_account_ids(stackset_name, region)
            
            # Extract account and region information from the StackSet ARN
            arn = details['ARN']
            res = extract_account_and_region(arn)
            account_id = res['account']
            regi = res['region']
            
            # Create session for the account that owns this StackSet
            child_session = get_boto3_session_for_account(account_id, regi)
            print("Making all the Stack Set for the account no. => " + account_id)
            
            # Create the self-managed StackSet with the same configuration
            create_self_managed_stackset(
                child_session,
                stackset_name,
                details,
                account_ids=acct,  # Deploy to the same accounts
                regions=regi,      # Deploy to the same regions
                admin_role_name=ADMIN_ROLE_NAME,
                exec_role_name=EXEC_ROLE_NAME
            )

    print("Deleting original service-managed StackSets...")
    # Clean up: Delete all the original service-managed StackSets
    for region, stacksets in stacksets_by_region.items():
        for stackset_name in stacksets:
            # Get StackSet details for deletion
            details_1 = extract_stackset_details_with_fallback(delegated_session, region, stackset_name)
            
            # Delete the service-managed StackSet
            delete_stackset(delegated_session, region, stackset_name, details_1)
            print(f"Deleted service-managed StackSet {stackset_name} in {region}.")
    
    print("Disassociating delegated admin...")
    # Final cleanup: Remove the delegated admin association
    disassociate_delegated_admin(org_client, delegated_admin_id)

# Entry point of the script
if __name__ == "__main__":
    main()