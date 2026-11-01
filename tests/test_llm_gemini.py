"""GeminiProvider / ClaudeProvider request + response handling, without network calls."""
from types import SimpleNamespace

import pytest
from google import genai
from google.genai import _transformers, errors

from fundbridge.agents.followup import DraftOut, PromiseOut
from fundbridge.agents.prospector import SectorChoice
from fundbridge.agents.researcher import ExplanationsOut
from fundbridge.agents.writer import EmailsOut
from fundbridge.crosscheck import VerifierOut
from fundbridge.llm import ClaudeProvider, GeminiProvider, ProviderError, _validate

SCHEMAS = [SectorChoice, ExplanationsOut, EmailsOut, PromiseOut, DraftOut, VerifierOut]


@pytest.mark.parametrize("schema", SCHEMAS, ids=lambda s: s.__name__)
def test_sdk_accepts_every_output_schema(schema):
    client = genai.Client(api_key="test-key")  # no request is made
    assert _transformers.t_schema(client._api_client, schema) is not None


def gemini_with(resp=None, exc=None):
    p = GeminiProvider("test-key")
    calls = []

    def generate_content(**kw):
        calls.append(kw)
        if exc:
            raise exc
        return resp

    p.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return p, calls


USAGE = SimpleNamespace(prompt_token_count=100, candidates_token_count=20, thoughts_token_count=30)


def test_gemini_request_shape_and_tokens():
    out = PromiseOut(has_promise=True, what="review", date_text="by 5 Nov")
    p, calls = gemini_with(SimpleNamespace(parsed=out, text="", prompt_feedback=None, usage_metadata=USAGE))
    raw = p.generate("gemini-3.5-flash-lite", "sys", "user", PromiseOut, 8192)
    assert raw.payload == out and raw.input_tokens == 100 and raw.output_tokens == 50
    cfg = calls[0]["config"]
    assert calls[0]["model"] == "gemini-3.5-flash-lite" and cfg.system_instruction == "sys"
    assert cfg.response_mime_type == "application/json" and cfg.response_schema is PromiseOut
    assert cfg.max_output_tokens == 8192 and cfg.thinking_config.thinking_level.name == "LOW"


def test_gemini_429_maps_to_rate_limit():
    p, _ = gemini_with(exc=errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(ProviderError) as e:
        p.generate("m", "s", "u", PromiseOut, 100)
    assert e.value.kind == "rate_limit"


def test_gemini_blocked_prompt_is_refusal():
    blocked = SimpleNamespace(parsed=None, text="", prompt_feedback=SimpleNamespace(block_reason="SAFETY"), usage_metadata=None)
    p, _ = gemini_with(blocked)
    with pytest.raises(ProviderError) as e:
        p.generate("m", "s", "u", PromiseOut, 100)
    assert e.value.kind == "refusal"


def test_claude_strict_json_instruction_and_parsing():
    p = ClaudeProvider("test-key")
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=50, output_tokens=12),
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text='{"has_promise": false, "what": "", "date_text": ""}')])

    p.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    raw = p.generate("claude-haiku-5-5", "sys", "user", PromiseOut, 4096)
    assert "ONLY one JSON object" in seen["system"] and '"has_promise"' in seen["system"]
    assert seen["output_config"] == {"effort": "low"} and "temperature" not in seen
    assert _validate(PromiseOut, raw.payload).has_promise is False and raw.input_tokens == 50


def test_validate_tolerates_fences_and_prose():
    assert _validate(PromiseOut, 'Here:\n```json\n{"has_promise": true, "what": "x", "date_text": ""}\n```').what == "x"
