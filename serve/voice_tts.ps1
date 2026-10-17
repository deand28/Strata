# Fixed local data boundary: JSON is read from stdin; no user text becomes code.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($request.op -eq 'voices') {
        $voices = @($speaker.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
            @{ name = $_.VoiceInfo.Name; language = $_.VoiceInfo.Culture.Name }
        })
        [Console]::Out.Write((@{ voices = $voices } | ConvertTo-Json -Depth 4 -Compress))
    } elseif ($request.op -eq 'synthesize') {
        if ($request.text -isnot [string] -or $request.text.Length -lt 1 -or $request.text.Length -gt 5000) { throw 'Invalid text length' }
        if ($request.voice) { $speaker.SelectVoice([string]$request.voice) }
        $stream = New-Object System.IO.MemoryStream
        try {
            $speaker.SetOutputToWaveStream($stream)
            $speaker.Speak([string]$request.text)
            $speaker.SetOutputToNull()
            [Console]::Out.Write([Convert]::ToBase64String($stream.ToArray()))
        } finally { $stream.Dispose() }
    } else { throw 'Unknown speech operation' }
} finally { $speaker.Dispose() }
