"""Internal usage points, independent of provider prices and request quotas."""
import json
from decimal import Decimal, ROUND_HALF_UP

DEFAULT_POLICY = {'tokens_per_point': 1000, 'search_points': 10}
PRECISION = Decimal('0.000001')
SCALE = 1_000_000


def point_micros(value):
    return int((Decimal(str(value)) * SCALE).to_integral_value(rounding=ROUND_HALF_UP))


def reservation_amount(kind, policy, token_bound):
    rate = policy.get('pricing')
    if rate:
        if rate['unit'] == 'request':
            return point_micros(rate['points'])
        if rate['points'] == 0:
            return 0
        if token_bound is not None:
            amount = Decimal(token_bound) * Decimal(str(rate['points'])) * SCALE / rate['unit_size']
            return int(amount.to_integral_value(rounding='ROUND_CEILING'))
        return 100 * SCALE
    if kind == 'search':
        return policy['search_points'] * SCALE
    if token_bound is None:
        return 100 * SCALE
    amount = Decimal(token_bound) * SCALE / policy['tokens_per_point']
    return max(1, int(amount.to_integral_value(rounding='ROUND_CEILING')))


def validate_policy(value):
    if not isinstance(value, dict) or set(value) - DEFAULT_POLICY.keys():
        raise ValueError('credits 仅支持 tokens_per_point 和 search_points')
    policy = dict(DEFAULT_POLICY, **value)
    for key, amount in policy.items():
        if type(amount) is not int or not 1 <= amount <= 1_000_000_000:
            raise ValueError('credits.' + key + ' 必须为1到1000000000的整数')
    return policy


def policy_snapshot(policy):
    return dict(version=1, **validate_policy(policy))


def token_count(usage):
    """Prefer reported totals; never add cached/reasoning detail twice."""
    if isinstance(usage, str):
        try:usage = json.loads(usage)
        except (ValueError, TypeError):return None
    if not isinstance(usage, dict):return None
    def count(value):
        return value if type(value) is int and 0 <= value <= 10**15 else None
    for key in ('total_tokens', 'totalTokenCount'):
        total = count(usage.get(key))
        if total is not None:return total
    for incoming, outgoing in (('prompt_tokens', 'completion_tokens'), ('input_tokens', 'output_tokens'), ('promptTokenCount', 'candidatesTokenCount')):
        first, second = count(usage.get(incoming)), count(usage.get(outgoing))
        if first is not None and second is not None:return first + second
    return None


def points(value):
    return float(value.quantize(PRECISION, rounding=ROUND_HALF_UP))


def measure(request):
    saved = request.get('credit_policy')
    policy = json.loads(saved) if isinstance(saved, str) else saved
    if not policy:
        return dict(points=None,total_tokens=token_count(request.get('usage')),policy=None,rate_source='missing_snapshot',status='missing_policy')
    tokens = token_count(request.get('usage')) if request['kind'] == 'model' else None
    amount = (Decimal(tokens) / policy['tokens_per_point'] if tokens is not None else None)
    if request['kind'] == 'search':amount = Decimal(policy['search_points'])
    rate=policy.get('pricing')
    if rate:
        amount=Decimal(str(rate['points'])) if rate['unit']=='request' else (Decimal(tokens)*Decimal(str(rate['points']))/rate['unit_size'] if tokens is not None else None)
        if rate['points']==0:amount=Decimal(0)
    return dict(points=points(amount) if amount is not None else None,
                total_tokens=tokens, policy=policy,
                rate_source='snapshot',
                status='measured' if amount is not None else 'missing_usage')


def summarize(requests):
    model_points = Decimal(0)
    search_points = Decimal(0)
    tool_points = Decimal(0)
    result = dict(requests=0, model_requests=0, search_requests=0, tool_requests=0, model_tokens=0,
                  unmetered_model_requests=0, unsettled_requests=0, missing_policy_requests=0)
    for request in requests:
        credit = request.get('credits') or measure(request)
        result['requests'] += 1
        result['unsettled_requests'] += request['status'] in ('started', 'unknown')
        result['missing_policy_requests'] += credit['rate_source'] == 'missing_snapshot'
        if request['kind'] == 'model':
            result['model_requests'] += 1
            if credit['points'] is None:result['unmetered_model_requests'] += 1
            if credit['total_tokens'] is not None:result['model_tokens'] += credit['total_tokens']
            if credit['points'] is not None:model_points += Decimal(str(credit['points']))
        elif request['kind'] == 'search':
            result['search_requests'] += 1
            if credit['points'] is not None:search_points += Decimal(str(credit['points']))
        elif request['kind'] == 'tool':
            result['tool_requests'] += 1
            if credit['points'] is not None:tool_points += Decimal(str(credit['points']))
    return dict(result, points=points(model_points + search_points + tool_points),
                model_points=points(model_points), search_points=points(search_points), tool_points=points(tool_points),
                complete=not (result['unmetered_model_requests'] or result['unsettled_requests'] or result['missing_policy_requests']))
