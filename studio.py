"""Editable website and bot presets; no executable user-supplied code."""
import copy
import html
import io
import json
import re
import zipfile
from urllib.parse import urlsplit
from telegram_app import validate_app_url
from bot_content import GREETING, HELP

SITE = dict(brand='Запас', headline='Поставки вовремя. Решения спокойнее.',
    description='Помощник для продавцов Ozon и WB: остатки, сроки пополнения и сравнение стоимости ожидания.',
    button='Обсудить ранний доступ', url='', theme='violet',
    features=['Видно, какие товары пора пополнять', 'Сроки до появления товара в продаже', 'Сравнение: отправить сейчас или подождать'],
    show_features=True, show_steps=True)
BOT = dict(name='Запас · помощник', greeting=GREETING,
    summary_label='Обзор магазина', stock_label='Проверить остатки', help_label='Как это работает',
    low_stock=True, supply_due=True, tariff_change=True, profit_loss=True, enabled=False, app_url='')


def validate_site(data):
    if not isinstance(data, dict) or set(data) - set(SITE):
        raise ValueError('Неизвестные поля страницы.')
    result = {**copy.deepcopy(SITE), **data}
    for key, maximum in [('brand',40),('headline',110),('description',500),('button',60),('url',500)]:
        if not isinstance(result[key],str) or len(result[key])>maximum or (key!='url' and not result[key].strip()):
            raise ValueError('Проверьте длину и заполнение текста страницы.')
    if result['url']:
        parsed=urlsplit(result['url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Для кнопки нужна ссылка HTTPS без логина и пароля.')
    if result['theme'] not in ('violet','ocean','amber'):
        raise ValueError('Выберите один из трёх стилей.')
    if (not isinstance(result['features'],list) or not 1<=len(result['features'])<=6 or
            any(not isinstance(x,str) or not x.strip() or len(x)>160 for x in result['features'])):
        raise ValueError('Добавьте от 1 до 6 преимуществ длиной до 160 символов.')
    if any(type(result[k]) is not bool for k in ('show_features','show_steps')):
        raise ValueError('Неверный переключатель раздела.')
    return result


def validate_bot(data):
    if not isinstance(data,dict) or set(data)-set(BOT):
        raise ValueError('Неизвестные поля бота.')
    result={**BOT,**data}
    result['app_url']=validate_app_url(result['app_url'])
    for key, maximum in [('name',60),('greeting',1000),('summary_label',30),('stock_label',30),('help_label',30)]:
        if not isinstance(result[key],str) or not result[key].strip() or len(result[key])>maximum:
            raise ValueError('Проверьте текст и длину кнопок бота.')
    labels=[result[k] for k in ('summary_label','stock_label','help_label')]
    if len(set(labels))!=3:
        raise ValueError('Названия кнопок должны различаться.')
    if any(type(result[k]) is not bool for k in ('low_stock','supply_due','tariff_change','profit_loss','enabled')):
        raise ValueError('Неверный переключатель бота.')
    return result


def render_site(config, stylesheet='/site.css', preview=True):
    c=validate_site(config)
    esc=lambda s:html.escape(s,quote=True)
    features=''.join(f'<article><span>0{i+1}</span><h3>{esc(text)}</h3></article>' for i,text in enumerate(c['features']))
    feature_section=f'<section class="features">{features}</section>' if c['show_features'] else ''
    steps='<section class="steps"><h2>Как начать</h2><p>01 · Обсудите задачу</p><p>02 · Проверьте расчёт на своём магазине</p><p>03 · Подключите уведомления</p></section>' if c['show_steps'] else ''
    link=f'<a class="cta" href="{esc(c["url"])}" target="_blank" rel="noopener noreferrer">{esc(c["button"])} ↗</a>' if c['url'] else '<span class="cta inactive">Контакт для записи пока не указан</span>'
    note='<div class="preview-note">Предпросмотр · страница не опубликована</div>' if preview else ''
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(c['brand'])}</title><meta name="description" content="{esc(c['description'])}"><link rel="stylesheet" href="{stylesheet}"></head><body class="{c['theme']}">{note}<main><nav><strong>{esc(c['brand'])} ↗</strong><span>Помощник продавца</span></nav><section class="hero"><div class="pill">OZON + WILDBERRIES</div><h1>{esc(c['headline'])}</h1><p>{esc(c['description'])}</p>{link}<small>Ранний доступ · возможности проверяются на магазинах продавцов</small></section>{feature_section}{steps}<footer>{esc(c['brand'])} · Без обещаний гарантированного дохода</footer></main></body></html>'''


def site_archive(config, root):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('index.html',render_site(config,'site.css',False))
        for name in ('site.css','manrope.ttf','Manrope-OFL.txt'):
            z.write(root/'static'/name,name)
        z.writestr('README.txt','Статическая страница. Разместите файлы на HTTPS-хостинге. Это не публикация и не подключение оплаты.\n')
    return buffer.getvalue()


def bot_reply(text, config, snapshot):
    c=validate_bot(config)
    text=text.strip()
    command=text.split()[0].split('@')[0].lower() if text.startswith('/') else text
    if command in ('/subscription','/subscribe') or text=='Мой доступ':
        access=snapshot.get('subscription',{})
        if access.get('status')=='owner':return 'Кабинет владельца: доступ без ограничений подписки. Приём оплаты пока не подключён.'
        status=access.get('status','pending')
        return ('Пробный период начнётся после первой успешной загрузки настоящего магазина.' if status=='pending' else 'Срок доступа закончился. Данные сохранены.' if status=='expired' else 'Доступ действует. Осталось дней: '+str(access.get('days_left',0)))+' Автоматического продления нет. Покупка по СБП внутри Telegram не предлагается.'
    if command in ('/paysupport','/support','/terms'):
        access=snapshot.get('subscription',{})
        url=access.get('terms_url') if command=='/terms' else access.get('support_url')
        if url and url.startswith('https://'):
            return ('Условия использования: ' if command=='/terms' else 'Поддержка по оплате и доступу: ')+url+' Не отправляйте пароли, API-ключи или полные банковские реквизиты в обращении.'
        return 'Приём оплаты пока не запущен. Не переводите деньги на карты или по сообщениям от посторонних. Контакт поддержки и условия оплаты будут опубликованы на самостоятельном сайте до начала продаж. Telegram не является продавцом подписки.'
    if snapshot.get('account',{}).get('web') and command in ('/app','/settings','/menu'):
        return 'Это уведомления вашего веб-кабинета. Самостоятельное приложение работает в браузере; здесь доступны /summary, /stock, /supply, /alerts и /subscription.'
    if snapshot.get('account',{}).get('web') and (command in ('/help','/start') or text==c['help_label']):
        return 'Ваш Telegram связан с личным веб-кабинетом. В браузере выберите площадку, подключите API и задайте сроки поставки. /summary — обзор, /stock — остатки, /supply — сроки, /alerts — события. Полная инструкция — вкладка «Помощь» на сайте. Не отправляйте ключи API в чат.'
    if command=='/start':
        return c['greeting']
    if command in ('/help','/instructions') or text in (c['help_label'],'Инструкция','Помощь'):
        return HELP
    if command in ('/app','/settings','/menu') or text=='Открыть приложение':
        return 'Нажмите «Открыть приложение» в меню ниже или «Открыть Запас» рядом с полем сообщения. Внутри: выбор площадки, обзор, товары, поставки, настройки и полная инструкция во вкладке «Помощь».' if c['app_url'] else 'Приложение ещё не подключено: нужен рабочий HTTPS-адрес. Настройка — в локальной панели «Студия → Конструктор бота». Инструкция: /help.'
    if command=='/status':
        con=snapshot.get('connections',{})
        mode='ДЕМО — товары вымышленные' if snapshot.get('meta',{}).get('mode')!='live' else 'Данные из API — проверьте время обновления и ошибки ниже'
        return ('СОСТОЯНИЕ ПОДКЛЮЧЕНИЯ\n'+mode+'\nПлощадка: '+(snapshot.get('marketplace') or 'ещё не выбрана')+
            '\nOzon: '+('ключ добавлен' if con.get('ozon') else 'не подключён')+'\nWB: '+('ключ добавлен' if con.get('wb') else 'не подключён')+
            '\nПриложение: '+('адрес настроен' if c['app_url'] else 'не подключено')+'\nПоследние данные: '+(snapshot.get('meta',{}).get('updated') or 'ещё не загружены')+
            '\n'+(snapshot.get('meta',{}).get('error') or 'Подробности синхронизации смотрите в приложении.'))
    if command=='/summary' or text==c['summary_label']:
        products=snapshot['products']
        prefix='ДЕМО · вымышленные данные\n' if snapshot['meta'].get('mode')!='live' else ''
        return prefix+f"Товаров: {len(products)}\nТребуют внимания: {sum(p['level']!='ok' for p in products)}\nНет в наличии: {sum(p['quantity']==0 for p in products)}"
    if command in ('/stock','/stocks') or text==c['stock_label']:
        products=sorted(snapshot['products'],key=lambda p:p['level']=='ok')[:10]
        prefix='ДЕМО · вымышленные данные\n' if snapshot['meta'].get('mode')!='live' else ''
        return prefix+('\n'.join(f"{p['platform']} · {p['offer']}: {p['quantity']} шт." for p in products) or 'Остатки ещё не загружены.')
    if command=='/supply' or text=='Сроки поставок':
        prefix='ДЕМО · вымышленные данные\n' if snapshot['meta'].get('mode')!='live' else ''
        rows=[]
        for p in snapshot['products']:
            plan=p.get('plan',{})
            if plan.get('configured') and 'gap_max' in plan:
                rows.append(f"{p['platform']} · {p['offer']}: {plan['advice']} Возможный перерыв: {plan['gap_min']:.1f}–{plan['gap_max']:.1f} дн.")
        return prefix+('\n\n'.join(rows[:5]) or 'Настройте срок пополнения в приложении — до момента появления товара в продаже.')
    if command=='/alerts' or text=='Последние события':
        return '\n\n'.join(a['message'] for a in snapshot.get('alerts',[])[:5]) or 'Пока нет событий.'
    return 'Такой команды пока нет. /app — открыть приложение, /summary — обзор, /stock — остатки, /supply — поставки, /alerts — события, /status — подключения, /help — инструкция. Можно пользоваться кнопками меню.'
