from app.security.pii import (
    PIIDetector,
    PIIType,
    extract_code,
    luhn_valid,
    verhoeff_valid,
)
from app.security.redaction import RedactionProfile, redact, redact_data, strip_secrets


def types(text):
    return {m.type for m in PIIDetector(include_amounts=True).detect(text)}


def test_detects_indian_identifiers():
    assert PIIType.PAN in types("my pan is ABCPE1234F")
    assert PIIType.CARD_NUMBER in types("card 4111 1111 1111 1111")
    assert PIIType.PHONE in types("call me on +91 98765 43210")
    assert PIIType.EMAIL in types("mail priya.sharma@example.com")
    assert PIIType.IFSC in types("ifsc HDFC0001234")
    assert PIIType.ACCOUNT_NUMBER in types("account number 50100217788")
    assert PIIType.DOB in types("my date of birth is 12/04/1990")
    assert PIIType.UPI_ID in types("send to rahul@okhdfc")


def test_aadhaar_requires_valid_checksum():
    assert verhoeff_valid("234123412346")
    assert PIIType.AADHAAR in types("aadhaar 2341 2341 2346")
    assert PIIType.AADHAAR not in types("aadhaar 2341 2341 2345")


def test_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")


def test_secrets_detected_and_stripped():
    t = "my otp is 482913 and pin 1234, cvv 123, password: hunter22"
    found = types(t)
    assert {PIIType.OTP, PIIType.PIN, PIIType.CVV, PIIType.PASSWORD} <= found
    stored = strip_secrets(t)
    for secret in ("482913", "1234", "123,", "hunter22"):
        assert secret not in stored
    assert "[OTP REDACTED]" in stored


def test_storage_profile_keeps_non_secret_context():
    assert "50100217788" in strip_secrets("account 50100217788 balance?")


def test_log_profile_masks_everything():
    out = redact("Priya 9876543210 card 4111111111111111 sent ₹1,00,000", RedactionProfile.LOG)
    assert "9876543210" not in out and "4111111111111111" not in out and "1,00,000" not in out
    assert "XXXX-XXXX-XXXX-1111" in out


def test_redact_data_sensitive_keys():
    out = redact_data({"otp": "123456", "nested": {"api_key": "k", "note": "pin 9999"}, "amount": 5}, RedactionProfile.AUDIT)
    assert out["otp"] == "[REDACTED]" and out["nested"]["api_key"] == "[REDACTED]"
    assert "9999" not in out["nested"]["note"] and out["amount"] == 5


def test_extract_code_variants():
    assert extract_code("123456") == "123456"
    assert extract_code("123 456") == "123456"
    assert extract_code("my otp is 482913") == "482913"
    assert extract_code("one two three four five six") == "123456"
    assert extract_code("transfer 5000 to rahul") is None


def test_digits_inside_reference_ids_are_not_pii():
    for ref in ("CH-8c8504816961", "IMPS8504816961AB", "TXN987654321012"):
        assert redact(ref, RedactionProfile.AUDIT) == ref
