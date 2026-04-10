# 05 — KDV Integrator

## Scope

- Repo-root: `/opt/kdv-integrator/kdv-integrator-event/`
- Деплойний compose: `/opt/kdv-integrator/kdv-integrator-event/docker-compose.yml`
- Swarm override: `/opt/kdv-integrator/kdv-integrator-event/docker-compose.swarm.yml`
- Цільова нода: `dev-manager-01` (`app_zone=manager`)
- Рекомендований stack: `kdv_integrator_event`

## Pre-Flight

```bash
cd /opt/kdv-integrator/kdv-integrator-event
test -f docker-compose.yml
test -f docker-compose.swarm.yml
test -f .env
docker network ls | grep -E "proxy-net"
```

## Swarm Override

`docker-compose.swarm.yml`:

```yaml
services:
  kdv-api:
    container_name: !reset null
    restart: !reset null
    pull_policy: !reset null
    deploy:
      replicas: 1
      placement:
        constraints:
          - node.labels.app_zone == manager
```

## Deploy

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.swarm.yml config \
| sed '/^name:/d' \
| docker stack deploy --with-registry-auth -c - kdv_integrator_event
```

## Verify

```bash
docker service ls --filter label=com.docker.stack.namespace=kdv_integrator_event
docker stack ps kdv_integrator_event
docker service logs --since 10m --tail 100 kdv_integrator_event_kdv-api
curl -H "Host: repo.pinokew.buzz" http://127.0.0.1:8080/kdv/api/health
curl -H "Host: repo.pinokew.buzz" http://127.0.0.1:8080/kdv/api/ready
```

## Rollback

```bash
docker service update --rollback kdv_integrator_event_kdv-api
# або
docker stack rm kdv_integrator_event
```
