# Автоматический кросспостинг: Telegram (@dremin_live) ➔ Meta Threads

Автономный сервис на macOS для автоматической публикации постов из канала **Dremin live** в **Meta Threads** через официальный Threads API.

Поддерживает:
- 📸 **Одиночные фотографии** с описанием
- 🖼 **Карусели / Альбомы до 10 фотографий** (автоматическая склейка частей медиа-группы Telegram)
- 📝 **Текстовые посты**
- 🛡 **Защита от дублей** (база SQLite хранит обработанные `message_id`)
- 🏷 **Автоматическая подпись** (например: `\n\nDremin live | t.me/dremin_live`)
- ⚙️ **Фоновый демон macOS** (работает через системный `launchd`, запускается при входе в систему)
- 🚫 **Без внешних зависимостей** (чистый Python 3, встроенные модули `urllib` и `sqlite3`).

---

## Быстрый старт: 3 шага

### Шаг 1. Создать Telegram-бота (1 минута)
1. Откройте в Telegram диалог с **[@BotFather](https://t.me/BotFather)**.
2. Отправьте команду `/newbot`.
3. Введите любое имя (например, `Dremin Crossposter`) и юзернейм (например, `dremin_crosspost_bot`).
4. Скопируйте полученный **HTTP API Token** (формата `123456789:ABCdef...`).
5. Зайдите в ваш канал **Dremin live** ➔ *Управление каналом* ➔ *Администраторы* ➔ *Добавить администратора* ➔ выберите вашего бота (достаточно прав на чтение сообщений).

---

### Шаг 2. Получить токен Meta Threads API

Meta Threads API доступен для всех аккаунтов. Чтобы получить доступ:
1. Зайдите на **[developers.facebook.com](https://developers.facebook.com/)** под своим профилем Facebook/Instagram.
2. Нажмите **Мои приложения (My Apps)** ➔ **Создать приложение (Create App)**.
3. Выберите сценарий: **Другое (Other)** ➔ Тип: **Для бизнеса (Business)** или **Потребительское (Consumer)**.
4. В списке продуктов выберите **Threads** и нажмите *Настроить (Set Up)*.
5. В разделе **Threads** ➔ **App Roles (Роли приложения)** добавьте ваш Instagram/Threads аккаунт в качестве Тестировщика (Tester).
6. В разделе **Tools** ➔ **Graph API Explorer** (или Threads Token Generator):
   * Выберите приложение и аккаунт Threads.
   * Разрешите права (scopes): `threads_basic`, `threads_content_publish`.
   * Нажмите **Generate Access Token**.
   * Скопируйте **User ID** (ваш числовой идентификатор Threads) и **Long-Lived Access Token** (токен со сроком 60 дней, который можно обновлять автоматически).

---

### Шаг 3. Заполнить `config.json`

Скопируйте шаблон:
```bash
cp config.example.json config.json
```

Откройте `config.json` и вставьте ваши ключи:
```json
{
  "telegram_bot_token": "ВАШ_ТОКЕН_ИЗ_BOTFATHER",
  "telegram_channel_id": "@dremin_live",
  "threads_user_id": "ВАШ_THREADS_USER_ID",
  "threads_access_token": "ВАШ_THREADS_ACCESS_TOKEN",
  "append_signature": true,
  "signature_text": "\n\nDremin live | t.me/dremin_live",
  "poll_interval_seconds": 20
}
```

---

## Тестирование

Проверить подключение к обоим API без публикации:
```bash
python3 crosspost.py --test-all
```

Запустить в тестовом режиме "холостого хода" (будет слушать Telegram, но не публиковать в Threads):
```bash
python3 crosspost.py --dry-run
```

---

## Запуск в фоне (Автозапуск на Mac)

Чтобы скрипт постоянно работал в фоне и стартовал сам при включении Mac:
```bash
./setup_service.sh
```

Логи будут писаться в:
- `crosspost.log` — журнал работы и публикации постов
- `stdout.log` / `stderr.log` — системный вывод

Остановить фоновую службу:
```bash
./stop_service.sh
```
