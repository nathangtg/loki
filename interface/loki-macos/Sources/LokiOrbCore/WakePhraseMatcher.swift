import Foundation

/// Fixed list of common misrecognitions of "Hey Loki" that are accepted as the wake phrase
/// (Requirement 5.9). Single constant; compared case-insensitively after normalization.
public let WakeMishearingList: [String] = ["hey loci", "hey low key"]

/// Pure wake-phrase matching over recognizer transcripts.
public enum WakePhraseMatcher {
    public static let wakePhrase = "hey loki"

    /// Lowercases, replaces punctuation with spaces and collapses whitespace.
    public static func normalize(_ text: String) -> String {
        let lowered = text.lowercased()
        let mapped = lowered.unicodeScalars.map { scalar -> Character in
            if CharacterSet.punctuationCharacters.contains(scalar)
                || CharacterSet.symbols.contains(scalar)
            {
                return " "
            }
            return Character(scalar)
        }
        return String(mapped)
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }

    /// `true` iff the normalized transcript contains "hey loki" or any `WakeMishearingList` entry
    /// as whole words.
    public static func matches(_ transcript: String) -> Bool {
        let padded = " " + normalize(transcript) + " "
        for phrase in [wakePhrase] + WakeMishearingList where padded.contains(" " + phrase + " ") {
            return true
        }
        return false
    }
}
