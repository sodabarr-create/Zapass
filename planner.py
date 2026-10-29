"""Explainable scenario calculations. No automatic purchasing or invented probabilities."""
import datetime as dt
import math
import statistics


DEFAULTS = dict(preparation=2, transit=3, acceptance_min=2, acceptance_max=10,
                safety=2, wait=3, batch=100, margin=200, base_fee=1000,
                coefficient_now=None, coefficient_wait=None, seasonal_factor=1,
                warehouse='', warehouse_id=0, box_type=2)


def validate_profile(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError('Неизвестные поля маршрута.')
    result = {**DEFAULTS, **data}
    for key in ('preparation', 'transit', 'acceptance_min', 'acceptance_max', 'safety', 'wait'):
        value = result[key]
        if type(value) is not int or not 0 <= value <= 180:
            raise ValueError('Сроки должны быть целыми числами от 0 до 180 дней.')
    for key, maximum in [('batch', 1000000), ('margin', 1000000), ('base_fee', 100000000),
                          ('seasonal_factor', 5), ('coefficient_now', 100), ('coefficient_wait', 100)]:
        value = result[key]
        if value is None and key.startswith('coefficient_'):
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError('Проверьте сумму, размер партии и коэффициенты.')
    if result['batch'] <= 0 or not .2 <= result['seasonal_factor'] <= 5:
        raise ValueError('Партия должна быть положительной; множитель спроса — от 0.2 до 5.')
    if result['acceptance_min'] > result['acceptance_max']:
        raise ValueError('Минимальный срок приёмки не может быть больше максимального.')
    if type(result['warehouse']) is not str or len(result['warehouse']) > 120:
        raise ValueError('Название склада: до 120 символов.')
    for key in ('warehouse_id', 'box_type'):
        if type(result[key]) is not int or result[key] < 0:
            raise ValueError('ID склада и тип упаковки должны быть неотрицательными целыми числами.')
    return result


def demand_model(product, today):
    """14 complete calendar days as baseline; short spikes never replace it."""
    history = {r['date']: max(0, float(r['units'])) for r in product.get('history', [])}
    complete = [today - dt.timedelta(days=n) for n in range(1, 15)]
    baseline = product.get('daily')
    recent = None
    if history and all(str(d) in history for d in complete):
        baseline = sum(history[str(d)] for d in complete) / 14
        recent = sum(history[str(d)] for d in complete[:3]) / 3
    spike = recent is not None and baseline is not None and recent >= max(3, baseline * 1.5)
    high = max(baseline or 0, recent or 0) if spike else baseline
    weekdays = None
    dates = [today - dt.timedelta(days=n) for n in range(1, 57)]
    if all(str(d) in history for d in dates):
        mean = sum(history[str(d)] for d in dates) / 56
        if mean > 0:
            weekdays = {weekday: min(3, max(.25, statistics.mean(
                history[str(d)] for d in dates if d.weekday() == weekday) / mean)) for weekday in range(7)}
    return dict(baseline=baseline, recent=recent, spike=spike, high=high,
                weekdays=weekdays, seasonal_status='weekday' if weekdays else 'insufficient')


def forecast(product, profile=None, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    model = demand_model(product, today)
    result = dict(model=model, configured=profile is not None, scenarios=[], advice='Настройте сроки пополнения для этого товара.')
    if profile is None:
        return result
    p = validate_profile(profile)
    base = model['baseline']
    if base is None or base <= 0:
        result['advice'] = 'Нет положительного темпа заказов. Дату пополнения оценить нельзя.'
        return result
    factors = model['weekdays']
    def daily(day, high=False):
        rate = model['high'] if high else base
        return rate * p['seasonal_factor'] * (factors[day.weekday()] if factors else 1)
    def cover(high=False):
        remaining = max(0, product['quantity'])
        for n in range(730):
            units = daily(today + dt.timedelta(days=n), high)
            if units > 0 and remaining < units:
                return n + remaining / units
            remaining -= units
        return 730.0
    coverage, fast_coverage = cover(), cover(True)
    min_lead = p['preparation'] + p['transit'] + p['acceptance_min']
    max_lead = p['preparation'] + p['transit'] + p['acceptance_max']
    deadline = fast_coverage - max_lead - p['safety']
    result.update(coverage=coverage, fast_coverage=fast_coverage, lead_min=min_lead,
                  lead_max=max_lead, deadline_days=deadline,
                  order_by=str(today + dt.timedelta(days=math.floor(deadline))),
                  gap_min=max(0, min_lead - coverage), gap_max=max(0, max_lead - fast_coverage),
                  profile=p)
    # One replenishment of the same batch, same shared horizon for both choices.
    # Compare through the day both alternatives should be available. An arbitrary
    # long horizon would consume both finite batches and hide the cost of delay.
    horizon = max_lead + p['wait'] + 1
    def missed_units(arrival, high=False):
        stock, missed = max(0, product['quantity']), 0
        for n in range(horizon):
            if n == arrival:
                stock += p['batch']
            demand = daily(today + dt.timedelta(days=n), high)
            missed += max(0, demand - stock)
            stock = max(0, stock - demand)
        return missed
    for key, label, delay, coefficient in [('now', 'Отправить сейчас', 0, p['coefficient_now']),
                                           ('wait', f"Подождать {p['wait']} дн.", p['wait'], p['coefficient_wait'])]:
        outcomes = [missed_units(lead + delay, high) for lead in (min_lead, max_lead) for high in (False, True)]
        missed_min, missed_max = min(outcomes), max(outcomes)
        fee = p['base_fee'] * coefficient if coefficient is not None else None
        result['scenarios'].append(dict(key=key, label=label, coefficient=coefficient, fee=fee,
            arrival_min=str(today + dt.timedelta(days=min_lead + delay)),
            arrival_max=str(today + dt.timedelta(days=max_lead + delay)),
            gap_min=max(0, min_lead + delay - coverage), gap_max=max(0, max_lead + delay - fast_coverage),
            missed_min=missed_min, missed_max=missed_max,
            total_min=None if fee is None else fee + missed_min * p['margin'],
            total_max=None if fee is None else fee + missed_max * p['margin']))
    first, second = result['scenarios']
    result['horizon_days'] = horizon
    if first['fee'] is None or second['fee'] is None:
        result['comparison'] = 'Для денежного сравнения укажите оба коэффициента. Будущий коэффициент — ваше допущение, не обещание WB.'
    elif first['total_max'] < second['total_min']:
        result['comparison'] = 'В этих сценариях отправка сейчас дешевле даже при неблагоприятном исходе. Это не гарантия.'
    elif second['total_max'] < first['total_min']:
        result['comparison'] = 'В этих сценариях ожидание дешевле даже при неблагоприятном исходе. Будущий тариф нужно подтвердить.'
    else:
        result['comparison'] = 'Диапазоны пересекаются: уверенного победителя нет. Решение зависит от спроса, приёмки и будущего тарифа.'
    result['advice'] = ('Оформляйте пополнение сейчас: безопасная дата уже наступила.' if deadline <= 0 else
                        f"Оформите пополнение не позднее {result['order_by']} по осторожному сценарию.")
    return result
