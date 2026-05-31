param(
    [Parameter(Mandatory=$true)]
    [string]$ArgocdServer,

    [Parameter(Mandatory=$true)]
    [string]$AdminPassword,

    [Parameter(Mandatory=$true)]
    [string]$GithubUser,

    [Parameter(Mandatory=$true)]
    [string]$GithubToken,

    [Parameter()]
    [string]$RepoUrl = "https://github.com/imdancin/reforger-funhouse.git",

    [Parameter()]
    [string]$RepoName = "reforger-funhouse"
)

Write-Host "Logging into ArgoCD at $ArgocdServer"
& .\argocd.exe login $ArgocdServer --username admin --password $AdminPassword --insecure

Write-Host "Adding private GitHub repository to ArgoCD: $RepoUrl"
& .\argocd.exe repo add $RepoUrl --username $GithubUser --password $GithubToken --name $RepoName --insecure

Write-Host "Private repository registered with ArgoCD. Verify with: .\argocd.exe repo list"