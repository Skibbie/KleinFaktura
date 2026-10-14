"""Programm-Update aus den öffentlichen GitHub-Releases (ohne Anmeldung, über die GitHub-API).

Ablauf:
1. `latest_release` fragt die neueste Version ab (nur lesend).
2. `download` lädt das Release-ZIP, prüft die SHA-256-Prüfsumme (sofern GitHub sie liefert) und entpackt es
   neben den Programmordner (`<Programmordner>.neu`).
3. `launch_helper` startet ein PowerShell-Skript, das wartet, bis das Programm beendet ist, den Programmordner
   gegen den neuen tauscht (alter Stand bleibt als `<Programmordner>.vorher`) und das Programm neu startet.
Die Daten (%LOCALAPPDATA%\\KleinFaktura) werden dabei nicht berührt.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app import config

REPO = "Skibbie/KleinFaktura"
API = f"https://api.github.com/repos/{REPO}"
EXE_NAME = f"{config.APP_NAME}.exe"
ZIP_PREFIX = f"{config.APP_NAME}/"  # Ordner im Release-ZIP
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Öffentlicher Ed25519-Schlüssel (Base64) zur Prüfung der Release-Signatur. Gegenstück ist das GitHub-Secret
# UPDATE_SIGNING_KEY (tools/make_update_key.py, tools/sign_release.py). Updates ohne gültige Signatur werden
# abgelehnt – auch wenn jemand Zugriff auf das GitHub-Konto oder die Release-Dateien hätte.
UPDATE_PUBLIC_KEY = "6LmnqnP3YCjKeEc3zTdBogAvCd66MCe1E8n/mNPh6GA="
SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    notes: str
    assets: list[dict] = field(default_factory=list)  # GitHub-Assets (name, browser_download_url, digest)


def parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except ValueError:
        return ()


def is_newer(remote: str, local: str = config.APP_VERSION) -> bool:
    r = parse_version(remote)
    return bool(r) and r > parse_version(local)


def _open(url: str, timeout: int, accept: str = "application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (feste https-Adresse)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("Auf GitHub wurde kein veröffentlichtes Release gefunden.") from e
        if e.code == 403:
            raise UpdateError("GitHub hat die Anfrage abgelehnt (Abfragelimit). Bitte später erneut versuchen.") from e
        raise UpdateError(f"GitHub meldet Fehler {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"GitHub ist nicht erreichbar: {getattr(e, 'reason', e)}") from e


def _release(data: dict) -> Release:
    return Release(str(data.get("tag_name", "")).lstrip("v"), (data.get("body") or "").strip(),
                   list(data.get("assets") or []))


def latest_release() -> Release:
    with _open(f"{API}/releases/latest", timeout=20) as r:
        return _release(json.load(r))


def install_dir() -> Path | None:
    """Programmordner der laufenden Exe; None, wenn aus dem Quellcode gestartet."""
    if not config.IS_RELEASE:
        return None
    return Path(sys.executable).resolve().parent


def _asset(release: Release, name: str) -> dict:
    for a in release.assets:
        if a.get("name") == name:
            return a
    raise UpdateError(f"Im Release {release.version} ist kein {name} enthalten.")


def zip_asset(release: Release) -> dict:
    return _asset(release, f"{config.APP_NAME}-{release.version}.zip")


def setup_asset(release: Release) -> dict:
    return _asset(release, f"{config.APP_NAME}-Setup-{release.version}.exe")


def installed_by_setup(target: Path) -> bool:
    """Mit dem Installer eingerichtet (Deinstallationsprogramm im Programmordner): Update über das neue Setup,
    sonst (entpacktes ZIP) über den Ordnertausch."""
    return any(target.glob("unins*.exe"))


def _read_small(asset: dict, limit: int = 64 * 1024) -> bytes:
    with _open(asset["browser_download_url"], timeout=60, accept="application/octet-stream") as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError(f"{asset.get('name')} ist unerwartet groß.")
    return data


def verify_signature(sums: bytes, signature_b64: bytes, public_key_b64: str | None = None) -> dict[str, str]:
    """Prüft die Ed25519-Signatur der Prüfsummendatei und gibt {Dateiname: sha256} zurück."""
    import base64
    import binascii
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key_b64 = UPDATE_PUBLIC_KEY if public_key_b64 is None else public_key_b64
    if not key_b64:
        raise UpdateError("In dieser Programmversion ist kein Schlüssel zur Update-Prüfung hinterlegt.")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64)).verify(base64.b64decode(signature_b64.strip()), sums)
    except (InvalidSignature, binascii.Error, ValueError) as e:
        raise UpdateError("Die Signatur des Updates ist ungültig. Aus Sicherheitsgründen wird es nicht installiert – "
                          "bitte später erneut versuchen oder den Hersteller informieren.") from e
    out = {}
    for line in sums.decode("utf-8").splitlines():
        digest, _, name = line.strip().partition("  ")
        if name:
            out[name] = digest.lower()
    return out


def signed_sums(release: Release) -> dict[str, str]:
    """Signierte Prüfsummen des Releases; fehlt die Signatur, wird das Update abgelehnt."""
    try:
        sums, sig = _asset(release, SUMS_NAME), _asset(release, SIG_NAME)
    except UpdateError as e:
        raise UpdateError(f"Das Release {release.version} ist nicht signiert und wird aus Sicherheitsgründen nicht "
                          "automatisch installiert. Bitte das Setup bei Bedarf selbst von GitHub laden.") from e
    return verify_signature(_read_small(sums), _read_small(sig))


def _fetch(asset: dict, dest: Path, expected_sha256: str | None = None) -> Path:
    """Lädt ein Release-Asset und prüft die Prüfsumme: gegen die signierte Liste und, falls vorhanden, gegen die
    von GitHub gemeldete."""
    sha = hashlib.sha256()
    with _open(asset["browser_download_url"], timeout=900, accept="application/octet-stream") as r, \
            open(dest, "wb") as out:
        while chunk := r.read(1 << 20):
            sha.update(chunk)
            out.write(chunk)
    try:
        check_digest(asset, sha.hexdigest())
        if expected_sha256 is not None and sha.hexdigest().lower() != expected_sha256.lower():
            raise UpdateError("Die heruntergeladene Datei passt nicht zur signierten Prüfsumme – Update abgebrochen.")
    except UpdateError:
        dest.unlink(missing_ok=True)
        raise
    return dest


def _expected(sums: dict[str, str], asset: dict) -> str:
    if asset["name"] not in sums:
        raise UpdateError(f"{asset['name']} fehlt in der signierten Prüfsummenliste – Update abgebrochen.")
    return sums[asset["name"]]


def download(release: Release, target: Path) -> Path:
    """Lädt das Release-ZIP und entpackt es nach <target>.neu. Gibt den neuen Programmordner zurück."""
    asset = zip_asset(release)
    expected = _expected(signed_sums(release), asset)
    new_dir = target.with_name(target.name + ".neu")
    shutil.rmtree(new_dir, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix="kleinfaktura-update-") as tmp:
        extract(_fetch(asset, Path(tmp) / asset["name"], expected), new_dir)
    return new_dir


def download_setup(release: Release) -> Path:
    """Lädt das Setup des Releases in einen Temp-Ordner (bleibt bis zur Installation liegen)."""
    asset = setup_asset(release)
    expected = _expected(signed_sums(release), asset)
    # eigener Ordner mit Zufallsnamen: nicht vorhersehbar, nicht von anderen Prozessen vorbelegt
    folder = Path(tempfile.mkdtemp(prefix="kleinfaktura-update-"))
    return _fetch(asset, folder / asset["name"], expected)


def check_digest(asset: dict, sha256_hex: str) -> None:
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and digest[7:].lower() != sha256_hex.lower():
        raise UpdateError("Die heruntergeladene Datei ist beschädigt (Prüfsumme stimmt nicht).")


def extract(zip_path: Path, new_dir: Path) -> None:
    """Entpackt den Programmordner aus dem ZIP nach new_dir und prüft, dass die Exe enthalten ist."""
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(ZIP_PREFIX) or name.endswith("/"):
                continue
            rel = Path(name[len(ZIP_PREFIX):])
            if rel.is_absolute() or ".." in rel.parts:
                raise UpdateError(f"Ungültiger Pfad im ZIP: {name}")
            dest = new_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    if not (new_dir / EXE_NAME).exists():
        shutil.rmtree(new_dir, ignore_errors=True)
        raise UpdateError(f"Das ZIP enthält kein {EXE_NAME}.")


HELPER = r"""
# KleinFaktura: nach dem Beenden die neue Version einspielen (von update.py erzeugt).
# -Setup: $Source ist das Setup des Releases und wird still ausgeführt; sonst ist $Source der entpackte neue
# Programmordner und wird gegen $Target getauscht.
param([int]$ProcId, [string]$Source, [string]$Target, [string]$Log, [string]$Exe, [switch]$Setup, [switch]$NoStart)
function Write-Log($m) { Add-Content -Path $Log -Value "$(Get-Date -Format s)  $m" -Encoding utf8 }
function Fail($m, [switch]$NoRestart) {
    Write-Log "FEHLER: $m"
    if (-not $NoStart) {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show("Das Update konnte nicht installiert werden:`n$m`n`nDetails: $Log",
            "Programm-Update") | Out-Null
        $exe = Join-Path $Target $Exe
        if ((Test-Path $exe) -and -not $NoRestart) { Start-Process $exe -WorkingDirectory $Target }
    }
    exit 1
}
Write-Log "Update gestartet: $Source -> $Target"
Wait-Process -Id $ProcId -Timeout 180 -ErrorAction SilentlyContinue
if (Get-Process -Id $ProcId -ErrorAction SilentlyContinue) { Fail "Das Programm wurde nicht beendet." -NoRestart }
if ($Setup) {
    $setupArgs = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$Target`"", "/LOG=`"$Log.setup.txt`"")
    try { $p = Start-Process $Source -ArgumentList $setupArgs -Wait -PassThru -ErrorAction Stop }
    catch { Fail "Setup konnte nicht gestartet werden: $_" }
    if ($p.ExitCode -ne 0) { Fail "Setup beendet mit Code $($p.ExitCode) (Protokoll: $Log.setup.txt)" }
    Remove-Item $Source -ErrorAction SilentlyContinue
    Write-Log "Update per Setup installiert"
    if (-not $NoStart) { Start-Process (Join-Path $Target $Exe) -WorkingDirectory $Target }
    exit 0
}
$old = "$Target.vorher"
Remove-Item $old -Recurse -Force -ErrorAction SilentlyContinue
$moved = $false
for ($i = 0; $i -lt 15 -and -not $moved; $i++) {
    try { Move-Item $Target $old -ErrorAction Stop; $moved = $true } catch { Start-Sleep -Seconds 1 }
}
if (-not $moved) { Fail "Der Programmordner ist noch in Benutzung: $Target" }
try { Move-Item $Source $Target -ErrorAction Stop }
catch { Move-Item $old $Target; Fail "Neue Version konnte nicht verschoben werden: $_" }
Write-Log "Update installiert, vorherige Version: $old"
if (-not $NoStart) { Start-Process (Join-Path $Target $Exe) -WorkingDirectory $Target }
"""


def write_helper(directory: Path) -> Path:
    p = directory / "kleinfaktura-update.ps1"
    p.write_text(HELPER, encoding="utf-8-sig")  # BOM, damit Windows PowerShell 5 die Umlaute richtig liest
    return p


def helper_command(script: Path, pid: int, source: Path, target: Path, log: Path, start: bool = True,
                   setup: bool = False) -> list[str]:
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
           "-ProcId", str(pid), "-Source", str(source), "-Target", str(target), "-Log", str(log), "-Exe", EXE_NAME]
    if setup:
        cmd.append("-Setup")
    return cmd if start else cmd + ["-NoStart"]


def launch_helper(source: Path, target: Path, setup: bool = False) -> None:
    """Startet das Update-Skript losgelöst; es wartet, bis dieser Prozess beendet ist.
    source: neuer Programmordner (Ordnertausch) bzw. Setup-Datei (setup=True)."""
    tmp = Path(tempfile.mkdtemp(prefix="kleinfaktura-update-"))   # Zufallsname statt fester Skriptdatei
    script = write_helper(tmp)
    cmd = helper_command(script, os.getpid(), source, target, config.data_dir() / "update.log", setup=setup)
    # cwd ausdrücklich außerhalb des Programmordners, sonst hält das Skript ihn selbst fest
    # (die Desktop-Verknüpfung startet mit dem Programmordner als Arbeitsverzeichnis)
    subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
