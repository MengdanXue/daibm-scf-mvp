$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$zkpRoot = Join-Path $projectRoot "advanced\zkp"

Push-Location $zkpRoot
try {
    # Circom2's WASI filesystem cannot emit artifacts below a Windows path
    # containing non-ASCII characters. The fixed /work mount is also the
    # reproducible Node 24 build environment used by CI and the demo.
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        docker run --rm -v "${zkpRoot}:/work" -w /work node:24-bookworm-slim sh -lc "npm ci && npm run build && npm run verify:artifacts"
        if ($LASTEXITCODE -ne 0) { throw "Docker ZKP artifact build failed" }
    }
    else {
        throw "Docker is required to build ZKP artifacts reproducibly with Node.js 24"
    }
}
finally {
    Pop-Location
}
