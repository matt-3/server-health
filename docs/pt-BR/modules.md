# Módulos

Tudo o que o monitor mede é um módulo: um arquivo em `server_health/modules/`.
`SERVER_HEALTH_MODULES` decide quais rodam:

| Valor | Roda |
|---|---|
| `auto` (padrão) | todo módulo cujo `detect` encontra nesta máquina o que ele precisa; um módulo sem nada para vigiar (nenhuma app, nenhum domínio) não faz nada e não custa nada |
| `host` | só memória, swap, disco, inodes, load e OOM |
| `host,docker,ssh` | exatamente esses |
| `auto,-ports,-maintenance` | o auto, sem esses |
| `auto,url_check` | o auto, mais um [módulo de terceiros](writing-a-module.md) |

Um módulo listado sem o módulo de que ele precisa não roda, e o `--dry-run` diz por quê.
`server-health --list-modules` mostra todos os módulos e quais estão ligados; o
`--dry-run` imprime a lista dos ativos no topo.

## Os módulos

A **chave** é a identidade de cada métrica (o que o anti-spam compara e o que encaminha
a métrica de uma app para o canal dela).

### `host` (Linux)

| Chave | Mede | Alerta |
|---|---|---|
| `host.memory` | usada, disponível, média de 7 dias, espera por memória (PSI) | disponível ≤ 10% por 15 min (aviso), ≤ 5% (crítico); espera `some` ≥ 10% (aviso); `full` ≥ 5% (crítico) |
| `host.swap` | swap em uso e a taxa de **swap-out** desde a última rodada | swap-out ≥ 50 KB/s (aviso), ≥ 200 (crítico) |
| `host.disk` | uso, crescimento por dia (7 dias) e quando ele enche | ≥ 75% / 85%; cheio em 14 / 3 dias |
| `host.inodes` | inodes em uso | ≥ 80% / 90% |
| `host.load` | load de 1 e 5 min por vCPU | 1,0 por 15 min / 2,0 por 10 min |
| `host.oom` | processos mortos pelo kernel por falta de memória (evento) | qualquer um |

### `backup`

Ligado quando `SERVER_HEALTH_BACKUP_STAMP` ou `SERVER_HEALTH_BACKUP_GLOB` está definido.
`host.backup`: idade do arquivo de carimbo (26h / 50h). `host.backup_size`: o arquivo de
backup mais novo contra a mediana dos anteriores; abaixo de 50% é aviso (um backup que
rodou e saiu incompleto).

### `ssh` (journald)

`host.ssh_new` (evento): uma entrada vinda de uma faixa de IP (/24, /48 no IPv6) que não
entrou nos últimos 30 dias. A primeira rodada aprende os últimos 30 dias em silêncio.

### `maintenance` (Debian/Ubuntu)

`mnt.security_age` e `mnt.reboot_age`: atualizações de segurança ou um reinício
pendentes há mais de 7 dias. Só no resumo diário: atualizações pendentes, banimentos do
Fail2Ban, entradas por SSH em 24h e o espaço que o Docker poderia liberar.

### `updates`

`mnt.update`: uma vez por dia ele lê o `versions.json` do projeto (um GET pequeno; nada
sobre o servidor é enviado). Uma versão mais nova com correção de segurança ou correção
crítica é aviso, repetido a cada 24h até a atualização; uma com novidades ou correções
comuns só aparece no resumo diário. `SERVER_HEALTH_UPDATE_CHECK=false` o desliga.

### `systemd`

`host.systemd`: units com falha (`systemctl --failed`) por 10 min. As units que podem
falhar vão em `SERVER_HEALTH_SYSTEMD_IGNORE`.

### `clock`

`host.clock`: o relógio não sincronizado (`timedatectl`) por 30 min.

### `ports`

`host.ports_new` (evento): uma porta TCP passou a escutar fora do loopback (`ss -ltn`).
A primeira rodada aprende o que está aberto.

### `docker`

Sem métrica própria (só `host.docker` quando o `docker ps` falha): ele lê os contêineres
e a memória deles para os outros módulos e para o "Quem usa a memória".

### De onde vêm as apps

Os módulos de app (`http`, `nginx`, `postgres`, `rails`, `indicators`) trabalham sobre
uma lista de apps. Com o Dokku, as apps dele entram na lista sozinhas. Qualquer outra app
(Docker Compose, Kamal, um serviço do systemd) entra declarando onde estão as coisas dela
no [TOML](app-indicators.md#uma-app-fora-do-dokku): `health_url`, `nginx_log`,
`database_url` ou `database_container`.

### `dokku` (precisa de `docker`)

| Chave | Mede |
|---|---|
| `app.<app>.ps` | processos por tipo, tempo de pé, deploy (commit e data), memória; crítico quando uma app com deploy fica sem contêiner por 5 min |
| `app.<app>.restart` | um reinício fora de deploy (evento) |
| `app.<app>.oom` | um contêiner morto por memória (evento) |
| `app.<app>.deploy` | deploy suspeito (evento): 30 min depois de um deploy, 5xx, falhas de job ou tempo do `/up` muito piores que na hora anterior |
| `app.<app>.storage` | diário: o TOML diz `storage_mount = true` e o Dokku não tem storage mount |
| `app.<app>.body_size` | diário: o `client-max-body-size` do nginx ou o `MAX_REQUEST_BODY` do Thruster abaixo do `max_body_size` do TOML |

Enquanto um deploy roda (`git-hook`, `ps:rebuild`, `docker build`), os avisos de memória,
swap e load são ignorados, e o swap-out da rodada logo depois dele também.

### `http`

`app.<app>.up`: o health check. Para uma app do Dokku, o `/up` (ou o `health_path` do
TOML) pedido pela própria máquina (`curl --resolve <domain>:443:127.0.0.1`), sem a CDN
no meio; para outra app, o `health_url` dela como foi dado.
Crítico sem 2xx por 5 min; aviso acima de 2 s por 15 min.

### `nginx`

Lê o log de acesso de cada app no formato combined do nginx: o do Dokku por padrão, ou o
`nginx_log` do TOML, ou o padrão de `SERVER_HEALTH_NGINX_LOG`. Sem log, ele não lê nada.

| Chave | Mede |
|---|---|
| `app.<app>.http` | requisições desde a última rodada: pessoas, robôs por categoria, 5xx (aviso: 10 e 5%; crítico: 50 e 20%) |
| `app.<app>.bots` | um único robô (que não seja buscador, prévia de link ou monitor externo) com 300+ requisições em 5 min |
| `app.<app>.traffic` | pessoas nos últimos 30 min abaixo de 20% do habitual daquela meia hora (precisa de 14 dias de histórico) |

Os robôs são classificados pelo User-Agent e pelo caminho (`BOT_RULES` no módulo):
buscadores, IA de treino, IA de busca e de usuário, prévias de link, SEO, monitores
externos, varredura, scripts, outros robôs. O relatório semanal mostra os principais
robôs de cada categoria, os robôs novos, as páginas mais lidas pela IA e pelos buscadores
e os caminhos de ataque mais tentados. Os caminhos são guardados com os identificadores
trocados por `:id`; nenhum IP é guardado.

### `postgres`

Os bancos vêm do Dokku (`dokku postgres:connect`) e dos TOMLs: `database_url` (psql na
máquina; a senha vai no `PGPASSWORD`, nunca na linha de comando, que o `ps` mostra a
todos os usuários) ou `database_container` (psql dentro daquele contêiner, por
`docker exec`).

| Chave | Mede |
|---|---|
| `db.<service>.size` | tamanho e crescimento por dia (2 GB / 3 GB) |
| `db.<service>.conn` | conexões contra o `max_connections` (80% / 95%) |
| `db.<service>.long_tx` | a transação aberta mais longa (5 min / 30 min) |
| `db.<service>.vacuum` | tabelas com 10.000+ linhas mortas e sem vacuum há 7 dias |
| `db.<service>.cache_hit` | resumo diário: taxa de acerto do cache |
| `db.<service>.tables` | resumo diário: as 6 maiores tabelas e o crescimento delas |

Todas as consultas rodam numa sessão somente leitura com timeout de 5 s por comando.
Cada app custa uma conexão por rodada: as consultas do `rails` e do `indicators` a
compartilham.

### `rails` (precisa de `postgres`)

Roda só onde existem as tabelas do Solid Queue.

| Chave | Mede |
|---|---|
| `app.<app>.sq` | heartbeat do worker, do dispatcher e do scheduler (3 min / 5 min) |
| `app.<app>.sq_failed` | jobs com falha em 30 min (1 / 20) e em 24h, principais classes |
| `app.<app>.job_error` | uma classe de job falhando com uma exceção não vista em 7 dias (evento) |
| `app.<app>.sq_queues` | por fila, o job pronto mais antigo (10 min / 30 min); jobs agendados atrasados |
| `app.<app>.sq_recurring` | tarefas recorrentes (de minutos, de horas) atrasadas o dobro do intervalo habitual |
| `app.<app>.sq_long_tasks` | tarefas diárias, semanais e mensais passadas do horário mais 2h |

### `indicators` (precisa de `postgres`)

`ind.<app>.<key>`: os indicadores SQL de cada app, do [TOML](app-indicators.md) dela.

### `tls`

`app.<app>.tls` e `host.tls`: o vencimento de certificado mais próximo entre os domínios
(21 / 7 dias), lido da própria máquina na porta 443 com o nome de cada domínio. Os
domínios vêm do Dokku, do `health_url` `https` de cada app e de
`SERVER_HEALTH_TLS_DOMAINS`. Conferido a cada 6 horas; um domínio servido por outra
máquina é pulado.
