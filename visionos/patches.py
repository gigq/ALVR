"""Source changes for the pinned Vision Pro client and its matching Rust core.

Keep the upstream preimages explicit: an upstream change must fail rather than
silently producing a client that opens the microphone again.
"""
from typing import Dict, List, Tuple

Replacement = Tuple[str, str, int]

OLD_AUDIO = r'''    // Ensure that the audio session is direct stereo, so that SteamVR can handle
    // all the fancy effects as it pleases.
    // Also ensures that the microphone uses the right noise cancellation.
    func fixAudioForDirectStereo() {
        audioIsOff = false
        let audioSession = AVAudioSession.sharedInstance()
        do {
            try audioSession.setActive(true)
            try audioSession.setCategory(.playAndRecord, options: [.mixWithOthers, .allowBluetoothA2DP, .allowAirPlay])
            try audioSession.setMode(.voiceChat)
            try audioSession.setPreferredOutputNumberOfChannels(2)
            try audioSession.setIntendedSpatialExperience(.bypassed)
        } catch {
            print("Failed to set the audio session configuration?")
        }
    }
    
    // On visionOS 1, the app would have audio crackling on exiting, so
    // we avoid it by quickly shutting off the audio on exit.
    func preventAudioCracklingOnExit() {
        if audioIsOff {
            return
        }
        audioIsOff = true
        let audioSession = AVAudioSession.sharedInstance()
        do {
            try audioSession.setActive(false)
        } catch {
            print("Failed to set the audio session configuration? \(error)")
        }
    }
'''

AUDIO_METHODS = r'''    // This build leaves microphone capture to Discord (or another local app).
    // Playback mixing is configured BEFORE activation, including on reentry.
    @discardableResult
    func fixAudioForDirectStereo() -> Bool {
        audioSessionLock.lock()
        defer { audioSessionLock.unlock() }
        let audioSession = AVAudioSession.sharedInstance()
        do {
            try audioSession.setCategory(.playback, mode: .default, options: [.mixWithOthers])
        } catch {
            print("ALVR external voice chat: playback configuration failed: \(error)")
            return false
        }

        // Spatial/stereo preferences must not prevent otherwise valid playback.
        do {
            try audioSession.setIntendedSpatialExperience(.bypassed)
        } catch {
            print("ALVR external voice chat: spatial bypass unavailable: \(error)")
        }
        do {
            try audioSession.setActive(true)
            audioIsOff = false
        } catch {
            print("ALVR external voice chat: audio activation failed: \(error)")
            return false
        }
        do {
            if audioSession.maximumOutputNumberOfChannels >= 2 {
                try audioSession.setPreferredOutputNumberOfChannels(2)
            }
        } catch {
            print("ALVR external voice chat: stereo preference unavailable: \(error)")
        }
        return true
    }

    // Retain the upstream exit workaround, but only mark audio off after success.
    func preventAudioCracklingOnExit() {
        audioSessionLock.lock()
        defer { audioSessionLock.unlock() }
        guard !audioIsOff else { return }
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
            audioIsOff = true
        } catch {
            print("ALVR external voice chat: audio deactivation failed: \(error)")
        }
    }
'''

CLIENT_PATCHES: Dict[str, List[Replacement]] = {
    "ALVRClient/EventHandler.swift": [
        ("    var audioIsOff = false\n", "    var audioIsOff = true\n    private let audioSessionLock = NSLock()\n", 1),
        ("    func initializeAlvr() {\n        fixAudioForDirectStereo()\n",
         "    func initializeAlvr() {\n        guard fixAudioForDirectStereo() else { return }\n", 1),
        ("    func start() {\n        alvr_resume()\n\n        fixAudioForDirectStereo()\n",
         "    func start() {\n        guard fixAudioForDirectStereo() else { return }\n        alvr_resume()\n", 1),
        (OLD_AUDIO, AUDIO_METHODS, 1),
    ],
}

CORE_PATCHES: Dict[str, List[Replacement]] = {
    "alvr/client_core/Cargo.toml": [
        ("[features]\n", "[features]\n# Playback-only client: leave capture to a separate local voice-chat app.\nexternal-voice-chat = []\n", 1),
    ],
    "alvr/client_core/src/connection.rs": [
        ("    let microphone_sample_rate = AudioDevice::new_input(None)\n",
         "    // Protocol v20 requires a rate even when input is unused. Do not probe\n"
         "    // CPAL: its input-format query initializes an input-enabled AudioUnit.\n"
         "    // This valid placeholder is never used to capture or send samples.\n"
         "    #[cfg(feature = \"external-voice-chat\")]\n"
         "    let microphone_sample_rate = 48_000;\n"
         "    #[cfg(not(feature = \"external-voice-chat\"))]\n"
         "    let microphone_sample_rate = AudioDevice::new_input(None)\n", 1),
        ("    let microphone_thread = if matches!(settings.audio.microphone, Switch::Enabled(_)) {\n",
         "    // Compile out input-device creation AND recording, irrespective of\n"
         "    // the streamer's microphone setting. Game audio is unchanged.\n"
         "    #[cfg(feature = \"external-voice-chat\")]\n"
         "    let microphone_thread = thread::spawn(|| ());\n"
         "    #[cfg(not(feature = \"external-voice-chat\"))]\n"
         "    let microphone_thread = if matches!(settings.audio.microphone, Switch::Enabled(_)) {\n", 1),
    ],
}


def transform(source: str, replacements: List[Replacement], label: str) -> str:
    for old, new, expected in replacements:
        count = source.count(old)
        if count != expected:
            raise RuntimeError(f"{label}: expected {expected} occurrence(s), found {count}: {old[:80]!r}")
        source = source.replace(old, new)
    return source
