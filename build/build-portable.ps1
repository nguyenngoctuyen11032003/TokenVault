<#
  Builds templates\portable-runtime\ - the prepared runtime folder from which the Manager
  assembles "no-exe" portable packages (docs: plan Task 25).

    templates\portable-runtime\runtime\   official embeddable Python 3.12 (Authenticode-signed
                                          by the Python Software Foundation) + vendored wheels
                                          (Lib\site-packages) + TokenVault source (app\tokenvault),
                                          python312._pth enabling both, tkinter copied from the
                                          base interpreter (consent screen) - all byte-compiled
    templates\portable-runtime\scripts\   Install.cmd, install.ps1, Start-Manager.cmd, ...
    templates\portable-runtime\manifest.json

  and, unless -NoManagerZip, dist\TokenVault-Manager-portable.zip (generic, no credential).

  Needs internet once (python.org + PyPI wheels). Offline: -EmbedZip <local zip> and
  -WheelDir <folder of cp312-win_amd64 wheels>.

  -RefreshOnly: the runtime folder already exists (embedded Python + wheels are kept as
  they are); only the TokenVault sources (app\tokenvault), pricing table and scripts\ are
  replaced from this checkout and byte-compiled. Fast and offline - packaging\build.ps1 uses
  it so an installer never ships stale sources.
#>
[CmdletBinding()]
param(
    [string]$Python,
    [string]$OutDir,
    [string]$EmbedVersion = '3.12.10',
    # SHA-256 of python-3.12.10-embed-amd64.zip (python.org/ftp/python/3.12.10/). python.org
    # publishes Sigstore bundles instead of a SHA-256 for this file, so the value is pinned here
    # (computed from a download whose python.exe/pythonw.exe/python312.dll Authenticode
    # signatures were verified as 'Python Software Foundation').
    [string]$EmbedSha256 = '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3',
    [string]$EmbedZip,
    [string]$WheelDir,
    [switch]$SkipTk,
    [switch]$NoManagerZip,
    [switch]$RefreshOnly
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $repo 'templates\portable-runtime' }
if (-not $Python) {
    $cand = @(@(Join-Path $repo '.venv\Scripts\python.exe') | Where-Object { Test-Path $_ })
    if ($cand) { $Python = $cand[0] } else { $Python = 'python' }
}
$work = Join-Path ([IO.Path]::GetTempPath()) ('tv-portable-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $work | Out-Null

function Step($m) { Write-Host "== $m" -ForegroundColor Cyan }
function Assert-Psf($file) {
    $s = Get-AuthenticodeSignature -LiteralPath $file
    $cn = ''
    if ($s.SignerCertificate) { $cn = $s.SignerCertificate.Subject }
    if ($s.Status -ne 'Valid' -or $cn -notlike '*CN=Python Software Foundation*') {
        throw "Authenticode check failed for $file : status=$($s.Status) signer=$cn"
    }
    return $cn
}

try {
  if (-not $RefreshOnly) {
    # 1. embeddable Python ---------------------------------------------------------------------
    Step "embeddable Python $EmbedVersion"
    if (-not $EmbedZip) {
        $EmbedZip = Join-Path $work "python-$EmbedVersion-embed-amd64.zip"
        $url = "https://www.python.org/ftp/python/$EmbedVersion/python-$EmbedVersion-embed-amd64.zip"
        Invoke-WebRequest -Uri $url -OutFile $EmbedZip -UseBasicParsing
    }
    $sha = (Get-FileHash -LiteralPath $EmbedZip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sha -ne $EmbedSha256.ToLowerInvariant()) { throw "SHA-256 mismatch for the embeddable zip: got $sha, expected $EmbedSha256" }
    Write-Host "SHA-256 ok: $sha"

    $rt = Join-Path $OutDir 'runtime'
    if (Test-Path $OutDir) { Remove-Item -LiteralPath $OutDir -Recurse -Force }
    New-Item -ItemType Directory -Force $rt | Out-Null
    Expand-Archive -LiteralPath $EmbedZip -DestinationPath $rt -Force
    $signers = @{}
    foreach ($f in 'python.exe', 'pythonw.exe', 'python312.dll') { $signers[$f] = Assert-Psf (Join-Path $rt $f) }
    Write-Host "Authenticode ok: $($signers['python.exe'])"

    # 2. path file: site-packages + our sources, site enabled ---------------------------------
    Step 'python312._pth'
    $pth = Join-Path $rt 'python312._pth'
    [IO.File]::WriteAllText($pth, "python312.zip`r`n.`r`nLib\site-packages`r`napp`r`nimport site`r`n", (New-Object Text.UTF8Encoding($false)))
    $sp = Join-Path $rt 'Lib\site-packages'
    New-Item -ItemType Directory -Force $sp | Out-Null

    # 3. vendored wheels (platform wheels for cp312-win_amd64) ---------------------------------
    Step 'wheels'
    # pywebview's dependency proxy_tools is sdist-only (pure Python): it is installed in a second step
    # with the host pip; everything else is a cp312-win_amd64 / pure wheel (--only-binary).
    $deps = @('websockets', 'cryptography', 'watchdog', 'pystray', 'Pillow', 'reportlab>=4,<6', 'pythonnet', 'bottle', 'typing_extensions')
    $pipArgs = @('-m', 'pip', 'install', '--target', $sp, '--platform', 'win_amd64', '--python-version', '3.12', '--implementation', 'cp', '--only-binary=:all:', '--no-compile', '--disable-pip-version-check', '--upgrade')
    if ($WheelDir) { $pipArgs += @('--no-index', '--find-links', $WheelDir) }
    & $Python @pipArgs @deps
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    & $Python @pipArgs --no-deps 'pywebview>=5'
    if ($LASTEXITCODE -ne 0) { throw 'pip install pywebview failed' }
    & $Python -m pip install --target $sp --no-deps --no-compile --disable-pip-version-check proxy_tools
    if ($LASTEXITCODE -ne 0) { throw 'pip install proxy_tools failed' }

    # 4. tkinter for the consent screen (the embeddable build has none) -------------------------
    if (-not $SkipTk) {
        Step 'tkinter from the base interpreter'
        $base = (& $Python -c "import sys;print(sys.base_prefix)").Trim()
        $need = @("$base\DLLs\_tkinter.pyd", "$base\DLLs\tcl86t.dll", "$base\DLLs\tk86t.dll", "$base\Lib\tkinter\__init__.py", "$base\tcl\tcl8.6\init.tcl", "$base\tcl\tk8.6\tk.tcl")
        if (($need | Where-Object { -not (Test-Path $_) }).Count -eq 0) {
            foreach ($f in '_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'zlib1.dll') {
                if (Test-Path "$base\DLLs\$f") { Copy-Item "$base\DLLs\$f" $rt -Force }
            }
            Copy-Item "$base\Lib\tkinter" (Join-Path $sp 'tkinter') -Recurse -Force
            $tcl = Join-Path $rt 'tcl'
            New-Item -ItemType Directory -Force $tcl | Out-Null
            foreach ($d in 'tcl8.6', 'tk8.6', 'tcl8') { if (Test-Path "$base\tcl\$d") { Copy-Item "$base\tcl\$d" (Join-Path $tcl $d) -Recurse -Force } }
            Get-ChildItem $tcl -Recurse -Directory -Filter 'demos' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning 'base interpreter has no tkinter/Tcl: the consent screen will fall back to a native message box'
        }
    }

  } else {
    $rt = Join-Path $OutDir 'runtime'
    $sp = Join-Path $rt 'Lib\site-packages'
    if (-not (Test-Path -LiteralPath (Join-Path $rt 'python.exe'))) { throw "-RefreshOnly: no runtime at $rt (run build-portable.ps1 without it first)" }
    $sha = ''; $signers = @{ 'python.exe' = '' }
  }

    # 5. TokenVault sources + byte-compile ------------------------------------------------------
    Step 'TokenVault sources'
    $app = Join-Path $rt 'app'
    if ($RefreshOnly -and (Test-Path -LiteralPath $app)) { Remove-Item -LiteralPath $app -Recurse -Force }
    New-Item -ItemType Directory -Force $app | Out-Null
    Copy-Item (Join-Path $repo 'src\tokenvault') (Join-Path $app 'tokenvault') -Recurse -Force
    Get-ChildItem $app -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    # pricing table (the Manager resolves <runtime>\data\pricing.json next to app\)
    New-Item -ItemType Directory -Force (Join-Path $rt 'data') | Out-Null
    Copy-Item (Join-Path $repo 'data\pricing.json') (Join-Path $rt 'data\pricing.json') -Force
    Step 'compile'
    & (Join-Path $rt 'python.exe') -m compileall -q -j 0 $app $sp
    if ($LASTEXITCODE -ne 0) { throw 'compileall failed' }

    # 6. scripts + manifest ---------------------------------------------------------------------
    Step 'scripts'
    $sc = Join-Path $OutDir 'scripts'
    if ($RefreshOnly -and (Test-Path -LiteralPath $sc)) { Remove-Item -LiteralPath $sc -Recurse -Force }
    New-Item -ItemType Directory -Force $sc | Out-Null
    Copy-Item (Join-Path $repo 'packaging\portable\*') $sc -Force -Exclude '*.py'
    if ($RefreshOnly) {
        & (Join-Path $rt 'python.exe') -c "import tokenvault.manager.app, tokenvault.adapter, webview, reportlab"
        if ($LASTEXITCODE -ne 0) { throw 'imports failed after the source refresh' }
        Write-Host "Portable runtime sources refreshed: $OutDir" -ForegroundColor Green
        return
    }
    $unsigned = @(Get-ChildItem $rt -Recurse -Include *.pyd, *.dll -File | Where-Object { (Get-AuthenticodeSignature $_.FullName).Status -ne 'Valid' } | ForEach-Object { $_.FullName.Substring($rt.Length + 1) })
    $manifest = [ordered]@{
        python = $EmbedVersion; embed_sha256 = $sha; python_signer = $signers['python.exe']
        built = (Get-Date).ToString('s'); unsigned_native_modules = $unsigned.Count
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutDir 'manifest.json') -Encoding ASCII
    Write-Host "native modules without an Authenticode signature (wheels): $($unsigned.Count)"

    # 7. selftest of the runtime itself ----------------------------------------------------------
    Step 'selftest'
    $out = (& (Join-Path $rt 'python.exe') -m tokenvault.adapter --selftest) -join "`n"
    Write-Host $out
    if ($LASTEXITCODE -ne 0 -or $out -match 'MISSING' -or $out -notmatch 'signed_by_psf=yes') { throw 'adapter --selftest failed (missing dependency or interpreter not signed by the PSF)' }
    & (Join-Path $rt 'python.exe') -c "import webview, reportlab, bottle, proxy_tools, clr_loader, pythonnet, tokenvault.manager.app"
    if ($LASTEXITCODE -ne 0) { throw 'Manager imports failed in the runtime' }

    # 8. Manager ZIP -------------------------------------------------------------------------------
    if (-not $NoManagerZip) {
        Step 'Manager portable zip'
        $zip = Join-Path $repo 'dist\TokenVault-Manager-portable.zip'
        $env:PYTHONPATH = Join-Path $repo 'src'
        & $Python -m tokenvault.manager.portable_packager manager-zip $OutDir $zip
        if ($LASTEXITCODE -ne 0) { throw 'manager zip failed' }
    }
    Write-Host "Portable runtime ready: $OutDir" -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
