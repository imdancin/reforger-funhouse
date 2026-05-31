param(
    [Parameter(Mandatory=$false)]
    [int]$LocalPort = 8080,

    [Parameter(Mandatory=$false)]
    [string]$Namespace = "argocd"
)

Write-Host "Starting port-forward for ArgoCD server on localhost:$LocalPort -> argocd-server.$Namespace:443"
Write-Host "Use Ctrl+C to stop the forwarding."

kubectl port-forward svc/argocd-server -n $Namespace $LocalPort:443
