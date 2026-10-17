# Remote state in S3 with native locking. The bucket name is supplied at init
# time (see ../../backend.hcl.example) so it is not hard-coded here:
#   terraform init -backend-config=../../backend.hcl
terraform {
  backend "s3" {
    key          = "secure-event-driven-orders-aws/prod/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
