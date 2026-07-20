param(
    [string]$PythonExecutable = "python",
    [string]$Version = "0.2.1"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$buildDirectory = Join-Path $projectRoot "build\pyinstaller"
$distDirectory = Join-Path $projectRoot "dist"
$releaseDirectory = Join-Path $projectRoot "release"
$appName = ([char[]](0x5D4C, 0x5957, 0x89E3, 0x538B, 0x52A9, 0x624B)) -join ""
$englishLabel = ([char[]](0x82F1, 0x6587)) -join ""
$guideLabel = ([char[]](0x4F7F, 0x7528, 0x8BF4, 0x660E)) -join ""
$mainLabel = ([char[]](0x4E3B, 0x754C, 0x9762)) -join ""
$passwordPoolLabel = ([char[]](0x5BC6, 0x7801, 0x6C60)) -join ""
$settingsLabel = ([char[]](0x8BBE, 0x7F6E, 0x754C, 0x9762)) -join ""
$publicLabel = ([char[]](0x516C, 0x5F00, 0x7248)) -join ""
$artifactName = "$appName-v$Version-Windows-x64"
$localizedExecutableName = "$appName.exe"
$englishReadmeName = "README-$englishLabel.md"
$userGuideName = "$guideLabel.txt"
$readmeAssetNames = @(
    "$mainLabel.png",
    "$passwordPoolLabel.png",
    "$settingsLabel-$publicLabel.png"
)

function Reset-ProjectDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $projectPrefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $fullPath | Out-Null
}

$requiredFiles = @(
    "app.py",
    "README.md",
    $englishReadmeName,
    "packaging\$userGuideName",
    "NOTICE.md",
    "LICENSE"
) + @($readmeAssetNames | ForEach-Object { "assets\$_" })

foreach ($requiredFile in $requiredFiles) {
    $requiredPath = Join-Path $projectRoot $requiredFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required release file is missing: $requiredPath"
    }
}

& $PythonExecutable -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed for: $PythonExecutable"
}

Reset-ProjectDirectory -Path $buildDirectory
Reset-ProjectDirectory -Path $distDirectory
Reset-ProjectDirectory -Path $releaseDirectory

$iconAssetArgument = (Join-Path $projectRoot "assets\app.ico") + ";assets"
$logoAssetArgument = (Join-Path $projectRoot "assets\app-logo.png") + ";assets"
& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "NestedExtractionAssistant" `
    --icon (Join-Path $projectRoot "assets\app.ico") `
    --add-data $iconAssetArgument `
    --add-data $logoAssetArgument `
    --version-file (Join-Path $projectRoot "packaging\windows_version_info.txt") `
    --workpath $buildDirectory `
    --distpath $distDirectory `
    --specpath $buildDirectory `
    (Join-Path $projectRoot "app.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code: $LASTEXITCODE"
}

$builtDirectory = Join-Path $distDirectory "NestedExtractionAssistant"
$builtExecutable = Join-Path $builtDirectory "NestedExtractionAssistant.exe"
if (-not (Test-Path -LiteralPath $builtExecutable -PathType Leaf)) {
    throw "The packaged executable was not created: $builtExecutable"
}

$packageDirectory = Join-Path $releaseDirectory $artifactName
New-Item -ItemType Directory -Path $packageDirectory | Out-Null
Copy-Item -Path (Join-Path $builtDirectory "*") -Destination $packageDirectory -Recurse
$packagedExecutable = Join-Path $packageDirectory "NestedExtractionAssistant.exe"
$localizedExecutable = Join-Path $packageDirectory $localizedExecutableName
if (-not (Test-Path -LiteralPath $packagedExecutable -PathType Leaf)) {
    throw "The executable to rename is missing: $packagedExecutable"
}
Move-Item -LiteralPath $packagedExecutable -Destination $localizedExecutable
if (-not (Test-Path -LiteralPath $localizedExecutable -PathType Leaf)) {
    throw "The localized executable was not created: $localizedExecutable"
}
$userGuidePath = Join-Path $projectRoot "packaging\$userGuideName"
Copy-Item -LiteralPath $userGuidePath -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot $englishReadmeName) -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "NOTICE.md") -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $packageDirectory
$packageAssetsDirectory = Join-Path $packageDirectory "assets"
New-Item -ItemType Directory -Path $packageAssetsDirectory | Out-Null
foreach ($assetName in $readmeAssetNames) {
    $assetPath = Join-Path $projectRoot "assets\$assetName"
    Copy-Item -LiteralPath $assetPath -Destination $packageAssetsDirectory
}

$releaseZip = Join-Path $releaseDirectory "$artifactName.zip"
Compress-Archive `
    -Path (Join-Path $packageDirectory "*") `
    -DestinationPath $releaseZip `
    -CompressionLevel Optimal

Write-Host "Release files created:"
Get-Item -LiteralPath $releaseZip |
    Select-Object Name, Length, LastWriteTime |
    Format-Table -AutoSize
