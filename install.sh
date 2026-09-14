#!/bin/sh
# TypixDeck Launcher + Store bootstrap. Run as your ordinary desktop user.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 077
if [ ! -x /usr/bin/python3 ]; then
    echo 'Python 3 is required. On Raspberry Pi OS: sudo apt-get install python3' >&2
    exit 1
fi
exec /usr/bin/python3 -I - "$@" <<'__TYPIX_INSTALL_PYTHON__'
import argparse
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

# The same verifier runs again inside the isolated, short-lived root helper.
# It receives copied data, never Python code or a public key from the download.
COMMON_SOURCE = r'''
import hashlib
import json
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PUBLIC_KEY = b"-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAcrjpuCd5UD4lJySQSsr9kqPjzjnFAlBK5Jbf+TfyL88=\n-----END PUBLIC KEY-----\n"
BASE_URL = "https://raw.githubusercontent.com/typixdeck/store/main/debs/"
SELECTED = {"ai.typixdeck.launcher": "typix-launcher", "ai.typixdeck.store": "typix-store"}
KEY_PATH = Path("/usr/share/typix-store/keys/bootstrap.pem")
CONFIG_PATH = Path("/etc/typix-store/github.json")
SOURCE_CONFIG = {"repository": "typixdeck/store", "mode": "raw", "ref": "main", "directory": "debs", "publicKey": str(KEY_PATH)}
MAX_CATALOG = 4 * 1024 * 1024
MAX_PACKAGE = 100 * 1024 * 1024
RESERVE = 128 * 1024 * 1024
APT = ["/usr/bin/apt-get", "-o", "DPkg::Lock::Timeout=60", "-o", "Acquire::http::Timeout=15",
       "-o", "Acquire::https::Timeout=15", "-o", "Acquire::Retries=1"]

class InstallError(ValueError):
    pass

def require(condition, message):
    if not condition:
        raise InstallError(message)

def bounded_output(command, limit=65536, timeout=20):
    """Only for read-only inspection; never use this timeout for apt/dpkg writes."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    result = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "Inspection timed out: " + command[0])
            if not select.select([process.stdout], [], [], min(remaining, 0.2))[0]:
                continue
            block = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(result)))
            if not block:
                break
            result.extend(block)
            require(len(result) <= limit, "Inspection output exceeds its size limit")
        code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return code, bytes(result)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        process.stdout.close()

def profile():
    require(os.uname().sysname == "Linux", "Supported platform: official Raspberry Pi OS ARM64")
    code, architecture = bounded_output(["/usr/bin/dpkg", "--print-architecture"])
    require(code == 0 and architecture.strip() == b"arm64", "This installer requires ARM64 userland (dpkg architecture arm64)")
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.startswith("#"):
            release[key] = value.strip().strip('\"').strip("'")
    identifier = release.get("ID")
    official = identifier in {"raspbian", "raspios"} or (identifier == "debian" and Path("/etc/rpi-issue").is_file())
    codename = release.get("VERSION_CODENAME")
    require(official and codename in {"bookworm", "trixie"}, "Supported OS: official Raspberry Pi OS ARM64 Bookworm or Trixie")
    return "raspios-" + codename

def audit_dpkg():
    code, output = bounded_output(["/usr/bin/dpkg", "--audit"], timeout=30)
    require(code == 0 and not output.strip(), "dpkg reports unfinished package work. Run sudo dpkg --audit and resolve it before retrying")

def dependencies(value):
    require(isinstance(value, str) and bool(value.strip()) and "\n" not in value, "Invalid signed dependency declaration")
    return sorted(re.sub(r"\s+", "", clause) for clause in value.split(",") if clause.strip())

def verify_catalog(contents, signature, os_name, now=None):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    require(0 < len(contents) <= MAX_CATALOG and len(signature) == 64, "Catalog/signature size is invalid")
    try:
        load_pem_public_key(PUBLIC_KEY).verify(signature, contents)
    except Exception as exc:
        raise InstallError("Catalog signature does not match the pinned Ed25519 key") from exc
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key in catalog")
            result[key] = value
        return result
    data = json.loads(contents, object_pairs_hook=unique_object)
    require(isinstance(data, dict) and data.get("schemaVersion") == 1, "Unsupported catalog schema")
    require(data.get("channel") == "github-repository" and data.get("repository") == "typixdeck/store", "Catalog repository/channel does not match the pinned source")
    created = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
    expiry = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
    require(created.tzinfo is not None and expiry.tzinfo is not None, "Catalog dates must include a timezone")
    require(created <= (now or datetime.now(timezone.utc)) < expiry, "Catalog is not yet valid or expired; check the device clock and retry the current release")
    entries = data.get("applications")
    require(isinstance(entries, list), "Invalid catalog applications")
    selected, seen_ids, seen_packages = [], set(), set()
    for app in entries:
        require(isinstance(app, dict), "Invalid application record")
        app_id, package = app.get("id"), app.get("package")
        require(isinstance(app_id, str) and isinstance(package, str), "Invalid package identity")
        require(app_id not in seen_ids and package not in seen_packages, "Duplicate application/package identity")
        seen_ids.add(app_id)
        seen_packages.add(package)
        if app_id not in SELECTED:
            require(package not in SELECTED.values(), "Selected package has an unexpected application ID")
            continue
        require(package == SELECTED[app_id], "Selected application/package identity mismatch")
        version = app.get("currentVersion")
        require(isinstance(version, str) and re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]*", version), "Invalid package version")
        versions = app.get("versions")
        require(isinstance(versions, list) and all(isinstance(item, dict) for item in versions), "Invalid version records")
        records = [item for item in versions if item.get("version") == version]
        require(len(records) == 1, "Selected package lacks a unique current version")
        record = records[0]
        artifact, compatibility = record.get("artifact"), record.get("compatibility")
        require(isinstance(artifact, dict) and isinstance(compatibility, dict), "Invalid package metadata")
        require(artifact.get("payload") == "complete-deb", "Installer accepts complete deb payloads only")
        require(artifact.get("arch") in {"all", "arm64"}, "Package architecture is incompatible")
        require(artifact.get("filename") == f"{package}_{version}_{artifact['arch']}.deb", "Package filename does not match its signed identity")
        require(isinstance(artifact.get("sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", artifact["sha256"]), "Invalid signed SHA-256")
        require(type(artifact.get("sizeBytes")) is int and 0 < artifact["sizeBytes"] <= MAX_PACKAGE, "Package download exceeds 100 MiB limit")
        require(type(artifact.get("installedSizeBytes")) is int and 0 < artifact["installedSizeBytes"] <= 2 * 1024**3, "Invalid expanded installation size")
        dependencies(artifact.get("depends"))
        for name in ("arch", "os"):
            require(isinstance(compatibility.get(name), list) and all(isinstance(item, str) for item in compatibility[name]), "Invalid compatibility list")
        require("arm64" in compatibility["arch"] and os_name in compatibility["os"], "Signed package does not support this operating system")
        require(set(compatibility["os"]).issubset({"raspios-bookworm", "raspios-trixie"}), "Unsupported OS compatibility declaration")
        require(type(compatibility.get("minFreeDiskMB")) is int and 0 <= compatibility["minFreeDiskMB"] <= 4096, "Invalid free space requirement")
        selected.append({"id": app_id, "package": package, "version": version, "artifact": artifact, "compatibility": compatibility})
    require({item["id"] for item in selected} == set(SELECTED), "Signed source must contain both Launcher and Store; nothing has been installed")
    return sorted(selected, key=lambda item: item["package"])

def verify_package(path, item):
    artifact = item["artifact"]
    require(path.stat().st_size == artifact["sizeBytes"], "Package size mismatch: " + item["package"])
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(262144), b""):
            digest.update(block)
    require(digest.hexdigest() == artifact["sha256"].lower(), "Package SHA-256 mismatch: " + item["package"])
    fields = ["Package", "Version", "Architecture", "Depends", "X-Typix-Compatible-OS"]
    code, output = bounded_output(["/usr/bin/dpkg-deb", "-f", str(path), *fields])
    require(code == 0, "Cannot read package control fields")
    actual = {}
    for line in output.decode("utf-8").splitlines():
        key, separator, value = line.partition(":")
        require(separator and key in fields and key not in actual, "Invalid or duplicate package control field")
        actual[key] = value.strip()
    require(actual.get("Package") == item["package"] and actual.get("Version") == item["version"] and actual.get("Architecture") == artifact["arch"], "Package identity differs from signed metadata")
    require(dependencies(actual.get("Depends")) == dependencies(artifact["depends"]), "Package dependencies differ from signed metadata")
    require({part.strip() for part in actual.get("X-Typix-Compatible-OS", "").split(",") if part.strip()} == set(item["compatibility"]["os"]), "Package OS compatibility differs from signed metadata")

def version_check(items):
    for item in items:
        code, output = bounded_output(["/usr/bin/dpkg-query", "-W", "-f=${db:Status-Status}\\t${Version}", item["package"]])
        status, _, installed = output.decode().partition("\t")
        if code == 0 and status == "installed":
            installed = installed.strip()
            result, _ = bounded_output(["/usr/bin/dpkg", "--compare-versions", installed, "gt", item["version"]])
            require(result in (0, 1), "Cannot compare installed package version")
            require(result != 0, "Refusing downgrade of " + item["package"] + " (installed " + installed + ")")
            action = "already installed" if installed == item["version"] else "update from " + installed
        else:
            action = "new installation"
        print(f"  {item['package']} {item['version']} ({action})", flush=True)

def space_check(directory, items, staging=False):
    downloads = sum(item["artifact"]["sizeBytes"] for item in items)
    expanded = sum(max(item["artifact"]["installedSizeBytes"], item["compatibility"]["minFreeDiskMB"] * 1024**2) for item in items)
    require(shutil.disk_usage(directory).free >= downloads + RESERVE, "Not enough download/staging disk space")
    require(shutil.disk_usage("/").free >= expanded + downloads + RESERVE, "Not enough space for expanded packages plus recovery reserve")

def root_owned(path, directory=False):
    require(path.is_absolute(), "System path must be absolute")
    for component in reversed((path, *path.parents)):
        details = component.lstat()
        require(details.st_uid == 0 and not details.st_mode & 0o022 and not stat.S_ISLNK(details.st_mode), "Unsafe system path ownership: " + str(component))
        require(stat.S_ISDIR(details.st_mode) if component != path or directory else stat.S_ISREG(details.st_mode), "Unexpected system path type: " + str(component))

def check_config():
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_public_key
    expected_key = load_pem_public_key(PUBLIC_KEY).public_bytes(Encoding.Raw, PublicFormat.Raw)
    def key_matches(path):
        root_owned(path)
        require(path.stat().st_size <= 4096, "System public key is too large")
        actual = load_pem_public_key(path.read_bytes()).public_bytes(Encoding.Raw, PublicFormat.Raw)
        require(actual == expected_key, "Existing Store trust key conflicts with the pinned installer key")
    for path in (KEY_PATH, CONFIG_PATH):
        for parent in reversed(path.parents):
            if os.path.lexists(parent):
                root_owned(parent, directory=True)
        if os.path.lexists(path):
            root_owned(path)
    if os.path.lexists(KEY_PATH):
        key_matches(KEY_PATH)
    if os.path.lexists(CONFIG_PATH):
        require(CONFIG_PATH.stat().st_size <= 8192, "Existing Store configuration is too large")
        config = json.loads(CONFIG_PATH.read_bytes())
        require(isinstance(config, dict) and all(config.get(key) == value for key, value in SOURCE_CONFIG.items() if key != "publicKey"), "Existing customized Store source conflicts; it has been preserved. Review /etc/typix-store/github.json before installing")
        require(isinstance(config.get("publicKey"), str), "Existing Store source lacks a trusted key")
        key_matches(Path(config["publicKey"]))
'''
exec(COMMON_SOURCE)

ROOT_SOURCE = r'''
import sys
import syslog

def make_system_directory(path):
    for component in reversed((path, *path.parents)):
        if not os.path.lexists(component):
            root_owned(component.parent, directory=True)
            component.mkdir(mode=0o755)
        root_owned(component, directory=True)

def copy_input(source_fd, name, destination, uid, limit):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
    try:
        details = os.fstat(descriptor)
        require(stat.S_ISREG(details.st_mode) and details.st_uid == uid and 0 < details.st_size <= limit, "Input must be a bounded regular file owned by the invoking user")
        with os.fdopen(descriptor, "rb", closefd=False) as source, destination.open("xb") as output:
            total = 0
            while True:
                block = source.read(min(262144, limit - total + 1))
                if not block:
                    break
                total += len(block)
                require(total <= limit, "Input changed or exceeded its size limit while staging")
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o444)
    finally:
        os.close(descriptor)

def create_system_file(path, contents):
    make_system_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".bootstrap-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        # link is exclusive; it cannot overwrite an existing file or symlink.
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)

def apt_transaction(arguments):
    print("apt will show its proposed changes and ask normally. Once package work begins, wait for completion; this installer will not force-kill dpkg.", flush=True)
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        result = subprocess.run(APT + arguments, stdin=sys.stdin, check=False,
                                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "DEBIAN_FRONTEND": "readline"})
        require(result.returncode == 0, "apt did not complete; existing settings/data were preserved. Inspect apt output and dpkg --audit before retrying")
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)

def root_main():
    require(os.geteuid() == 0, "The staging helper requires sudo")
    uid = int(os.environ.get("SUDO_UID", "0"))
    require(uid > 0, "Invoke the installer as your ordinary desktop user, without sudo")
    profile()
    audit_dpkg()
    if sys.argv[1:] == ["--dependencies"]:
        apt_transaction(["update"])
        apt_transaction(["--no-remove", "install", "python3-cryptography", "ca-certificates"])
        return
    require(len(sys.argv) == 2, "Invalid staging request")
    check_config()
    source = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        details = os.fstat(source)
        require(details.st_uid == uid and not details.st_mode & 0o077, "Staging input directory must be private and owned by the invoking user")
        destination = Path("/var/cache/typix-bootstrap")
        make_system_directory(destination)
        require(shutil.disk_usage(destination).free >= MAX_CATALOG + RESERVE, "Insufficient root staging space")
        with tempfile.TemporaryDirectory(prefix="install-", dir=destination) as temporary:
            stage = Path(temporary)
            copy_input(source, "catalog.json", stage / "catalog.json", uid, MAX_CATALOG)
            copy_input(source, "catalog.json.sig", stage / "catalog.json.sig", uid, 64)
            items = verify_catalog((stage / "catalog.json").read_bytes(), (stage / "catalog.json.sig").read_bytes(), profile())
            space_check(stage, items)
            paths = []
            for item in items:
                path = stage / item["artifact"]["filename"]
                copy_input(source, path.name, path, uid, item["artifact"]["sizeBytes"])
                verify_package(path, item)
                root_owned(path)
                paths.append(str(path))
            space_check(stage, items, staging=True)
            version_check(items)
            audit_dpkg()
            check_config()
            # apt's _apt user may read these immutable-to-users files.
            stage.chmod(0o755)
            syslog.openlog("typix-bootstrap")
            for item in items:
                syslog.syslog(syslog.LOG_NOTICE, f"verified install uid={uid} package={item['package']} version={item['version']} sha256={item['artifact']['sha256']}")
            apt_transaction(["--no-remove", "-o", "Dpkg::Options::=--force-confold", "install", *paths])
            audit_dpkg()
            check_config()
            if not os.path.lexists(KEY_PATH):
                create_system_file(KEY_PATH, PUBLIC_KEY)
            if not os.path.lexists(CONFIG_PATH):
                create_system_file(CONFIG_PATH, (json.dumps(SOURCE_CONFIG, indent=2) + "\n").encode())
            check_config()
    finally:
        os.close(source)

try:
    root_main()
except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
    print("Installation stopped: " + str(exc), file=sys.stderr)
    raise SystemExit(1)
'''


def download(filename, destination, limit):
    import urllib.parse
    import urllib.request

    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_~:-]*", filename), "Invalid download filename")
    url = BASE_URL + urllib.parse.quote(filename, safe="")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise InstallError("Unexpected redirect from the pinned GitHub source")

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "TypixDeckBootstrap/1", "Accept-Encoding": "identity"})
    deadline = time.monotonic() + 90
    with opener.open(request, timeout=10) as response, destination.open("xb") as output:
        require(response.status == 200 and response.geturl() == url, "Source did not return a complete pinned asset")
        declared = response.headers.get("Content-Length")
        require(declared is None or 0 < int(declared) <= limit, "Download exceeds its declared size limit")
        total = 0
        while True:
            require(time.monotonic() < deadline, "Download deadline reached; rerun the installer to retry")
            require(shutil.disk_usage(destination.parent).free >= RESERVE, "Download stopped before exhausting disk space")
            block = response.read1(min(65536, limit - total + 1))
            require(time.monotonic() <= deadline, "Download deadline reached; rerun the installer to retry")
            if not block:
                break
            total += len(block)
            require(total <= limit, "Download exceeded its size limit")
            output.write(block)
        require(total > 0 and (declared is None or total == int(declared)), "Incomplete download; rerun to retry")


def run_privileged(argument):
    require(Path("/usr/bin/sudo").is_file(), "sudo is required for bounded package installation")
    # The script is piped into sh, so obtain prompts from the actual terminal.
    try:
        terminal = open("/dev/tty", "rb", buffering=0)
    except OSError as exc:
        raise InstallError("Installation needs a terminal for sudo/apt prompts. Download install.sh, then run sh install.sh in your terminal") from exc
    command = ["/usr/bin/sudo", "/usr/bin/python3", "-I", "-c", COMMON_SOURCE + "\n" + ROOT_SOURCE, argument]
    previous = signal.getsignal(signal.SIGINT)
    try:
        with terminal:
            process = subprocess.Popen(command, stdin=terminal)
            while True:
                try:
                    result = process.wait()
                    break
                except KeyboardInterrupt:
                    print("Waiting for the authorization/package transaction to finish safely…", file=sys.stderr)
            require(result == 0, "Authorization was declined or installation failed; review the message above. No automatic retry was attempted")
    finally:
        signal.signal(signal.SIGINT, previous)


def desktop_shortcut():
    code, output = bounded_output(["/usr/bin/xdg-user-dir", "DESKTOP"])
    directory = Path(output.decode().strip()) if code == 0 else Path.home() / "Desktop"
    require(directory.is_absolute(), "Desktop path must be absolute")
    if directory.resolve() == Path.home().resolve():
        print("Desktop shortcuts are disabled by the user; no shortcut added.")
        return
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "typix-store.desktop"
    if os.path.lexists(target):
        print("Existing Store desktop shortcut preserved.")
        return
    source = Path("/usr/share/applications/typix-store.desktop")
    root_owned(source)
    require(source.stat().st_size <= 16384, "Store desktop entry is unexpectedly large")
    descriptor, temporary = tempfile.mkstemp(prefix=".typix-store-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            print("Existing Store desktop shortcut preserved.")
            return
    finally:
        os.unlink(temporary)
    print("Added Store shortcut: " + str(target))


def main():
    parser = argparse.ArgumentParser(description="Install Launcher and Store from the pinned signed TypixDeck GitHub source. Run as your desktop user.")
    parser.add_argument("--check", action="store_true", help="download, verify and simulate apt resolution; never invoke sudo or change the system")
    parser.add_argument("--start", action="store_true", help="explicitly start the Launcher user service after installing; never enable or restart it")
    args = parser.parse_args()
    require(not (args.check and args.start), "--check and --start cannot be combined")
    require(os.geteuid() != 0, "Run this installer as your ordinary desktop user, not with sudo")
    os_name = profile()
    audit_dpkg()
    try:
        import cryptography
    except ImportError:
        require(not args.check, "--check requires python3-cryptography; install it with sudo apt-get install python3-cryptography, then retry --check")
        print("Installing the standard Raspberry Pi OS signature-verification dependency.", flush=True)
        run_privileged("--dependencies")
    check_config()
    print("Verifying signed Launcher + Store packages for " + os_name + " (arm64)…", flush=True)
    with tempfile.TemporaryDirectory(prefix="typix-install-") as temporary:
        directory = Path(temporary)
        require(shutil.disk_usage(directory).free >= MAX_CATALOG + RESERVE, "Not enough temporary download space")
        download("catalog.json", directory / "catalog.json", MAX_CATALOG)
        download("catalog.json.sig", directory / "catalog.json.sig", 64)
        items = verify_catalog((directory / "catalog.json").read_bytes(), (directory / "catalog.json.sig").read_bytes(), os_name)
        space_check(directory, items)
        version_check(items)
        paths = []
        for item in items:
            path = directory / item["artifact"]["filename"]
            download(path.name, path, item["artifact"]["sizeBytes"])
            verify_package(path, item)
            paths.append(str(path))
        space_check(directory, items)
        print("Signatures, hashes, package fields and available disk space verified.", flush=True)
        simulation = subprocess.run(APT + ["--simulate", "--no-remove", "install", *paths], check=False, timeout=90)
        require(simulation.returncode == 0, "apt preflight failed. Review its output; package indexes may need sudo apt-get update. No application packages were changed")
        if args.check:
            print("CHECK PASSED: both complete packages verified; no sudo or system changes performed.")
            return 0
        run_privileged(str(directory))
    desktop_shortcut()
    subprocess.run(["/usr/bin/systemctl", "--user", "daemon-reload"], check=False)
    print("Launcher and Store are installed. Existing settings and data were preserved.")
    if args.start:
        result = subprocess.run(["/usr/bin/systemctl", "--user", "start", "typix-launcher.service"], check=False)
        require(result.returncode == 0, "Packages are installed, but Launcher could not start in this user session")
    else:
        print("Start when ready: systemctl --user start typix-launcher.service\nAutostart remains your choice in Launcher Settings (F9).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Cancelled before package installation; temporary downloads removed. Rerun to retry.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("Installation stopped: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
__TYPIX_INSTALL_PYTHON__
