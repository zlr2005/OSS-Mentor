param([string]$ResumeDirectory, [string]$PlanPath, [switch]$IgnoreRollingHourBudget, [switch]$IgnoreRollingDayBudget, [switch]$ExtendedRun)
$ErrorActionPreference = 'Stop'
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
Set-Location -LiteralPath $taskWorkspace
$plan = Join-Path $taskWorkspace 'data/newcomer_discovery_20261022_action_v3/plan.json'
if ($ResumeDirectory -and -not $PlanPath) { $plan = Join-Path ([IO.Path]::GetFullPath($ResumeDirectory)) 'optimized_plan.json' }
if ($PlanPath) { $plan = [IO.Path]::GetFullPath($PlanPath) }
$collectorArgs = @('-u',(Join-Path $PSScriptRoot 'optimized_teacher_collection.py'),'--plan',$plan)
if ($ResumeDirectory) { $collectorArgs += @('--resume',$ResumeDirectory) }
if ($IgnoreRollingHourBudget) { $collectorArgs += '--ignore-rolling-hour-budget' }
if ($IgnoreRollingDayBudget) { $collectorArgs += '--ignore-rolling-day-budget' }
if ($ExtendedRun) { $collectorArgs += '--extended-run' }
& (Get-Command python).Source @collectorArgs
