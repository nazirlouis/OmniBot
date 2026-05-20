// VykoBLE.swift — Core Bluetooth client for the Vyko Box-3 firmware.
//
// Wire protocol (firmware side: firmware/main/command_parser/command_parser.c):
//   - Service UUID         : 7b4a0001-9c3a-4b8e-9f5d-1e6c4a2b3d4e
//   - Command characteristic : 7b4a0002-9c3a-4b8e-9f5d-1e6c4a2b3d4e   (write)
//   - Event characteristic   : 7b4a0003-9c3a-4b8e-9f5d-1e6c4a2b3d4e   (notify, unused for now)
// Peripheral name advertises as "VYKO_xxxx" (last 2 bytes of BT MAC).
//
// MTU is 247 → ~244 bytes per ATT write → ~210 chars of chat text per JSON
// message. ConversationVM is responsible for chunking long AI replies; this
// module just writes whatever string it's given (truncates on size mismatch
// rather than failing silently).

import Foundation
import CoreBluetooth

@MainActor
final class VykoBLE: NSObject, ObservableObject {

    enum ConnState: Equatable {
        case poweredOff          // BT off in Settings
        case unauthorized        // user denied Bluetooth permission
        case idle                // BT on, not scanning
        case scanning
        case connecting(name: String)
        case connected(name: String)
    }

    @Published private(set) var state: ConnState = .idle

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var commandChar: CBCharacteristic?

    // Wire UUIDs — must match firmware/main/ble/ble.c
    private static let serviceUUID =
        CBUUID(string: "7b4a0001-9c3a-4b8e-9f5d-1e6c4a2b3d4e")
    private static let commandCharUUID =
        CBUUID(string: "7b4a0002-9c3a-4b8e-9f5d-1e6c4a2b3d4e")

    override init() {
        super.init()
        // queue:nil → callbacks on main, matching @MainActor expectations
        central = CBCentralManager(delegate: self, queue: nil)
    }

    // MARK: - Public API

    /// Kick off a scan. Safe to call repeatedly — no-ops while connected.
    func startScanning() {
        guard central.state == .poweredOn else { return }
        if case .connected = state { return }
        state = .scanning
        // We don't filter on service UUID at scan time because the
        // ESP32 advertises a name prefix and the service is in the
        // scan response. Filtering by [serviceUUID] is more efficient
        // but only works when the service is in the primary adv data.
        central.scanForPeripherals(withServices: nil, options: nil)
    }

    /// Disconnect (if connected) and stop scanning. Used on app
    /// background or when the user wants to explicitly drop the link.
    func disconnect() {
        if let p = peripheral { central.cancelPeripheralConnection(p) }
        if central.isScanning { central.stopScan() }
        commandChar = nil
        peripheral = nil
        state = .idle
    }

    /// Write a JSON command to the Box-3.
    /// `payload` is the raw JSON dict; encoding + truncation happens here.
    /// Throws if not connected or if the encoded payload exceeds MTU.
    func sendCommand(_ payload: [String: Any]) async throws {
        guard let p = peripheral, let c = commandChar else {
            throw BLEError.notConnected
        }
        // Compact JSON — drop unnecessary whitespace.
        let data = try JSONSerialization.data(withJSONObject: payload,
                                              options: [.withoutEscapingSlashes])
        // ATT write limit: maximumWriteValueLength reflects the negotiated
        // MTU minus the 3-byte ATT header. We use Write-With-Response so
        // the firmware's command_parser_enqueue() backpressures naturally;
        // no need for a manual delay between writes.
        let maxLen = p.maximumWriteValueLength(for: .withResponse)
        if data.count > maxLen {
            throw BLEError.payloadTooLarge(size: data.count, max: maxLen)
        }
        p.writeValue(data, for: c, type: .withResponse)
    }

    // MARK: - Helpers

    enum BLEError: LocalizedError {
        case notConnected
        case payloadTooLarge(size: Int, max: Int)

        var errorDescription: String? {
            switch self {
            case .notConnected:
                return "Box-3 not connected"
            case .payloadTooLarge(let size, let max):
                return "BLE payload \(size) B exceeds MTU limit \(max) B"
            }
        }
    }
}

// MARK: - CBCentralManagerDelegate

extension VykoBLE: CBCentralManagerDelegate {

    nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        Task { @MainActor in
            switch central.state {
            case .poweredOn:
                self.state = .idle
                self.startScanning()
            case .poweredOff:
                self.state = .poweredOff
            case .unauthorized:
                self.state = .unauthorized
            default:
                self.state = .idle
            }
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager,
                                    didDiscover peripheral: CBPeripheral,
                                    advertisementData: [String: Any],
                                    rssi RSSI: NSNumber) {
        // Match on advertised name prefix. The firmware sets the name
        // to "VYKO_xxxx" — see firmware/main/ble/ble.c.
        let advName = (advertisementData[CBAdvertisementDataLocalNameKey]
                       as? String) ?? peripheral.name ?? ""
        guard advName.hasPrefix("VYKO_") else { return }

        Task { @MainActor in
            self.central.stopScan()
            self.peripheral = peripheral
            peripheral.delegate = self
            self.state = .connecting(name: advName)
            self.central.connect(peripheral, options: nil)
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager,
                                    didConnect peripheral: CBPeripheral) {
        Task { @MainActor in
            peripheral.discoverServices([Self.serviceUUID])
        }
    }

    nonisolated func centralManager(_ central: CBCentralManager,
                                    didDisconnectPeripheral peripheral: CBPeripheral,
                                    error: Error?) {
        Task { @MainActor in
            self.commandChar = nil
            self.peripheral = nil
            self.state = .idle
            // Auto-restart scan so user doesn't have to manually
            // re-pair after walking out of range.
            self.startScanning()
        }
    }
}

// MARK: - CBPeripheralDelegate

extension VykoBLE: CBPeripheralDelegate {

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didDiscoverServices error: Error?) {
        Task { @MainActor in
            guard let svc = peripheral.services?.first(where: { $0.uuid == Self.serviceUUID }) else {
                return
            }
            peripheral.discoverCharacteristics([Self.commandCharUUID], for: svc)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didDiscoverCharacteristicsFor service: CBService,
                                error: Error?) {
        Task { @MainActor in
            guard let char = service.characteristics?.first(where: { $0.uuid == Self.commandCharUUID }) else {
                return
            }
            self.commandChar = char
            let name = peripheral.name ?? "VYKO"
            self.state = .connected(name: name)
        }
    }

    nonisolated func peripheral(_ peripheral: CBPeripheral,
                                didWriteValueFor characteristic: CBCharacteristic,
                                error: Error?) {
        // No-op — the await on writeValue is fire-and-forget for now.
        // Could be promoted to a continuation if back-pressure matters.
        if let error { print("[VykoBLE] write error:", error) }
    }
}
