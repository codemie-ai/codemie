# CodeMie Resources in GCP

CodeMie resources are the resources that are used to deploy the CodeMie application.

<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | 1.5.4 |
| <a name="requirement_google"></a> [google](#requirement\_google) | 5.25.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
|------|--------|---------|
| <a name="module_gke_codemie_sa"></a> [gke\_codemie\_sa](#module\_gke\_codemie\_sa) | terraform-google-modules/kubernetes-engine/google//modules/workload-identity | 30.2.0 |

## Resources

No resources.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | The project ID to host the cluster in | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | The region to host the cluster in | `string` | `"europe-west3"` | no |

## Outputs

No outputs.
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
