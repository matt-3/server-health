# Alertas

## Quando ele alerta

Um alerta é uma **mudança de estado**. A identidade dele é a chave da métrica com o
nível (`host.memory` em aviso), nunca o valor medido: 3.067 MB e 3.071 MB são o mesmo
aviso.

| Situação | Mensagem |
|---|---|
| Uma métrica passa de OK para aviso ou crítico | na hora (o crítico com `@here`) |
| O aviso vira crítico, ou o crítico baixa para aviso | na hora |
| Volta ao normal | "Normalizou", com quanto tempo durou |
| Segue crítico | um lembrete a cada 2h |
| Segue em aviso | um lembrete a cada 24h |
| Uma ocorrência (OOM kill, reinício de contêiner, erro novo em job, SSH de um IP novo, porta nova, deploy suspeito) | uma mensagem por ocorrência, sem "Normalizou" |
| Tudo OK | nada |
| Resumo diário (`SERVER_HEALTH_DAILY_AT`, 09:00 por padrão) | sempre, com a cor do estado atual |
| Relatório semanal (`SERVER_HEALTH_WEEKLY_AT`, sábado às 07:00 por padrão) | sempre, com o painel anexado |

**Persistência.** Algumas métricas só alertam depois de alguns minutos no mesmo nível:
memória (15 min em aviso), load (15 min em aviso, 10 em crítico), uma app sem processos
e um `/up` falhando (5 min), Solid Queue sem heartbeat (5 min) e os indicadores que
pedirem (`persist_min`). O pico de um build ou o reinício de um deploy não vira alerta.
No relatório, uma métrica nessa janela mostra "aguardando confirmação".

**Um deploy rodando.** Enquanto um `git-hook`, um `ps:rebuild` ou um `docker build`
roda, os avisos de memória, swap e load são ignorados; processos, `/up` e o resto
continuam contando. O swap-out mede o intervalo desde a rodada anterior, então também é
ignorado na rodada logo depois de um build ou depois que aparece um contêiner novo de
app. Um alerta que já estava aberto continua aberto durante o deploy, em silêncio: um
deploy não diz que o problema acabou. Ele só fecha quando a medida volta ao normal.

**Uma métrica que some** (uma app removida, uma coleta que falhou) guarda o estado por
24h em silêncio, para não haver um "Normalizou" falso.

## A mensagem

O nome do servidor vai no topo; o título diz o nível e o que mudou
(`🔴 Crítico · Servidor · Memória`), e o texto começa com o valor medido e o porquê.
Abaixo, a seção da métrica com cada linha marcada (🔴 crítico, 🟡 aviso, 🟢 OK), os
problemas primeiro e em negrito, e "Abertos agora" com tudo o que segue em aviso ou
crítico. Num alerta de memória, "Quem usa a memória" lista os contêineres que mais
consomem. O Discord não colore texto: a cor é a barra lateral, e o estado de cada linha
é o emoji dela.

Cada app tem as próprias seções: o Dokku grava um log do nginx por app, e tudo o que é
da app (processos, `/up`, requisições, robôs, banco, Solid Queue, indicadores) fica na
seção dela. Memória, disco, load e manutenção são do servidor inteiro.

## O resumo diário

Ele abre com "Precisa de atenção" quando há algo aberto, depois todas as seções com as
linhas delas, as últimas 24 horas do servidor (RAM mín/média/máx, espera por memória,
swap-out, load, OOM kills), as requisições e os robôs do dia de cada app, as maiores
tabelas e os indicadores de resumo ("números do dia").

## O relatório semanal

Enviado no `SERVER_HEALTH_WEEKLY_AT` (sábado às 07:00 por padrão), ele cobre os 7 dias
anteriores contra a semana de antes:

- **Alertas da semana:** quantos abriram, cada um com quantas vezes e por quanto tempo,
  e os que seguem abertos.
- **Por app:** disponibilidade (rodadas com o `/up` em 2xx, resposta mediana e pior),
  requisições de pessoas com a hora de pico, 5xx, deploys, reinícios fora de deploy e
  jobs com falha por classe.
- **Robôs, por app:** total e fatia das requisições, cada categoria com os principais
  robôs, os robôs vistos pela primeira vez em 4 semanas, as páginas mais lidas pela IA e
  pelos buscadores e os caminhos de ataque mais tentados.
- **Números da semana, por app:** a soma dos indicadores de resumo dos 7 resumos diários.
- **Servidor, bancos e manutenção:** RAM, espera por memória, swap-out, load e OOM da
  semana, crescimento e previsão do disco, o tamanho de cada banco no início e no fim, e
  a manutenção pendente.
- **O [painel](dashboard.md)**, como um arquivo HTML anexado à mensagem.

## O que não alerta, de propósito

- **RAM em uso.** 80% em uso com 20% disponível e ninguém esperando memória é normal
  para um servidor com duas apps. A ocupação aparece no relatório, ao lado da média de 7
  dias, como informação.
- **Swap em uso.** É a sobra de um pico passado: o kernel só traz as páginas de volta
  quando alguém as usa, e o número não baixa sozinho.
- **Swap-in.** É o contrário de pressão: a máquina saindo do swap.

## Limiares e por quê

| Métrica | Aviso | Crítico | Por quê |
|---|---|---|---|
| Memória disponível (`MemAvailable`) | ≤ 10% por 15 min | ≤ 5% | a folga real para um pico; abaixo de 5% o OOM killer está perto |
| Espera por memória (PSI `some`, 5 min) | ≥ 10% por 15 min | - | processos parados esperando memória: a lentidão já começou |
| Todos esperando (PSI `full`, 1 min) | - | ≥ 5% | todos os processos parados ao mesmo tempo |
| Swap-out | 50 KB/s | 200 KB/s | o kernel empurrando páginas para o disco agora |
| Disco | 75% | 85% | um disco cheio para deploys, logs e o Postgres de uma vez |
| Previsão do disco (ritmo de 7 dias) | cheio em 14 dias | em 3 dias | hora de limpar ou aumentar o disco |
| Inodes | 80% | 90% | sem inode, nenhum arquivo novo, mesmo com espaço livre |
| Load (5 min, por vCPU) | 1,0 por 15 min | 2,0 por 10 min | uma fila de CPU se formando |
| OOM kill | - | qualquer um | algo já morreu por falta de memória |
| Banco | 2 GB | 3 GB | um banco grande come o page cache da máquina |
| Conexões do Postgres | 80% do máximo | 95% | perto do limite a app recusa requisições |
| Transação aberta | 5 min | 30 min | segura locks e o vacuum |
| Vacuum atrasado | uma tabela com 10.000+ linhas mortas e sem vacuum em 7 dias | - | as consultas ficam lentas e a tabela cresce |
| App sem processos | - | 5 min | uma app com deploy fora do ar |
| `/up` | acima de 2 s por 15 min | sem 2xx por 5 min | lenta ou fora do ar, medido sem a CDN |
| 5xx no nginx (por rodada) | 10 e 5% | 50 e 20% | erros em volume; os dois mínimos evitam alarmes com pouco tráfego |
| Solid Queue sem heartbeat | 3 min | 5 min (5 min seguidos) | sem worker nenhum job roda; sem scheduler nenhuma tarefa agendada |
| Jobs com falha (30 min) | 1 | 20 | um job que falhou e está na lista de falhas |
| Erro novo em job (evento) | uma classe de job com uma exceção não vista em 7 dias, ou nunca vista | - | o "new issue" do Sentry, para jobs |
| Deploy suspeito (evento, 30 min depois) | 5xx 3× maior com 2% e 10 erros; ou falhas de job 3× com 5; ou `/up` mediano 2× com 300 ms | - | o deploy que piorou alguma coisa, com o commit |
| Fila parada (job pronto mais antigo) | 10 min | 30 min | worker parado, lento ou sem thread livre |
| Tarefa recorrente (de minutos e de horas) | o dobro do intervalo habitual | - | a tarefa não rodou no ritmo dela |
| Tarefa diária, semanal ou mensal | o intervalo do agendamento mais 2h | - | um expurgo ou um lembrete diário não rodou |
| Pessoas em 30 min | abaixo de 20% da mediana da mesma meia hora em 7 dias, por 10 min | - | DNS, CDN ou redirecionamento quebrado enquanto o `/up` responde; só com 14 dias de histórico e uma base de 50+ |
| Backup | 26 h | 50 h | o backup diário não terminou |
| Tamanho do backup | abaixo de 50% do habitual | - | um backup que rodou e saiu incompleto |
| Atualização de segurança pendente | há mais de 7 dias | - | uma correção de segurança esperando |
| Reinício pendente | há mais de 7 dias | - | o kernel ou a biblioteca atualizados só valem depois de um reboot |
| Atualização do server-health | uma versão mais nova com correção de segurança ou correção crítica | - | o próprio monitor precisa da correção |
| SSH de uma faixa de IP nova (evento) | um /24 (IPv4) ou /48 (IPv6) não visto em 30 dias | - | um acesso que ninguém do time fez aparece no mesmo minuto |
| Porta nova escutando (evento) | qualquer uma | - | um serviço exposto que ninguém abriu de propósito |
| Unit do systemd com falha | por 10 min | - | algo morreu fora do Docker |
| Relógio não sincronizado | por 30 min | - | TLS, tokens com validade e agendamentos quebram |
| Certificado TLS | vence em 21 dias | em 7 dias | um certificado vencido derruba o site em todo navegador |
| Robô em volume (um único robô, que não seja buscador, prévia ou monitor) | 300 requisições em 5 min | - | um crawler agressivo pesando na CPU |
| Storage mount / tamanho de upload (diário) | ausente / abaixo do TOML | - | uploads perdidos no próximo deploy / recusados com 413 e sem rastro no log da app |

### A primeira semana

Os limiares e o horário de silêncio se ajustam pelo que chegou ao Discord. Os avisos
enviados de madrugada saem do log (aqui, das 23:00 às 07:00 em São Paulo, 02:00 às 10:00
no relógio UTC do servidor):

```bash
sudo zcat -f /var/log/server-health.log* \
  | awk '/Discord: sent \((AVISO|NORMALIZOU|SEGUE|MUDANÇAS)\)/ { h = substr($2, 1, 2) + 0; if (h >= 2 && h < 10) print }'
```

O log grava o tipo da mensagem no idioma das mensagens (`SERVER_HEALTH_LANG`): em inglês
os tipos são `WARNING`, `BACK TO NORMAL`, `STILL OPEN` e `CHANGES`. Um aviso que se
repete de madrugada sem pedir ação pede um limiar mais alto ou uma persistência mais
longa (`SERVER_HEALTH_T_*`, ou `persist_min` no TOML). Se o problema é só a hora, defina
`SERVER_HEALTH_QUIET_HOURS="23-7"`.

## Recebi um alerta

A mensagem traz o que mudou, a explicação e todas as métricas da parte afetada.

```bash
sudo server-health --dry-run           # o estado agora, completo
sudo tail -n 50 /var/log/server-health.log
```

| Alerta | Primeiro passo |
|---|---|
| **Memória** | "Quem usa a memória" dá o nome do contêiner. Web acima do normal: `dokku ps:restart <app>` alivia na hora (sem downtime). Crescendo semana após semana: hora de um plano maior |
| **Swap-out** | o mesmo da memória: a mesma falta de folga, vista pelo disco |
| **OOM kill / Contêiner morto por memória** | `sudo journalctl -k --since '1 hour ago' \| grep -i -A5 'killed process'` e `dokku ps:report <app>`. Web e worker reiniciam sozinhos; confira o `/up` |
| **Disco ou previsão do disco** | `sudo docker system df` e `sudo docker system prune`. Um banco crescendo: as maiores tabelas estão no resumo diário |
| **Inodes** | `sudo du --inodes -x / \| sort -n \| tail`: em geral uma pasta com milhões de arquivos pequenos |
| **Load** | `htop` e `sudo docker stats --no-stream`. Fora de deploy e sustentado, falta CPU |
| **Processos: NENHUM processo de pé** | `dokku ps:report <app>` e `dokku logs <app> -n 200` |
| **Reinício fora de deploy** | o processo morreu e o Docker o subiu de novo: `dokku logs <app> -n 300` em volta daquela hora |
| **Saúde (`/up`)** | falhando com os processos de pé: `dokku logs <app> -n 200` e `dokku nginx:show-config <app>`. Lento: banco ou memória (veja as outras métricas na mensagem) |
| **Requisições (5xx)** | `dokku logs <app> -n 300 \| grep -E 'Error\|500'` e o rastreador de erros da app |
| **Tamanho do banco** | as maiores tabelas estão no resumo diário. Um `solid_cache_entries` grande: limpe o cache |
| **Conexões** | `dokku postgres:connect <service>` e `SELECT state, count(*) FROM pg_stat_activity GROUP BY 1;` |
| **Transação mais longa** | `SELECT pid, now() - xact_start, state, left(query, 80) FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY 2 DESC LIMIT 5;` |
| **Vacuum atrasado** | `SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 5;` e depois `VACUUM (ANALYZE) <table>;` numa hora tranquila |
| **Solid Queue sem heartbeat** | `dokku ps:report <app>` (o processo `worker` está de pé?) e `dokku logs <app> -p worker -n 200` |
| **Jobs com falha / Erro novo em job** | o painel de jobs da app (Mission Control): a classe e o erro de cada um. A mensagem nunca leva o texto do erro (ele pode ter dados pessoais). Na primeira rodada depois da instalação, o monitor aprende em silêncio as falhas que já existiam |
| **Deploy suspeito** | a mensagem traz o commit e o que piorou. O rastreador de erros naquela hora e `dokku logs <app> -n 300`; voltar atrás é um deploy do commit anterior |
| **Fila parada** | uma fila com o job mais antigo esperando: worker sem thread livre ou preso num job lento. Uma fila com centenas de jobs e o mais antigo de dias atrás: nenhum worker escuta essa fila (confira o `config/queue.yml`; uma tarefa recorrente com `command` vai para `solid_queue_recurring` a menos que tenha um `queue:`) |
| **Tarefas recorrentes / Tarefas diárias e semanais** | o scheduler parou, ou a tarefa está falhando, travada ou saiu do `recurring.yml`: o painel de jobs |
| **Pessoas em 30 min** | o site está fora do ar para quem está fora, com a máquina de pé: abra pelo celular, confira o DNS e as regras e o status da CDN. Um feriado ou uma noite atípica também baixam o número |
| **Robô em volume** | a mensagem dá o nome dele. Se pesa e não traz nada (crawler de treino de IA, SEO, um script), bloqueie o User-Agent dele na CDN (na Cloudflare: Security → WAF → Custom rules) |
| **Indicadores** | a mensagem traz a `explanation` do indicador, que diz o primeiro passo |
| **Config do monitor** | `sudo server-health --check-app /etc/server-health/apps.d/<app>.toml` mostra o erro |
| **Entrada por SSH de IP novo** | a mensagem traz o usuário, o IP e o método. Alguém do time numa rede nova? Se não: `sudo last -i`, `sudo journalctl -u ssh --since '1 hour ago'`, troque a chave e confira o `~/.ssh/authorized_keys` |
| **Porta nova aberta** | `sudo ss -ltnp` mostra o processo. Feche a porta ou coloque-a atrás do firewall |
| **Serviços com falha** | `systemctl status <unit>` e `journalctl -u <unit> -n 50` |
| **Relógio** | `timedatectl timesync-status` e `sudo systemctl restart systemd-timesyncd` |
| **Certificado TLS** | renove (`dokku letsencrypt:auto-renew`, ou o do seu provedor) |
| **Backup / Tamanho do backup** | o log do script de backup; abra o backup mais novo antes de confiar nele |
| **Storage mount** | `dokku storage:ensure-directory <app>` e `dokku storage:mount <app> /var/lib/dokku/data/storage/<app>:/rails/storage`, depois um restart |
| **Limite de upload** | `dokku nginx:set <app> client-max-body-size 50m` e `dokku config:set <app> MAX_REQUEST_BODY=<bytes>` |
| **Atualização de segurança / Reinício pendente** | `sudo apt update && sudo apt upgrade`, e um reboot numa hora tranquila |
| **Atualização do server-health** | leia as notas da versão no `CHANGELOG.md` e rode o comando da mensagem (a [atualização](installation.md#atualização)) |
| **Dokku ou Docker sem acesso** | o monitor roda como root? `sudo cat /etc/cron.d/server-health` |
