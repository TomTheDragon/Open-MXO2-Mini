<#
  MXO2 register capture helper for Windows 7 (PowerShell 2.0 compatible).

  Finds the Matrox MXO2 card (PCI 102b:8350), reads its 4 KiB register block (BAR0) with
  32-bit reads through RW-Everything's command line, checks that the data really is the
  Gaia register block, and walks you through idle / preview / capture snapshots.

  Output: a folder mxo2-dumps\<date-time>\ next to this script. Copy the whole folder to Linux
  (re/notes/win/) and run:  python3 -I re/tools/dumpdiff.py re/notes/win/<folder>

  Start it with mxo2-capture.cmd (double-click). It asks for administrator rights.
  Options:  -RwPath C:\path\Rw.exe   -Bar0 0xFAFFE000   -Chunk 128
#>
param(
    [string]$RwPath = '',
    [string]$Bar0 = '',
    [int]$Chunk = 128
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RegBytes = 4096

# ---------------------------------------------------------------- admin rights
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Asking for administrator rights...'
    $argList = '-NoProfile -ExecutionPolicy Bypass -File "' + $MyInvocation.MyCommand.Definition + '"'
    if ($RwPath) { $argList += ' -RwPath "' + $RwPath + '"' }
    if ($Bar0)   { $argList += ' -Bar0 ' + $Bar0 }
    $argList += ' -Chunk ' + $Chunk
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

# ---------------------------------------------------------------- output folder + log
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$OutDir = Join-Path $ScriptDir (Join-Path 'mxo2-dumps' $stamp)
$RawDir = Join-Path $OutDir 'raw'
New-Item -ItemType Directory -Path $RawDir -Force | Out-Null
$LogFile = Join-Path $OutDir 'session.txt'

function Log([string]$msg, [string]$color = '') {
    $line = (Get-Date -Format 'HH:mm:ss') + '  ' + $msg
    Add-Content -Path $LogFile -Value $line
    if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
}

function Fail([string]$msg) {
    Log ('ERROR: ' + $msg) 'Red'
    Write-Host ''
    Write-Host ('Everything so far is in ' + $OutDir) -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

function Hex([uint64]$v, [int]$digits = 8) { return ('0x{0:X' + $digits + '}') -f $v }

Log ('MXO2 capture helper, output folder ' + $OutDir) 'Cyan'
Log ('Windows ' + [Environment]::OSVersion.VersionString + ', CPU arch ' + $env:PROCESSOR_ARCHITECTURE + ', PowerShell ' + $PSVersionTable.PSVersion)

# ---------------------------------------------------------------- find Rw.exe
function Find-Rw {
    if ($RwPath) { return $RwPath }
    $candidates = @(
        (Join-Path $ScriptDir 'Rw.exe'),
        (Join-Path $ScriptDir 'Win64\Rw.exe'),
        (Join-Path $ScriptDir 'RwPortable\Rw.exe'),
        "$env:ProgramFiles\RW-Everything\Rw.exe",
        "${env:ProgramFiles(x86)}\RW-Everything\Rw.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $found = Get-ChildItem -Path $ScriptDir -Filter 'Rw.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.FullName }
    Write-Host 'Rw.exe (RW-Everything) not found.' -ForegroundColor Yellow
    Write-Host 'Put it next to this script, or type its full path here.'
    return (Read-Host 'Path to Rw.exe').Trim('"', ' ')
}
$Rw = Find-Rw
if (-not (Test-Path $Rw)) { Fail ('Rw.exe not found at ' + $Rw) }
Log ('RW-Everything: ' + $Rw + ' (version ' + (Get-Item $Rw).VersionInfo.FileVersion + ')')

$rwRunning = Get-Process -Name 'Rw' -ErrorAction SilentlyContinue
if ($rwRunning) {
    Write-Host 'The RW-Everything window is open. Please close it first.' -ForegroundColor Yellow
    Read-Host 'Press Enter when it is closed'
}

# ---------------------------------------------------------------- RW command runner
$script:RwCalls = 0
function Invoke-Rw([string]$commands, [string]$tag) {
    $script:RwCalls++
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $Rw
    $psi.Arguments = '/Min /Nologo /Stdout /Command="' + $commands + '"'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = [Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    $raw = '### ' + $tag + "`r`n### " + $psi.Arguments + "`r`n### exit " + $p.ExitCode + "`r`n" + $out
    if ($err) { $raw += "`r`n### stderr`r`n" + $err }
    Add-Content -Path (Join-Path $RawDir ($tag + '.txt')) -Value $raw
    return $out
}

# Hashtable key for an address (avoids integer type mismatches between uint64/int/decimal).
function AKey($v) { return ('{0:X}' -f [uint64]$v) }

# Parse RW output: every line that contains a 0x-number equal to a requested address gives
# address -> last 0x-number on that line. Lines without 0x fall back to bare 8+ digit hex.
# If no line names an address but there is exactly one value line per request, map them in order.
function Parse-Reads([string]$text, [hashtable]$wanted, [string[]]$order) {
    $result = @{}
    $single = @()
    foreach ($line in ($text -split "`r?`n")) {
        $one = @([regex]::Matches($line, '0[xX]([0-9A-Fa-f]+)'))
        if ($one.Count -eq 1) { $single += $one[0].Groups[1].Value }
        $tokens = @([regex]::Matches($line, '0[xX]([0-9A-Fa-f]+)') | ForEach-Object { $_.Groups[1].Value })
        if ($tokens.Count -lt 2) {
            $tokens = @([regex]::Matches($line, '\b([0-9A-Fa-f]{8,16})\b') | ForEach-Object { $_.Groups[1].Value })
        }
        if ($tokens.Count -lt 2) { continue }
        $addr = AKey ([Convert]::ToUInt64($tokens[0], 16))
        if (-not $wanted.ContainsKey($addr)) { continue }
        $result[$addr] = [uint32]([Convert]::ToUInt64($tokens[$tokens.Count - 1], 16) % [uint64]4294967296)
    }
    if ($result.Count -eq 0 -and $single.Count -eq $order.Count) {
        for ($k = 0; $k -lt $order.Count; $k++) {
            $result[$order[$k]] = [uint32]([Convert]::ToUInt64($single[$k], 16) % [uint64]4294967296)
        }
    }
    return $result
}

# Read a list of offsets (relative to BAR0) with 32-bit accesses. Returns hashtable offset -> value.
function Read-Regs([uint64]$base, [int[]]$offsets, [string]$tag) {
    $values = @{}
    for ($i = 0; $i -lt $offsets.Count; $i += $Chunk) {
        $last = [Math]::Min($i + $Chunk, $offsets.Count) - 1
        $wanted = @{}
        $order = @()
        $cmds = @()
        foreach ($off in $offsets[$i..$last]) {
            $a = [uint64]$base + [uint64]$off
            $wanted[(AKey $a)] = $off
            $order += (AKey $a)
            $cmds += ('R32 ' + (Hex $a))
        }
        $out = Invoke-Rw ($cmds -join ';') $tag
        $parsed = Parse-Reads $out $wanted $order
        foreach ($a in $parsed.Keys) { $values[$wanted[$a]] = $parsed[$a] }
        Write-Progress -Activity ('Reading registers (' + $tag + ')') -PercentComplete ((($last + 1) * 100) / $offsets.Count)
    }
    Write-Progress -Activity 'Reading registers' -Completed
    return $values
}

# ---------------------------------------------------------------- find the card
$pciDev = $null
$devs = @(Get-WmiObject Win32_PnPEntity | Where-Object { $_.DeviceID -like 'PCI\VEN_102B&DEV_8350*' })
if ($devs.Count -eq 0) {
    Log 'No PCI device 102b:8350 found by Windows (is the box connected and the Matrox driver installed?)' 'Yellow'
} else {
    $pciDev = $devs[0]
    Log ('Card: ' + $pciDev.Name + '  [' + $pciDev.DeviceID + ']  status ' + $pciDev.Status)
    try {
        $drv = Get-WmiObject Win32_PnPSignedDriver | Where-Object { $_.DeviceID -eq $pciDev.DeviceID } | Select-Object -First 1
        if ($drv) { Log ('Driver: ' + $drv.DriverProviderName + ' ' + $drv.DriverVersion + ' (' + $drv.InfName + ')') }
    } catch { }
}

$Base = [uint64]0
if ($Bar0) {
    $Base = [Convert]::ToUInt64(($Bar0 -replace '^0[xX]', ''), 16)
    Log ('BAR0 given on the command line: ' + (Hex $Base))
} elseif ($pciDev) {
    $ranges = @($pciDev.GetRelated('Win32_DeviceMemoryAddress'))
    foreach ($r in $ranges) {
        $size = [uint64]$r.EndingAddress - [uint64]$r.StartingAddress + 1
        Log ('  memory range ' + (Hex ([uint64]$r.StartingAddress)) + ' - ' + (Hex ([uint64]$r.EndingAddress)) + '  (' + $size + ' bytes)')
        # BAR0 = 8 KiB register block, BAR1 = 8 MiB window.
        if ($size -le 0x10000 -and $Base -eq 0) { $Base = [uint64]$r.StartingAddress }
    }
    if ($Base -eq 0) { Log 'Could not tell which memory range is BAR0.' 'Yellow' }
}

# Save PCI config space as a cross-check (location from the registry).
if ($pciDev) {
    try {
        $loc = (Get-ItemProperty ('HKLM:\SYSTEM\CurrentControlSet\Enum\' + $pciDev.DeviceID)).LocationInformation
        Log ('Location: ' + $loc)
        if ($loc -match 'bus (\d+), device (\d+), function (\d+)') {
            $cfgFile = Join-Path $OutDir 'pci-config.bin'
            Invoke-Rw ('SAVE "' + $cfgFile + '" PCI ' + $matches[1] + ' ' + $matches[2] + ' ' + $matches[3]) 'pci-config' | Out-Null
            if (Test-Path $cfgFile) {
                $cfg = [IO.File]::ReadAllBytes($cfgFile)
                if ($cfg.Length -ge 0x18) {
                    $b0 = [uint64][BitConverter]::ToUInt32($cfg, 0x10)
                    $b0 = $b0 - ($b0 % 16)
                    $b1 = [uint64][BitConverter]::ToUInt32($cfg, 0x14)
                    $b1 = $b1 - ($b1 % 16)
                    Log ('Config space: ID ' + ('{0:x4}:{1:x4}' -f [BitConverter]::ToUInt16($cfg, 0), [BitConverter]::ToUInt16($cfg, 2)) +
                         ', BAR0 ' + (Hex $b0) + ', BAR1 ' + (Hex $b1))
                    if ($Base -eq 0 -and $b0 -ne 0) { $Base = [uint64]$b0 }
                    elseif ($b0 -ne 0 -and $b0 -ne $Base) {
                        Log ('Config space BAR0 differs from Windows resource list - using config space ' + (Hex $b0)) 'Yellow'
                        $Base = [uint64]$b0
                    }
                }
            }
        }
    } catch { Log ('(config space read skipped: ' + $_ + ')') }
}

if ($Base -eq 0) {
    Write-Host 'Enter BAR0 by hand (Device Manager > card > Resources > first "Memory Range", e.g. FAFFE000):'
    $Base = [Convert]::ToUInt64(((Read-Host 'BAR0') -replace '^0[xX]', '').Trim(), 16)
}
Log ('Using BAR0 = ' + (Hex $Base)) 'Cyan'

# ---------------------------------------------------------------- self-test
Log 'Self-test: reading BoardStatus (0x0) and GaiaVersion (0x8)...'
$t = Read-Regs $Base @(0, 8) 'selftest'
$rawTest = Get-Content (Join-Path $RawDir 'selftest.txt') | Out-String
if (-not ($t.ContainsKey(0) -and $t.ContainsKey(8))) {
    Write-Host $rawTest
    Fail 'Could not understand RW-Everything output (shown above). Send me the folder anyway.'
}
Log ('  BoardStatus = ' + (Hex $t[0]) + '   GaiaVersion = ' + (Hex $t[8]))
if (([Math]::Floor([double]$t[0] / 65536) -ne 0x0501) -or ($t[8] -ne 0xF)) {
    Log 'These do not look like the Gaia registers (expected 0x0501xxxx and 0x0000000F).' 'Red'
    Write-Host $rawTest
    if ((Read-Host 'Continue anyway? (y/N)') -ne 'y') { Fail 'Self-test failed.' }
} else {
    Log 'Self-test OK - these are the card registers.' 'Green'
}

# ---------------------------------------------------------------- snapshots
$AllOffsets = @(0..1023 | ForEach-Object { $_ * 4 })
$script:SnapNo = 0
$script:Idle = $null

function Snapshot([string]$label) {
    $script:SnapNo++
    $name = ('{0:D2}-{1}' -f $script:SnapNo, $label)
    $t0 = Get-Date
    $v = Read-Regs $Base $AllOffsets $name
    $bytes = New-Object byte[] $RegBytes
    $missing = @()
    foreach ($off in $AllOffsets) {
        if ($v.ContainsKey($off)) {
            [Array]::Copy([BitConverter]::GetBytes([uint32]$v[$off]), 0, $bytes, $off, 4)
        } else { $missing += $off }
    }
    [IO.File]::WriteAllBytes((Join-Path $OutDir ($name + '.bin')), $bytes)
    $secs = [int]((Get-Date) - $t0).TotalSeconds
    $msg = $name + '.bin saved (' + $secs + ' s), BoardStatus ' + (Hex ([BitConverter]::ToUInt32($bytes, 0)))
    if ($script:Idle) {
        $diff = 0
        for ($o = 0; $o -lt $RegBytes; $o += 4) {
            if ([BitConverter]::ToUInt32($bytes, $o) -ne [BitConverter]::ToUInt32($script:Idle, $o)) { $diff++ }
        }
        $msg += ', ' + $diff + ' registers differ from the first idle dump'
    }
    if ($missing.Count -gt 0) {
        Log ($msg + ' - WARNING: ' + $missing.Count + ' registers could not be read (written as 0)') 'Yellow'
    } else { Log $msg 'Green' }
    if (-not $script:Idle) { $script:Idle = $bytes }
}

function Step([string]$label, [string]$instruction, [int]$count) {
    Write-Host ''
    Write-Host ('=== ' + $label + ' ===') -ForegroundColor Cyan
    Write-Host $instruction
    $note = Read-Host 'Press Enter when ready (type a note first if you like, or "skip")'
    if ($note -eq 'skip') { Log ('Step ' + $label + ' skipped'); return }
    Log ('Step ' + $label + ': ' + $instruction)
    if ($note) { Log ('  note: ' + $note) }
    for ($i = 1; $i -le $count; $i++) { Snapshot ($label + '-' + $i) }
}

Write-Host ''
Write-Host 'Guided session. Leave the HDMI source connected the whole time.' -ForegroundColor Cyan
Write-Host 'Each snapshot takes a few seconds; do not touch the Matrox software while it runs.'

Step 'idle'      'Close all Matrox / capture software. Box connected, HDMI source plugged in and playing.' 2
Step 'preview'   'Open the Matrox capture software and select the HDMI input so the live picture is shown (do NOT record yet). Write the video format in the note (e.g. 1080p30).' 2
Step 'recording' 'Start recording now and keep it running until the snapshots are done.' 3
Step 'stopped'   'Stop recording (software may stay open).' 1
Step 'closed'    'Close the Matrox software completely.' 1

while ($true) {
    Write-Host ''
    $label = Read-Host 'Extra snapshot? Type a short label (e.g. "sd-input"), or press Enter to finish'
    if (-not $label) { break }
    $label = ($label -replace '[^A-Za-z0-9_-]', '-')
    Snapshot $label
}

Log ('Done. ' + $script:SnapNo + ' snapshots, ' + $script:RwCalls + ' RW-Everything calls.') 'Green'
Write-Host ''
Write-Host 'Copy this whole folder to the Linux machine (e.g. via USB stick):' -ForegroundColor Cyan
Write-Host ('  ' + $OutDir) -ForegroundColor White
Write-Host 'and put it under re/notes/win/ in the project.'
Start-Process explorer.exe $OutDir
Read-Host 'Press Enter to close'
