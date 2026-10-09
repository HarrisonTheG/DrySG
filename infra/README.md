Infrastructure as Code (IaC) managed via Terraform. Handles cloud provisioning and deployment configurations.

## Configure Local set up
Set up AWS CLI

```
aws configure sso  
```
Paste the SSO start URL and SSO Region from Access keys , name the profile `drysg`

## Login

```
aws sso login --profile drysg
```

## Set up terraform s3 bucket for terraform state (One time set up)
```
aws s3api create-bucket --bucket drysg-tfstate-637107289229 --region ap-southeast-1 --create-bucket-configuration LocationConstraint=ap-southeast-1

aws s3api put-bucket-versioning --bucket drysg-tfstate-637107289229 --versioning-configuration Status=Enabled
```
