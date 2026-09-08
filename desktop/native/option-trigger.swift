import Cocoa
import CoreGraphics

// Two short, unmodified taps of the same Option key. Emit on the second release.
struct OptionDoubleTap {
    var ready = false
    var downKey: Int? = nil
    var downAt: Double = 0
    var firstKey: Int? = nil
    var firstReleasedAt: Double = 0

    mutating func update(left: Bool, right: Bool, other: Bool, now: Double, interrupted: Bool = false) -> Bool {
        let key = left ? 58 : (right ? 61 : 0)
        if other || interrupted || (left && right) {
            downKey = nil
            firstKey = nil
            ready = key == 0 && !other
            return false
        }
        if key == 0 {
            let released = downKey
            downKey = nil
            ready = true
            guard let released = released, now - downAt <= 0.25 else {
                if released != nil { firstKey = nil }
                return false
            }
            if firstKey == released && now - firstReleasedAt <= 0.4 {
                firstKey = nil
                return true
            }
            firstKey = released
            firstReleasedAt = now
            return false
        }
        if let down = downKey {
            if down != key { downKey = nil; firstKey = nil; ready = false }
        } else if ready {
            downKey = key
            downAt = now
            ready = false
        }
        return false
    }
}

if CommandLine.arguments.contains("--self-test") {
    var detector = OptionDoubleTap()
    func step(_ left: Bool, _ right: Bool, _ time: Double, _ expected: Bool = false, other: Bool = false, interrupted: Bool = false) {
        precondition(detector.update(left: left, right: right, other: other, now: time, interrupted: interrupted) == expected)
    }
    step(true, false, 0) // A held key at startup is ignored.
    step(false, false, 0.1)
    step(true, false, 1); step(false, false, 1.1)
    step(true, false, 1.2); step(false, false, 1.3, true)
    step(false, false, 1.31) // No duplicate on repeated release.
    step(false, true, 2); step(false, false, 2.1)
    step(false, true, 2.2); step(false, false, 2.3, true)
    step(true, false, 3); step(false, false, 3.1)
    step(false, true, 3.2); step(false, false, 3.3) // Mixed sides are not a double tap.
    step(false, true, 4); step(false, false, 4.1) // Expired first tap.
    step(false, true, 4.2); step(false, false, 4.6) // Long hold cancels.
    step(true, false, 5); step(false, false, 5.1)
    step(true, true, 5.2); step(false, false, 5.3) // Old chord no longer triggers.
    step(true, false, 6); step(true, false, 6.05, interrupted: true)
    step(false, false, 6.1); step(true, false, 6.2); step(false, false, 6.3)
    step(false, false, 6.35, interrupted: true) // Typing/clicking cancels the pending tap.
    step(true, false, 6.4); step(false, false, 6.5)
    step(true, false, 6.6, other: true); step(false, false, 6.7)
    print("Option double-tap state tests passed")
    exit(0)
}

func send(_ type: String, _ message: String = "") {
    let data = try! JSONSerialization.data(withJSONObject: ["type": type, "message": message])
    FileHandle.standardOutput.write(data + Data([10]))
}

// Key/button-down events only cancel a gesture; their contents are never read or sent.
guard CGPreflightListenEventAccess() || CGRequestListenEventAccess() else {
    send("permission", "Enable Multiverse in System Settings → Privacy & Security → Input Monitoring, then restart the app.")
    exit(3)
}
var detector = OptionDoubleTap()
_ = detector.update(left: CGEventSource.keyState(.combinedSessionState, key: 58),
                 right: CGEventSource.keyState(.combinedSessionState, key: 61), other: false, now: ProcessInfo.processInfo.systemUptime)
var tap: CFMachPort?
let mask = [CGEventType.flagsChanged, .keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown].reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                       options: .listenOnly, eventsOfInterest: mask,
                       callback: { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        detector = OptionDoubleTap()
        if let tap = tap { CGEvent.tapEnable(tap: tap, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    let left = CGEventSource.keyState(.combinedSessionState, key: 58)
    let right = CGEventSource.keyState(.combinedSessionState, key: 61)
    let other = !event.flags.intersection([.maskCommand, .maskControl, .maskShift, .maskSecondaryFn]).isEmpty
    if detector.update(left: left, right: right, other: other, now: ProcessInfo.processInfo.systemUptime, interrupted: type != .flagsChanged) { send("trigger") }
    return Unmanaged.passUnretained(event)
}, userInfo: nil)
guard let tap = tap, let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else {
    send("permission", "The Option shortcut listener could not start. Enable Input Monitoring for Multiverse and restart it.")
    exit(3)
}
CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
send("ready")
CFRunLoopRun()
