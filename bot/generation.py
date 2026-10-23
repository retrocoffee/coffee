"""Cloudflare Workers AI menu artwork; one request, no provider fallback or retries."""
import base64
import binascii
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from bot.images import MAX_IMAGE_BYTES, prepare_image

DEFAULT_MODEL = '@cf/black-forest-labs/flux-1-schnell'
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
    return {str(e.get('code')) for e in data['errors'] if isinstance(e, dict)}


def provider_error(status, codes):
    # Forward only our own messages, never provider bodies, headers or credentials.
    if '3036' in codes:
        return ValueError('Дневная бесплатная квота Cloudflare исчерпана. Дождитесь её обновления в 00:00 UTC. Автоматического повтора не будет.')
    if status == 401 or '10000' in codes:
        return ValueError('Cloudflare не принял токен. Проверьте CLOUDFLARE_API_TOKEN и перезапустите бота.')
    if status == 403 or codes.intersection({'5016', '5018', '3023', '3041', '5035'}):
        return ValueError('Нет доступа к Workers AI. Проверьте Account ID и разрешения токена Workers AI Read / Edit для этого аккаунта.')
    if status == 429 or '3040' in codes:
        return ValueError('Cloudflare сообщил о лимите или высокой нагрузке. Проверьте квоту Workers AI и попробуйте позже.')
    if status in (408, 504) or codes.intersection({'3007', '3008'}):
        return ValueError('Cloudflare не успел создать картинку. Автоматического повтора не будет; повторить можно кнопкой.')
    return ValueError('Cloudflare не выполнил генерацию. Попробуйте позже или измените описание.')


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
    payload = {'prompt': prompt, 'steps': 4, 'seed': secrets.randbelow(2**32)}
    request = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{DEFAULT_MODEL}',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + token,
                                                  'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
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
        raise provider_error(None, error_codes(data))
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
