module "gke_codemie_sa" {
  source  = "terraform-google-modules/kubernetes-engine/google//modules/workload-identity"
  version = "30.2.0"

  project_id = var.project_id

  # The role is assigned manually by EPAM Support Team
  # roles = ["roles/aiplatform.user"]

  use_existing_k8s_sa = true
  annotate_k8s_sa     = false
  name                = "codemie"
  k8s_sa_name         = "codemie"
  namespace           = "codemie"
}
