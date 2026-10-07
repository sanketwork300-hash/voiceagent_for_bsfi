import pytest

from app.i18n.detector import LanguageDetector
from app.i18n.formatting import format_inr, number_to_indian_words, to_speech_text
from app.i18n.lexicon import extract_amount, extract_payee, rule_classify

d = LanguageDetector()


@pytest.mark.parametrize("text,code,script", [
    ("What is my loan balance?", "en", "Latn"),
    ("Mera credit card ka outstanding kitna hai?", "hi", "Latn"),
    ("मेरा लोन बैलेंस कितना है?", "hi", "Deva"),
    ("माझ्या खात्यात किती शिल्लक आहे?", "mr", "Deva"),
    ("என் கணக்கு இருப்பு என்ன?", "ta", "Taml"),
    ("నా బ్యాలెన్స్ ఎంత?", "te", "Telu"),
    ("আমার ব্যালেন্স কত?", "bn", "Beng"),
    ("ನನ್ನ ಬ್ಯಾಲೆನ್ಸ್ ಎಷ್ಟು?", "kn", "Knda"),
    ("મારું બેલેન્સ કેટલું છે?", "gu", "Gujr"),
    ("ਮੇਰਾ ਬੈਲੇਂਸ ਕਿੰਨਾ ਹੈ?", "pa", "Guru"),
    ("എന്റെ ബാലൻസ് എത്ര?", "ml", "Mlym"),
])
def test_detects_languages_and_scripts(text, code, script):
    got = d.detect(text)
    assert (got.code, got.script) == (code, script)


def test_hinglish_is_code_mixed():
    got = d.detect("Mera credit card ka outstanding kitna hai?")
    assert got.code_mixed and got.label == "hinglish"


@pytest.mark.parametrize("text,intent", [
    ("What is the home loan interest rate?", "KNOWLEDGE_QUERY"),
    ("What are home loan foreclosure charges?", "KNOWLEDGE_QUERY"),
    ("What is my loan balance?", "CUSTOMER_DATA_QUERY"),
    ("Mera credit card ka outstanding kitna hai?", "CUSTOMER_DATA_QUERY"),
    ("Block my card.", "ACTION_REQUEST"),
    ("Transfer ₹100,000 to Rahul.", "ACTION_REQUEST"),
    ("Someone stole money from my account.", "FRAUD_REQUEST"),
    ("I want to speak to a human", "HUMAN_HANDOFF"),
    ("hello", "GENERAL_CONVERSATION"),
])
def test_rule_intents(text, intent):
    assert rule_classify(text)[0] == intent


@pytest.mark.parametrize("text,amount", [
    ("Transfer ₹100,000 to Rahul", 100000), ("send rs 1,00,000", 100000), ("2 lakh bhejo", 200000),
    ("transfer 50k", 50000), ("₹1.5 crore", 15000000), ("Rahul ko 5000 rupaye bhejo", 5000),
])
def test_amount_extraction(text, amount):
    assert extract_amount(text) == amount


def test_payee_extraction():
    assert extract_payee("Transfer ₹100,000 to Rahul.") == "Rahul"
    assert extract_payee("Rahul ko 5000 bhejo") == "Rahul"


def test_inr_formatting():
    assert format_inr(2845000) == "₹28,45,000"
    assert format_inr(1172.5) == "₹1,172.50"
    assert format_inr(100000) == "₹1,00,000"
    assert number_to_indian_words(2845000) == "twenty eight lakh forty five thousand"


def test_speech_rendering():
    en = to_speech_text("Your balance is ₹1,00,000 on card XXXX4242 [1].", "en")
    assert "₹" not in en and "[1]" not in en and "one lakh rupees" in en and "4 2 4 2" in en
    hi = to_speech_text("Outstanding ₹23,450 hai.", "hi")
    assert "23 hazaar 450 rupaye" in hi
