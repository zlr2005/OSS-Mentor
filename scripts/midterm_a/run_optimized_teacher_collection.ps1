param([string]$ResumeDirectory, [switch]$IgnoreRollingHourBudget)
$ErrorActionPreference = 'Stop'
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $taskWorkspace
$collector = Join-Path $PSScriptRoot 'optimized_teacher_collection.py'
$plan = Join-Path $taskWorkspace 'config/midterm_datasets/teacher_round21_optimized_plan.json'
$collectorArgs = @('-u',$collector,'--plan',$plan)
if ($ResumeDirectory) { $collectorArgs += @('--resume',$ResumeDirectory) }
if ($IgnoreRollingHourBudget) { $collectorArgs += '--ignore-rolling-hour-budget' }
& (Get-Command python).Source @collectorArgs
