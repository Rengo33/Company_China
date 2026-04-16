"""
Translation quality assessment.

Detects language, checks for untranslated Chinese text,
counts grammar errors, and flags common machine-translation patterns.
"""

import re
from dataclasses import dataclass

from langdetect import detect_langs, LangDetectException


@dataclass
class LanguageReport:
    primary_language: str
    has_mixed_chinese: bool
    grammar_errors_per_100_words: float
    chinglish_pattern_count: int
    score: int  # 0–100

# Common machine-translation / Chinglish patterns
CHINGLISH_PATTERNS = [
    r"\bwe provide (?:you )?(?:the )?best\b",
    r"\bhigh quality (?:and )?low price\b",
    r"\bwelcome to (?:visit|contact) us\b",
    r"\bour company (?:is )?(?:a )?professional\b",
    r"\bfactory direct\b",
    r"\bwarm(?:ly)? welcome\b",
    r"\bsuperior quality\b",
    r"\bsincerely (?:hope|welcome|invite)\b",
    r"\bcooperat(?:e|ion) with (?:you|us)\b",
    r"\bwin-win (?:cooperation|situation)\b",
    r"\bprovide (?:you )?(?:with )?satisf(?:y|actory|ied)\b",
    r"\bestablished in \d{4}\b.*\bsquare meters?\b",
    r"\bstrict quality control\b",
    r"\badvanced (?:technology|equipment)\b",
    r"\brich experience\b",
    r"\bstrong (?:technical )?(?:strength|team|force)\b",
    r"\bnew and old customers\b",
    r"\bdear (?:friend|customer)s?\b",
]

# Chinese character unicode range
CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def assess_language(text: str) -> LanguageReport:
    """
    Assess text quality for a supposedly-English web page.

    Returns a LanguageReport with a score from 0 (terrible) to 100 (native quality).
    """
    if not text or len(text.strip()) < 50:
        return LanguageReport(
            primary_language="unknown",
            has_mixed_chinese=False,
            grammar_errors_per_100_words=0,
            chinglish_pattern_count=0,
            score=50,  # neutral if no content
        )

    # Detect language mix
    primary_lang = "unknown"
    try:
        langs = detect_langs(text[:5000])
        if langs:
            primary_lang = langs[0].lang
    except LangDetectException:
        pass

    # Check for mixed Chinese characters in English text
    chinese_chars = len(CHINESE_RE.findall(text))
    total_chars = len(text)
    has_mixed = chinese_chars > 5 and (chinese_chars / max(total_chars, 1)) > 0.01

    # Count Chinglish patterns
    text_lower = text.lower()
    chinglish_count = 0
    for pattern in CHINGLISH_PATTERNS:
        chinglish_count += len(re.findall(pattern, text_lower))

    # Grammar error estimation (lightweight — no external service needed)
    words = text.split()
    word_count = len(words)
    grammar_errors = _estimate_grammar_errors(text)
    errors_per_100 = (grammar_errors / max(word_count, 1)) * 100

    # Calculate score
    score = 100

    # Penalize mixed Chinese in English content
    if has_mixed:
        score -= 25

    # Penalize Chinglish patterns (up to -30)
    score -= min(chinglish_count * 5, 30)

    # Penalize grammar issues (up to -30)
    score -= min(int(errors_per_100 * 3), 30)

    # Penalize non-English primary language on supposedly English page
    if primary_lang not in ("en", "unknown"):
        score -= 15

    score = max(0, min(100, score))

    return LanguageReport(
        primary_language=primary_lang,
        has_mixed_chinese=has_mixed,
        grammar_errors_per_100_words=round(errors_per_100, 1),
        chinglish_pattern_count=chinglish_count,
        score=score,
    )


def _estimate_grammar_errors(text: str) -> int:
    """
    Lightweight grammar error estimation without external services.
    Checks for common issues in machine-translated text.
    """
    errors = 0
    sentences = re.split(r'[.!?]+', text)

    for sentence in sentences:
        s = sentence.strip()
        if not s or len(s) < 10:
            continue

        # Missing articles before countable nouns (common in Chinese-English translation)
        errors += len(re.findall(r"\b(?:is|was|are|were) [a-z]+ (?:company|factory|product|service)\b", s.lower()))

        # Double spaces or odd spacing
        errors += len(re.findall(r"  +", s))

        # Sentence starting with lowercase (after first sentence)
        if s[0].islower() and len(s) > 20:
            errors += 1

        # Very long sentences (> 60 words) — common in MT
        if len(s.split()) > 60:
            errors += 1

    return errors
