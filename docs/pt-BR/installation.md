# Instalação

Uma vez por servidor, não por app: o monitor encontra as apps do Dokku a cada rodada, e as
outras apps pelo TOML delas. Os
comandos abaixo rodam no servidor, como um usuário com `sudo`. Os comandos longos são
quebrados com `\`: uma linha única muito longa colada dentro de `ssh '...'` chega
quebrada, e cada pedaço vira um comando separado.

## 1. Requisitos

```bash
python3 --version                # 3.11 ou mais novo (o Ubuntu 24.04 traz o 3.12)
timedatectl | grep "Time zone"   # o cron abaixo conta em UTC
cat /proc/pressure/memory        # o PSI precisa existir (qualquer kernel a partir do 4.20; Ubuntu 24.04: sim)
```

O monitor roda como root, porque `docker stats`, `dokku` e os logs do nginx exigem. Rodar
como usuário comum pediria `sudo` sem senha para o `docker`, o que é o mesmo que dar
root sem senha a esse usuário. Ele só lê: nenhum comando muda o servidor, as apps ou os
bancos, e o SQL roda em sessões somente leitura.

## 2. Discord e o heartbeat

- **Webhook:** no canal do servidor, Editar canal → Integrações → Webhooks → Copiar URL.
  É uma credencial: vai só para a config do servidor, nunca para o git.
- **Um canal por app (opcional):** sem mais nada, toda mensagem vai para esse webhook.
  Para separar, adicione `SERVER_HEALTH_DISCORD_WEBHOOK_<APP>` com o nome da app no
  Dokku em maiúsculas e `-` trocado por `_` (`my-app` → `SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP`).
  Os processos da app, o `/up`, as requisições, o Solid Queue, o deploy, os robôs, os
  indicadores e o banco passam a ir para o canal dela, cada canal recebe o próprio
  resumo diário e relatório semanal, e o `@here` só toca no canal do alerta crítico.
  Servidor e manutenção ficam no webhook do servidor, que continua obrigatório. Se o
  webhook de uma app falhar, a mensagem vai pelo do servidor, e o log registra.
- **Heartbeat (recomendado):** no [healthchecks.io](https://healthchecks.io) (o plano
  gratuito basta), crie um check com período de 5 min e tolerância de 25 min, ligue a
  integração com o Discord e copie a URL de ping. Ele cobre o que o monitor não consegue
  ver: o servidor inteiro fora do ar. Cada servidor precisa do próprio check.
- **Outros canais:** `SERVER_HEALTH_WEBHOOK_URL` manda toda mensagem como JSON para
  qualquer URL (veja a [configuração](configuration.md#webhook-genérico));
  `SERVER_HEALTH_SENTRY_DSN` registra os alertas novos no Sentry.

## 3. Instalar

Se alguma app tiver indicadores, copie antes o TOML dela para o servidor (do seu computador):

```bash
scp path/to/my-app/scripts/monitor/my-app.toml deploy@<server>:/tmp/
```

No servidor (troque `vX.Y.Z` pela versão do [changelog](../../CHANGELOG.md)):

```bash
curl -fsSL https://raw.githubusercontent.com/matt-3/server-health/vX.Y.Z/install.sh \
  | sudo bash -s -- vX.Y.Z /tmp/my-app.toml \
  && rm /tmp/my-app.toml
```

O `install.sh`:

1. baixa o `.tar.gz` da tag no GitHub e confere se ele informa a versão pedida;
2. roda o `--check-app` da nova versão em todos os TOMLs (os instalados e os novos); se
   um falhar, **nada muda**;
3. instala o pacote em `/usr/local/lib/server-health/<version>`, aponta
   `/usr/local/lib/server-health/current` para ele e grava o comando `server-health`
   em `/usr/local/bin`;
4. instala os TOMLs que você passou em `/etc/server-health/apps.d/`;
5. guarda a versão anterior, para um rollback.

É seguro rodar via `curl | bash`: o script só roda depois de lido por inteiro, e nenhum
comando dele lê a entrada padrão.

## 4. Config

```bash
sudo install -o root -g root -m 600 /dev/null /etc/default/server-health
sudo nano /etc/default/server-health
```

```bash
SERVER_HEALTH_DISCORD_WEBHOOK=https://discord.com/api/webhooks/<id>/<token>
SERVER_HEALTH_LABEL=my-server-prod
SERVER_HEALTH_TZ=America/Sao_Paulo
SERVER_HEALTH_LANG=en
SERVER_HEALTH_PING_URL=https://hc-ping.com/<uuid>
# Opcionais:
# SERVER_HEALTH_DISCORD_WEBHOOK_MY_APP=https://discord.com/api/webhooks/<id>/<token>
# SERVER_HEALTH_BACKUP_STAMP=/var/backups/my-app/.last-ok
# SERVER_HEALTH_MODULES=auto
```

Todas as configurações estão em [configuration.md](configuration.md). Depois teste antes
de agendar:

```bash
sudo server-health --list-modules      # quais módulos estão ligados
sudo server-health --dry-run           # o relatório inteiro; confira "Canais: Discord=ok"
sudo server-health --simulate memory   # um alerta crítico de teste no canal do servidor
sudo server-health --simulate app      # um alerta de app, no canal da app se ela tiver um
sudo server-health --force             # o resumo diário, marcado como teste
sudo server-health --force-weekly      # o relatório semanal, marcado como teste
```

Os testes nunca mudam o estado e chegam marcados `TESTE`. A primeira rodada mostra
`1a amostra` no swap e nas requisições: eles medem a diferença entre duas rodadas.

## 5. Agendar

```bash
sudo tee /etc/cron.d/server-health >/dev/null <<'EOF'
# server-health: uma rodada a cada 5 minutos. O resumo diário e o relatório semanal saem
# na primeira rodada depois de SERVER_HEALTH_DAILY_AT / SERVER_HEALTH_WEEKLY_AT.
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /usr/local/bin/server-health >/dev/null 2>&1
EOF
sudo chmod 644 /etc/cron.d/server-health

sudo tee /etc/logrotate.d/server-health >/dev/null <<'EOF'
/var/log/server-health.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
EOF
```

O arquivo do `cron.d` precisa terminar com uma quebra de linha e não ter ponto no nome:
senão o cron o ignora em silêncio.

O resumo diário sai às 09:00 e o relatório semanal no sábado às 07:00, no
`SERVER_HEALTH_TZ`; mude os horários na config ([configuração](configuration.md#relatórios)).
O `sudo server-health --dry-run` diz quando saem os próximos ("Próximo resumo diário: ... ·
próximo relatório semanal: ..."). Dez minutos depois:

```bash
sudo tail -n 5 /var/log/server-health.log   # uma linha "round: ..." a cada 5 min
sudo ls -la /var/lib/server-health/         # state.json e history.jsonl
```

O ping do heartbeat só sai nas rodadas do cron, nunca no `--dry-run`, no `--force` nem
no `--simulate`.

## Arquivos no servidor

| Caminho | O quê |
|---|---|
| `/usr/local/bin/server-health` | o comando |
| `/usr/local/lib/server-health/<version>/` | cada versão instalada (a ativa e a anterior) |
| `/usr/local/lib/server-health/current` | link para a versão ativa |
| `/etc/default/server-health` | a config, com os webhooks (root, **600**, fora do git) |
| `/etc/server-health/apps.d/<app>.toml` | os indicadores de cada app (root, 644) |
| `/etc/server-health/modules.d/<name>.py` | módulos de terceiros (root, 644) |
| `/etc/cron.d/server-health` | a rodada de 5 minutos |
| `/var/lib/server-health/state.json` | o estado: o nível de cada métrica, contadores, offsets do nginx |
| `/var/lib/server-health/history.jsonl` | uma amostra por rodada, 35 dias, para médias, tendências e o painel |
| `/var/lib/server-health/bots.json` | por dia, as requisições de cada robô e as páginas que os robôs pediram, 35 dias |
| `/var/lib/server-health/alerts.jsonl` | uma linha por alerta aberto, alterado ou fechado, 90 dias |
| `/var/log/server-health.log` | uma linha por rodada e por mensagem enviada |

## Dados guardados

Tudo o que o monitor grava tem um limite, então nada se acumula:

| O quê | Guardado por |
|---|---|
| histórico (`history.jsonl`) e detalhe dos robôs (`bots.json`) | 35 dias |
| alertas (`alerts.jsonl`) | 90 dias |
| estado: métricas que sumiram, reinícios de contêiner | 24 horas / 7 dias |
| estado: erros de job já vistos, faixas de SSH, portas abertas, cache de certificados | 30 dias |
| estado: última execução das tarefas diárias, semanais e mensais | 62 dias |
| o log | 5 MB, depois vira `server-health.log.1` (mais o logrotate, se você o configurou) |
| versões instaladas | a ativa e a anterior |

O histórico e os alertas são aparados uma vez por dia. Num servidor com duas apps, a
pasta de estado inteira fica em torno de poucos megabytes.

## Atualização

O mesmo comando da instalação, com a nova versão. Mudou só o TOML de uma app? Rode com a
versão já instalada (`server-health --version`) e o TOML novo. O cron pega a nova
versão na rodada seguinte; o estado sobrevive à atualização.

## Rollback

```bash
ls /usr/local/lib/server-health/                       # as versões guardadas
sudo ln -sfn 1.0.0 /usr/local/lib/server-health/current
server-health --version
```

## Desinstalar

```bash
sudo rm -f /etc/cron.d/server-health /etc/logrotate.d/server-health \
  /usr/local/bin/server-health
sudo rm -rf /usr/local/lib/server-health /var/lib/server-health /etc/server-health
sudo rm -f /etc/default/server-health /var/log/server-health.log*
```
