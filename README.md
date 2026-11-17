# Принтер на Raspberry Pi

Локальный сайт, который делает из USB-МФУ Pantum M6507 сетевую станцию печати и сканирования. Телефон в той же сети открывает страницу Pi: печать, скан, свои файлы.

Сервер один: FastAPI отдаёт и API, и собранный интерфейс. Node на плате нужен, только если интерфейс собирается там же: так делает `deploy/update.sh`.

Сайт без HTTPS и без пароля пускает гостя печатать и сканировать. Держите его только в домашней сети, порт наружу не пробрасывайте.

## Где код

- `server/main.py` — собирает приложение, отдаёт статику, запускает очистку.
- `server/db.py` — SQLite: пользователи, сессии, файлы.
- `server/auth.py` — регистрация, вход, cookie, гость на 24 часа.
- `server/files.py` — загрузка, список, скачивание, удаление, предпросмотр, конвертация в PDF.
- `server/printer.py` — состояние CUPS, печать и стадия задания.
- `server/scanner.py` — скан через `scanimage` и поворот, если задан `SCAN_ROTATE`.
- `server/cleanup.py` — раз в час удаляет просроченных гостей и их файлы.
- `web/src` — интерфейс на Vue. Сборка кладётся в `web/dist`.
- `deploy/pi-web-printer.service` — unit systemd.
- `deploy/update.sh` — обновление копии в `/opt/pi-web-printer`.

Гость появляется при первой загрузке или скане, не при открытии главной. Если потом войти или зарегистрироваться, его файлы переходят на аккаунт. Файлы на диске при этом не перекладываются: владелец хранится в базе.

## Сборка интерфейса

На Pi 3B+ с 1 ГБ `npm` без подкачки легко не влезает в память. На рабочей плате включены zram и запасной swap, про них ниже: с ними `deploy/update.sh` собирает интерфейс прямо на Pi. Можно собрать и на другом компьютере, тогда на плату попадает готовый каталог `web/dist`.

```bash
cd web
npm install
npm run build
```

На Pi должен оказаться каталог `web/dist` рядом с `server`. В репозиторий сборка не входит.

Для правки интерфейса на компьютере разработчика: в одном терминале `uvicorn server.main:app --reload`, в другом `npm run dev` в `web`. Vite проксирует `/api` на порт 8000.

## Установка на Raspberry Pi OS

Рабочая машина — Raspberry Pi 3B+, 1 ГБ RAM, Raspberry Pi OS Lite 64-bit (Debian 12 bookworm). Пользователь службы — `flutter`. Если у вас другое имя, замените его в командах и в unit-файле.

### Пакеты

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-cups \
  cups cups-client cups-filters \
  libreoffice-writer libreoffice-calc libreoffice-impress
```

`python3-cups` — готовый модуль `cups`. Собирать `pycups` через pip на этой плате не нужно.

Пользователь службы должен печатать и видеть сканер. В unit-файле `User` и `Group` — `flutter`, дополнительные группы — `lp` и `scanner`.

```bash
sudo usermod -aG lp,scanner flutter
```

Новые группы подхватываются после повторного входа или перезапуска службы.

### Драйвер Pantum

Официальный Linux-драйвер Pantum собран под amd64, пакета для aarch64 нет. На этой плате он работает через box64: печать и скан с M6507 проверены на устройстве. Пакет нельзя ставить через `dpkg -i`. Ниже схема, которая уже настроена.

Запасной путь, если аппарат отвечает по IPP-over-USB, — в конце раздела. На этом M6507 он не используется.

#### box64

box64 v0.4.5. Бинарник собран не на плате, а на x86-ноутбуке, в контейнере `debian:bookworm` с `--platform linux/amd64` и `gcc-aarch64-linux-gnu`. Сборка с `cmake -DRPI3ARM64=1`.

Куда разложено:

- бинарник — `/usr/local/bin/box64`;
- библиотеки box64 — `/usr/lib/box64-x86_64-linux-gnu`;
- настройки — `/etc/box64.box64rc`;
- регистрация binfmt — `/etc/binfmt.d/box64.conf`, чтобы CUPS запускал x86-фильтры прозрачно.

#### Файлы драйвера

Пакет Pantum Linux Driver V1.1.186 (M6500 Series), файл `pantum_1.1.186-1_amd64.deb`. Его распаковывают, а не ставят:

```bash
dpkg-deb -x pantum_1.1.186-1_amd64.deb /tmp/pantum
```

Дальше скопировано:

- фильтры из `usr/lib/cups/filter` в `/usr/lib/cups/filter` (у очереди фильтр `ptm6500Filter`);
- каталог `/opt/pantum` целиком;
- PPD в `/usr/share/cups/model/Pantum`.

Очередь:

```bash
sudo lpadmin -p M6507 -E \
  -v "usb://Pantum/M6500-series?serial=..." \
  -P /usr/share/cups/model/Pantum/Pantum-M6500-Series.ppd
```

Адрес с серийным номером берите из `lpinfo -v`, не из этого примера. В службе `PRINTER_NAME=M6507`. Если переменную не задавать, берётся первая очередь.

Размер бумаги в списке на сайте — это атрибут очереди `media-supported`. Пункт, выбранный сразу, — `media-default`: у этого PPD его задаёт опция PageSize. Если CUPS размер по умолчанию не прислал, на странице выбирается A4. A4 в списке стоит первым. Длинные имена вроде `na_letter_8.5x11in` на экране показаны как Letter, в задание CUPS уходит исходное имя.

Чтобы у очереди и у системы по умолчанию был A4:

```bash
sudo lpadmin -p M6507 -o PageSize=A4
echo a4 | sudo tee /etc/papersize
```

Тонер и бумага читаются из атрибутов CUPS (`marker-levels`, `printer-state-reasons`). Если драйвер их не присылает, на главной будет «неизвестно» — это не ошибка сайта. После печати страница спрашивает стадию задания (`job-state`: в очереди, печатает, готово). Долю страниц этот драйвер не сообщает, поэтому шкала шагает по стадиям, а не по процентам. Проверка:

```bash
cd /opt/pi-web-printer
.venv/bin/python -c "import cups; c=cups.Connection(); a=c.getPrinterAttributes('M6507'); print(a.get('marker-levels'), a.get('marker-high-levels'), a.get('printer-state-reasons'))"
```

#### Библиотеки x86-64 для фильтра

```bash
sudo dpkg --add-architecture amd64
```

Дальше `apt download` пакетов `:amd64`: `libcups2`, `libcupsimage2` и их зависимости. Список зависимостей собирается рекурсивно через `apt-cache depends --recurse`. Пакеты `libc6` и `libgcc` не кладут: их даёт сам box64. Распаковка — в `/opt/x64libs`.

В `/etc/box64.box64rc` секция фильтра:

```
[ptm6500Filter]
BOX64_LD_LIBRARY_PATH=/opt/x64libs:/opt/pantum/lib:/opt/pantum/lib/CMSlib
```

#### Сканер

Тем же способом в `/opt/x64libs` распакованы x86-пакеты `sane-utils` и `libsane1` (`:amd64`). `scanimage` из них скопирован в `/opt/x64libs/scanimage`. Бэкенды Pantum (`libsane-pantum*`) лежат в `/opt/x64libs/sane`. x86 `libsane` ищет бэкенды по вшитому пути, поэтому есть симлинк:

```bash
sudo ln -s /opt/x64libs/sane /usr/lib/x86_64-linux-gnu/sane
```

Конфиги `pantum*.conf` и `dll.d/*` лежат в `/etc/sane.d`. Правила udev взяты из пакета. Стандартный `dll.conf` убран в `/etc/sane.d/off`, иначе SANE грузит сотни бэкендов. Бэкенду `pantum6500` нужна `libjpeg.so.8` из Ubuntu: пакет `libjpeg-turbo8` архитектуры amd64, релиз jammy, тоже распакован в `/opt/x64libs`.

В `/etc/box64.box64rc` секция сканера:

```
[scanimage]
BOX64_LD_LIBRARY_PATH=/opt/x64libs:/opt/x64libs/sane:/opt/pantum/lib
BOX64_EMULATED_LIBS=libusb-1.0.so.0
BOX64_NOBANNER=1
```

`BOX64_EMULATED_LIBS=libusb-1.0.so.0` нужен потому, что обёртка libusb в box64 не знает `libusb_set_option`.

Приложение вызывает просто `scanimage`. В `PATH` раньше `/usr/bin` стоит обёртка `/usr/local/bin/scanimage`:

```sh
#!/bin/sh
# x86 scanimage под box64. Служебный вывод box64 уходит в лог, а не в stdout/stderr.
export BOX64_TRACE_FILE=/var/log/pi-web-printer/box64.log
exec /opt/x64libs/scanimage "$@"
```

Каталог лога принадлежит пользователю службы:

```bash
sudo mkdir -p /var/log/pi-web-printer
sudo chown flutter:flutter /var/log/pi-web-printer
```

В логе box64 бывают сообщения про `libstdc++` и `PltResolver`. На скан они не влияют, их можно не трогать.

`SCANNER_DEVICE` лучше не задавать. Адрес `pantum6500:libusb:001:NNN` меняется при переподключении USB, и записанная строка перестаёт находить сканер. Без переменной `scanimage` берёт устройство сам.

Проверка: `scanimage -L`.

На 1 ГБ цветной скан 600 dpi легко не влезает в память. Для текста оставляйте 150 или 300 dpi и оттенки серого. Сайт не даст сохранить скан больше 80 МБ.

Сканы этого МФУ приходят перевёрнутыми на 180°. Для этого в unit-файле раскомментируйте `SCAN_ROTATE=180`.

#### Запасной вариант: IPP-over-USB

Если аппарат сам отвечает по IPP и eSCL, драйвер и box64 не нужны. Тогда ставят `ipp-usb`, `cups-ipp-utils` и `sane-airscan`:

```bash
sudo apt install -y cups-ipp-utils ipp-usb sane-utils sane-airscan
sudo systemctl enable --now cups ipp-usb
lpinfo -v
ippfind
driverless
scanimage -L
```

Адрес вида `ipp://127.0.0.1:...` добавляют так (URI копируйте из вывода):

```bash
sudo lpadmin -p Pantum-M6507 -E -v "ipp://127.0.0.1:60000/ipp/print" -m everywhere
```

Сканер в этом варианте выглядит как `escl:http://127.0.0.1:60000`. Эту строку можно записать в `SCANNER_DEVICE`.

#### Память для сборки npm

На плате стоит `zram-tools`. В `/etc/default/zramswap` заданы `ALGO=zstd`, `PERCENT=50`, `PRIORITY=100`. Дополнительно есть swap-файл на 512 МБ как запас. Без этого сборка `npm` на 1 ГБ часто не доходит до конца.

### Сервер

```bash
sudo mkdir -p /opt/pi-web-printer /var/lib/pi-web-printer
sudo rsync -a --exclude .venv --exclude data --exclude web/node_modules ./ /opt/pi-web-printer/
sudo chown -R flutter:flutter /opt/pi-web-printer /var/lib/pi-web-printer
cd /opt/pi-web-printer
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
touch .venv/.installed
```

`--system-site-packages` нужен, чтобы внутри venv был виден модуль `cups` из apt. Файл `.venv/.installed` смотрит `deploy/update.sh`: пока его нет, скрипт ставит зависимости заново.

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

В `deploy/pi-web-printer.service` поправьте `User`, `Group` и при необходимости `PRINTER_NAME` и `SCAN_ROTATE`. `SCANNER_DEVICE` на этом МФУ лучше оставить закомментированным.

```bash
sudo cp deploy/pi-web-printer.service /etc/systemd/system/pi-web-printer.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-web-printer
sudo systemctl status pi-web-printer
journalctl -u pi-web-printer -f
```

Один процесс. Несколько воркеров uvicorn не включайте: на 1 ГБ LibreOffice и проверка пароля и так занимают память.

Скрипт обновления не перезаписывает unit в `/etc`: там локальные настройки. Если в репозитории изменился `deploy/pi-web-printer.service`, скрипт только напишет об этом.

### Обновление

На плате репозиторий лежит в `/opt/pi-web-printer`. От пользователя службы:

```bash
./deploy/update.sh
```

Скрипт делает `git pull --ff-only`. Зависимости Python ставит, только если изменился `requirements.txt` или ещё нет `.venv/.installed`. Интерфейс собирает (`npm ci` и `npm run build`), только если изменился каталог `web/` или нет `web/dist`. Затем перезапускает службу и печатает текущий коммит.

### Переменные

| Переменная | По умолчанию | Зачем |
| --- | --- | --- |
| `DATA_DIR` | каталог `data` в проекте | база и файлы |
| `PRINTER_NAME` | первая очередь CUPS | какую очередь печатать |
| `SCANNER_DEVICE` | устройство по умолчанию | строка из `scanimage -L`; на этом МФУ лучше не задавать |
| `SCAN_ROTATE` | 0 | поворот скана по часовой стрелке: 0, 90, 180 или 270 |
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
