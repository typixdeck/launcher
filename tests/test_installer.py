"""Bootstrap security boundaries; no sudo, live network, or system writes."""
import copy
import hashlib
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def load_installer():
    contents = INSTALLER.read_text()
    source = contents.split("<<'__TYPIX_INSTALL_PYTHON__'\n", 1)[1].rsplit("\n__TYPIX_INSTALL_PYTHON__", 1)[0]
    namespace = {"__name__": "installer_test"}
    exec(compile(source, str(INSTALLER), "exec"), namespace)
    # Expose root helpers without entering root_main.
    exec(namespace["ROOT_SOURCE"].split("\ntry:\n    root_main()", 1)[0], namespace)
    return namespace


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.api = load_installer()
        self.private = Ed25519PrivateKey.generate()
        self.api["PUBLIC_KEY"] = self.private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        self.now = datetime.now(timezone.utc)
        self.payload = b"bounded package fixture"
        entries = []
        for app_id, package in self.api["SELECTED"].items():
            entries.append({
                "id": app_id, "package": package, "currentVersion": "1.0-1",
                "versions": [{"version": "1.0-1", "artifact": {
                    "arch": "all", "filename": f"{package}_1.0-1_all.deb", "payload": "complete-deb",
                    "sizeBytes": len(self.payload), "sha256": hashlib.sha256(self.payload).hexdigest(),
                    "installedSizeBytes": 4096, "depends": "python3 (>= 3.11), python3-gi",
                }, "compatibility": {"arch": ["arm64"], "os": ["raspios-bookworm", "raspios-trixie"], "minFreeDiskMB": 64}}],
            })
        self.catalog = {"schemaVersion": 1, "channel": "github-repository", "repository": "typixdeck/store",
                        "generatedAt": (self.now - timedelta(minutes=1)).isoformat(),
                        "expiresAt": (self.now + timedelta(days=1)).isoformat(), "applications": entries}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def verify(self, data=None):
        contents = json.dumps(data or self.catalog).encode()
        return self.api["verify_catalog"](contents, self.private.sign(contents), "raspios-trixie", self.now)

    def test_both_pinned_applications_are_selected(self):
        self.assertEqual([item["package"] for item in self.verify()], ["typix-launcher", "typix-store"])

    def test_exact_raw_bytes_and_pinned_key_are_required(self):
        contents = json.dumps(self.catalog).encode()
        for data, signature in [(contents + b"\n", self.private.sign(contents)),
                                (contents, Ed25519PrivateKey.generate().sign(contents))]:
            with self.assertRaises(self.api["InstallError"]):
                self.api["verify_catalog"](data, signature, "raspios-trixie", self.now)

    def test_signed_but_wrong_source_expired_and_missing_package_are_rejected(self):
        for field, value in [("repository", "attacker/store"), ("channel", "development-offline"),
                             ("expiresAt", (self.now - timedelta(seconds=1)).isoformat()),
                             ("generatedAt", (self.now + timedelta(days=1)).isoformat()),
                             ("applications", self.catalog["applications"][:1])]:
            with self.subTest(field=field):
                bad = copy.deepcopy(self.catalog)
                bad[field] = value
                with self.assertRaises(self.api["InstallError"]):
                    self.verify(bad)

    def test_signed_identity_payload_arch_and_filename_conflicts_rejected(self):
        for field, value in [("payload", "wrapper"), ("arch", "armhf"),
                             ("filename", "../escape.deb"), ("sizeBytes", 100 * 1024**2 + 1)]:
            with self.subTest(field=field):
                bad = copy.deepcopy(self.catalog)
                bad["applications"][0]["versions"][0]["artifact"][field] = value
                with self.assertRaises(self.api["InstallError"]):
                    self.verify(bad)

    def test_signed_duplicate_json_keys_are_rejected(self):
        contents = json.dumps(self.catalog).encode().replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1')
        with self.assertRaises(self.api["InstallError"]):
            self.api["verify_catalog"](contents, self.private.sign(contents), "raspios-trixie", self.now)

    def test_package_hash_and_each_control_field_are_checked(self):
        item = self.verify()[0]
        path = self.directory / item["artifact"]["filename"]
        path.write_bytes(self.payload)
        fields = {"Package": item["package"], "Version": item["version"], "Architecture": "all",
                  "Depends": "python3(>=3.11),python3-gi", "X-Typix-Compatible-OS": "raspios-bookworm, raspios-trixie"}
        self.api["bounded_output"] = lambda _args: (0, "\n".join(f"{key}: {value}" for key, value in fields.items()).encode())
        self.api["verify_package"](path, item)
        for name in fields:
            with self.subTest(field=name):
                original = fields[name]
                fields[name] = "wrong"
                with self.assertRaises(self.api["InstallError"]):
                    self.api["verify_package"](path, item)
                fields[name] = original
        path.write_bytes(b"x" * len(self.payload))
        with self.assertRaisesRegex(self.api["InstallError"], "SHA-256"):
            self.api["verify_package"](path, item)

    def test_nonempty_dpkg_audit_output_fails_even_with_exit_zero(self):
        self.api["bounded_output"] = lambda *args, **kwargs: (0, b"package left unconfigured\n")
        with self.assertRaises(self.api["InstallError"]):
            self.api["audit_dpkg"]()

    def test_installed_newer_version_refuses_downgrade(self):
        self.api["bounded_output"] = Mock(side_effect=[(0, b"installed\t2.0-1"), (0, b"")])
        with self.assertRaisesRegex(self.api["InstallError"], "downgrade"):
            self.api["version_check"](self.verify()[:1])

    def test_copy_uses_nofollow_and_caller_ownership(self):
        source = self.directory / "source"
        source.mkdir()
        (source / "package.deb").write_bytes(self.payload)
        (source / "linked.deb").symlink_to(source / "package.deb")
        descriptor = os.open(source, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        destination = self.directory / "copy.deb"
        self.api["copy_input"](descriptor, "package.deb", destination, os.getuid(), len(self.payload))
        self.assertEqual(destination.read_bytes(), self.payload)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o444)
        with self.assertRaises(OSError):
            self.api["copy_input"](descriptor, "linked.deb", self.directory / "link-copy.deb", os.getuid(), 100)
        with self.assertRaises(self.api["InstallError"]):
            self.api["copy_input"](descriptor, "package.deb", self.directory / "wrong-owner.deb", os.getuid() + 1, 100)

    def test_staged_copy_is_independent_and_reverified_after_source_changes(self):
        item = self.verify()[0]
        source = self.directory / "source.deb"
        source.write_bytes(self.payload)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        destination = self.directory / "root-copy.deb"
        self.api["copy_input"](descriptor, source.name, destination, os.getuid(), len(self.payload))
        source.write_bytes(b"changed")
        self.assertEqual(destination.read_bytes(), self.payload)
        changed = dict(item, artifact=dict(item["artifact"], sha256="0" * 64))
        with self.assertRaisesRegex(self.api["InstallError"], "SHA-256"):
            self.api["verify_package"](destination, changed)

    def test_conflicting_system_config_is_preserved_and_rejected(self):
        config = self.directory / "github.json"
        contents = json.dumps(dict(self.api["SOURCE_CONFIG"], repository="custom/store"))
        config.write_text(contents)
        self.api["CONFIG_PATH"] = config
        self.api["KEY_PATH"] = self.directory / "missing.pem"
        self.api["root_owned"] = Mock()
        with self.assertRaisesRegex(self.api["InstallError"], "customized"):
            self.api["check_config"]()
        self.assertEqual(config.read_text(), contents)

    def test_equivalent_config_may_use_existing_matching_development_key(self):
        key = self.directory / "development.pem"
        key.write_bytes(self.api["PUBLIC_KEY"])
        config = self.directory / "github.json"
        config.write_text(json.dumps(dict(self.api["SOURCE_CONFIG"], publicKey=str(key))))
        self.api["CONFIG_PATH"] = config
        self.api["KEY_PATH"] = self.directory / "bootstrap.pem"
        self.api["root_owned"] = Mock()
        self.api["check_config"]()
        self.assertFalse(self.api["KEY_PATH"].exists())

    def test_low_expanded_disk_space_is_rejected(self):
        with patch.object(self.api["shutil"], "disk_usage", side_effect=[SimpleNamespace(free=1024**3), SimpleNamespace(free=1024)]):
            with self.assertRaisesRegex(self.api["InstallError"], "expanded"):
                self.api["space_check"](self.directory, self.verify(), staging=True)

    def test_check_downloads_and_verifies_without_sudo_or_shortcut_changes(self):
        self.api["profile"] = lambda: "raspios-trixie"
        self.api["audit_dpkg"] = Mock()
        self.api["check_config"] = Mock()
        self.api["space_check"] = Mock()
        self.api["version_check"] = Mock()
        self.api["verify_package"] = Mock()
        self.api["run_privileged"] = Mock(side_effect=AssertionError("--check invoked sudo"))
        self.api["desktop_shortcut"] = Mock(side_effect=AssertionError("--check changed desktop"))
        contents = json.dumps(self.catalog).encode()
        downloads = {"catalog.json": contents, "catalog.json.sig": self.private.sign(contents)}
        self.api["download"] = lambda name, target, limit: target.write_bytes(downloads.get(name, self.payload))
        with patch.object(self.api["os"], "geteuid", return_value=1000), \
                patch.object(self.api["sys"], "argv", ["install.sh", "--check"]), \
                patch.object(self.api["subprocess"], "run", return_value=SimpleNamespace(returncode=0)) as runner:
            self.assertEqual(self.api["main"](), 0)
        self.assertEqual(self.api["verify_package"].call_count, 2)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("--simulate", runner.call_args.args[0])

    def test_download_uses_read1_and_refuses_oversized_body(self):
        import urllib.request
        response = Mock()
        response.status = 200
        response.headers = {}
        response.geturl.return_value = self.api["BASE_URL"] + "catalog.json"
        response.read1.side_effect = [b"too large", b""]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(urllib.request, "build_opener", return_value=SimpleNamespace(open=lambda *args, **kwargs: response)), \
                patch.object(self.api["shutil"], "disk_usage", return_value=SimpleNamespace(free=1024**3)):
            with self.assertRaisesRegex(self.api["InstallError"], "size limit"):
                self.api["download"]("catalog.json", self.directory / "catalog.json", 4)
        response.read.assert_not_called()

    def test_download_checks_deadline_after_each_read1(self):
        import urllib.request
        response = Mock()
        response.status = 200
        response.headers = {}
        response.geturl.return_value = self.api["BASE_URL"] + "catalog.json"
        response.read1.return_value = b"data"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(urllib.request, "build_opener", return_value=SimpleNamespace(open=lambda *args, **kwargs: response)), \
                patch.object(self.api["shutil"], "disk_usage", return_value=SimpleNamespace(free=1024**3)), \
                patch.object(self.api["time"], "monotonic", side_effect=[0, 1, 91]):
            with self.assertRaisesRegex(self.api["InstallError"], "deadline"):
                self.api["download"]("catalog.json", self.directory / "catalog.json", 10)
        self.assertEqual((self.directory / "catalog.json").stat().st_size, 0)

    def test_disabled_desktop_does_not_create_a_home_shortcut(self):
        self.api["bounded_output"] = lambda *args: (0, str(self.directory).encode())
        with patch.object(Path, "home", return_value=self.directory):
            self.api["desktop_shortcut"]()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_shortcut_disk_failure_never_leaves_a_partial_final_file(self):
        source = Path("/usr/share/applications/typix-store.desktop")
        self.api["bounded_output"] = lambda *args: (0, str(self.directory).encode())
        self.api["root_owned"] = Mock()
        original_stat = Path.stat
        original_read = Path.read_bytes
        with patch.object(Path, "stat", lambda path, *args, **kwargs: SimpleNamespace(st_size=20) if path == source else original_stat(path, *args, **kwargs)), \
                patch.object(Path, "read_bytes", lambda path: b"[Desktop Entry]\n" if path == source else original_read(path)), \
                patch.object(self.api["os"], "fsync", side_effect=OSError("No space left on device")):
            with self.assertRaises(OSError):
                self.api["desktop_shortcut"]()
        self.assertFalse((self.directory / "typix-store.desktop").exists())
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
