"""Secrets scanner: placeholders pass, credential-shaped values fail. Test values are built at runtime so this
file never contains a credential-shaped string itself."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scan_secrets import scan_text  # noqa: E402


def test_placeholders_and_env_reads_pass():
    text = "\n".join([
        "COMP3110_API_KEY=YOUR_VIRTUAL_KEY",
        "SCOUT_SECRET=change-me-to-a-long-random-string",
        'api_key=os.environ["COMP3110_API_KEY"]',
        'GATEWAY_KEY = "test-gateway-key-not-real"',
    ])
    assert scan_text(text) == []


def test_credential_shaped_values_fail():
    fake_key = "sk-" + "a1B2c3D4" * 4
    fake_assignment = "COMP3110_API_KEY=" + "Q7w8E9r0T1y2U3i4O5p6"
    hits = scan_text(f"key = '{fake_key}'\n{fake_assignment}\n")
    assert [n for n, _ in hits] == [1, 2]
