$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$releaseRoot = Join-Path $projectRoot "release"
$distRoot = Join-Path $projectRoot "dist"
$exeName = "DeepcoinScreen"
$releaseVersion = if ($env:DEEPCOIN_VERSION) { $env:DEEPCOIN_VERSION } else { "1.0.0" }

Set-Location $projectRoot
python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed" }

if (Test-Path $releaseRoot) { Remove-Item -LiteralPath $releaseRoot -Recurse -Force }
if (Test-Path $distRoot) { Remove-Item -LiteralPath $distRoot -Recurse -Force }
New-Item -ItemType Directory -Path $releaseRoot | Out-Null

python -m PyInstaller --noconfirm --clean --onefile --windowed --name $exeName --add-data "static;static" launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

Copy-Item -LiteralPath (Join-Path $distRoot ($exeName + ".exe")) -Destination $releaseRoot
$batSource = Join-Path $projectRoot "start_deepcoin.bat"
Copy-Item -LiteralPath $batSource -Destination (Join-Path $releaseRoot "start_deepcoin.bat")
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination (Join-Path $releaseRoot "README.md")
Set-Content -LiteralPath (Join-Path $releaseRoot "version.txt") -Value $releaseVersion -Encoding ascii

$zipPath = Join-Path $projectRoot "DeepcoinScreen-Windows.zip"
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Release directory: $releaseRoot"
Write-Host "ZIP package: $zipPath"
