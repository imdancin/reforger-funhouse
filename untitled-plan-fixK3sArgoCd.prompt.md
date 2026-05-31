## Plan: Fix K3s/ArgoCD bootstrap and app sync

TL;DR - The repo can provision infra, but the critical failure path is bootstrapping the EC2 node into K3s, installing ArgoCD, and allowing ArgoCD to sync the private GitHub app manifest.

**Steps**
1. Fix ArgoCD app destination URL in `compute.tf`.
2. Build private GitHub repo auth into the ArgoCD bootstrapping path or document `argocd repo add` clearly.
3. Ensure the host node name and local PV nodeAffinity align, and that `/opt/arma-server-data` exists.
4. Strengthen ArgoCD readiness waits in `compute.tf` before applying `root-arma-app`.
5. Update `README.md` with clear verification steps and current known limitations.

**Relevant files**
- `compute.tf`
- `cluster-manifests/templates/storage.yaml`
- `cluster-manifests/templates/deployment.yaml`
- `cluster-manifests/values-freedomfighters.yaml`
- `README.md`

**Verification**
- Run `terraform init -reconfigure`
- Run `terraform apply -var "instance_count=1" -auto-approve`
- Use SSM or cloud-init logs to confirm bootstrap completed
- Check:
  - `kubectl get nodes`
  - `kubectl get pods -n argocd`
  - `kubectl get applications -n argocd`
  - `kubectl get pv,pvc`
- Confirm `root-arma-app` sync/health and that the game workload enters `Running`