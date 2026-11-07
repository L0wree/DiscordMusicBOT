import os
import sys
import json
import gzip
import urllib.request
import importlib.metadata
import subprocess
import concurrent.futures

REQUIREMENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")


def parse_requirements():
    """Extract package names from requirements.txt."""
    packages = []
    if not os.path.exists(REQUIREMENTS_FILE):
        return [
            "discord.py",
            "yt-dlp",
            "python-dotenv",
            "pystray",
            "Pillow",
            "PyNaCl",
        ]

    with open(REQUIREMENTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            pkg_name = line.split(";")[0].split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
            if pkg_name:
                packages.append(pkg_name)
    return packages


def get_latest_pypi_version(package_name: str, timeout: float = 2.5) -> str:
    """Fetch the latest release version of a package from PyPI."""
    url = f"https://pypi.org/pypi/{package_name}/json"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DiscordMusicBOT-UpdateChecker/1.0",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
        return data["info"]["version"]


def check_updates(timeout_per_pkg: float = 2.5, total_timeout: float = 5.0):
    """Check for outdated packages against PyPI concurrently."""
    pkgs = parse_requirements()
    outdated = []

    def _check_single(pkg):
        try:
            installed = importlib.metadata.version(pkg)
        except Exception:
            installed = None

        try:
            latest = get_latest_pypi_version(pkg, timeout=timeout_per_pkg)
            if installed is None:
                return {"package": pkg, "installed": "Not installed", "latest": latest}
            elif installed != latest:
                return {"package": pkg, "installed": installed, "latest": latest}
        except Exception:
            pass
        return None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(pkgs))) as executor:
            futures = [executor.submit(_check_single, pkg) for pkg in pkgs]
            done, _ = concurrent.futures.wait(futures, timeout=total_timeout)
            for f in done:
                res = f.result()
                if res:
                    outdated.append(res)
    except Exception:
        pass

    return outdated


def check_and_apply_updates(auto_apply: bool = True) -> list:
    """Checks for updates and optionally installs them via pip."""
    print("[*] Checking for package updates...")
    outdated = check_updates()
    if not outdated:
        print("[*] All packages are up to date.")
        return []

    print(f"[*] Found {len(outdated)} update(s):")
    for item in outdated:
        print(f"    - {item['package']}: {item['installed']} -> {item['latest']}")

    if not auto_apply:
        return outdated

    print("[*] Installing updates...")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    pkg_names = [item["package"] for item in outdated]
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + pkg_names
    res = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if res.returncode == 0:
        print("[*] Updates installed successfully.")
    else:
        print(f"[!] Update error (code {res.returncode}): {res.stderr}")

    return outdated


if __name__ == "__main__":
    check_and_apply_updates(auto_apply=True)

