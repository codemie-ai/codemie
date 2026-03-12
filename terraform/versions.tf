terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "5.25.0"
    }
  }
  required_version = "1.5.4"
}

provider "google" {
  project = var.project_id
  region  = var.region
}
