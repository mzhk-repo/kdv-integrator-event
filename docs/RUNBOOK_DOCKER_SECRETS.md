# Runbook: Додавання нового Docker Secret

Мета: безпечно додавати нові секрети в Docker Swarm без регресій у runtime.

## 1) Коли змінна має бути secret

Змінна має бути Docker Secret, якщо це:
- пароль;
- API key/token;
- приватний ключ/сертифікат;
- будь-яке чутливе значення, яке не повинно потрапляти в Git.

Якщо змінна не секретна (URL, host, mode, ліміти, таймаути), вона має бути в `.env.public`.

## 2) Naming convention

Рекомендується:
1. ENV-ім'я у застосунку: `UPPER_SNAKE_CASE` (наприклад, `NEW_API_TOKEN`).
2. Ім'я Docker secret у Swarm: `lower_snake_case` (наприклад, `new_api_token`).
3. Назва vault-змінної в Ansible: `vault_<lower_snake_case>` (наприклад, `vault_new_api_token`).

## 3) Кроки зміни

### Крок 1. Додати в Ansible vault

Файл:
- `/opt/Ansible/ansible/inventories/dev/group_vars/all/swarm_secrets.vault.yml`

Додати:

```yaml
vault_new_api_token: "REPLACE_ME"
```

### Крок 2. Додати mapping в Ansible vars

Файл:
- `/opt/Ansible/ansible/inventories/dev/group_vars/all/vars.yml`

У `docker_secrets` додати:

```yaml
- name: "new_api_token"
  value: "{{ vault_new_api_token }}"
```

### Крок 3. Підключити secret у swarm compose

Файл:
- `docker-compose.swarm.yml`

1. У сервісі додати в `secrets`:

```yaml
- NEW_API_TOKEN
```

2. У глобальному `secrets:` додати:

```yaml
NEW_API_TOKEN:
  external: true
  name: new_api_token
```

Умова сумісності з wrapper:
- `source`/секрет має монтуватись як `/run/secrets/NEW_API_TOKEN` (або треба явно налаштувати `target` з таким іменем).
- `scripts/entrypoint.sh` експортує ім'я файлу як ENV-ключ.

### Крок 4. Оновити `.env.public` (лише за потреби)

Додаємо тільки non-secret public змінні.
Секретні ключі в `.env.public` не додаємо.

### Крок 5. Застосувати secrets

```bash
cd /opt/Ansible/ansible
ansible-playbook -i inventories/dev/hosts.yml playbooks/swarm.yml --tags secrets
```

### Крок 6. Деплой stack

```bash
cd /opt/kdv-integrator/kdv-integrator-event
docker compose --env-file .env.public -f docker-compose.yml -f docker-compose.swarm.yml config \
| sed '/^name:/d' \
| docker stack deploy -c - kdv_integrator_event
```

## 4) Перевірка після деплою

1. Сервіс живий:

```bash
docker service ls | rg kdv_integrator_event_kdv-api
```

2. Secret існує в Swarm:

```bash
docker secret ls | rg new_api_token
```

3. Secret підключений до сервісу:

```bash
docker service inspect kdv_integrator_event_kdv-api \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Secrets}}{{println .SecretName}}{{end}}'
```

4. Secret змонтований у контейнері:

```bash
CID=$(docker ps --filter label=com.docker.swarm.service.name=kdv_integrator_event_kdv-api --format '{{.ID}}' | head -n1)
docker exec "$CID" ls -1 /run/secrets | sort
```

5. Якщо треба, перевірка що ENV реально в процесі:

```bash
docker exec "$CID" sh -lc "tr '\0' '\n' < /proc/1/environ | rg '^NEW_API_TOKEN='"
```

Увага:
- не виводьте значення секрета у лог/термінал;
- перевіряйте лише факт наявності або довжину.

## 5) Troubleshooting

### Проблема: `Additional property pull_policy is not allowed`

Причина:
- `docker stack deploy` не приймає окремі compose-поля.

Рішення:
- деплоїти через `docker compose ... config | sed '/^name:/d' | docker stack deploy -c - ...`.

### Проблема: auth loop / сервіс у `legacy` замість `dual`

Причина:
- `KDV_AUTH_MODE` або інші public ENV не потрапили в runtime.

Рішення:
1. Додати явний `environment:` у `docker-compose.swarm.yml`.
2. Деплоїти з `--env-file .env.public`.
3. Перевірити `/proc/1/environ`.

### Проблема: `/app/scripts/entrypoint.sh: no such file or directory`

Причина:
- поточний image не містить скрипт.

Рішення:
1. Релізнути новий image зі скриптом.
2. Тимчасово додати bind-mount `entrypoint.sh` і `chmod +x` на хості.

## 6) Rollback

1. Відкотити зміни в `docker-compose.swarm.yml` і Ansible vars.
2. Повторно застосувати Ansible (за потреби).
3. Перезадеплоїти попередній стабільний маніфест stack.

## 7) Після змін

1. Оновити документацію, якщо змінився процес.
2. Додати запис в активний changelog:
- Context
- Change
- Verification
- Risks
- Rollback
