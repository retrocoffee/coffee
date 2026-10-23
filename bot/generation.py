"""Cloudflare Workers AI menu artwork; one request, no provider fallback or retries."""
import base64
import binascii
import json
import logging
import os
import re
import urllib.error
import urllib.request
from bot.images import MAX_IMAGE_BYTES, prepare_image

DEFAULT_MODEL = '@cf/black-forest-labs/flux-1-schnell'
LOG = logging.getLogger('menu-bot.generation')
MAX_DESCRIPTION = 1500
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SETUP_MESSAGE = ('Генерация ещё не настроена. Добавьте два Repository secrets в GitHub → '
                 'Settings → Secrets and variables → Actions: CLOUDFLARE_API_TOKEN '
                 '(токен Workers AI) и CLOUDFLARE_ACCOUNT_ID (Account ID, 32 символа, не Zone ID). '
                 'Затем перезапустите Telegram menu admin. Токен в чат не отправляйте.')


def available():
    token = os.environ.get('CLOUDFLARE_API_TOKEN', '').strip()
    account = os.environ.get('CLOUDFLARE_ACCOUNT_ID', '').strip()
    return bool(token and '\n' not in token and '\r' not in token and re.fullmatch(r'[a-fA-F0-9]{32}', account))


def description_text(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= MAX_DESCRIPTION:
        raise ValueError(f'Опишите напиток текстом от 1 до {MAX_DESCRIPTION} символов.')
    return raw.strip()


def error_codes(data):
    if not isinstance(data, dict) or not isinstance(data.get('errors'), list):
        return set()
    # Provider text may contain credentials, account details or the prompt.
    # Only short ASCII numeric codes are safe to show or log.
    return {str(e['code']) for e in data['errors'] if isinstance(e, dict)
            and type(e.get('code')) in (str, int)
            and re.fullmatch(r'[0-9]{1,6}', str(e['code']))}


def provider_error(status, codes):
    # Forward only our own messages and numeric diagnostics, never raw responses.
    status = status if type(status) is int and 100 <= status <= 599 else None
    diagnostics = ([f'HTTP {status}'] if status is not None else [])
    if codes:
        diagnostics.append('Cloudflare ' + ', '.join(sorted(codes)[:8]))
    details = '; '.join(diagnostics) or 'Cloudflare не передал числовой код'
    LOG.warning('Workers AI request failed: %s', details)
    if '3036' in codes:
        message = 'Дневная бесплатная квота Cloudflare исчерпана. Дождитесь её обновления в 00:00 UTC (05:00 по Нижневартовску). Автоматического повтора не будет.'
    elif status == 401 or '10000' in codes:
        message = 'Cloudflare не принял токен. Проверьте CLOUDFLARE_API_TOKEN и перезапустите бота.'
    elif '5035' in codes:
        message = 'Cloudflare требует Workers Paid для этой модели. Проверьте доступность модели в Workers AI. Бот не меняет тариф и модель автоматически.'
    elif '3023' in codes:
        message = 'Cloudflare ограничил доступ аккаунта к Workers AI. Проверьте состояние аккаунта в панели Cloudflare.'
    elif '5016' in codes:
        message = 'Cloudflare требует принять условия использования модели в панели Workers AI.'
    elif codes.intersection({'5018', '3041'}):
        message = 'Эта модель недоступна вашему аккаунту. Проверьте доступ к модели в панели Workers AI.'
    elif status == 403:
        message = 'Нет доступа к Workers AI. Проверьте Account ID и разрешения токена Workers AI Read / Edit для этого аккаунта.'
    elif codes.intersection({'5007', '3042'}) or status == 404:
        message = 'Cloudflare не нашёл модель или адрес API. Проверьте Account ID и доступность модели FLUX.1 schnell.'
    elif status == 429 or '3040' in codes:
        message = 'Cloudflare сообщил о лимите или высокой нагрузке. Проверьте квоту Workers AI и попробуйте позже.'
    elif status in (408, 504) or codes.intersection({'3007', '3008'}):
        message = 'Cloudflare не успел создать картинку. Автоматического повтора не будет; повторить можно кнопкой.'
    elif status in (400, 413, 422) or codes.intersection({'5004', '3003', '3006'}):
        message = 'Cloudflare отклонил параметры запроса к модели. Сохраните код ошибки ниже для проверки; повторение того же запроса может не помочь.'
    elif status is not None and status >= 500:
        message = 'Временная ошибка сервиса Cloudflare Workers AI. Попробуйте позже. Автоматического повтора не будет.'
    else:
        message = 'Cloudflare не выполнил генерацию. Сохраните код ошибки ниже для проверки причины отказа.'
    return ValueError(message + '\n\nДиагностика: ' + details + '.')


def generate_image(name, description):
    description = description_text(description)
    if not available():
        raise ValueError(SETUP_MESSAGE)
    token = os.environ['CLOUDFLARE_API_TOKEN'].strip()
    account = os.environ['CLOUDFLARE_ACCOUNT_ID'].strip()
    # Keep the style and all validated product data inside FLUX's 2048-character limit.
    prompt = ('A small cafe menu illustration. Minimalist black and gray ink line drawing, '
              'clear silhouette at 30px. One drink or ingredient composition, centered and fully '
              'visible on pure white. No text, prices, labels, logos, watermark, frame or scenery. '
              f'Product: {name}\nDescription: {description}')
    if len(prompt) > 2048:
        raise ValueError('Описание вместе с названием слишком длинное. Сократите описание картинки.')
    # FLUX's input schema documents prompt and steps only. Let the model choose
    # its randomness instead of sending an unsupported seed parameter.
    payload = {'prompt': prompt, 'steps': 4}
    request = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{DEFAULT_MODEL}',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token,
                                                  'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        codes = set()
        try:
            codes = error_codes(json.loads(error.read(8192)))
        except (ValueError, OSError):
            pass
        raise provider_error(error.code, codes) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ValueError('Не удалось дождаться Cloudflare. Автоматического повтора не будет; запрос мог израсходовать квоту. Повторить можно кнопкой.') from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Cloudflare вернул слишком большой ответ. Попробуйте другой вариант.')
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError('Cloudflare вернул некорректный ответ. Попробуйте другой вариант.') from None
    if isinstance(data, dict) and data.get('success') is False:
        raise provider_error(status, error_codes(data))
    try:
        # The REST API wraps model output in result; the model itself returns image.
        result = data.get('result', data)
        encoded = result['image']
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
            raise ValueError()
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error):
        raise ValueError('Cloudflare вернул некорректную картинку. Попробуйте другой вариант.') from None
    return prepare_image(content)
