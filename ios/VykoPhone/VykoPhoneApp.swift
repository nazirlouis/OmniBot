// VykoPhoneApp.swift — app entry point.
//
// The whole app is built around a single ConversationVM instance kept
// alive on the @StateObject (it owns the BLE link, the speech I/O, and
// the LLM client). All views read state from it via @EnvironmentObject.
//
// iOS 17+ target. Real device required — Core Bluetooth peripheral
// scanning does not work in the iOS Simulator.

import SwiftUI

@main
struct VykoPhoneApp: App {
    @StateObject private var vm = ConversationVM()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(vm)
                .task {
                    // Kick off BLE scanning once the central manager
                    // reports poweredOn. ConversationVM handles the
                    // state machine; the .task just primes it.
                    await vm.start()
                }
        }
    }
}
