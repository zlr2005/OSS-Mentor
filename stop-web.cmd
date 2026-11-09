@echo off
setlocal
set PORT=%~1
if "%PORT%"=="" set PORT=8765

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$port=[int]'%PORT%';" ^
  "$connection=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;" ^
  "if(-not $connection){Write-Host ('OSS-Mentor is not running on port '+$port); exit 1};" ^
  "$process=Get-Process -Id $connection.OwningProcess -ErrorAction Stop;" ^
  "if($process.ProcessName -notmatch '^python'){Write-Host ('Port '+$port+' is used by '+$process.ProcessName+', not the OSS-Mentor Python server.'); exit 2};" ^
  "Stop-Process -Id $process.Id -Force;" ^
  "Write-Host ('OSS-Mentor stopped on port '+$port)"

if errorlevel 1 pause
endlocal
