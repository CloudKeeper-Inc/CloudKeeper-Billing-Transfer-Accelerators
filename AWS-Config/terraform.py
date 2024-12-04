import os
import subprocess
from utils import update_terraform_file

def terraform_apply(account_id, region, terraform_state_file, s3BucketName= None, snsTopicARN= None):
    # Check if the Terraform state file exists
    folder_path = os.path.dirname(terraform_state_file.split('/')[0] + '/')
    terraform_initialized = os.path.isfile(terraform_state_file)

    if not terraform_initialized:
        print("Terraform is not initialized. Initializing...")
        subprocess.run(["terraform", "init"], cwd=folder_path)

    print(f"Processing region {region} in account {account_id}...")

    workspace_name = f'{account_id}.{region}'

    # Construct the filename for the .tfvars file
    tfvars_file = f"terraform.{workspace_name}.tfvars"

    # Check if a .tfvars file exists for the current workspace
    if not os.path.isfile(folder_path + '/' + tfvars_file):
        print(f"Error: Terraform .tfvars file '{tfvars_file}' not found for workspace '{workspace_name}'")
        exit(1)

    # Check if the workspace exists
    try:
        result = subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        print(f"Workspace '{workspace_name}' found. Exiting...")
        return
    except subprocess.CalledProcessError as e:
        print(f"Error selecting workspace '{workspace_name}': {e.stderr}")
        result = subprocess.run(
            ["terraform", "workspace", "new", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        result = subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,  # This ensures that the output is returned as a string
            cwd=folder_path  # Set the working directory to the folder path
        )
        print(f"Workspace '{workspace_name}' found. Applying Terraform...")
    except FileNotFoundError as e:
        print(f"Terraform command not found: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    try:
        if terraform_state_file == 'masterAccount/.terraform/terraform.tfstate':
            # Run the Terraform plan command with the generate-config-out option
            plan_result = subprocess.run(
                ["terraform", "plan", "-input=false", f"-generate-config-out=generated.{workspace_name}.tf", "-var-file=" + tfvars_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                cwd=folder_path  # Set the working directory to the folder path
            )
            print("Terraform plan executed successfully.")
            print(plan_result.stdout)
        elif terraform_state_file == 'alreadyEnabled/.terraform/terraform.tfstate':
            print('Member Account')
            plan_command = [
                "terraform", "plan", "-input=false",
                f"-generate-config-out=generated.{workspace_name}.tf",
                "-var-file=" + tfvars_file
            ]
            plan_result = subprocess.run(plan_command, 
                                     stdout=subprocess.PIPE, 
                                     stderr=subprocess.PIPE, 
                                     check=True, text=True, cwd=folder_path)
            print("Terraform plan executed successfully.")
            print(plan_result.stdout)  # Print the output of the terraform plan command
            generated_tf_file = f"alreadyEnabled/generated.{workspace_name}.tf"
            update_terraform_file(generated_tf_file, s3BucketName, snsTopicARN, workspace_name.split('.')[0])

            # Check if the generated file exists before applying
            if not os.path.exists(generated_tf_file):
                print(f"Generated file {generated_tf_file} does not exist.")
                return

    except Exception as e:
        print(f"An unexpected error occurred: {e}")

    # Apply Terraform with the matching .tfvars file
    subprocess.run(["terraform", "apply", "-auto-approve","-var-file=" + tfvars_file], check=True, cwd=folder_path)
