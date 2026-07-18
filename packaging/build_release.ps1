param(
    [string]$PythonExecutable = "python",
    [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$buildDirectory = Join-Path $projectRoot "build\pyinstaller"
$distDirectory = Join-Path $projectRoot "dist"
$releaseDirectory = Join-Path $projectRoot "release"
$artifactName = "NestedExtractionAssistant-v$Version-win64"
$appName = ([char[]](0x5D4C, 0x5957, 0x89E3, 0x538B, 0x52A9, 0x624B)) -join ""
$localizedExecutableName = "$appName.exe"

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

foreach ($requiredFile in @("app.py", "README.md", "NOTICE.md", "LICENSE")) {
    $requiredPath = Join-Path $projectRoot $requiredFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required release file is missing: $requiredPath"
    }
}

& $PythonExecutable -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed for: $PythonExecutable"
}

$tkPaths = & $PythonExecutable -c "import tkinter; root = tkinter.Tk(); root.withdraw(); print(root.tk.eval('set tcl_library')); print(root.tk.eval('set tk_library')); root.destroy()"
if ($LASTEXITCODE -ne 0 -or $tkPaths.Count -ne 2) {
    throw "Unable to locate the Tcl/Tk script libraries."
}
$tclLibrary = [IO.Path]::GetFullPath($tkPaths[0])
$tkLibrary = [IO.Path]::GetFullPath($tkPaths[1])
if (-not (Test-Path -LiteralPath (Join-Path $tclLibrary "init.tcl") -PathType Leaf)) {
    throw "The Tcl library does not contain init.tcl: $tclLibrary"
}
if (-not (Test-Path -LiteralPath (Join-Path $tkLibrary "tk.tcl") -PathType Leaf)) {
    throw "The Tk library does not contain tk.tcl: $tkLibrary"
}

Reset-ProjectDirectory -Path $buildDirectory
Reset-ProjectDirectory -Path $distDirectory
Reset-ProjectDirectory -Path $releaseDirectory

$assetsArgument = (Join-Path $projectRoot "assets") + ";assets"
& $PythonExecutable -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name "NestedExtractionAssistant" `
    --icon (Join-Path $projectRoot "assets\app.ico") `
    --add-data $assetsArgument `
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

$libraryDirectory = Join-Path $builtDirectory "lib"
New-Item -ItemType Directory -Path $libraryDirectory | Out-Null
Copy-Item -LiteralPath $tclLibrary -Destination $libraryDirectory -Recurse
Copy-Item -LiteralPath $tkLibrary -Destination $libraryDirectory -Recurse

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
Copy-Item -LiteralPath (Join-Path $projectRoot "packaging\USER_GUIDE.zh-CN.txt") -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "NOTICE.md") -Destination $packageDirectory
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $packageDirectory

$releaseZip = Join-Path $releaseDirectory "$artifactName.zip"
Compress-Archive -Path (Join-Path $packageDirectory "*") -DestinationPath $releaseZip -CompressionLevel Optimal

$checksums = @($releaseZip) | ForEach-Object {
    $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_
    "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($_))"
}
$checksums | Set-Content -Encoding utf8 (Join-Path $releaseDirectory "SHA256SUMS.txt")

Write-Host "Release files created:"
Get-Item -LiteralPath $releaseZip, (Join-Path $releaseDirectory "SHA256SUMS.txt") |
    Select-Object Name, Length, LastWriteTime |
    Format-Table -AutoSize
