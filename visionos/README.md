# ALVR Vision Pro: external voice chat / Discord

This build plays PC/game audio alongside other visionOS apps and **never opens
ALVR's microphone input**. Discord on the headset is intended to keep handling
both incoming call audio and the microphone. There is no runtime switch to enable
ALVR microphone forwarding in this build, and no new setting to enable first.

**Status:** source implementation with offline regression tests. The Swift audio
methods are tested against a mock AVAudioSession, not Apple's runtime. A full
Xcode build and an actual Discord call on a Vision Pro are still required to
confirm end-to-end behavior. No signed app or successful hardware test is implied.

## Build on your Mac

Prerequisites: Python 3.9+, Git, Rust installed with rustup, and a recent Xcode
with the iOS and visionOS SDKs. Use an Xcode version that supports your headset's
installed OS and the pinned client sources (Xcode 26 or newer is recommended).
Select that Xcode installation with `xcode-select` and complete its first-launch
setup. You will need your own Apple signing team and a paired Vision Pro with
Developer Mode enabled to install the app.

```sh
git clone https://github.com/gigq/ALVR.git
cd ALVR
python3 visionos/build.py
```

For an existing checkout, run `git pull --ff-only` before the last command.
The script fetches the pinned Vision Pro source and its **matching** core, applies
the included audio changes, installs the Rust iOS target (and cbindgen if missing),
builds the core with `external-voice-chat`, repacks its framework using the pinned
upstream script, and opens `ALVRClient.xcodeproj` in Xcode.

In Xcode, choose your team under **Signing & Capabilities for both targets**:
`ALVRClient` and `ALVREyeBroadcast`. Select your paired Vision Pro as the run
destination and press Run. A Team ID can instead be supplied on the first run:

```sh
python3 visionos/build.py --team YOURTEAMID
```

Replace `YOURTEAMID` with your actual 10-character Team ID. The helper uses new
bundle identifiers (`com.gigq.ALVRDiscord` and its broadcast-extension suffix),
updates their app-group identifiers, and names the app **ALVR Discord** so it can
coexist with the upstream app. Automatic signing may need to register the new
App Group for your team. The upstream low-latency-streaming entitlement is
preserved; resolve any provisioning error in Xcode rather than silently removing
that capability. Signing approval/profile availability is not automated here.

Your subsequent Xcode signing edits are preserved. To change the helper's initial
bundle ID or Team ID later, use a fresh `--work-dir`, or edit signing in Xcode.
Do not install this beside another custom build with the same bundle ID; use
`--bundle-id com.yourdomain.ALVRDiscord` when appropriate.

## Use

Join a Discord voice channel **on the Vision Pro**, then launch **ALVR Discord**
and connect to the PC. Keep game audio enabled in the streamer. The expected
result is game audio plus Discord audio, with the headset microphone going only
to Discord. Discord audio is not captured or forwarded by ALVR.

ALVR's input probing and recording code are compiled out even when microphone
streaming is enabled on the PC. It is still best to turn off that streamer setting
because its virtual-microphone device is unused. A server configured to use a
missing virtual microphone can still fail on the server before streaming starts;
a client-only change cannot provision or repair a PC audio device.

This also means the Vision Pro microphone will **not** reach PC-side Discord,
SteamVR, or in-game voice chat through ALVR. This is intentional. Ordinary ALVR
builds outside this helper are unchanged.

## Why this lives in the shared-core fork

`gigq/ALVR` is a fork of the shared ALVR repository, not the separate Swift/Metal
app repository. This directory is a reproducible source overlay/build entry point;
it avoids requiring you to create another GitHub fork or manually edit two trees.
Actual patched sources are prepared under:

```
visionos/_build/alvr-visionos/ALVRClient/EventHandler.swift
visionos/_build/alvr-visionos/ALVR/alvr/client_core/Cargo.toml
visionos/_build/alvr-visionos/ALVR/alvr/client_core/src/connection.rs
```

The source transformations are explicit and reviewable in `patches.py`.
They validate exact upstream preimages, reject mismatched pins, and refuse to
overwrite local edits to the patched audio/core files. Reruns are idempotent.
No reset, clean, or deletion of your main checkout is performed. Use a new
`--work-dir` when preserving experiments in an older generated tree.

Pinned sources:

- Vision Pro app: `alvr-org/alvr-visionos` at
  `fb2576a2a4df23a20647e327acc4d8f785da0d77` (app version 20.14.6).
- Its core submodule: `alvr-org/ALVR` at
  `e3fd448029c795b1b2d5835c84c6588bf01bae0d` (core version 20.14.1).

**Keep a compatible 20.x PC streamer, preferably the one already working with
your Vision Pro client.** The core version above is the compatibility baseline.
This is not a port to ALVR v21, and building the PC streamer from this fork's
current `master` is not part of this setup. No video, tracking, or foveation changes
are intentionally made by this overlay. The iOS-to-visionOS framework repacking
is the pinned client's existing build method, not a new native Rust visionOS port.

## Changes

`EventHandler.swift` configures `.playback`, `.default`, and `.mixWithOthers`
**before** activating the session or resuming the core. Initialization aborts on
audio-policy/activation failure instead of silently continuing with an unintended
session. Startup and headset reentry use the same playback-only function. Session
configuration/deactivation is serialized, the stereo and spatial-bypass hints are
best-effort, and exit deactivation notifies other apps. No recording-capable
category or voice-chat mode is selected.

The `external-voice-chat` Cargo feature excludes both the initial CPAL microphone
format probe and the microphone recording thread at compile time. The legacy
wire protocol still requires a microphone-rate field, so it advertises a valid
48,000 Hz placeholder without touching input hardware. That value is not a claim
about the microphone's actual rate, and no microphone samples are transmitted.
The normal build without this feature retains upstream microphone behavior.

## Validation and diagnostics

```sh
# Offline tests, including Swift policy tests when swiftc is available:
python3 -m unittest discover -s visionos/tests -v

# Download and inspect the patched sources without compiling:
python3 visionos/build.py --prepare-only

# Full generic-device Xcode compilation, without signing or installation:
python3 visionos/build.py --unsigned --no-open
```

Tests use synthetic Git repositories built from the inspected upstream anchors.
They cover patch preconditions, startup ordering, microphone guards, repeat
preparation, pin mismatches, preservation of edits, signing personalization, and
actual compiled Swift replacement methods against a mock audio session. They do
not validate the whole ALVR program, Apple's SDK, network streaming, provisioning,
or actual audio routing. The build command uses Cargo's lockfile; installing a
missing cbindgen uses its currently available release with `--locked`.

On-device acceptance checks: join Discord before launch; verify others hear your
microphone during streaming; verify incoming Discord speech and game audio;
disconnect/reconnect; remove/re-don the headset; and enter/leave the immersive
space. Also test a call joined while streaming. Check Xcode logs for messages
prefixed `ALVR external voice chat:` when audio configuration fails. A browser-based
Discord client or an app suspended by visionOS may still have its own lifecycle
limitations; ALVR cannot override another app's background behavior.

## References

- [Vision Pro client source](https://github.com/alvr-org/alvr-visionos/tree/fb2576a2a4df23a20647e327acc4d8f785da0d77)
- [Pinned connection pipeline](https://github.com/alvr-org/ALVR/blob/e3fd448029c795b1b2d5835c84c6588bf01bae0d/alvr/client_core/src/connection.rs)
- [Upstream build guide](https://github.com/alvr-org/alvr-visionos/wiki/Building)
- [Apple: mixWithOthers](https://developer.apple.com/documentation/avfaudio/avaudiosession/categoryoptions-swift.struct/mixwithothers)
- [Apple: playback category](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/playback)

Upstream code remains under its original MIT license. These changes do not access
Discord account data or implement a Discord client.
