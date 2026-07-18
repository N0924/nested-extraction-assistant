param(
    [string]$Repository = "N0924/nested-extraction-assistant",
    [string]$Proxy = "http://127.0.0.1:10808"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$expectedRemotes = @(
    "https://github.com/$Repository",
    "https://github.com/$Repository.git"
)

function Test-NativeSuccess {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $Command *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

Get-Command gh -ErrorAction Stop | Out-Null
Get-Command git -ErrorAction Stop | Out-Null

$oldHttpProxy = $env:HTTP_PROXY
$oldHttpsProxy = $env:HTTPS_PROXY
$oldGitConfigCount = $env:GIT_CONFIG_COUNT
$oldGitConfigKey = $env:GIT_CONFIG_KEY_0
$oldGitConfigValue = $env:GIT_CONFIG_VALUE_0

try {
    $env:HTTP_PROXY = $Proxy
    $env:HTTPS_PROXY = $Proxy
    $env:GIT_CONFIG_COUNT = "1"
    $env:GIT_CONFIG_KEY_0 = "safe.directory"
    $env:GIT_CONFIG_VALUE_0 = $projectRoot

    $currentBranch = & git -C $projectRoot branch --show-current
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne "main") {
        throw "The project must be committed on main. Current branch: $currentBranch"
    }

    $pendingChanges = & git -C $projectRoot status --porcelain
    if ($LASTEXITCODE -ne 0 -or $pendingChanges) {
        throw "The project has uncommitted changes. Source publishing stopped."
    }

    & gh auth status
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI authentication is invalid. Run gh auth login again."
    }

    $repositoryExists = Test-NativeSuccess {
        gh repo view $Repository --json nameWithOwner
    }

    if (-not $repositoryExists) {
        & gh repo create $Repository `
            --public `
            --source $projectRoot `
            --remote origin `
            --push `
            --description "Windows desktop tool for nested archive extraction"
        if ($LASTEXITCODE -ne 0) {
            throw "Creating the repository or pushing main failed."
        }
    }
    else {
        $visibility = & gh repo view $Repository --json visibility --jq .visibility
        if ($LASTEXITCODE -ne 0 -or $visibility -ne "PUBLIC") {
            throw "The target repository exists but is not public: $Repository"
        }

        $remotes = @(& git -C $projectRoot remote)
        if ($LASTEXITCODE -ne 0) {
            throw "Reading Git remotes failed."
        }
        if ($remotes -notcontains "origin") {
            & git -C $projectRoot remote add origin $expectedRemotes[1]
            if ($LASTEXITCODE -ne 0) {
                throw "Adding the GitHub remote failed."
            }
        }
        else {
            $origin = (& git -C $projectRoot remote get-url origin).TrimEnd("/")
            if ($LASTEXITCODE -ne 0 -or $expectedRemotes -notcontains $origin) {
                throw "The origin remote points elsewhere: $origin"
            }
        }

        & git -C $projectRoot push --set-upstream origin main
        if ($LASTEXITCODE -ne 0) {
            throw "Pushing the main branch failed."
        }
    }

    Write-Host "Source published: https://github.com/$Repository"
    Write-Host "No release files were uploaded."
}
finally {
    $env:HTTP_PROXY = $oldHttpProxy
    $env:HTTPS_PROXY = $oldHttpsProxy
    $env:GIT_CONFIG_COUNT = $oldGitConfigCount
    $env:GIT_CONFIG_KEY_0 = $oldGitConfigKey
    $env:GIT_CONFIG_VALUE_0 = $oldGitConfigValue
}
