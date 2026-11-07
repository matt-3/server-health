# server-health

[English](README.md) | Português

[![test](https://github.com/matt-3/server-health/actions/workflows/test.yml/badge.svg)](https://github.com/matt-3/server-health/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

**Um monitor leve para um VPS Linux, que avisa quando algo muda e fica quieto no resto do
tempo.** Ele roda pelo cron a cada 5 minutos, mede a máquina e as apps dela e posta no
Discord (ou em qualquer webhook) quando um problema começa, enquanto ele dura e quando
ele acaba. Todo dia um resumo; toda semana um relatório com um painel.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/alert-dark.svg">
  <img alt="Um alerta crítico de memória no Discord, com os contêineres que mais usam memória, e a mensagem 'Normalizou' 35 minutos depois" src="docs/assets/alert-light.svg" width="720">
</picture>

- **Sem agente, sem daemon, sem banco, sem interface web.** Um pacote Python sem
  dependências e uma linha de cron. Uma rodada leva uns 70 ms e 30 MB, e depois nada roda
  até a próxima.
- **Alerta por pressão, não por números que parecem altos.** RAM 80% em uso é um dia
  normal; ficar sem folga, processos esperando memória ou o kernel paginando para o disco
  não são.
- **Alerta por mudança.** Uma mensagem quando um problema começa, um lembrete enquanto
  ele dura, "Normalizou" com quanto tempo levou. O mesmo problema nunca inunda o canal.
- **Cresce com o servidor.** Primeiro a máquina; depois o Docker, o Dokku, a URL de
  health e o log de acesso de qualquer app, o Postgres, o Solid Queue do Rails e os seus
  próprios indicadores SQL, cada um como um módulo opcional. Um módulo seu é um arquivo
  Python.

## Conteúdo

- [Início rápido](#início-rápido)
- [O que ele vigia](#o-que-ele-vigia)
- [Exemplos](#exemplos)
- [Resumo diário e relatório semanal](#resumo-diário-e-relatório-semanal)
- [Comandos](#comandos)
- [Perguntas frequentes](#perguntas-frequentes)
- [Documentação](#documentação)
- [Contribuindo](#contribuindo)

## Início rápido

No servidor, como um usuário com `sudo` (qualquer Linux com Python 3.11+; o Ubuntu 24.04
já traz):

```bash
# 1. Instale uma versão (a mais recente está no CHANGELOG.md)
curl -fsSL https://raw.githubusercontent.com/matt-3/server-health/v1.0.0/install.sh \
  | sudo bash -s -- v1.0.0

# 2. Diga onde postar (Discord: configurações do canal > Integrações > Webhooks)
sudo install -o root -g root -m 600 /dev/null /etc/default/server-health
sudo tee /etc/default/server-health >/dev/null <<'EOF'
SERVER_HEALTH_DISCORD_WEBHOOK=https://discord.com/api/webhooks/<id>/<token>
SERVER_HEALTH_LABEL=my-server
SERVER_HEALTH_TZ=Europe/Lisbon
EOF

# 3. Olhe, teste, agende
sudo server-health --dry-run              # o que ele mede agora
sudo server-health --simulate memory      # um alerta de teste no Discord
echo '*/5 * * * * root /usr/local/bin/server-health >/dev/null 2>&1' \
  | sudo tee /etc/cron.d/server-health >/dev/null
```

> [!WARNING]
> O `/etc/default/server-health` guarda segredos: os webhooks e, se você acrescentar, as
> URLs de banco. Mantenha o dono root e o modo 600, como o passo 2 cria.

Essa é a configuração inteira. O resto desta página é o que você pode acrescentar.

> [!TIP]
> Um monitor no próprio servidor não consegue avisar que o servidor caiu. Acrescente um
> check de heartbeat gratuito com `SERVER_HEALTH_PING_URL`, e ele alerta quando os pings
> param ([como](docs/pt-BR/installation.md#2-discord-e-o-heartbeat)).

## O que ele vigia

**O servidor**, em qualquer Linux:

| Módulo | Alerta quando |
|---|---|
| `host` | a memória disponível acaba ou há processos esperando memória; o kernel pagina para o disco; o disco enche ou vai encher em poucos dias; os inodes acabam; a fila de CPU cresce; o kernel mata um processo (OOM) |
| `systemd` | uma unit do systemd falha |
| `clock` | o relógio deixa de estar sincronizado |
| `ports` | uma porta nova passa a escutar para fora |
| `ssh` | alguém entra de uma faixa de IP não vista em 30 dias |
| `maintenance` | atualizações de segurança ou um reinício esperam há mais de uma semana (Debian/Ubuntu) |
| `backup` | o último backup bom está velho demais, ou o mais novo é bem menor que o habitual |
| `tls` | um certificado vence em 21 dias (aviso) ou em 7 (crítico) |
| `updates` | um server-health mais novo corrige um problema de segurança ou crítico |

**As apps**, quando existem:

| Módulo | Alerta quando | Funciona com |
|---|---|---|
| `docker` | (alimenta o "Quem usa a memória") | Docker |
| `dokku` | uma app fica sem processo rodando; um contêiner reinicia ou morre por memória; um deploy piora muito os 5xx, as falhas de job ou o tempo de resposta; um upload seria perdido ou recusado | Dokku |
| `http` | a URL de health deixa de responder 2xx, ou fica lenta | apps do Dokku, ou `health_url` |
| `nginx` | 5xx em volume; um robô martela a app; os visitantes caem muito abaixo do habitual para a hora | um log de acesso |
| `postgres` | um banco fica grande demais, fica sem conexões, segura uma transação longa ou atrasa o vacuum | Dokku, `database_url` ou `database_container` |
| `rails` | o worker, o dispatcher ou o scheduler do Solid Queue para; jobs falham; um job falha com um erro nunca visto; uma fila para; uma tarefa recorrente perde o ritmo | Postgres com Solid Queue |
| `indicators` | um dos seus indicadores SQL passa do limite | Postgres |

Com `SERVER_HEALTH_MODULES=auto` (o padrão), um módulo sem nada para vigiar não faz nada e
não custa nada. Todos os módulos, métricas e limiares:
[docs/pt-BR/modules.md](docs/pt-BR/modules.md).

## Exemplos

### Só a máquina

Um VPS com um banco ou um servidor de jogo, e nada mais: o padrão já resolve. Para manter
exatamente o básico:

```bash
SERVER_HEALTH_MODULES=host,systemd,ssh
```

### Um servidor com Dokku

Nada a declarar: as apps, os bancos e os logs delas são encontrados a cada rodada. Dê a
cada app um canal próprio no Discord, se quiser (o canal do servidor fica com os alertas
da máquina):

```bash
SERVER_HEALTH_DISCORD_WEBHOOK_SHOP=https://discord.com/api/webhooks/<id>/<token>
```

### Uma app fora do Dokku (Docker Compose, Kamal, systemd)

Declare onde estão as coisas dela em `/etc/server-health/apps.d/shop.toml`:

```toml
app = "shop"
health_url = "https://shop.example.com/up"
nginx_log = "/var/log/nginx/shop.access.log"
database_url = "env:SHOP_DATABASE_URL"   # a URL em si fica na config, que só o root lê
```

e em `/etc/default/server-health`:

```bash
SHOP_DATABASE_URL=postgres://monitor:secret@127.0.0.1:5432/shop
```

> [!NOTE]
> Uma `database_url` com a senha escrita no TOML é recusada: qualquer usuário da máquina
> lê esse arquivo, e só o root lê o arquivo de config.

### Os seus próprios indicadores

O que só a app sabe que importa, em SQL (somente leitura, contagens e somas):

```toml
[[indicator]]
key = "orders_stuck"
label = "Orders stuck in processing"
sql = "SELECT count(*) FROM orders WHERE status = 'processing' AND updated_at < now() - interval '15 minutes'"
warn_at = 1
critical_at = 5
explanation = "o job de processamento parou no meio: confira o Solid Queue"
```

O `sudo server-health --check-app shop.toml` valida o arquivo e roda cada consulta uma
vez. Mais em [docs/pt-BR/app-indicators.md](docs/pt-BR/app-indicators.md).

### Fora do Discord

Toda mensagem também vai, como JSON, para qualquer URL: o [ntfy](https://ntfy.sh), um
workflow do Slack, o n8n ou um script seu.

```bash
SERVER_HEALTH_WEBHOOK_URL=https://ntfy.sh/my-server-alerts
```

### Manutenção planejada

```bash
sudo server-health --mute 2h   # as rodadas continuam medindo; nenhum alerta sai
sudo server-health --unmute
```

## Resumo diário e relatório semanal

Todo dia o resumo lista o que precisa de atenção, cada parte do servidor e de cada app,
as últimas 24 horas e os números de produto do dia:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/summary-dark.svg">
  <img alt="O resumo diário no Discord: o que precisa de atenção, o servidor, uma app com as requisições e os robôs dela, e os números do dia" src="docs/assets/summary-light.svg" width="720">
</picture>

O relatório semanal compara a semana com a anterior (alertas, disponibilidade, tráfego,
robôs, bancos) e leva o painel, uma página HTML com os gráficos dos últimos sete dias:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/dashboard-dark.svg">
  <img alt="O painel: memória usada e espera por memória ao longo de sete dias, e as requisições por hora de uma app" src="docs/assets/dashboard-light.svg" width="720">
</picture>

Eles saem às 09:00 e no sábado às 07:00, no seu fuso horário. Mude os horários ou
desligue:

```bash
SERVER_HEALTH_DAILY_AT=18:30
SERVER_HEALTH_WEEKLY_AT=mon 09:00   # ou off
```

## Comandos

| Comando | O que faz |
|---|---|
| `server-health` | uma rodada (o que o cron roda a cada 5 minutos) |
| `server-health --dry-run` | o relatório inteiro, o que seria alertado e quando sai o próximo resumo; não manda nada |
| `server-health --simulate memory` | um alerta de teste (`memory`, `disk`, `worker`, `app`, `indicator`, `oom`) |
| `server-health --force` / `--force-weekly` | o resumo diário / o relatório semanal agora, marcado como teste |
| `server-health --mute 2h` / `--unmute` | silencia os alertas durante uma manutenção planejada |
| `server-health --list-modules` | quais módulos existem e quais estão ligados |
| `server-health --check-app app.toml` | valida o TOML de uma app e roda cada consulta uma vez |
| `server-health --dashboard out.html` | grava o painel |
| `server-health --serve` | serve o painel em 127.0.0.1:8730, para abrir por um túnel SSH |

## Perguntas frequentes

### Peso e custo

<details>
<summary><b>Ele é pesado?</b></summary>

Uns 70 ms de CPU e 30 MB de memória por rodada, e nada roda entre uma e outra. As rodadas
vêm a cada 5 minutos; as verificações lentas ou que mudam devagar (certificados, a busca
por atualização, o `apt`) ficam em cache por horas, e ele abre no máximo uma sessão de
banco por app.

</details>

<details>
<summary><b>Ele fica crescendo no disco?</b></summary>

Não: o histórico fica 35 dias, os alertas 90 dias, e o log tem teto de 5 MB. Os dados
velhos são aparados uma vez por dia pelas rodadas normais.

</details>

### Dados e privacidade

<details>
<summary><b>Ele muda alguma coisa no meu servidor?</b></summary>

Não. Todo comando só lê, e o SQL roda em sessões somente leitura, com timeout.

</details>

<details>
<summary><b>Que dados saem do servidor?</b></summary>

Só as mensagens que você vê no seu canal. Os indicadores são contagens e somas; os
caminhos das requisições são guardados com os ids trocados por `:id`; nenhum IP de
visitante é guardado. A busca por atualização é um GET por dia a este repositório, sem
levar nada sobre o servidor (`SERVER_HEALTH_UPDATE_CHECK=false` a desliga).

</details>

### Instalação e operação

<details>
<summary><b>Ele precisa de Docker ou Dokku?</b></summary>

Não. Eles acrescentam módulos; a máquina é vigiada sem eles.

</details>

<details>
<summary><b>E se o servidor cair?</b></summary>

Um monitor no próprio servidor não consegue avisar isso. Acrescente um check de heartbeat
gratuito (healthchecks.io ou parecido) com `SERVER_HEALTH_PING_URL`: quando os pings
param, ele alerta.

</details>

<details>
<summary><b>Como desligo um módulo que não quero?</b></summary>

Liste-o com um sinal de menos depois do `auto`:
`SERVER_HEALTH_MODULES=auto,-ports,-maintenance`. O `server-health --list-modules` mostra
quais módulos existem e quais estão ligados.

</details>

<details>
<summary><b>Como atualizo?</b></summary>

Rode de novo o comando de instalação, com a nova versão. O cron pega a versão na rodada
seguinte, o estado sobrevive, e a versão anterior fica guardada para um rollback
([instalação](docs/pt-BR/installation.md#atualização)).

</details>

<details>
<summary><b>Dá para ver os dados sem o Discord?</b></summary>

Sim: o `--dry-run` mostra o estado atual, e o `--dashboard` e o `--serve` mostram os
gráficos.

</details>

## Documentação

- [Instalação](docs/pt-BR/installation.md): requisitos, config, cron, dados guardados, atualização e rollback
- [Configuração](docs/pt-BR/configuration.md): todas as configurações, limiares, canais, relatórios, horário de silêncio, idioma
- [Módulos](docs/pt-BR/modules.md): o que cada módulo mede e como o `auto` decide
- [Apps](docs/pt-BR/app-indicators.md): apps fora do Dokku e os seus indicadores SQL
- [Alertas](docs/pt-BR/alerts.md): quando ele alerta, os limiares e o porquê, e o que fazer em cada alerta
- [Painel](docs/pt-BR/dashboard.md): a página HTML com os gráficos
- [Escrevendo um módulo](docs/pt-BR/writing-a-module.md): as suas próprias verificações ou notificadores
- [Publicando uma versão](docs/pt-BR/releasing.md): testes, versões e tags

As mensagens saem em inglês ou em português (`SERVER_HEALTH_LANG=pt-BR`).

## Contribuindo

Relatos de bug, correções, mensagens melhores, módulos novos (outro banco, outro servidor
web, outra plataforma), notificadores novos e traduções são bem-vindos. Um módulo é um
arquivo: veja [escrevendo um módulo](docs/pt-BR/writing-a-module.md) e o
[CONTRIBUTING.pt-BR.md](CONTRIBUTING.pt-BR.md). Para algo grande, abra uma issue antes.

```bash
python3 -m unittest discover -s tests   # o monitor inteiro contra as fixtures, sem servidor
```

## Licença

[MIT](LICENSE).
