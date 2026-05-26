import AudioToolbox
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

enum TapError: Error, CustomStringConvertible {
    case noDisplay
    case unsupportedAudioFormat(String)

    var description: String {
        switch self {
        case .noDisplay:
            "No active display is available for ScreenCaptureKit audio capture."
        case .unsupportedAudioFormat(let detail):
            "Unsupported audio format from ScreenCaptureKit: \(detail)"
        }
    }
}

final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
    private let outputQueue = DispatchQueue(label: "lampgo.audio-tap.output")
    private var stream: SCStream?

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw TapError.noDisplay
        }

        let ownPid = ProcessInfo.processInfo.processIdentifier
        let excludedApps = content.applications.filter { $0.processID == ownPid }
        let filter = SCContentFilter(
            display: display,
            excludingApplications: excludedApps,
            exceptingWindows: []
        )

        let config = SCStreamConfiguration()
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.queueDepth = 3
        config.capturesAudio = true
        config.sampleRate = 48_000
        config.channelCount = 2
        config.excludesCurrentProcessAudio = true

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: outputQueue)
        try await stream.startCapture()
        self.stream = stream
        fputs("LampgoAudioTap started: pcm16le 48000Hz stereo\n", stderr)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fputs("LampgoAudioTap stopped: \(error)\n", stderr)
        fflush(stderr)
        exit(2)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else {
            return
        }
        do {
            let pcm = try pcm16StereoData(from: sampleBuffer)
            if !pcm.isEmpty {
                FileHandle.standardOutput.write(pcm)
            }
        } catch {
            fputs("LampgoAudioTap audio conversion error: \(error)\n", stderr)
            fflush(stderr)
        }
    }

    private func pcm16StereoData(from sampleBuffer: CMSampleBuffer) throws -> Data {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription)?.pointee
        else {
            throw TapError.unsupportedAudioFormat("missing stream description")
        }

        let channelCount = max(1, Int(asbd.mChannelsPerFrame))
        let frameCount = CMSampleBufferGetNumSamples(sampleBuffer)
        let flags = asbd.mFormatFlags
        let isFloat = (flags & kAudioFormatFlagIsFloat) != 0
        let isNonInterleaved = (flags & kAudioFormatFlagIsNonInterleaved) != 0

        var blockBuffer: CMBlockBuffer?
        let bufferListSize = AudioBufferList.sizeInBytes(maximumBuffers: max(1, channelCount))
        let rawBufferList = UnsafeMutableRawPointer.allocate(
            byteCount: bufferListSize,
            alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer {
            rawBufferList.deallocate()
        }
        rawBufferList.initializeMemory(as: UInt8.self, repeating: 0, count: bufferListSize)
        let audioBufferList = rawBufferList.bindMemory(to: AudioBufferList.self, capacity: 1)
        let bufferList = UnsafeMutableAudioBufferListPointer(audioBufferList)

        var neededSize = 0
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &neededSize,
            bufferListOut: audioBufferList,
            bufferListSize: bufferListSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: 0,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr else {
            throw TapError.unsupportedAudioFormat("AudioBufferList status \(status)")
        }

        if isFloat && asbd.mBitsPerChannel == 32 {
            return convertFloat32(bufferList, frameCount: frameCount, channels: channelCount, nonInterleaved: isNonInterleaved)
        }
        if !isFloat && asbd.mBitsPerChannel == 16 {
            return convertInt16(bufferList, frameCount: frameCount, channels: channelCount, nonInterleaved: isNonInterleaved)
        }

        throw TapError.unsupportedAudioFormat(
            "\(asbd.mFormatID) bits=\(asbd.mBitsPerChannel) flags=\(asbd.mFormatFlags)"
        )
    }

    private func convertFloat32(
        _ buffers: UnsafeMutableAudioBufferListPointer,
        frameCount: Int,
        channels: Int,
        nonInterleaved: Bool
    ) -> Data {
        var out = Data()
        out.reserveCapacity(frameCount * 2 * MemoryLayout<Int16>.size)
        for frame in 0..<frameCount {
            for channel in 0..<2 {
                let sourceChannel = min(channel, channels - 1)
                let sample: Float32
                if nonInterleaved {
                    let buffer = buffers[min(sourceChannel, buffers.count - 1)]
                    let ptr = buffer.mData!.assumingMemoryBound(to: Float32.self)
                    sample = ptr[frame]
                } else {
                    let buffer = buffers[0]
                    let ptr = buffer.mData!.assumingMemoryBound(to: Float32.self)
                    sample = ptr[frame * channels + sourceChannel]
                }
                appendPCM16(sample, to: &out)
            }
        }
        return out
    }

    private func convertInt16(
        _ buffers: UnsafeMutableAudioBufferListPointer,
        frameCount: Int,
        channels: Int,
        nonInterleaved: Bool
    ) -> Data {
        var out = Data()
        out.reserveCapacity(frameCount * 2 * MemoryLayout<Int16>.size)
        for frame in 0..<frameCount {
            for channel in 0..<2 {
                let sourceChannel = min(channel, channels - 1)
                let sample: Int16
                if nonInterleaved {
                    let buffer = buffers[min(sourceChannel, buffers.count - 1)]
                    let ptr = buffer.mData!.assumingMemoryBound(to: Int16.self)
                    sample = ptr[frame]
                } else {
                    let buffer = buffers[0]
                    let ptr = buffer.mData!.assumingMemoryBound(to: Int16.self)
                    sample = ptr[frame * channels + sourceChannel]
                }
                var littleEndian = sample.littleEndian
                withUnsafeBytes(of: &littleEndian) { bytes in
                    out.append(contentsOf: bytes)
                }
            }
        }
        return out
    }

    private func appendPCM16(_ sample: Float32, to data: inout Data) {
        let clipped = min(max(sample, -1.0), 1.0)
        var pcm = Int16(clipped * 32_767.0).littleEndian
        withUnsafeBytes(of: &pcm) { bytes in
            data.append(contentsOf: bytes)
        }
    }
}

@main
struct LampgoAudioTap {
    static func main() async {
        guard CGPreflightScreenCaptureAccess() else {
            fputs("LampgoAudioTap permission: requesting Screen Recording / system audio access\n", stderr)
            let granted = CGRequestScreenCaptureAccess()
            if granted {
                fputs("LampgoAudioTap permission granted; restart LampGo and enter music mode again.\n", stderr)
            } else {
                fputs("LampgoAudioTap permission not granted; enable Screen Recording for LampGo or the terminal app in System Settings.\n", stderr)
            }
            exit(64)
        }

        let tap = AudioTap()
        do {
            try await tap.start()
            while true {
                try await Task.sleep(nanoseconds: 60_000_000_000)
            }
        } catch {
            fputs("LampgoAudioTap failed: \(error)\n", stderr)
            fputs("Grant Screen Recording permission to the terminal or bundled app running this helper.\n", stderr)
            exit(1)
        }
    }
}
