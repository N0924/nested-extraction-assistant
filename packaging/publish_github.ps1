param(
    [string]$Repository = "N0924/nested-extraction-assistant",
    [string]$Version = "0.2.0",
    [string]$Proxy = "http://127.0.0.1:10808"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$tagName = "v$Version"
$appName = ([char[]](0x5D4C, 0x5957, 0x89E3, 0x538B, 0x52A9, 0x624B)) -join ""
$artifactName = "NestedExtractionAssistant-v$Version-win64"
$releaseZip = Join-Path $projectRoot "release\$artifactName.zip"
$checksums = Join-Path $projectRoot "release\SHA256SUMS.txt"
$releaseNotes = Join-Path $PSScriptRoot "release-notes-v$Version.md"
$expectedRemote = "https://github.com/$Repository.git"

foreach ($requiredFile in @($releaseZip, $checksums, $releaseNotes)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "A release file is missing. Rebuild before publishing: $requiredFile"
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
        throw "The project has uncommitted changes. Publishing stopped."
    }

    & gh auth status
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI authentication is invalid. Run gh auth login again."
    }

    & gh repo view $Repository --json nameWithOwner 1>$null 2>$null
    $repositoryExists = $LASTEXITCODE -eq 0

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

        $origin = & git -C $projectRoot remote get-url origin 2>$null
        if ($LASTEXITCODE -ne 0) {
            & git -C $projectRoot remote add origin $expectedRemote
            if ($LASTEXITCODE -ne 0) {
                throw "Adding the GitHub remote failed."
            }
        }
        elseif ($origin -ne $expectedRemote) {
            throw "The origin remote points elsewhere: $origin"
        }

        & git -C $projectRoot push --set-upstream origin main
        if ($LASTEXITCODE -ne 0) {
            throw "Pushing the main branch failed."
        }
    }

    & gh release view $tagName --repo $Repository 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) {
        throw "Release $tagName already exists and will not be overwritten."
    }

    & git -C $projectRoot rev-parse --verify "refs/tags/$tagName" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) {
        & git -C $projectRoot tag -a $tagName -m "$appName $tagName"
        if ($LASTEXITCODE -ne 0) {
            throw "Creating the local release tag failed."
        }
    }

    & git -C $projectRoot push origin $tagName
    if ($LASTEXITCODE -ne 0) {
        throw "Pushing the release tag failed."
    }

    & gh release create $tagName `
        $releaseZip `
        $checksums `
        --repo $Repository `
        --verify-tag `
        --title "$appName $tagName" `
        --notes-file $releaseNotes
    if ($LASTEXITCODE -ne 0) {
        throw "Creating the GitHub Release failed."
    }

    Write-Host "Published: https://github.com/$Repository"
    Write-Host "Release: https://github.com/$Repository/releases/tag/$tagName"
}
finally {
    $env:HTTP_PROXY = $oldHttpProxy
    $env:HTTPS_PROXY = $oldHttpsProxy
    $env:GIT_CONFIG_COUNT = $oldGitConfigCount
    $env:GIT_CONFIG_KEY_0 = $oldGitConfigKey
    $env:GIT_CONFIG_VALUE_0 = $oldGitConfigValue
}
