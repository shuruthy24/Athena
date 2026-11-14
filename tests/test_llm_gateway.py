"""GatewayLLM against the mock COMP3110 gateway: one retry per logical call, fallback route, usage and cost."""
import pytest

from athena.llm import GatewayLLM, LLMError
from athena.pricing import estimate_cost_usd, price_for
from conftest import GATEWAY_KEY, MODEL, MockHandler

SYSTEM = "TASK: critic\nReply with JSON."
USER = '{"facts": {}, "claims": []}'


@pytest.fixture()
def gateway(mock_server):
    MockHandler.calls.clear()
    MockHandler.fail_next = []
    yield lambda fallback=None: GatewayLLM(f"{mock_server}/v1", GATEWAY_KEY, timeout_s=5, fallback_model=fallback)
    MockHandler.fail_next = []


def chat_calls():
    return [c for c in MockHandler.calls if c["path"] == "/v1/chat/completions"]


def test_lists_models_and_records_usage_and_cost(gateway):
    llm = gateway()
    assert MODEL in llm.models()
    r = llm.chat(MODEL, SYSTEM, USER, role="critic")
    assert r.model == MODEL and r.input_tokens == 1000 and r.output_tokens == 200 and r.attempts == 1
    assert r.cost_usd == pytest.approx(1000 / 1e6 * 0.1872 + 200 / 1e6 * 0.6117)
    body = chat_calls()[0]["body"]
    assert body["temperature"] == 0 and body["stream"] is False


def test_one_retry_on_server_error_then_success(gateway):
    MockHandler.fail_next = [503]
    r = gateway().chat(MODEL, SYSTEM, USER)
    assert r.attempts == 2 and len(chat_calls()) == 2


def test_second_failure_is_not_retried_again(gateway):
    MockHandler.fail_next = [503, 503, 503]
    with pytest.raises(LLMError):
        gateway().chat(MODEL, SYSTEM, USER)
    assert len(chat_calls()) == 2  # first attempt plus exactly one retry


def test_client_errors_are_not_retried(gateway):
    MockHandler.fail_next = [400]
    with pytest.raises(LLMError):
        gateway().chat(MODEL, SYSTEM, USER)
    assert len(chat_calls()) == 1


def test_retry_goes_to_the_fallback_model(gateway):
    MockHandler.fail_next = [429]
    r = gateway(fallback="gpt-oss-120b-bedrock").chat(MODEL, SYSTEM, USER)
    assert [c["body"]["model"] for c in chat_calls()] == [MODEL, "gpt-oss-120b-bedrock"]
    assert r.requested_model == "gpt-oss-120b-bedrock" and r.cost_usd is None  # Bedrock route is unpriced


def test_wrong_key_is_rejected(mock_server):
    with pytest.raises(LLMError):
        GatewayLLM(f"{mock_server}/v1", "wrong-key", timeout_s=5).models()


def test_missing_settings_fail_loudly():
    with pytest.raises(LLMError):
        GatewayLLM("", GATEWAY_KEY)
    with pytest.raises(LLMError):
        GatewayLLM("http://127.0.0.1:9/v1", "")


def test_prices_come_from_the_course_table_and_unknown_models_are_unpriced():
    assert price_for("scx-gemma-4-31B-it") == {"input": 0.3370, "output": 1.0174}
    assert estimate_cost_usd("scx-Meta-Llama-3.3-70B-Instruct", 2_000_000, 1_000_000) == pytest.approx(
        2 * 0.5929 + 1.2171)
    assert price_for("aws-nova-lite-v1") is None and estimate_cost_usd("aws-nova-lite-v1", 10, 10) is None
    assert estimate_cost_usd(MODEL, None, 10) is None
