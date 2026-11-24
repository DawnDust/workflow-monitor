$ErrorActionPreference = 'Stop'
$ask = '{"decision":"ask"}'

function Get-Prop($obj, [string]$name) {
    if ($null -eq $obj) { return $null }
    $prop = $obj.PSObject.Properties[$name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Find-WorkflowProjectRoot([object]$payload) {
    $candidates = New-Object System.Collections.Generic.List[string]
    $cwd = Get-Prop $payload 'cwd'
    if ($cwd -is [string] -and -not [string]::IsNullOrWhiteSpace($cwd)) {
        $candidates.Add($cwd)
    }
    $workspacePaths = Get-Prop $payload 'workspacePaths'
    if ($workspacePaths -is [System.Collections.IEnumerable] -and -not ($workspacePaths -is [string])) {
        foreach ($item in $workspacePaths) {
            if ($item -is [string] -and -not [string]::IsNullOrWhiteSpace($item)) {
                $candidates.Add($item)
            }
        }
    }
    foreach ($raw in $candidates) {
        try {
            $dir = New-Object System.IO.DirectoryInfo ([System.IO.Path]::GetFullPath($raw))
        } catch {
            continue
        }
        while ($null -ne $dir) {
            $primary = Join-Path $dir.FullName '.workflow-monitor\workflow.json'
            $legacy = Join-Path $dir.FullName '.codex\project-maintenance-workflow.json'
            if ((Test-Path -LiteralPath $primary -PathType Leaf) -or (Test-Path -LiteralPath $legacy -PathType Leaf)) {
                return $dir.FullName
            }
            $dir = $dir.Parent
        }
    }
    return $null
}

function Test-ProtectedRelative([string]$relative) {
    $norm = $relative.Replace('\', '/').Trim().ToLowerInvariant()
    $list = @($norm)
    if ($norm -match '^\.worktrees/[^/]+/(.+)$') {
        $list += $Matches[1]
    }
    foreach ($cand in $list) {
        if ($cand -eq 'maintenance/events.jsonl') { return $true }
        if ($cand.StartsWith('.git/workflow-monitor/')) { return $true }
        if ($cand.StartsWith('.project_hooks/') -and ($cand.Contains('.sqlite3') -or $cand.StartsWith('.project_hooks/active-task'))) {
            return $true
        }
    }
    return $false
}

try {
    $rawInput = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($rawInput)) {
        Write-Output $ask
        exit 0
    }
    $payload = $rawInput | ConvertFrom-Json
    if ($null -eq $payload) {
        Write-Output $ask
        exit 0
    }
    $projectRoot = Find-WorkflowProjectRoot $payload
    if ($null -eq $projectRoot) {
        Write-Output $ask
        exit 0
    }
    $toolCall = Get-Prop $payload 'toolCall'
    $name = [string](Get-Prop $toolCall 'name')
    $toolArgs = Get-Prop $toolCall 'args'
    if ($null -eq $toolArgs) {
        Write-Output $ask
        exit 0
    }

    if ($name -eq 'write_file' -or $name -eq 'edit_file') {
        $rawPath = Get-Prop $toolArgs 'Path'
        if (-not ($rawPath -is [string]) -or [string]::IsNullOrWhiteSpace($rawPath)) {
            $rawPath = Get-Prop $toolArgs 'path'
        }
        if (-not ($rawPath -is [string]) -or [string]::IsNullOrWhiteSpace($rawPath)) {
            Write-Output $ask
            exit 0
        }
        $cwd = Get-Prop $payload 'cwd'
        $baseDir = if ($cwd -is [string] -and -not [string]::IsNullOrWhiteSpace($cwd)) { $cwd } else { $projectRoot }
        $targetFull = if ([System.IO.Path]::IsPathRooted($rawPath)) {
            [System.IO.Path]::GetFullPath($rawPath)
        } else {
            [System.IO.Path]::GetFullPath([System.IO.Path]::Combine([string]$baseDir, $rawPath))
        }
        $rootFull = [System.IO.Path]::GetFullPath($projectRoot).Replace('\', '/').TrimEnd('/')
        $targetNorm = $targetFull.Replace('\', '/').TrimEnd('/')
        $rootPrefix = $rootFull + '/'
        $rel = $null
        if ($targetNorm.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = $targetNorm.Substring($rootPrefix.Length)
        } elseif ($targetNorm.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = ''
        } else {
            Write-Output $ask
            exit 0
        }
        if (Test-ProtectedRelative $rel) {
            Write-Output '{"decision":"deny","reason":"\u7981\u6b62\u76f4\u63a5\u4fee\u6539\u7ef4\u62a4\u4e8b\u4ef6\u65e5\u5fd7\u3001\u6d3b\u52a8\u72b6\u6001\u6216 SQLite \u6570\u636e\u5e93\uff1b\u8bf7\u4f7f\u7528 workflow-monitor.exe \u751f\u547d\u5468\u671f\u547d\u4ee4\u3002"}'
            exit 0
        }
        Write-Output '{"decision":"allow"}'
        exit 0
    }

    if ($name -eq 'run_command') {
        $cmdLine = Get-Prop $toolArgs 'CommandLine'
        if ($null -eq $cmdLine) { $cmdLine = Get-Prop $toolArgs 'command' }
        $command = ([string]$cmdLine).Trim()
        if ([string]::IsNullOrWhiteSpace($command)) {
            Write-Output $ask
            exit 0
        }
        $wfRe = '\bworkflow-monitor(?:\.exe)?\b'
        $targetRe = '(?:maintenance[\\/]events\.jsonl|\.project_hooks[\\/]active-task(?:\.json)?|\.project_hooks[\\/]maintenance\.sqlite3(?:-wal|-shm)?|\.git[\\/]workflow-monitor[\\/]registry\.sqlite3(?:-wal|-shm)?)'
        $opRe = '(?:\b(?:rm|rmdir|del|erase|remove-item|ri|clear-content|set-content|out-file|truncate|sqlite3)\b|>>?)'
        $highRiskRe = '(?:\bgit\s+(?:push|merge)\b|\bgh\s+pr\s+merge\b|\btask\s+abandon\b|\bdb\s+rebuild\b)'
        if ((-not ($command -match $wfRe)) -and ($command -match $targetRe) -and ($command -match $opRe)) {
            Write-Output '{"decision":"deny","reason":"\u7981\u6b62\u901a\u8fc7\u7ec8\u7aef\u547d\u4ee4\u76f4\u63a5\u5220\u9664\u6d3b\u52a8\u72b6\u6001\u6216\u6539\u5199\u4e8b\u4ef6\u65e5\u5fd7/SQLite\uff1b\u8bf7\u4f7f\u7528 workflow-monitor.exe \u547d\u4ee4\u3002"}'
            exit 0
        }
        if ($command -match $highRiskRe) {
            Write-Output '{"decision":"force_ask","reason":"\u8be5\u547d\u4ee4\u6d89\u53ca\u5916\u90e8\u4ea4\u4ed8\u6216\u9ad8\u98ce\u9669\u7ef4\u62a4\u64cd\u4f5c\uff0c\u6309 Workflow Monitor \u89c4\u8303\u5fc5\u987b\u83b7\u5f97\u7528\u6237\u660e\u786e\u786e\u8ba4\u3002"}'
            exit 0
        }
        Write-Output $ask
        exit 0
    }

    Write-Output $ask
    exit 0
} catch {
    Write-Output $ask
    exit 0
}
