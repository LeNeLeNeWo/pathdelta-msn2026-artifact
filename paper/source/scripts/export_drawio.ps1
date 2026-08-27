param(
    [string]$DrawIo = "D:\drawio\draw.io\draw.io.exe"
)

$ErrorActionPreference = "Stop"
$paperRoot = Split-Path -Parent $PSScriptRoot
$figureDir = Join-Path $paperRoot "figures"
$source = Join-Path $figureDir "system-overview.drawio"
$rawPdf = Join-Path $figureDir "system_overview.raw.pdf"
$pdf = Join-Path $figureDir "system-overview.drawio.pdf"
$svg = Join-Path $figureDir "system_overview.svg"

if (-not (Test-Path -LiteralPath $DrawIo)) {
    throw "draw.io CLI not found at $DrawIo"
}

[xml]$xml = Get-Content -Raw -LiteralPath $source
if (-not $xml.SelectSingleNode('//mxCell[@id="0"]') -or
    -not $xml.SelectSingleNode('//mxCell[@id="1"]')) {
    throw "Invalid draw.io root cells"
}

foreach ($job in @(
    @{ Format = "pdf"; Output = $rawPdf },
    @{ Format = "svg"; Output = $svg }
)) {
    $process = Start-Process -FilePath $DrawIo -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
        "-x", "-f", $job.Format, "-e", "-b", "6", "-o", $job.Output, $source
    )
    if ($process.ExitCode -ne 0) {
        throw "draw.io $($job.Format) export failed with exit code $($process.ExitCode)"
    }
}

function Convert-UbuntuPath([string]$Path) {
    return (wsl -d Ubuntu -- wslpath -a $Path).Trim()
}

$rawWsl = Convert-UbuntuPath $rawPdf
$pdfWsl = Convert-UbuntuPath $pdf
$gsCommand = "gs -q -dBATCH -dNOPAUSE -sDEVICE=pdfwrite " +
    "-dCompatibilityLevel=1.5 -dPDFSETTINGS=/prepress " +
    "-sOutputFile='$pdfWsl' '$rawWsl'"
wsl -d Ubuntu -- bash -lc $gsCommand
if ($LASTEXITCODE -ne 0) {
    throw "Ghostscript PDF 1.5 conversion failed"
}

Remove-Item -LiteralPath $rawPdf
Write-Host "Exported $pdf and $svg"
