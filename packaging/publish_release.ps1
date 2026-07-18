param(
    [string]$Repository = "N0924/nested-extraction-assistant",
    [string]$Version = "0.2.0",
    [string]$Proxy = "http://127.0.0.1:10808"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$tagName = "v$Version"
$appName = ([char[]](0x5D4C, 0x5957, 0x89E3, 0x538B, 0x52A9, 0x624B)) -join ""
$releaseNotesLabel = ([char[]](0x7248, 0x672C, 0x8BF4, 0x660E)) -join ""
$artifactName = "$appName-v$Version-Windows-x64"
$releaseZip = Join-Path $projectRoot "release\$artifactName.zip"
$checksums = Join-Path $projectRoot "release\SHA256SUMS.txt"
$releaseNotes = Join-Path $PSScriptRoot "$releaseNotesLabel-$tagName.md"
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
        throw "The project has uncommitted changes. Release publishing stopped."
    }

    & gh auth status
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI authentication is invalid. Run gh auth login again."
    }

    & gh repo view $Repository --json nameWithOwner 1>$null
    if ($LASTEXITCODE -ne 0) {
        throw "The source repository does not exist. Publish source first."
    }

    $origin = (& git -C $projectRoot remote get-url origin).TrimEnd("/")
    if ($LASTEXITCODE -ne 0 -or $expectedRemotes -notcontains $origin) {
        throw "The origin remote does not match the release repository: $origin"
    }

    $localCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
    $remoteMain = @(& git -C $projectRoot ls-remote origin refs/heads/main)
    if ($LASTEXITCODE -ne 0 -or $remoteMain.Count -ne 1) {
        throw "The remote main branch could not be verified."
    }
    $remoteCommit = ($remoteMain[0] -split "\s+")[0]
    if ($remoteCommit -ne $localCommit) {
        throw "Remote main is not up to date. Publish source before the release."
    }

    $releaseExists = Test-NativeSuccess {
        gh release view $tagName --repo $Repository
    }
    if ($releaseExists) {
        throw "Release $tagName already exists and will not be overwritten."
    }

    $localTag = & git -C $projectRoot tag --list $tagName
    if ($LASTEXITCODE -ne 0) {
        throw "Reading local Git tags failed."
    }
    if (-not $localTag) {
        & git -C $projectRoot tag -a $tagName -m "$appName $tagName"
        if ($LASTEXITCODE -ne 0) {
            throw "Creating the local release tag failed."
        }
    }

    $tagCommit = (& git -C $projectRoot rev-list -n 1 $tagName).Trim()
    if ($LASTEXITCODE -ne 0 -or $tagCommit -ne $localCommit) {
        throw "The local release tag does not point to the current commit."
    }

    $remoteTags = @(
        & git -C $projectRoot ls-remote --tags origin `
            "refs/tags/$tagName" `
            "refs/tags/$tagName^{}"
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Reading the remote release tag failed."
    }
    if ($remoteTags.Count -eq 0) {
        & git -C $projectRoot push origin $tagName
        if ($LASTEXITCODE -ne 0) {
            throw "Pushing the release tag failed."
        }
    }
    else {
        $peeledTag = $remoteTags | Where-Object { $_ -match "\^\{\}$" } | Select-Object -First 1
        $remoteTagLine = if ($peeledTag) { $peeledTag } else { $remoteTags[0] }
        $remoteTagCommit = ($remoteTagLine -split "\s+")[0]
        if ($remoteTagCommit -ne $localCommit) {
            throw "The remote release tag points to a different commit."
        }
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

    Write-Host "Release published: https://github.com/$Repository/releases/tag/$tagName"
}
finally {
    $env:HTTP_PROXY = $oldHttpProxy
    $env:HTTPS_PROXY = $oldHttpsProxy
    $env:GIT_CONFIG_COUNT = $oldGitConfigCount
    $env:GIT_CONFIG_KEY_0 = $oldGitConfigKey
    $env:GIT_CONFIG_VALUE_0 = $oldGitConfigValue
}
