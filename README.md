# РЕТРОКОФЕ

Статический сайт кофейни: крупные размытые 3D-зёрна, неподвижно зависшие в воздухе, минималистичная вывеска с объёмными буквами и тёплой подсветкой на графитовом фоне, меню с перелистыванием страницы. Все ресурсы хранятся локально; внешние сервисы при просмотре не требуются.

## Локальный запуск

```sh
npm run check
npm start
```

Открыть http://localhost:4173. Сборка и установка npm-пакетов не требуются.

## GitHub Actions → GitHub Pages

Workflow `.github/workflows/deploy.yml` проверяет сайт и публикует каталог `public` после push в `main`. Владелец репозитория должен один раз выбрать **Settings → Pages → Build and deployment → Source: GitHub Actions**. Затем запустить **Actions → Publish RetroCoffe → Run workflow**, если первый запуск произошёл до настройки Pages.

Ожидаемый адрес после успешного деплоя: https://retrocoffee.github.io/coffee/.

## Изменение меню

Позиции, объёмы, граммовка и цены находятся в `public/index.html`. Сейчас это пробные данные. Дизайн — `public/style.css`, навигация — `public/app.js`, 3D-сцена — `public/scene.js`.

Есть управление стрелками клавиатуры, свайпом, кнопками; поддержка `prefers-reduced-motion`; статичный фон при отсутствии WebGL. На телефоне разворот меню становится вертикальным.

## Зависимости и лицензии

Three.js 0.170.0 — MIT, лицензия в `public/assets/THREE-LICENSE.txt`. Montserrat, Cormorant Garamond и Manrope — SIL Open Font License; лицензии рядом с файлами шрифтов.
