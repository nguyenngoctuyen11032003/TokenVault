$ErrorActionPreference = "Continue"
$failed = $false
ruff check .; if ($LASTEXITCODE -ne 0) { $failed = $true }
mypy src; if ($LASTEXITCODE -ne 0) { $failed = $true }
pytest -q; if ($LASTEXITCODE -ne 0) { $failed = $true }
if ($failed) { exit 1 }
