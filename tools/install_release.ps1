# Installiert oder aktualisiert KleinFaktura aus dem neuesten (oder angegebenen) GitHub-Release.
# Aufruf:  powershell -ExecutionPolicy Bypass -File tools\install_release.ps1 [-Version 2.0.0]
# Keine Anmeldung noetig (oeffentliches Repository).
# Umstieg von EEE-Faktura: dessen Datenordner wird automatisch weiterbenutzt; die alte Desktop-Verknuepfung wird
# entfernt, damit nicht zwei Programme mit derselben Datenbank arbeiten. Den alten Programmordner erst nach dem
# ersten Start von KleinFaktura loeschen (das Logo wird beim ersten Start von dort uebernommen).
param([string]$Version = "", [string]$Repo = "Skibbie/kleinfaktura")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest ist mit Fortschrittsanzeige sehr langsam
$App = "KleinFaktura"
foreach ($name in @($App, "EEE-Faktura")) {
    if (Get-Process $name -ErrorAction SilentlyContinue) {
        throw "$name laeuft noch. Bitte zuerst schliessen (dabei wird die taegliche Sicherung erstellt)."
    }
}
$api = "https://api.github.com/repos/$Repo/releases/" + $(if ($Version) { "tags/v$Version" } else { "latest" })
$release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "$App-Installer" }
$ver = $release.tag_name.TrimStart("v")
$asset = $release.assets | Where-Object { $_.name -eq "$App-$ver.zip" } | Select-Object -First 1
if (-not $asset) { throw "Im Release $ver ist kein $App-$ver.zip enthalten." }

$tmp = Join-Path $env:TEMP "kleinfaktura-install"
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $tmp | Out-Null
$zip = Join-Path $tmp $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -Headers @{ "User-Agent" = "$App-Installer" }
if ($asset.digest -and $asset.digest.StartsWith("sha256:")) {
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($hash -ne $asset.digest.Substring(7).ToUpper()) { throw "Pruefsumme des Downloads stimmt nicht." }
}
Expand-Archive $zip -DestinationPath $tmp

$target = Join-Path $env:LOCALAPPDATA "Programs\$App"
if (Test-Path $target) {
    $old = "$target.vorher"
    Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $target $old
}
Move-Item (Join-Path $tmp $App) $target

$desktop = [Environment]::GetFolderPath("Desktop")
$legacyLnk = Join-Path $desktop "EEE-Faktura.lnk"
if (Test-Path $legacyLnk) {
    Remove-Item $legacyLnk
    Write-Host "Alte Verknuepfung EEE-Faktura entfernt."
}
$exe = Join-Path $target "$App.exe"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $desktop "$App.lnk"))
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $target
$lnk.IconLocation = $exe
$lnk.Save()
Remove-Item $tmp -Recurse -Force
Write-Host "Installiert: $($asset.name) -> $target (Desktop-Verknuepfung aktualisiert)"
$legacyDir = Join-Path $env:LOCALAPPDATA "Programs\EEE-Faktura"
if (Test-Path $legacyDir) {
    Write-Host "Hinweis: $legacyDir kann nach dem ersten Start von $App geloescht werden."
}
