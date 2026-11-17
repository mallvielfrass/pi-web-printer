#!/bin/bash
# Обновление копии в /opt/pi-web-printer.
# Запускать от пользователя службы, не через sudo: иначе git сделает файлы чужими.
# sudo внутри нужен только чтобы перезапустить systemd.
# Unit в /etc скрипт не перезаписывает: там локальные User и переменные.
set -euo pipefail
cd /opt/pi-web-printer

if [ ! -x .venv/bin/pip ]; then
  echo "Нет .venv. Создайте его: python3 -m venv --system-site-packages .venv"
  exit 1
fi

old=$(git rev-parse HEAD)
git pull --ff-only
new=$(git rev-parse HEAD)

# 0 — между этими коммитами путь изменился.
changed() {
  if [ "$old" = "$new" ]; then
    return 1
  fi
  if git diff --quiet "$old" "$new" -- "$@"; then
    return 1
  fi
  return 0
}

if changed requirements.txt || [ ! -f .venv/.installed ]; then
  .venv/bin/pip install -r requirements.txt
  touch .venv/.installed
fi

if changed web/ || [ ! -d web/dist ]; then
  (cd web && npm ci && npm run build)
fi

if changed deploy/pi-web-printer.service; then
  echo "Unit-файл в репозитории изменился."
  echo "Сверьте deploy/pi-web-printer.service с /etc/systemd/system/pi-web-printer.service."
  echo "Локальный unit не перезаписан."
fi

sudo systemctl restart pi-web-printer || echo "Служба ещё не установлена"
echo "Версия: $(git log -1 --format='%h %s')"
