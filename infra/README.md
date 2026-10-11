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

## Set AWS profile and terraform init

```
  $env:AWS_PROFILE="drysg"
  aws sts get-caller-identity
  terraform init
```

## Build & deploy Lambdas
The Lambdas share one zip built from `services/` (run from the repo root, Python 3.12+):

```
python scripts/build_lambda.py
```

Then from `infra/`:

```
terraform plan
terraform apply
```

Re-run the build script whenever backend code changes; Terraform detects the new zip hash and updates the functions.

First deploy tip: `terraform apply -var ingest_schedule_enabled=false`, invoke once by hand, then apply again without the flag to turn on the 5-minute schedule:

```
aws lambda invoke --function-name drysg-dev-ingest --profile drysg out.json
aws dynamodb scan --table-name drysg-dev-AreaSnapshot --select COUNT --profile drysg
```
