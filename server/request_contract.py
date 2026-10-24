"""Private request provenance. A matching receipt never authorizes a replay."""
import hashlib
import json

from . import config
from .openai_format import _tool_instructions
from .schemas import ChatCompletionRequest


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def request_payload(req):
    # Omitted, false and explicit null are different inputs. In particular,
    # materializing thinking=False overrides DEEPSEEK_TOOL_THINKING on restore.
    return req.model_dump(mode='json', exclude_unset=True)


def capture(req):
    provider = config.model_provider(req.model)
    protocol = config.tool_protocol_for_provider(provider)
    return {
        'version': 1,
        'explicit_fields': sorted(req.model_fields_set),
        'request_sha256': _hash(request_payload(req)),
        'effective': {
            'provider': provider,
            'model': config.canonical_model(req.model) or req.model,
            'thinking': config.effective_thinking(req),
            'search': req.search,
            'tool_protocol': protocol,
            'tool_instructions': config.tool_instructions_for_provider(provider),
            'tool_contract_sha256': _hash(_tool_instructions(req.tools or [], req.tool_choice, protocol)),
        },
        'replay_authorized': False,
    }


def restore(payload, receipt):
    """Restore only a new-format exact request under the same effective contract.

    Old normalized logs cannot prove field presence and must not be upgraded by
    guessing. This is a pure check: no dispatch, cache claim or pause mutation.
    """
    if (not isinstance(payload, dict) or not isinstance(receipt, dict)
            or set(payload) - ChatCompletionRequest.model_fields.keys()):
        raise ValueError('Unverified request snapshot')
    req = ChatCompletionRequest.model_validate(payload)
    if receipt != capture(req):
        raise ValueError('Request provenance or effective contract changed; no replay authorized')
    return req


def authorization_contract(req):
    """Use the recovery receipt for authorization, excluding delivery mode only.

    This is not a replay permit. The caller must also consume its durable ticket.
    """
    payload = request_payload(req)
    payload.pop('stream', None)
    return capture(ChatCompletionRequest.model_validate(payload))


def record_dispatch(req, prompt, model_type, thinking):
    # The effective body is recorded immediately before client.chat(), not
    # reconstructed later from defaults after a reload or configuration change.
    receipt = capture(req)
    receipt['effective']['thinking'] = thinking
    receipt['dispatch'] = {'model_type': model_type,
                           'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    req._dispatch_contract = receipt
    return receipt
