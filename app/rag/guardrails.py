"""app/rag/guardrails.py — Deterministic Input and Output Guardrails.

Zero extra LLM calls. Enforces enterprise safety:
1. Input checks:
   - Detects prompt-injection patterns and jailbreak attempts.
   - Blocks prohibited topics (malware, system exploit, fraud).
   - Detects high-stakes / action-like requests (fund transfers, credential changes, HR termination)
     that require Human-In-The-Loop (HITL) review.
   - Scans and redacts sensitive PII (emails, phone numbers, SSNs, credit cards, API keys)
     before querying pgvector and calling the LLM.
2. Output checks:
   - Scans and redacts accidental PII leaks from retrieved document text or LLM answers
     before persisting or returning to the client.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Prompt Injection & Attack Signatures ──────────────────────────────────────

INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\bignore\s+(?:all\s+|any\s+|the\s+|entire\s+)*(?:previous\s+|prior\s+)*(?:instructions|rules|system\s+prompt|guidelines)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|print|show|dump|disclose|output|echo|leak)\s+(?:your\s+|all\s+|the\s+|hidden\s+|internal\s+)*(?:system|developer|hidden)\s+(?:prompt|instructions|context)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|print|dump|expose|send|give\s+me|disclose|show|leak|share|export)\s+(?:your\s+|all\s+|the\s+)*(?:api\s*keys?|passwords?|credentials?|secrets?|access\s+tokens?|private\s+keys?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:bypass|disable|override|turn\s+off|circumvent)\s+(?:all\s+|any\s+|the\s+)?(?:guardrails|safety\s+checks|security\s+controls|content\s+filters)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pretend|act)\s+(?:you\s+are|as\s+if\s+you\s+are)\s+(?:the\s+)?(?:system|developer|administrator|root|unrestricted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:dan\s+mode|jailbreak|unfiltered\s+mode|developer\s+mode\s+enabled)\b",
        re.IGNORECASE,
    ),
]

# ── Blocked / Prohibited Request Topics ───────────────────────────────────────

BLOCKED_TOPIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:create\s+malware|write\s+(?:a\s+)?(?:keylogger|ransomware|trojan|rootkit|virus|exploit)|ddos\s+attack|steal\s+credit\s+cards?|hack\s+into\s+network)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:how\s+to\s+(?:make|build|detonate)\s+(?:a\s+)?(?:bomb|explosive|weapon))\b",
        re.IGNORECASE,
    ),
]

# ── High-Stakes / Action-Like Triggers (Escalated to HITL) ────────────────────

SENSITIVE_ACTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:wire\s+transfer|transfer\s+funds|disburse\s+funds|send\s+(?:a\s+)?payment|change\s+bank\s+account|update\s+direct\s+deposit|payroll\s+modification)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:grant\s+(?:admin|superuser|root)\s+access|elevate\s+privileges?|reset\s+root\s+password|delete\s+database|wipe\s+(?:production|all\s+data))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:terminate\s+employee|layoff\s+notice|severance\s+agreement|execute\s+legal\s+settlement|fire\s+employee)\b",
        re.IGNORECASE,
    ),
]

# ── PII Regex Patterns & Replacement Redactors ────────────────────────────────

PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # Private Key blocks
    (
        "api_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_API_KEY]",
    ),
    # AWS / Cloud Secret Keys
    (
        "api_key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED_API_KEY]",
    ),
    # Generic API Keys / Passwords in key-value form
    (
        "api_key",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?"
        ),
        "[REDACTED_API_KEY]",
    ),
    # Social Security Number (SSN: XXX-XX-XXXX)
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    # Credit Card Numbers (13 to 16 digits, with optional hyphens or spaces)
    (
        "credit_card",
        re.compile(
            r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|6(?:011|5[0-9]{2})[0-9]{12})\b"
        ),
        "[REDACTED_CREDIT_CARD]",
    ),
    (
        "credit_card",
        re.compile(r"\b\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}\b"),
        "[REDACTED_CREDIT_CARD]",
    ),
    # Email Addresses
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    # Phone Numbers (International and US formats)
    (
        "phone",
        re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
        "[REDACTED_PHONE]",
    ),
]


# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InputGuardrailResult:
    """Result of deterministic input safety evaluation."""

    is_blocked: bool
    block_reason: str | None
    is_sensitive_action: bool
    sanitized_query: str
    pii_redacted: bool
    policy_response: str | None = None


# ── Guardrail Functions ───────────────────────────────────────────────────────


def redact_pii(text: str) -> tuple[str, bool]:
    """Deterministically scan and redact sensitive PII patterns from text.

    Args:
        text: Input text (user query, chunk content, or generated answer).

    Returns:
        Tuple of (sanitized_text, was_redacted).
    """
    if not text:
        return text, False

    sanitized = text
    redacted = False

    for _category, pattern, replacement in PII_PATTERNS:
        new_text = pattern.sub(replacement, sanitized)
        if new_text != sanitized:
            redacted = True
            sanitized = new_text

    return sanitized, redacted


def evaluate_input(query: str) -> InputGuardrailResult:
    """Evaluate user prompt against deterministic safety rules.

    Zero extra LLM calls.
    1. Checks for empty input.
    2. Checks for prompt-injection attacks.
    3. Checks for blocked / malicious topics.
    4. Detects sensitive / action-like requests that require human review.
    5. Redacts PII in the query before downstream processing.

    Args:
        query: Raw prompt submitted by the user.

    Returns:
        InputGuardrailResult with decisions and sanitized prompt.
    """
    stripped = (query or "").strip()
    if not stripped:
        return InputGuardrailResult(
            is_blocked=True,
            block_reason="empty_query",
            is_sensitive_action=False,
            sanitized_query="",
            pii_redacted=False,
            policy_response="Please enter a non-empty question.",
        )

    # 1. Prompt Injection Detection
    for pattern in INJECTION_PATTERNS:
        if pattern.search(stripped):
            return InputGuardrailResult(
                is_blocked=True,
                block_reason="prompt_injection",
                is_sensitive_action=False,
                sanitized_query=stripped,
                pii_redacted=False,
                policy_response=(
                    "This request was blocked by input safety guardrails: "
                    "potential prompt injection or policy override pattern detected."
                ),
            )

    # 2. Blocked Topics Detection
    for pattern in BLOCKED_TOPIC_PATTERNS:
        if pattern.search(stripped):
            return InputGuardrailResult(
                is_blocked=True,
                block_reason="blocked_topic",
                is_sensitive_action=False,
                sanitized_query=stripped,
                pii_redacted=False,
                policy_response=(
                    "This request was blocked by input safety guardrails: "
                    "inquiry violates organization acceptable use policies."
                ),
            )

    # 3. Sensitive / Action-Like Request Detection (Escalated to HITL)
    is_sensitive = False
    for pattern in SENSITIVE_ACTION_PATTERNS:
        if pattern.search(stripped):
            is_sensitive = True
            break

    # 4. Redact PII in input
    sanitized_query, pii_redacted = redact_pii(stripped)

    return InputGuardrailResult(
        is_blocked=False,
        block_reason=None,
        is_sensitive_action=is_sensitive,
        sanitized_query=sanitized_query,
        pii_redacted=pii_redacted,
        policy_response=None,
    )


def evaluate_output(answer: str) -> tuple[str, bool]:
    """Scan and redact accidental PII in LLM or fallback response before delivery.

    Args:
        answer: Draft answer generated by the LLM.

    Returns:
        Tuple of (sanitized_answer, was_redacted).
    """
    return redact_pii(answer)
