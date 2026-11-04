param([string]$Python='python')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -m PyInstaller --noconfirm --clean MemberPay.spec
if ($LASTEXITCODE -ne 0) {throw 'MemberPay build failed'}
& $Python -m PyInstaller --noconfirm --clean MemberPaySetup.spec
if ($LASTEXITCODE -ne 0) {throw 'Installer build failed'}
Write-Host 'Installer: dist\MemberPaySetup.exe'
