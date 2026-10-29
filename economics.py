"""Transparent per-unit estimates. No assumed marketplace or tax rates."""
import math

FIELDS = ('price','cost','commission_pct','tax_pct','acquiring_pct','logistics','storage','returns','other')

def validate_economics(data):
    if not isinstance(data,dict) or set(data)-set(FIELDS):
        raise ValueError('Неизвестные поля расчёта.')
    result={}
    for key in FIELDS:
        value=data.get(key)
        if value is None or value=='':
            result[key]=None
        elif type(value) not in (int,float) or not math.isfinite(value) or value<0 or value>(100 if key.endswith('_pct') else 100000000):
            raise ValueError('Укажите неотрицательные суммы и проценты от 0 до 100.')
        else:
            result[key]=float(value)
    return result

def calculate(data, api_price=None):
    c=validate_economics(data or {})
    price=c['price'] if c['price'] is not None else api_price
    if type(price) not in (int,float) or not math.isfinite(price) or price<0:
        price=None
    missing=[key for key in FIELDS if (price if key=='price' else c[key]) is None]
    result=dict(inputs=c,price=price,price_source='manual' if c['price'] is not None else 'api' if price is not None else 'missing',missing=missing,profit=None,margin_pct=None,roi_pct=None,breakeven=None,breakdown=[],status='incomplete')
    if missing:return result
    rate=sum(c[k] for k in ('commission_pct','tax_pct','acquiring_pct'))/100
    fixed=sum(c[k] for k in ('cost','logistics','storage','returns','other'))
    breakdown=[dict(key=k,amount=round(c[k],2)) for k in ('cost','logistics','storage','returns','other')]
    breakdown += [dict(key=k,amount=round(price*c[k]/100,2)) for k in ('commission_pct','tax_pct','acquiring_pct')]
    profit=price*(1-rate)-fixed
    result.update(profit=round(profit,2),margin_pct=round(profit/price*100,1) if price else None,
        roi_pct=round(profit/c['cost']*100,1) if c['cost'] else None,
        breakeven=round(fixed/(1-rate),2) if rate<1 else None,breakdown=breakdown,
        status='loss' if profit<0 else 'positive' if profit>0 else 'zero')
    return result

def normalized_dimensions(height,width,length,unit='cm'):
    factors={'mm':.1,'cm':1,'m':100}
    if unit not in factors:return None
    values=[height,width,length]
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in values):return None
    h,w,l=[round(v*factors[unit],3) for v in values]
    return dict(height_cm=h,width_cm=w,length_cm=l,volume_l=round(h*w*l/1000,3))
