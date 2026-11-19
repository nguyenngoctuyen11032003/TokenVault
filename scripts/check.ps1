$ErrorActionPreference = "Continue"
$failed = $false
$py = ".venv\Scripts\python.exe"
& $py -m ruff check .; if ($LASTEXITCODE -ne 0) { $failed = $true }
& $py -m mypy src; if ($LASTEXITCODE -ne 0) { $failed = $true }
& $py -m pytest -q; if ($LASTEXITCODE -ne 0) { $failed = $true }
if ($failed) { exit 1 }
