# Lucky Draw Tally connector (PowerShell). Built into Windows, nothing to install.
# Asks the lucky-draw server whether a check is due; if so, asks Tally (on this PC) and sends Tally's reply back.
# It only ever reads from Tally.
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = @{}
Get-Content (Join-Path $here 'connector.ini') | ForEach-Object {
    if ($_ -match '^\s*([^#=][^=]*?)\s*=\s*(.*?)\s*$') { $cfg[$matches[1]] = $matches[2] }
}
$server = ([string]$cfg['server']).TrimEnd('/')
$key = [string]$cfg['key']
$tally = [string]$cfg['tally']
if (-not $tally) { $tally = 'http://localhost:9000' }
if (-not $server -or -not $key) {
    Write-Host 'connector.ini needs server= and key= lines. Download the connector again from the lucky-draw website.'
    exit 1
}
$version = '1'

function Log($m) { Write-Host ("{0}  {1}" -f (Get-Date -Format 'dd-MM-yyyy HH:mm:ss'), $m) }

function Call-Server($path, [byte[]]$bytes, $contentType) {
    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('X-Connector-Key', $key)
    $wc.Headers.Add('Content-Type', $contentType)
    return [Text.Encoding]::UTF8.GetString($wc.UploadData($server + $path, 'POST', $bytes))
}

function Ask-Tally($xml) {
    $req = [Net.HttpWebRequest]::Create($tally)
    $req.Method = 'POST'
    $req.ContentType = 'text/xml; charset=utf-8'
    $req.Timeout = 180000
    $req.ReadWriteTimeout = 180000
    $b = [Text.Encoding]::UTF8.GetBytes($xml)
    $req.ContentLength = $b.Length
    $st = $req.GetRequestStream()
    $st.Write($b, 0, $b.Length)
    $st.Close()
    $resp = $req.GetResponse()
    $ms = New-Object IO.MemoryStream
    $resp.GetResponseStream().CopyTo($ms)
    $resp.Close()
    return , $ms.ToArray()
}

Log "Connector started. Server: $server   Tally: $tally"
while ($true) {
    $wait = 15
    try {
        $pollBody = [Text.Encoding]::UTF8.GetBytes((@{ version = $version; computer = $env:COMPUTERNAME } | ConvertTo-Json -Compress))
        $reply = (Call-Server '/api/connector/poll' $pollBody 'application/json') | ConvertFrom-Json
        $wait = 10
        if ($reply.wait) { $wait = [int]$reply.wait }
        $job = $reply.job
        if ($job) {
            Log ("Job {0} ({1})" -f $job.id, $job.kind)
            $data = $null
            try {
                $data = Ask-Tally $job.request_xml
            } catch {
                $msg = "Cannot read from Tally at $tally. Is Tally open with the company loaded and the server on? ($($_.Exception.Message))"
                Log $msg
                try {
                    $errBody = [Text.Encoding]::UTF8.GetBytes((@{ message = $msg } | ConvertTo-Json -Compress))
                    $null = Call-Server ("/api/connector/error/" + $job.id) $errBody 'application/json'
                } catch { Log ("Could not report the problem to the server: " + $_.Exception.Message) }
            }
            if ($null -ne $data) {
                try {
                    $null = Call-Server ("/api/connector/result/" + $job.id) $data 'application/octet-stream'
                    Log ("Sent Tally's reply ({0} bytes) to the server." -f $data.Length)
                    $wait = 2
                } catch { Log ("Could not send Tally's reply to the server: " + $_.Exception.Message) }
            }
        }
    } catch {
        Log ("Cannot reach the lucky-draw server: " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $wait
}
