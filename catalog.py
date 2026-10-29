"""Optional catalog enrichment: failures never erase valid stock snapshots."""
import math
import time
from urllib.parse import urlencode
from economics import normalized_dimensions

def number(value):
    try:
        n=float(value)
        return n if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError):return None

def enrich(products, provider, request, headers):
    warnings=[]
    if not products:return warnings
    if provider=='WB':
        by_id={p['id'].split(':')[1]:p for p in products}
        try:
            offset=0
            for _ in range(100):
                result=request('https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter?'+urlencode({'limit':1000,'offset':offset}),None,headers)
                goods=result['data']['listGoods']
                if not isinstance(goods,list):raise ValueError()
                for item in goods:
                    p=by_id.get(str(item['nmID']))
                    if p is None:continue
                    prices=[number(size.get('discountedPrice')) for size in item.get('sizes',[])]
                    prices=[v for v in prices if v is not None]
                    if prices:
                        p.update(price_min=min(prices),price_max=max(prices),price=min(prices) if min(prices)==max(prices) else None,price_source='WB API · цена продавца после скидки')
                if len(goods)<1000:break
                offset+=len(goods);time.sleep(.7)
            else:raise ValueError()
        except (RuntimeError,ValueError,KeyError,TypeError):warnings.append('WB: цены не получены полностью; проверьте доступ «Цены и скидки».')
        try:
            cursor={'limit':100};seen=set()
            for _ in range(1000):
                result=request('https://content-api.wildberries.ru/content/v2/get/cards/list',{'settings':{'cursor':cursor,'filter':{'withPhoto':-1}}},headers)
                cards=result['cards']
                if not isinstance(cards,list):raise ValueError()
                for card in cards:
                    p=by_id.get(str(card['nmID']))
                    if p is None:continue
                    p['offer']=str(card.get('vendorCode') or p['offer'])
                    p['name']=str(card.get('title') or p['name'])
                    d=card.get('dimensions',{})
                    p['dimensions']=normalized_dimensions(d.get('height'),d.get('width'),d.get('length'))
                    p['catalog_source']='WB Content API'
                next_cursor=result.get('cursor',{})
                if len(cards)<100:break
                key=(next_cursor.get('updatedAt'),next_cursor.get('nmID'))
                if not all(key) or key in seen:raise ValueError()
                seen.add(key);cursor={**next_cursor,'limit':100};time.sleep(.7)
            else:raise ValueError()
        except (RuntimeError,ValueError,KeyError,TypeError):warnings.append('WB: названия и габариты не получены полностью; проверьте доступ «Контент».')
    elif provider=='Ozon':
        ids=sorted({int(p['id'].split(':')[1]) for p in products})
        by_id={i:[p for p in products if int(p['id'].split(':')[1])==i] for i in ids}
        for start in range(0,len(ids),1000):
            chunk=ids[start:start+1000]
            try:
                result=request('https://api-seller.ozon.ru/v3/product/info/list',{'product_id':chunk,'offer_id':[],'sku':[]},headers)
                for item in result['items']:
                    for p in by_id.get(int(item['id']),[]):
                        p['name']=str(item.get('name') or p['name'])
                        p.update(price=number(item.get('price')),price_source='Ozon API · цена продавца',catalog_source='Ozon Seller API')
            except (RuntimeError,ValueError,KeyError,TypeError):warnings.append('Ozon: цены и названия не получены для части товаров.')
            try:
                result=request('https://api-seller.ozon.ru/v4/product/info/attributes',{'filter':{'product_id':chunk,'visibility':'ALL'},'limit':1000,'sort_dir':'ASC'},headers)
                for item in result['result']:
                    for p in by_id.get(int(item['id']),[]):
                        p['dimensions']=normalized_dimensions(number(item.get('height')),number(item.get('width')),number(item.get('depth')),str(item.get('dimension_unit','')).lower())
            except (RuntimeError,ValueError,KeyError,TypeError):warnings.append('Ozon: габариты не получены для части товаров.')
            if start+1000<len(ids):time.sleep(1)
    return warnings
