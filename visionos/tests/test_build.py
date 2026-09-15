"""Offline regression tests. Synthetic Git fixtures do not replace a real SDK build."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build
import patches


def command(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE).strip()


def repository(path, patch_set):
    path.mkdir()
    command(path, "init", "-q")
    command(path, "config", "user.name", "ALVR Test")
    command(path, "config", "user.email", "test@example.invalid")
    for name, replacements in patch_set.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exact pinned-source anchors, separated by inert fixture text.
        target.write_text("\n".join(old for old, _, _ in replacements))
    command(path, "add", ".")
    command(path, "commit", "-qm", "fixture")
    return command(path, "rev-parse", "HEAD")


class PatchTests(unittest.TestCase):
    def test_every_patch_applies_to_its_preimage(self):
        for mapping in (patches.CLIENT_PATCHES, patches.CORE_PATCHES):
            for name, replacements in mapping.items():
                source = "\n".join(old for old, _, _ in replacements)
                output = patches.transform(source, replacements, name)
                for _, new, _ in replacements:
                    self.assertIn(new, output)

    def test_missing_anchor_fails_closed(self):
        with self.assertRaises(RuntimeError):
            patches.transform("upstream changed", [("expected", "replacement", 1)], "test")

    def test_duplicate_anchor_fails_closed(self):
        with self.assertRaises(RuntimeError):
            patches.transform("expected expected", [("expected", "replacement", 1)], "test")

    def test_start_configures_before_resuming_core(self):
        old, new, _ = patches.CLIENT_PATCHES["ALVRClient/EventHandler.swift"][2]
        self.assertLess(new.index("guard fixAudioForDirectStereo()"), new.index("alvr_resume()"))

    def test_policy_has_no_recording_category(self):
        self.assertIn(".playback", patches.AUDIO_METHODS)
        self.assertIn(".mixWithOthers", patches.AUDIO_METHODS)
        self.assertNotIn(".playAndRecord", patches.AUDIO_METHODS)
        self.assertNotIn(".voiceChat", patches.AUDIO_METHODS)

    def test_both_core_input_paths_are_compile_time_gated(self):
        items = patches.CORE_PATCHES["alvr/client_core/src/connection.rs"]
        for _, new, _ in items:
            self.assertIn('#[cfg(not(feature = "external-voice-chat"))]', new)
            self.assertIn('#[cfg(feature = "external-voice-chat")]', new)
        self.assertIn("48_000", items[0][1])
        self.assertIn("thread::spawn(|| ())", items[1][1])


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = self.root / "client-upstream"
        self.core = self.root / "core-upstream"
        self.core_rev = repository(self.core, patches.CORE_PATCHES)
        repository(self.client, patches.CLIENT_PATCHES)
        command(self.client, "update-index", "--add", "--cacheinfo", f"160000,{self.core_rev},ALVR")
        command(self.client, "commit", "-qm", "pin core")
        self.client_rev = command(self.client, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def pins(self):
        with mock.patch.multiple(build, CLIENT_URL=str(self.client), CLIENT_REV=self.client_rev,
                                 CORE_URL=str(self.core), CORE_REV=self.core_rev):
            yield

    def test_staging_does_not_write_and_repeated_application_is_noop(self):
        original = (self.client / "ALVRClient/EventHandler.swift").read_bytes()
        changes = build.staged_patches(self.client, patches.CLIENT_PATCHES)
        self.assertEqual((self.client / "ALVRClient/EventHandler.swift").read_bytes(), original)
        for path, content in changes:
            build.atomic_write(path, content)
        self.assertEqual(build.staged_patches(self.client, patches.CLIENT_PATCHES), [])

    def test_refuses_to_overwrite_user_source_edits(self):
        path = self.client / "ALVRClient/EventHandler.swift"
        path.write_text(path.read_text() + "// user edit\n")
        with self.assertRaisesRegex(RuntimeError, "Local changes"):
            build.staged_patches(self.client, patches.CLIENT_PATCHES)
        self.assertTrue(path.read_text().endswith("// user edit\n"))

    def test_end_to_end_prepare_and_repeat_with_local_git_remotes(self):
        with self.pins():
            client = build.prepare(self.root / "work", "com.example.ALVR", None)
            first = (client / "ALVRClient/EventHandler.swift").read_bytes()
            build.prepare(self.root / "work", "com.example.ALVR", None)
            self.assertEqual((client / "ALVRClient/EventHandler.swift").read_bytes(), first)
            self.assertIn(b".playback", first)
            cargo = (client / "ALVR/alvr/client_core/Cargo.toml").read_text()
            self.assertIn("external-voice-chat = []", cargo)

    def test_checkout_rejects_wrong_revision_without_reset(self):
        with self.assertRaisesRegex(RuntimeError, "Unexpected checkout"):
            build.checkout(str(self.client), "0" * 40, self.client)
        self.assertEqual(command(self.client, "rev-parse", "HEAD"), self.client_rev)

    def test_pin_mismatch_is_rejected_before_core_download(self):
        with self.pins(), mock.patch.object(build, "CORE_REV", "0" * 40):
            with self.assertRaisesRegex(RuntimeError, "client/core mismatch"):
                build.prepare(self.root / "work", "com.example.ALVR", None)
        self.assertFalse((self.root / "work/alvr-visionos/ALVR/.git").exists())

    def test_signing_setup_rebrands_both_targets_and_preserves_xcode_edits(self):
        project = self.client / "ALVRClient.xcodeproj/project.pbxproj"
        project.parent.mkdir()
        project.write_text("PRODUCT_BUNDLE_IDENTIFIER = alvr.client;\n"
                           "PRODUCT_BUNDLE_IDENTIFIER = alvr.client.ALVREyeBroadcast;\n"
                           "DEVELOPMENT_TEAM = A2R992S5N3;\n"
                           "INFOPLIST_KEY_CFBundleDisplayName = ALVR;\n")
        entitlement = self.client / "ALVRClient/ALVRClient.entitlements"
        entitlement.write_text("<string>group.alvr.client.ALVR</string>")
        build.personalize(self.client, "com.example.ALVR", "ABCDEFGHIJ")
        self.assertIn("com.example.ALVR.ALVREyeBroadcast", project.read_text())
        self.assertIn('DEVELOPMENT_TEAM = "ABCDEFGHIJ";', project.read_text())
        self.assertIn("group.com.example.ALVR.ALVR", entitlement.read_text())
        project.write_text(project.read_text() + "// edited in Xcode\n")
        before = project.read_bytes()
        build.personalize(self.client, "com.example.ALVR", "ABCDEFGHIJ")
        self.assertEqual(project.read_bytes(), before)
        with self.assertRaisesRegex(RuntimeError, "Signing options differ"):
            build.personalize(self.client, "com.other.ALVR", None)

    def test_atomic_write_preserves_mode(self):
        target = self.root / "executable"
        target.write_text("old")
        target.chmod(0o755)
        build.atomic_write(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)


class SwiftPolicyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("swiftc"), "Swift compiler not installed")
    def test_actual_replacement_methods_with_mock_audio_session(self):
        template = Path(__file__).with_name("AudioSessionHarness.swift").read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.swift"
            source.write_text(template.replace("// INSERT_AUDIO_METHODS", patches.AUDIO_METHODS))
            subprocess.run(["swiftc", "-swift-version", "5", source, "-o", root / "test"], check=True)
            subprocess.run([root / "test"], check=True)


if __name__ == "__main__":
    unittest.main()
