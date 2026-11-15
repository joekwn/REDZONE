# REDZONE - build the upload package on Windows.
# Run from C:\Users\ASUS\REDZONE with the website server STOPPED:
#     powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1
# Creates redzone_release.zip with the code and the database only
# (no raw CSVs, no Parquet chunks, no test emails).
$ErrorActionPreference = "Stop"
$out = "redzone_release.zip"
$tmp = Join-Path $env:TEMP "redzone_release"
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
New-Item -ItemType Directory -Path "$tmp\data" | Out-Null
Copy-Item -Recurse scripts, webapp, deploy -Destination $tmp
Copy-Item -Recurse docs -Destination $tmp
Copy-Item requirements.txt -Destination $tmp
Copy-Item data\redzone.duckdb -Destination "$tmp\data"
if (Test-Path data\secret.key) { Copy-Item data\secret.key -Destination "$tmp\data" }
Get-ChildItem -Recurse -Directory -Filter __pycache__ $tmp | Remove-Item -Recurse -Force
if (Test-Path $out) { Remove-Item $out }
Compress-Archive -Path "$tmp\*" -DestinationPath $out
Remove-Item -Recurse -Force $tmp
$mb = [math]::Round((Get-Item $out).Length / 1MB)
Write-Host "Created $out ($mb MB). Upload it to the server with scp (see docs\DEPLOY.md)."
