# РЕТРОКОФЕ

Минималистичный сайт меню: белый фон, чёрный текст и красные разделители. Шесть раскрывающихся категорий: сезонное меню, кофе, не кофе, холодное с молоком, очень холодные напитки и бабл напитки. Позиции, объёмы, цены и добавки перенесены из документа «менюА5.docx».

## Локальный запуск

```sh
npm run check
npm start
```

Открыть http://localhost:4173. Установка зависимостей и сборка не требуются.

## Публикация

GitHub Actions в `.github/workflows/deploy.yml` проверяет сайт и публикует `public` на GitHub Pages при push в `main`.

Адрес: https://retrocoffee.github.io/coffee/.

## Изменения

Меню и цены — `public/index.html`, оформление — `public/style.css`, логотип — `public/assets/logo.svg`. Разделы работают через нативные `details` и `summary`, в том числе с клавиатуры и без JavaScript. Скрипт `public/app.js` обеспечивает открытие одной категории за раз в старых браузерах.

Manrope — SIL Open Font License, лицензия в `public/assets/MANROPE-LICENSE.txt`.
