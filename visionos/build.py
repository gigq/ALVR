#!/usr/bin/env python3
"""Prepare/build a playback-only ALVR Vision Pro app. Requires Python 3.9+."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

from patches import CLIENT_PATCHES, CORE_PATCHES, transform

CLIENT_URL = "https://github.com/alvr-org/alvr-visionos.git"
CLIENT_REV = "fb2576a2a4df23a20647e327acc4d8f785da0d77"
CORE_URL = "https://github.com/alvr-org/ALVR.git"
CORE_REV = "e3fd448029c795b1b2d5835c84c6588bf01bae0d"
ROOT = Path(__file__).resolve().parent
DEFAULT_WORK = ROOT / "_build"


def run(args, cwd=None, env=None, capture=False):
    args = [str(arg) for arg in args]
    print("+ " + shlex.join(args), flush=True)
    return subprocess.run(args, cwd=cwd, env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def git_text(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def checkout(url: str, revision: str, destination: Path) -> None:
    if destination.is_symlink():
        raise RuntimeError(f"Refusing symlink checkout: {destination}")
    if destination.exists() and not any(destination.iterdir()):
        destination.rmdir()  # Git may leave an empty submodule directory.
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A failed download must not leave a half-initialized checkout in use.
        with tempfile.TemporaryDirectory(prefix=".alvr-fetch-", dir=destination.parent) as tmp:
            temp = Path(tmp) / "source"
            run(["git", "init", temp])
            run(["git", "remote", "add", "origin", url], cwd=temp)
            run(["git", "fetch", "--depth", "1", "origin", revision], cwd=temp)
            run(["git", "checkout", "--detach", revision], cwd=temp)
            temp.rename(destination)
    root = Path(git_text(destination, "rev-parse", "--show-toplevel").strip()).resolve()
    head = git_text(destination, "rev-parse", "HEAD").strip()
    if root != destination.resolve() or head != revision:
        raise RuntimeError(f"Unexpected checkout at {destination}; expected {revision}. "
                           "Use --work-dir with a fresh directory. No files were reset.")


def staged_patches(repo: Path, patches) -> List[Tuple[Path, bytes]]:
    changes = []
    for name, replacements in patches.items():
        original = git_text(repo, "show", "HEAD:" + name)
        updated = transform(original, replacements, name).encode("utf-8")
        path = repo / name
        if path.is_symlink():
            raise RuntimeError(f"Refusing to patch symlink: {path}")
        current = path.read_bytes()
        if current == updated:
            continue
        if current != original.encode("utf-8"):
            raise RuntimeError(f"Local changes in {path}; refusing to overwrite them. "
                               "Use a fresh --work-dir or preserve your changes manually.")
        changes.append((path, updated))
    return changes


def atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(content)
    try:
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def personalize(client: Path, bundle_id: str, team: Optional[str]) -> None:
    """Configure both targets once; later runs preserve Xcode signing edits."""
    marker = client / ".external-voice-chat-signing.json"
    if marker.exists():
        prior = json.loads(marker.read_text())
        if prior["bundle_id"] != bundle_id or (team and prior["team"] != team):
            raise RuntimeError("Signing options differ from this prepared build. "
                               "Use a fresh --work-dir, or change signing in Xcode.")
        return
    changes = []
    # Also updates any hard-coded broadcast extension identifiers in Swift.
    suffixes = {".swift", ".plist", ".entitlements", ".xcconfig", ".pbxproj"}
    for path in sorted(client.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        relative = path.relative_to(client)
        if relative.parts[0] in {"ALVR", ".git"}:
            continue
        source = path.read_text()
        updated = source.replace("group.alvr.client.ALVR", f"group.{bundle_id}.ALVR")
        updated = updated.replace("alvr.client.ALVREyeBroadcast", f"{bundle_id}.ALVREyeBroadcast")
        if path.suffix in {".xcconfig", ".pbxproj"}:
            updated = updated.replace("PRODUCT_BUNDLE_IDENTIFIER = alvr.client", f"PRODUCT_BUNDLE_IDENTIFIER = {bundle_id}")
            if path.suffix == ".pbxproj":
                updated = re.sub(r"DEVELOPMENT_TEAM = [A-Z0-9]+;", f'DEVELOPMENT_TEAM = "{team or ""}";', updated)
                updated = updated.replace("INFOPLIST_KEY_CFBundleDisplayName = ALVR;", 'INFOPLIST_KEY_CFBundleDisplayName = "ALVR Discord";')
            else:
                updated = re.sub(r"(?m)^DEVELOPMENT_TEAM = [A-Z0-9]+$", f"DEVELOPMENT_TEAM = {team or ''}", updated)
        if updated != source:
            changes.append((path, updated.encode("utf-8")))
    for path, content in changes:
        atomic_write(path, content)
    atomic_write(marker, json.dumps({"bundle_id": bundle_id, "team": team}, indent=2).encode())


def prepare(work: Path, bundle_id: str, team: Optional[str]) -> Path:
    client = work / "alvr-visionos"
    checkout(CLIENT_URL, CLIENT_REV, client)
    entry = git_text(client, "ls-tree", "HEAD", "ALVR").split()
    if entry[:3] != ["160000", "commit", CORE_REV]:
        raise RuntimeError("Pinned client/core mismatch: refusing to assemble an incompatible build.")
    core = client / "ALVR"
    checkout(CORE_URL, CORE_REV, core)
    # Validate BOTH sets before changing either; reruns accept only exact patches.
    changes = staged_patches(client, CLIENT_PATCHES) + staged_patches(core, CORE_PATCHES)
    for path, content in changes:
        atomic_write(path, content)
    personalize(client, bundle_id, team)
    print(f"Prepared external-voice-chat client: {client}")
    return client


def require_tools(names) -> None:
    missing = [name for name in names if not shutil.which(name)]
    if missing:
        raise RuntimeError("Missing tools: " + ", ".join(missing) +
                           ". Install Xcode with iOS/visionOS SDKs and Rust via rustup; see visionos/README.md.")


def build_core(client: Path) -> None:
    env = os.environ.copy()
    env.pop("SDKROOT", None)  # Follow this pinned client's upstream iOS/repack build.
    env["CARGO_TARGET_DIR"] = str(client / "ALVR" / "target")
    run(["rustup", "target", "add", "aarch64-apple-ios"], env=env)
    if not shutil.which("cbindgen"):
        run(["cargo", "install", "cbindgen", "--locked"], env=env)
    cbindgen = shutil.which("cbindgen") or str(Path.home() / ".cargo" / "bin" / "cbindgen")
    if not Path(cbindgen).is_file():
        raise RuntimeError("cbindgen installation was not found; add its bin directory to PATH.")
    core = client / "ALVR"
    run(["cargo", "build", "--manifest-path", core / "Cargo.toml", "--locked",
         "--target", "aarch64-apple-ios", "-p", "alvr_client_core", "--profile", "distribution",
         "--features", "external-voice-chat"], cwd=client, env=env)
    (core / "build").mkdir(exist_ok=True)
    run([cbindgen, "--config", "cbindgen.toml", "--crate", "alvr_client_core",
         "--output", core / "build" / "alvr_client_core.h"], cwd=core / "alvr" / "client_core", env=env)
    run(["bash", "repack_alvr_client.sh"], cwd=client, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--bundle-id", default="com.gigq.ALVRDiscord")
    parser.add_argument("--team", help="Your 10-character Apple Developer Team ID (optional).")
    parser.add_argument("--prepare-only", action="store_true", help="Fetch and patch sources without Apple/Rust build tools.")
    parser.add_argument("--unsigned", action="store_true", help="Also compile the app for a generic visionOS device, without signing.")
    parser.add_argument("--no-open", action="store_true", help="Do not open Xcode after building the core.")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", args.bundle_id):
        parser.error("--bundle-id must be a reverse-DNS identifier.")
    if args.team and not re.fullmatch(r"[A-Z0-9]{10}", args.team):
        parser.error("--team must be a 10-character Apple Developer Team ID.")
    if args.prepare_only and args.unsigned:
        parser.error("--prepare-only and --unsigned are mutually exclusive.")
    try:
        require_tools(["git"])
        if not args.prepare_only:
            if platform.system() != "Darwin":
                raise RuntimeError("Building the app requires macOS and Xcode. --prepare-only works without them.")
            require_tools(["xcodebuild", "xcrun", "rustup", "cargo"])
            run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
            run(["xcrun", "--sdk", "xros", "--show-sdk-path"])
        work = args.work_dir.expanduser().resolve()
        client = prepare(work, args.bundle_id, args.team)
        if args.prepare_only:
            return 0
        build_core(client)
        if args.unsigned:
            run(["xcodebuild", "-project", client / "ALVRClient.xcodeproj", "-scheme", "ALVRClient",
                 "-configuration", "Debug", "-destination", "generic/platform=visionOS",
                 "-derivedDataPath", work / "DerivedData", "CODE_SIGNING_ALLOWED=NO", "build"], cwd=client)
        print(f"Open {client / 'ALVRClient.xcodeproj'}; select your team for BOTH targets and run on Vision Pro.")
        print("Microphone capture/forwarding is disabled in this build. Discord keeps its own audio session.")
        if not args.no_open and not args.unsigned:
            run(["open", client / "ALVRClient.xcodeproj"])
        return 0
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Build stopped: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
