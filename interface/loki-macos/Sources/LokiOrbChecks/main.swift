import Foundation

// Register all checks here.
runSmokeChecks()

if recordedFailures.isEmpty {
    print("All checks passed.")
    exit(0)
} else {
    print("\(recordedFailures.count) check(s) failed.")
    exit(1)
}
