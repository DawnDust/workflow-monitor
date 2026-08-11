param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = "Stop"
$certificateBase64 = $env:WINDOWS_SIGNING_CERT_PFX_BASE64
$certificatePassword = $env:WINDOWS_SIGNING_CERT_PASSWORD
$metadata = [ordered]@{ status = "unsigned" }
$temporaryCertificate = $null

if ([bool]$certificateBase64 -xor [bool]$certificatePassword) {
    throw "Both Windows signing certificate and password secrets must be configured"
}

try {
    if ($certificateBase64 -and $certificatePassword) {
        $temporaryCertificate = Join-Path $env:RUNNER_TEMP "workflow-monitor-signing-$([guid]::NewGuid().ToString('N')).pfx"
        [IO.File]::WriteAllBytes($temporaryCertificate, [Convert]::FromBase64String($certificateBase64))
        $signTool = (Get-Command signtool.exe -ErrorAction Stop).Source
        & $signTool sign /fd SHA256 /td SHA256 /tr https://timestamp.digicert.com /f $temporaryCertificate /p $certificatePassword $Executable
        if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
        $signature = Get-AuthenticodeSignature -FilePath $Executable
        if ($signature.Status -ne "Valid" -or -not $signature.SignerCertificate) {
            throw "Authenticode verification failed: $($signature.Status)"
        }
        $metadata = [ordered]@{
            status = "signed"
            subject = $signature.SignerCertificate.Subject
            thumbprint = $signature.SignerCertificate.Thumbprint
        }
    }
    $metadata | ConvertTo-Json | Set-Content -LiteralPath $Output -Encoding utf8
}
finally {
    if ($temporaryCertificate -and (Test-Path -LiteralPath $temporaryCertificate)) {
        Remove-Item -LiteralPath $temporaryCertificate -Force
    }
}
