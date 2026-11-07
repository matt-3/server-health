# Configuração

A config é um arquivo `VAR=valor`, por padrão `/etc/default/server-health` (root, modo
600: ele guarda webhooks). Linhas que começam com `#` são comentários; os valores podem
ter aspas. Qualquer variável `SERVER_HEALTH_*` no ambiente sobrepõe o arquivo, e
`--config` aponta para outro arquivo.

## Configurações principais

| Configuração | Padrão | O quê |
|---|---|---|
| `SERVER_HEALTH_MODULES` | `auto` | quais módulos rodam: `auto`, uma lista (`host,docker`) ou `auto,-ports` ([módulos](modules.md)) |
| `SERVER_HEALTH_LANG` | `en` | idioma das mensagens: `en` ou `pt-BR` |
| `SERVER_HEALTH_LABEL` | o hostname | o nome do servidor nas mensagens (`my-server-prod`) |
| `SERVER_HEALTH_TZ` | `UTC` | fuso horário das mensagens, do horário de silêncio, do `active_hours` e dos agrupamentos por dia |
| `SERVER_HEALTH_QUIET_HOURS` | vazio (desligado) | `23-7`: os avisos nessa janela esperam e saem juntos no fim dela; o crítico sempre sai |
| `SERVER_HEALTH_DISCORD_WEBHOOK` | vazio | o canal do Discord do servidor |
| `SERVER_HEALTH_DISCORD_WEBHOOK_<APP>` | | o canal próprio de uma app (`MY_APP` para `my-app`) |
| `SERVER_HEALTH_DISCORD_MENTION` | `@here` | menção num alerta crítico novo; `@everyone`, `<@&ROLE_ID>` ou vazio |
| `SERVER_HEALTH_WEBHOOK_URL` | vazio | toda mensagem como JSON para esta URL ([abaixo](#webhook-genérico)) |
| `SERVER_HEALTH_SENTRY_DSN` | vazio | os alertas novos também registrados no Sentry |
| `SERVER_HEALTH_PING_URL` | vazio | URL de heartbeat chamada em toda rodada do cron (healthchecks.io) |
| `SERVER_HEALTH_DAILY_AT` | `09:00` | quando sai o resumo diário, ou `off` ([abaixo](#relatórios)) |
| `SERVER_HEALTH_WEEKLY_AT` | `sat 07:00` | quando sai o relatório semanal, ou `off` |
| `SERVER_HEALTH_WEEKLY_DASHBOARD` | `true` | anexa o [painel](dashboard.md) ao relatório semanal no Discord |
| `SERVER_HEALTH_UPDATE_CHECK` | `true` | uma vez por dia, procura um server-health mais novo ([updates](modules.md#updates)) |
| `SERVER_HEALTH_UPDATE_URL` | o `versions.json` do projeto | de onde essa lista é lida |
| `SERVER_HEALTH_APPS_DIR` | `/etc/server-health/apps.d` | os TOMLs das apps |
| `SERVER_HEALTH_MODULES_DIR` | `/etc/server-health/modules.d` | módulos de terceiros |
| `SERVER_HEALTH_STATE_DIR` | `/var/lib/server-health` | estado, histórico, alertas |
| `SERVER_HEALTH_LOG` | `/var/log/server-health.log` | uma linha por rodada e por mensagem |

## Configurações dos módulos

| Configuração | Módulo | O quê |
|---|---|---|
| `SERVER_HEALTH_BACKUP_STAMP` | backup | um arquivo que o script de backup toca no fim; a idade dele é a do backup |
| `SERVER_HEALTH_BACKUP_GLOB` | backup | os arquivos de backup (`/var/backups/my-app/*.dump`): o mais novo é comparado com o tamanho habitual |
| `SERVER_HEALTH_SYSTEMD_IGNORE` | systemd | units que podem falhar, separadas por vírgula |
| `SERVER_HEALTH_IGNORE_APPS` | dokku | apps do Dokku a pular, separadas por vírgula |
| `SERVER_HEALTH_HEALTH_PATH` | http | o caminho do health check das apps do Dokku (`/up`); o TOML de uma app pode definir o seu |
| `SERVER_HEALTH_NGINX_LOG` | nginx | onde fica o log de acesso de cada app, com `{app}` trocado pelo nome dela (padrão: o do Dokku, `/var/log/nginx/{app}-access.log`) |
| qualquer nome que você escolher | postgres | uma URL de banco com a senha, lida por `database_url = "env:NAME"` no TOML de uma app |
| `SERVER_HEALTH_TLS_DOMAINS` | tls | domínios extras cujo vencimento do certificado conferir |

## Limiares

Todo limiar pode ser mudado com o prefixo `SERVER_HEALTH_T_`, por exemplo
`SERVER_HEALTH_T_DB_WARN_GB=4`. Os motivos dos padrões estão em
[alerts.md](alerts.md#limiares-e-por-quê).

| Módulo | Limiares (padrão) |
|---|---|
| core | `REMIND_CRIT_MIN`=120, `REMIND_WARN_MIN`=1440 |
| updates | `UPDATE_CHECK_H`=24 |
| host | `MEM_AVAILABLE_WARN_PCT`=10, `MEM_AVAILABLE_CRIT_PCT`=5, `PSI_MEM_SOME_WARN`=10.0, `PSI_MEM_FULL_CRIT`=5.0, `MEM_WARN_PERSIST_MIN`=15, `SWAP_OUT_WARN_KBS`=50, `SWAP_OUT_CRIT_KBS`=200, `DISK_WARN_PCT`=75, `DISK_CRIT_PCT`=85, `DISK_FULL_WARN_DAYS`=14, `DISK_FULL_CRIT_DAYS`=3, `INODES_WARN_PCT`=80, `INODES_CRIT_PCT`=90, `LOAD_WARN_RATIO`=1.0, `LOAD_CRIT_RATIO`=2.0, `LOAD_WARN_PERSIST_MIN`=15, `LOAD_CRIT_PERSIST_MIN`=10 |
| backup | `BACKUP_WARN_H`=26, `BACKUP_CRIT_H`=50, `BACKUP_SHRINK_PCT`=50 |
| ssh | `SSH_KNOWN_DAYS`=30 |
| maintenance | `MAINT_STALE_DAYS`=7, `MAINT_CHECK_H`=6 |
| dokku | `APP_DOWN_PERSIST_MIN`=5, `DEPLOY_WINDOW_MIN`=30, `DEPLOY_5XX_RATIO`=3.0, `DEPLOY_5XX_MIN_PCT`=2.0, `DEPLOY_5XX_MIN_COUNT`=10, `DEPLOY_JOBS_RATIO`=3.0, `DEPLOY_JOBS_MIN`=5, `DEPLOY_UP_RATIO`=2.0, `DEPLOY_UP_MIN_MS`=300 |
| http | `UP_SLOW_WARN_S`=2.0, `UP_FAIL_PERSIST_MIN`=5 |
| nginx | `NGINX_5XX_WARN_COUNT`=10, `NGINX_5XX_WARN_PCT`=5.0, `NGINX_5XX_CRIT_COUNT`=50, `NGINX_5XX_CRIT_PCT`=20.0, `BOT_VOLUME_WARN`=300, `TRAFFIC_MIN_BASELINE`=50, `TRAFFIC_DROP_PCT`=20, `TRAFFIC_HISTORY_DAYS`=14 |
| postgres | `DB_WARN_GB`=2.0, `DB_CRIT_GB`=3.0, `DB_CONN_WARN_PCT`=80, `DB_CONN_CRIT_PCT`=95, `DB_LONG_TX_WARN_S`=300, `DB_LONG_TX_CRIT_S`=1800, `DB_DEAD_ROWS_MIN`=10000, `DB_VACUUM_STALE_DAYS`=7 |
| rails | `SQ_HEARTBEAT_WARN_S`=180, `SQ_HEARTBEAT_CRIT_S`=300, `SQ_HEARTBEAT_PERSIST_MIN`=5, `SQ_FAILED_WARN`=1, `SQ_FAILED_CRIT`=20, `SQ_QUEUE_AGE_WARN_S`=600, `SQ_QUEUE_AGE_CRIT_S`=1800, `JOB_ERROR_NEW_DAYS`=7, `TASK_GRACE_H`=2 |
| tls | `TLS_WARN_DAYS`=21, `TLS_CRIT_DAYS`=7, `TLS_CHECK_H`=6 |

## Canais

O **Discord** é o canal principal: um webhook para o servidor e, se quiser, um por app
([instalação](installation.md#2-discord-e-o-heartbeat)). Um alerta crítico novo menciona
`SERVER_HEALTH_DISCORD_MENTION`, então o celular toca.

### Webhook genérico

Com `SERVER_HEALTH_WEBHOOK_URL`, toda mensagem (alertas, lembretes, resumos, relatórios
semanais e testes) também é postada como JSON:

```json
{
  "server": "my-server-prod",
  "type": "alert",
  "level": "CRIT",
  "route": null,
  "title": "Crítico · Servidor · Memória",
  "text": "Crítico · Servidor · Memória\n3.673/3.819 MB (96%) ...\n\n== Servidor\nMemória: ..."
}
```

`type` é `alert`, `reminder`, `summary`, `weekly` ou `test`; `level` é `OK`, `WARN` ou
`CRIT`; `route` é a app com canal próprio no Discord, ou `null` para o servidor. É a
ponte para qualquer outra coisa: o [ntfy](https://ntfy.sh), um workflow do Slack, o n8n
ou um script seu.

### Sentry

Com `SERVER_HEALTH_SENTRY_DSN`, cada alerta **novo** (nunca um lembrete, um "Normalizou"
ou um resumo) vira um evento no Sentry com as tags `app:infra`, `severity` e `host`,
para as Alert Rules poderem filtrá-los.

## Relatórios

O cron só roda a rodada de 5 minutos. O resumo diário e o relatório semanal saem na
primeira rodada depois do horário deles, no `SERVER_HEALTH_TZ`:

```bash
SERVER_HEALTH_TZ=America/Sao_Paulo
SERVER_HEALTH_DAILY_AT=18:30        # todo dia às 18:30
SERVER_HEALTH_WEEKLY_AT=mon 09:00   # às segundas, às 09:00 (mon, tue, wed, thu, fri, sat, sun)
# SERVER_HEALTH_WEEKLY_AT=off       # sem relatório semanal
```

Cada um sai uma vez no seu dia. Um servidor desligado nesse horário manda o relatório na
primeira rodada depois, no mesmo dia; um dia ao qual ele não volta é pulado. O
`--dry-run` mostra quando saem os próximos, e o `--force` / `--force-weekly` os mandam
agora, marcados como teste.

## Horário de silêncio

Com `SERVER_HEALTH_QUIET_HOURS="23-7"` (no `SERVER_HEALTH_TZ`), um aviso novo, um que
piorou para aviso e um "Normalizou" nessa janela esperam numa fila e saem juntos na
primeira rodada depois dela, cada um com a hora em que aconteceu. Os lembretes de aviso
não entram na fila. Qualquer coisa crítica sai na hora.

## Silenciar

Uma manutenção planejada (um reboot, uma migração de banco) dispararia alertas de que
ninguém precisa:

```bash
sudo server-health --mute 2h     # 90m, 2h, 1d
sudo server-health --unmute
```

Enquanto silenciado, as rodadas continuam medindo e guardando o estado, mas não mandam
alerta; os resumos continuam saindo. Os alertas ainda abertos no fim voltam como
lembretes no intervalo de sempre. O `--dry-run` mostra o silêncio.
