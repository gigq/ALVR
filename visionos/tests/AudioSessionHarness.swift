import Foundation

enum TestError: Error { case injected }
final class AVAudioSession {
    enum Category { case playback }
    enum Mode { case `default` }
    enum Experience { case bypassed }
    struct Options: OptionSet {
        let rawValue: Int
        static let mixWithOthers = Options(rawValue: 1)
        static let notifyOthersOnDeactivation = Options(rawValue: 2)
    }
    static let session = AVAudioSession()
    static func sharedInstance() -> AVAudioSession { session }
    var calls: [String] = []
    var failure: String? = nil
    var maximumOutputNumberOfChannels = 2
    func record(_ name: String) throws {
        calls.append(name)
        if failure == name { throw TestError.injected }
    }
    func setCategory(_ category: Category, mode: Mode, options: Options) throws {
        assert(options == [.mixWithOthers])
        try record("category")
    }
    func setIntendedSpatialExperience(_ experience: Experience) throws { try record("spatial") }
    func setActive(_ active: Bool, options: Options = []) throws {
        if active {
            assert(calls.contains("category"))
            assert(options.isEmpty)
        } else {
            assert(options == [.notifyOthersOnDeactivation])
        }
        try record(active ? "activate" : "deactivate")
    }
    func setPreferredOutputNumberOfChannels(_ count: Int) throws {
        assert(count == 2)
        assert(calls.contains("activate"))
        try record("stereo")
    }
    func reset(_ failure: String? = nil, channels: Int = 2) {
        calls = []; self.failure = failure; maximumOutputNumberOfChannels = channels
    }
}

final class EventHandler {
    var audioIsOff = true
    private let audioSessionLock = NSLock()
// INSERT_AUDIO_METHODS
}

let session = AVAudioSession.session
let handler = EventHandler()
assert(handler.fixAudioForDirectStereo())
assert(session.calls == ["category", "spatial", "activate", "stereo"])
assert(!handler.audioIsOff)
handler.preventAudioCracklingOnExit()
assert(handler.audioIsOff)
let before = session.calls
handler.preventAudioCracklingOnExit()
assert(session.calls == before)

// Do not activate a non-mixing/default session if configuration failed.
session.reset("category")
assert(!handler.fixAudioForDirectStereo())
assert(session.calls == ["category"])
assert(handler.audioIsOff)

session.reset("activate")
assert(!handler.fixAudioForDirectStereo())
assert(handler.audioIsOff)
assert(!session.calls.contains("stereo"))

// Optional route preferences must not make a valid playback session fail.
session.reset("spatial")
assert(handler.fixAudioForDirectStereo())
session.reset("stereo")
assert(handler.fixAudioForDirectStereo())
session.reset(channels: 1)
assert(handler.fixAudioForDirectStereo())
assert(!session.calls.contains("stereo"))

// A failed deactivation remains eligible for a later retry.
session.reset("deactivate")
handler.preventAudioCracklingOnExit()
assert(!handler.audioIsOff)
session.reset()
handler.preventAudioCracklingOnExit()
assert(handler.audioIsOff)

// Reentry reinstates the same playback-only policy, not a recording session.
session.reset()
assert(handler.fixAudioForDirectStereo())
assert(session.calls == ["category", "spatial", "activate", "stereo"])
print("Audio-session policy harness passed (mock AVAudioSession, not device audio).")
