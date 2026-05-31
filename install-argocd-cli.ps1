# Install the ArgoCD CLI on Windows
# Usage: powershell -ExecutionPolicy Bypass -File .\install-argocd-cli.ps1

$version = "v2.9.5"
$arch = "amd64"
$uri = "https://github.com/argoproj/argo-cd/releases/download/$version/argocd-windows-$arch.exe"
$outFile = Join-Path $PWD "argocd.exe"

Write-Host "Downloading ArgoCD CLI $version to $outFile"
Invoke-WebRequest -Uri $uri -OutFile $outFile
Write-Host "Downloaded argocd.exe. You can now run `./argocd.exe help`."
Write-Host "Once ArgoCD is reachable, use `./argocd.exe login <argocd-server> --username admin --password <password>` and then `./argocd.exe repo add https://github.com/imdancin/reforger-funhouse.git --username <github-user> --password <token>`"
