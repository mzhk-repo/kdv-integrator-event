#!/bin/bash
set -e

# Читаємо секрети Swarm і експортуємо їх як змінні оточення
if [ -d "/run/secrets" ]; then
    for secret in /run/secrets/*; do
        if [ -f "$secret" ]; then
            export $(basename $secret)="$(cat $secret)"
        fi
    done
fi

# Виконуємо оригінальну команду
exec "$@"
