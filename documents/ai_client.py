"""Calling Claude, and keeping count of what it costs.

Every call goes through ``call()``, which records an ``AIUsage`` row with the
tokens used and the cost in US dollars, so the AI costs page can show spend
per day, per month, per order and per client.

Setup: put the key in the server's environment (``.env``):

    ANTHROPIC_API_KEY=sk-ant-...        # from console.anthropic.com
    AI_MODEL=claude-opus-5-5            # optional

Until a key is set, nothing calls the API: the rule-based order reader keeps
working and the AI features say they are not set up.
"""
import logging
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = 'claude-opus-5-5'
KEY_PLACEHOLDER = 'PASTE-YOUR-ANTHROPIC-API-KEY-HERE'

# US dollars per million tokens, from Anthropic's price list (Nov 2026).
# Check https://www.anthropic.com/pricing if prices change; the costs page
# uses this table. Cache writes are billed at 1.25x the input price.
PRICES = {
    'claude-opus-5-5':   {'input': Decimal('4.00'),  'output': Decimal('20.00'), 'cache_read': Decimal('0.20')},
    'claude-sonnet-5-5': {'input': Decimal('2.00'),  'output': Decimal('10.00'), 'cache_read': Decimal('0.10')},
    'claude-haiku-5-5':  {'input': Decimal('0.10'),  'output': Decimal('0.50'),  'cache_read': Decimal('0.01')},
    'claude-opus-5':     {'input': Decimal('5.00'),  'output': Decimal('25.00'), 'cache_read': Decimal('0.50')},
    'claude-opus-4-8':   {'input': Decimal('5.00'),  'output': Decimal('25.00'), 'cache_read': Decimal('0.50')},
    'claude-sonnet-5':   {'input': Decimal('2.00'),  'output': Decimal('10.00'), 'cache_read': Decimal('0.20')},
}
MILLION = Decimal('1000000')


class NotConfigured(Exception):
    pass


def api_key():
    key = (getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip()
    return '' if key in ('', KEY_PLACEHOLDER) else key


def model():
    configured = (getattr(settings, 'AI_MODEL', '') or '').strip()
    # The older settings default (an NVIDIA model) does not apply to Claude.
    return configured if configured.startswith('claude-') else DEFAULT_MODEL


def is_configured():
    return bool(api_key())


def usd_to_bdt():
    return Decimal(str(getattr(settings, 'USD_TO_BDT', '122')))


def cost_of(model_name, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
    price = PRICES.get(model_name)
    if price is None:
        # Unknown model: price it as Opus so spend is never under-reported.
        price = PRICES[DEFAULT_MODEL]
    return (Decimal(input_tokens) * price['input'] + Decimal(output_tokens) * price['output']
            + Decimal(cache_read) * price['cache_read'] + Decimal(cache_write) * price['input'] * Decimal('1.25')
            ) / MILLION


def _usage_rows(response):
    """One (model, usage) pair per billed attempt (fallbacks bill per attempt)."""
    usage = getattr(response, 'usage', None)
    iterations = getattr(usage, 'iterations', None) if usage is not None else None
    if iterations:
        rows = []
        for it in iterations:
            rows.append((getattr(it, 'model', None) or response.model, it))
        return rows
    return [(response.model, usage)] if usage is not None else []


def _record(purpose, order, model_name, response=None, error=''):
    from .models import AIUsage
    try:
        if response is None:
            AIUsage.objects.create(purpose=purpose, order=order, model=model_name, ok=False, error=error[:255])
            return
        request_id = getattr(response, '_request_id', '') or ''
        for used_model, usage in _usage_rows(response):
            tokens = dict(
                input_tokens=getattr(usage, 'input_tokens', 0) or 0,
                output_tokens=getattr(usage, 'output_tokens', 0) or 0,
                cache_read_tokens=getattr(usage, 'cache_read_input_tokens', 0) or 0,
                cache_write_tokens=getattr(usage, 'cache_creation_input_tokens', 0) or 0,
            )
            AIUsage.objects.create(
                purpose=purpose, order=order, model=used_model or model_name, request_id=request_id[:80],
                ok=getattr(response, 'stop_reason', '') != 'refusal', error=error[:255],
                cost_usd=cost_of(used_model or model_name, tokens['input_tokens'], tokens['output_tokens'],
                                 tokens['cache_read_tokens'], tokens['cache_write_tokens']),
                **tokens)
    except Exception:
        logger.exception('could not record AI usage')


def client():
    key = api_key()
    if not key:
        raise NotConfigured('The Claude API key is not set yet (ANTHROPIC_API_KEY in the server settings).')
    import anthropic
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)


def call(purpose, order=None, **params):
    """Send one request to Claude and record its cost.

    ``params`` are the Messages API arguments (system, messages, tools, ...).
    If Claude declines a request for policy reasons, Anthropic automatically
    retries it on a suitable fallback model (server-side fallbacks).
    """
    model_name = params.pop('model', None) or model()
    params.setdefault('max_tokens', 16000)
    params.setdefault('output_config', {'effort': 'medium'})
    try:
        response = client().beta.messages.create(
            model=model_name,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            **params,
        )
    except NotConfigured:
        raise
    except Exception as exc:
        _record(purpose, order, model_name, error=str(exc))
        raise
    _record(purpose, order, model_name, response)
    return response
