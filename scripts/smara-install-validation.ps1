function Get-SmaraNsisPayloadHash {
    param([Parameter(Mandatory)][string]$Path)
    # Tauri restores the unbundled executable after packaging, while NSIS
    # contains this exact marker patch. Account for it, not arbitrary differences.
    $smaraBytes = [IO.File]::ReadAllBytes($Path)
    $smaraMarker = '__TAURI_BUNDLE_TYPE_VAR_UNK'
    $smaraText = [Text.Encoding]::ASCII.GetString($smaraBytes)
    $smaraOffset = $smaraText.IndexOf($smaraMarker, [StringComparison]::Ordinal)
    if ($smaraOffset -lt 0 -or $smaraOffset -ne $smaraText.LastIndexOf($smaraMarker, [StringComparison]::Ordinal)) {
        throw 'Expected exactly one upstream unbundled Tauri marker.'
    }
    $smaraPatch = [Text.Encoding]::ASCII.GetBytes('__TAURI_BUNDLE_TYPE_VAR_NSS')
    [Array]::Copy($smaraPatch, 0, $smaraBytes, $smaraOffset, $smaraPatch.Length)
    $smaraSha = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($smaraSha.ComputeHash($smaraBytes)).Replace('-', '') }
    finally { $smaraSha.Dispose() }
}
