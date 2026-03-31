//! Lightweight token counting and truncation.
//!
//! Uses a whitespace-plus-punctuation heuristic that approximates
//! BPE token counts without requiring a full tokenizer model.
//! Sufficient for budget estimation; not for exact billing.

/// Punctuation and symbols that typically become their own token.
const SPLITTERS: &[char] = &[
    ' ', '\t', '\n', '\r', '.', ',', ';', ':', '!', '?', '(', ')', '[', ']', '{', '}', '"', '\'',
    '/', '\\', '-', '_', '@', '#', '$', '%', '&', '*', '+', '=', '<', '>', '|', '~', '`', '^',
];

/// Counts the approximate number of tokens in `text`.
///
/// Splits on whitespace and punctuation boundaries, producing a
/// rough BPE-style count. Empty or whitespace-only input returns 0.
pub fn count_tokens(text: &str) -> u32 {
    if text.is_empty() {
        return 0;
    }

    let mut count: u32 = 0;
    let mut in_word = false;

    for ch in text.chars() {
        if SPLITTERS.contains(&ch) {
            if in_word {
                count += 1;
                in_word = false;
            }
            // Non-whitespace punctuation counts as its own token.
            if !ch.is_whitespace() {
                count += 1;
            }
        } else {
            in_word = true;
        }
    }

    // Trailing word without a following splitter.
    if in_word {
        count += 1;
    }

    count
}

/// Truncates `text` to at most `max_tokens` approximate tokens.
///
/// Preserves whole words — never splits a word mid-character.
/// Returns the full string if it already fits within the budget.
pub fn truncate_to_tokens(text: &str, max_tokens: u32) -> String {
    if max_tokens == 0 {
        return String::new();
    }

    let mut tokens: u32 = 0;
    let mut last_end: usize = 0;
    let mut in_word = false;
    let mut word_start: usize = 0;

    for (i, ch) in text.char_indices() {
        if SPLITTERS.contains(&ch) {
            if in_word {
                tokens += 1;
                if tokens > max_tokens {
                    return text[..word_start].trim_end().to_string();
                }
                last_end = i;
                in_word = false;
            }
            if !ch.is_whitespace() {
                tokens += 1;
                if tokens > max_tokens {
                    return text[..i].trim_end().to_string();
                }
                last_end = i + ch.len_utf8();
            }
        } else {
            if !in_word {
                word_start = i;
                in_word = true;
            }
        }
    }

    // Handle trailing word.
    if in_word {
        tokens += 1;
        if tokens > max_tokens {
            return text[..word_start].trim_end().to_string();
        }
        last_end = text.len();
    }

    text[..last_end].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_string_is_zero_tokens() {
        assert_eq!(count_tokens(""), 0);
    }

    #[test]
    fn single_word() {
        assert_eq!(count_tokens("hello"), 1);
    }

    #[test]
    fn simple_sentence() {
        // "hello world" = 2 words
        assert_eq!(count_tokens("hello world"), 2);
    }

    #[test]
    fn punctuation_counts_as_tokens() {
        // "hello, world!" = hello + , + world + ! = 4
        assert_eq!(count_tokens("hello, world!"), 4);
    }

    #[test]
    fn whitespace_only_is_zero() {
        assert_eq!(count_tokens("   \t\n  "), 0);
    }

    #[test]
    fn truncate_within_budget() {
        let text = "hello world";
        assert_eq!(truncate_to_tokens(text, 10), "hello world");
    }

    #[test]
    fn truncate_to_zero() {
        assert_eq!(truncate_to_tokens("hello world", 0), "");
    }

    #[test]
    fn truncate_cuts_at_word_boundary() {
        let text = "one two three four five";
        let result = truncate_to_tokens(text, 3);
        assert_eq!(result, "one two three");
    }
}
