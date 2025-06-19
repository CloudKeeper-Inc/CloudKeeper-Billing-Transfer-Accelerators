# Multi-Account AWS RAM Replication

## Overview
This project automates the replication of an AWS Organizations RAM into a multi-account RAM setup. It ensures that all the member accounts are centrally collected in the master account, maintaining compliance and visibility across the AWS organization.

## Features
- Detects if AWS Organizations RAM is enabled.
- Replicates the organization-level RAM setup in a multi-account structure (Has some limitations with the setup).
- Replicates the sensitive data export to a single s3 bucket RAM.

## Prerequisites

Before you begin the migration process, ensure you have the following prerequisites in place:

1. **Python and Terraform**:
   - Ensure that you have Python and Terraform installed and properly configured on your system.
   - [Install Python](https://www.python.org/downloads/)

2. **AWS CLI**:
   - Install AWS CLI if not already installed. Follow the [AWS CLI installation guide](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html).
   - Configure the AWS CLI with your AWS credentials.

3. **Configuring AWS CLI**:
   - Ensure that you have AWS secret keys and access keys inside the credentials file with the profile name as the account number (including root management account). The default profile should have access keys of your organization's root account. Your `~/.aws/credentials` file should look like this:
     ```
     #root management account
     [default]  
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
    
     #root management account
     [123456789123]  
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     
     [789456123789]
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     
     [456123789456]
     aws_access_key_id = your_aws_access_key_id
     aws_secret_access_key = your_aws_secret_access_key
     ```

## Installation
1. Clone the repository:
   ```sh
   git clone https://github.com/CloudKeeper-Inc/CloudKeeper-Billing-Transfer-Accelerators.git
   cd AWS-RAM
   ```
2. Install dependencies:
   ```sh
   pip install boto3
   ```

## Usage
Run the script to initiate the multi-account RAM setup:
```sh
python3 main.py
```

## How It Works
1. **Checks AWS Organizations RAM:**
   - Fetches details of the existing organization RAM.
2. **Copy the Resource Share's info**
   - Creates a new resource share and deletes the old one 
   - Associate the principals to the resource share and also accepts the invite from each account 

## Limitations
### Some Services can only be shared by the AWS Organization this project omits those services if they are present in the resource share
1. **bedrock:CustomModel**
2. **billing:billingview**
3. **outposts:Outpost**
4. **ec2:LocalGatewayRouteTable**
5. **s3-outposts:Outpost**
6. **resource-explorer-2:View**
7. **servicecatalog:Applications**
8. **servicecatalog:AttributeGroups**
9. **ec2:CoipPool**
10. **ec2:Subnet**
11. **ec2:SecurityGroup**



## Troubleshooting
- **Profile Not Found Error:** Ensure the AWS profile is correctly set up:
  ```sh
  aws configure list-profiles
  ```
- **Resource Share not associated:** If this happens run the script again with the principal list and the arn of the of the principal in the princiapl map   

## License
MIT License

## Author
[**Priyansh Pathak**](https://github.com/PriyanshPathak2002)
