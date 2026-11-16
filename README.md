# Принтер на Raspberry Pi

Локальный сайт, который делает из USB-МФУ Pantum M6507 сетевую станцию печати и сканирования. Телефон в той же сети открывает страницу Pi: печать, скан, свои файлы.

Сервер один: FastAPI отдаёт и API, и собранный интерфейс. Node на Pi не нужен.

Сайт без HTTPS и без пароля пускает гостя печатать и сканировать. Держите его только в домашней сети, порт наружу не пробрасывайте.

## Где код

- `server/main.py` — собирает приложение, отдаёт статику, запускает очистку.
- `server/db.py` — SQLite: пользователи, сессии, файлы.
- `server/auth.py` — регистрация, вход, cookie, гость на 24 часа.
- `server/files.py` — загрузка, список, скачивание, удаление, предпросмотр, конвертация в PDF.
- `server/printer.py` — состояние CUPS и печать.
- `server/scanner.py` — скан через `scanimage`.
- `server/cleanup.py` — раз в час удаляет просроченных гостей и их файлы.
- `web/src` — интерфейс на Vue. Сборка кладётся в `web/dist`.

Гость появляется при первой загрузке или скане, не при открытии главной. Если потом войти или зарегистрироваться, его файлы переходят на аккаунт. Файлы на диске при этом не перекладываются: владелец хранится в базе.

## Сборка интерфейса

Собирайте на другом компьютере. На Pi 3B+ с 1 ГБ `npm` легко упирается в память.

```bash
cd web
npm install
npm run build
```

На Pi должен оказаться каталог `web/dist` рядом с `server`. В репозиторий сборка не входит.

Для правки интерфейса на компьютере разработчика: в одном терминале `uvicorn server.main:app --reload`, в другом `npm run dev` в `web`. Vite проксирует `/api` на порт 8000.

## Установка на Raspberry Pi OS

Проверено по составу пакетов Debian 13 (trixie), 64-bit. Имя пользователя ниже — `pi`. Если у вас другое, замените его в командах и в unit-файле.

### Пакеты

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-cups \
  cups cups-client cups-filters cups-ipp-utils ipp-usb \
  sane-utils sane-airscan \
  libreoffice-writer libreoffice-calc libreoffice-impress
```

`python3-cups` — готовый модуль `cups`. Собирать `pycups` через pip на этой плате не нужно.

Пользователь службы должен печатать и видеть сканер:

```bash
sudo usermod -aG lp,scanner pi
```

### Драйвер Pantum

Официальный Linux-драйвер Pantum собран под i386 и amd64. Пакета для aarch64 нет, на 64-bit Raspberry Pi OS он не ставится. Ставить его через qemu/box86 на плату с 1 ГБ памяти не стоит: и печать, и скан будут тяжёлыми и нестабильными.

Рабочий путь — если сам МФУ умеет IPP-over-USB и eSCL. Тогда хватает `ipp-usb`, CUPS driverless и `sane-airscan`. У части аппаратов M6507 это есть, у части нет. Это нужно проверить, а не предполагать.

```bash
sudo systemctl enable --now cups ipp-usb
lpinfo -v
ippfind
driverless
```

Что смотреть:

- В `lpinfo -v` есть адрес `ipp://127.0.0.1:...` или `ipp://...local`. Это ipp-usb. Принтер добавляется так (URI скопируйте из вывода, не из этого примера):

```bash
sudo lpadmin -p Pantum-M6507 -E -v "ipp://127.0.0.1:60000/ipp/print" -m everywhere
lpstat -p
```

- В списке только `usb://Pantum/...` и нет ipp-адреса. Драйвера под ARM нет, driverless этот USB сам не возьмёт. Пока устройство не заговорит по IPP, сайт покажет «В CUPS нет принтеров» или «Принтер не найден».

Имя очереди (`Pantum-M6507` выше) потом можно записать в `PRINTER_NAME`. Если переменную не задавать, берётся первая очередь.

Тонер и бумага читаются из атрибутов CUPS (`marker-levels`, `printer-state-reasons`). Если драйвер их не присылает, на главной будет «неизвестно» — это не ошибка сайта. Проверка:

```bash
cd /opt/pi-web-printer
.venv/bin/python -c "import cups; c=cups.Connection(); a=c.getPrinterAttributes('Pantum-M6507'); print(a.get('marker-levels'), a.get('marker-high-levels'), a.get('printer-state-reasons'))"
```

### Сканер

```bash
scanimage -L
```

Если устройство нашлось, строка выглядит примерно так: `device 'escl:http://127.0.0.1:60000' is a ...`. Её целиком, без слова `device` и без кавычек, можно записать в `SCANNER_DEVICE`. Если строка одна, переменную можно не задавать: `scanimage` возьмёт устройство сам.

Пустой `scanimage -L` значит, что SANE устройство не видит. Группа `scanner` и пакет `sane-airscan` уже должны стоять. Проприетарный бэкенд Pantum на ARM тоже не ставится.

На 1 ГБ цветной скан 600 dpi легко не влезает в память. Для текста оставляйте 150 или 300 dpi и оттенки серого. Сайт не даст сохранить скан больше 80 МБ.

### Сервер

```bash
sudo mkdir -p /opt/pi-web-printer /var/lib/pi-web-printer
sudo rsync -a --exclude .venv --exclude data --exclude web/node_modules ./ /opt/pi-web-printer/
sudo chown -R pi:pi /opt/pi-web-printer /var/lib/pi-web-printer
cd /opt/pi-web-printer
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`--system-site-packages` нужен, чтобы внутри venv был виден модуль `cups` из apt.

Проверка от пользователя службы:

```bash
.venv/bin/python -c "import cups; print(list(cups.Connection().getPrinters()))"
scanimage -L
```

Ручной запуск:

```bash
DATA_DIR=/var/lib/pi-web-printer .venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
```

С телефона в той же сети: `http://<адрес-pi>:8000`.

### systemd

В `deploy/pi-web-printer.service` поправьте `User`, `Group` и при необходимости `PRINTER_NAME` / `SCANNER_DEVICE`.

```bash
sudo cp deploy/pi-web-printer.service /etc/systemd/system/pi-web-printer.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-web-printer
sudo systemctl status pi-web-printer
journalctl -u pi-web-printer -f
```

Один процесс. Несколько воркеров uvicorn не включайте: на 1 ГБ LibreOffice и проверка пароля и так занимают память.

### Переменные

| Переменная | По умолчанию | Зачем |
| --- | --- | --- |
| `DATA_DIR` | каталог `data` в проекте | база и файлы |
| `PRINTER_NAME` | первая очередь CUPS | какую очередь печатать |
| `SCANNER_DEVICE` | устройство по умолчанию | строка из `scanimage -L` |
| `MAX_UPLOAD_MB` | 50 (не больше 200) | предел одного загружаемого файла |
| `COOKIE_SECURE` | выключено | `1`, только если перед сервером есть HTTPS |

## Тесты

Железо, CUPS и LibreOffice тестам не нужны: принтер и сканер подменяются.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

На машине разработки модуль `cups` может отсутствовать. Для тестов это нормально.
