# Escrevendo um módulo

Um módulo é um arquivo Python com uma subclasse de `server_health.module.Module`. Ele pode
ser embutido (em `server_health/modules/`, listado em `server_health/modules/__init__.py`)
ou um arquivo de terceiros no servidor, sem mexer no pacote.

## Um exemplo completo

[`examples/modules/url_check.py`](../../examples/modules/url_check.py) confere se algumas
URLs respondem 2xx:

```python
from server_health.i18n import fnum, t
from server_health.metric import Metric
from server_health.module import Module


class UrlCheck(Module):
    name = "url_check"                                  # único; o que SERVER_HEALTH_MODULES lista
    defaults = {"SERVER_HEALTH_URLS": ""}               # configurações que ele lê, com os padrões
    thresholds = {"URL_SLOW_WARN_S": 3.0}               # mudável com SERVER_HEALTH_T_URL_SLOW_WARN_S

    def detect(self, src, cfg):                         # para SERVER_HEALTH_MODULES=auto
        return bool(cfg.get("SERVER_HEALTH_URLS"))

    def host(self, ctx):                                # uma fase: roda uma vez por rodada
        ...
        ctx.add(Metric(f"url.{url}", ctx.server_section, url, value, "CRIT", t("url_check.down")))

    strings = {                                         # todo texto, em cada idioma
        "en": {"url_check.down": "the URL did not answer 2xx"},
        "pt-BR": {"url_check.down": "a URL não respondeu 2xx"},
    }


MODULE = UrlCheck()                                     # o que o carregador procura
```

## Instalando um módulo de terceiros

```bash
sudo install -d -m 755 /etc/server-health/modules.d
sudo install -o root -g root -m 644 url_check.py /etc/server-health/modules.d/url_check.py
```

e adicione o nome do arquivo à config: `SERVER_HEALTH_MODULES=auto,url_check`. Depois
`sudo server-health --list-modules` e `--dry-run`.

Um arquivo só carrega quando o nome dele está listado, e só se pertencer ao root (ou ao
usuário que roda o monitor) e ninguém mais puder escrever nele: o monitor roda como root,
então um módulo gravável seria uma porta para o root. Um arquivo que falha ao carregar
vira um aviso de config no relatório; ele nunca para o monitor.

## As fases

Uma rodada roda as fases nesta ordem, cada uma passando pelos módulos ligados na ordem do
registro. Implemente só as que precisar:

| Fase | Roda | Usada por |
|---|---|---|
| `before(ctx)` | primeiro | dokku (há um deploy rodando?) |
| `host(ctx)` | uma vez | host, backup, ssh, maintenance, systemd, clock, ports |
| `discover(ctx)` | uma vez | docker (contêineres), dokku (apps em `ctx.apps`) |
| `services(ctx)` | uma vez | postgres (cada serviço de banco) |
| `app(ctx, app)` | uma vez por app | dokku, http, nginx, postgres |
| `daily(ctx)` | só no resumo diário | maintenance, dokku (armadilhas de upload) |
| `after_app(ctx, app)` | uma vez por app, depois de todas | dokku (deploy suspeito), nginx (tráfego), tls |
| `finish(ctx)` | por último | dokku (nomes das seções), tls |

Depois de uma rodada normal, `save(ctx, store)` deixa um módulo guardar algo próprio (o
nginx guarda o `bots.json`).

## O contexto

O `ctx` é compartilhado pelos módulos durante uma rodada:

| Atributo | O quê |
|---|---|
| `ctx.src` | de onde vêm as leituras: `read(path)`, `run(key, argv)`, `stat`, `glob`, `disk`, `now()`... Use sempre (nunca `open` nem `subprocess`): os testes o trocam por fixtures |
| `ctx.cfg`, `ctx.th` | a config e os limiares |
| `ctx.state`, `ctx.updates` | o estado da rodada anterior, e o que gravar (`ctx.updates["my_key"] = ...`) |
| `ctx.history` | as amostras dos últimos 35 dias |
| `ctx.facts` | a amostra desta rodada (`ctx.facts["my_value"] = 1.5` vai para o histórico) |
| `ctx.add(metric)` | adiciona uma medida |
| `ctx.server_section`, `ctx.maintenance_section` | os nomes traduzidos das seções |
| `ctx.apps`, `ctx.app_config(app)`, `ctx.app_domains`, `ctx.services` | o que os outros módulos encontraram |
| `ctx.daily`, `ctx.weekly`, `ctx.now`, `ctx.tz`, `ctx.log` | a rodada |

`src.run(key, argv)` recebe uma chave: nos testes, a saída vem de
`tests/fixtures/<case>/cmd/<key>.txt` (e `.rc`, `.err`).

## A métrica

```python
Metric(key, section, label, value, level="OK", detail="", event=False, persist_min=0,
       crit_persist_min=0, deploy_sensitive=False, summary_only=False, items=[])
```

- `key`: identidade estável, nunca o valor. Começa com `host.`, `mnt.`, `app.<app>.`,
  `db.<service>.` ou `ind.<app>.`; os prefixos `app.` e `ind.` encaminham a métrica para
  o canal próprio da app.
- `level`: `OK`, `WARN` ou `CRIT`, como medido agora; o avaliador aplica a persistência.
- `detail`: o porquê, ou o que fazer. Vai no alerta; `` `code` `` é permitido.
- `event=True`: uma ocorrência (alerta em cada uma, sem "Normalizou").
- `summary_only=True`: informação para o resumo diário; nunca alerta.

## Hooks do relatório

| Hook | O quê |
|---|---|
| `daily_lines(r, section, app)` | linhas para o bloco "Últimas 24h" de uma seção |
| `weekly_app_lines(r, app)` | `(position, line)` para o bloco de uma app no relatório semanal |
| `weekly_app_fields(r, section, app)` | campos extras de uma app (nginx: robôs) |
| `weekly_fields(r)` | `(position, field)` para a parte do servidor inteiro (host: servidor, postgres: bancos) |

`r` é um `report.ReportContext`: `r.window(start, end)` devolve linhas do histórico, além
de `r.alerts_window`, `r.bots`, `r.daily_numbers`, `r.now`, `r.tz`, `r.bullets(lines)`.

## Hooks do banco

Os módulos que consultam o Postgres de uma app adicionam comandos à única sessão somente
leitura por app do módulo postgres: `service_queries(ctx, svc)` para a consulta por
serviço e `app_queries(ctx, app, svc)` + `app_results(ctx, app, res)` para a por app.
Cada comando devolve linhas marcadas com `server_health.parse.PSQL_MARK`; o auxiliar
`server_health.modules.postgres.q(key_sql, value_sql)` monta um.

## Textos

Todo texto que uma pessoa lê passa por `t("key", **values)`, com a chave em `strings`
para `en` e `pt-BR` (o inglês é o fallback). Os plurais usam `tn("key", n)` com
`key.one` e `key.other`. Números passam por `fnum`, tamanhos por `fbytes`, durações por
`fdur` e datas por `fdate`: eles seguem o idioma.

## Um notificador

Os canais também são plugáveis: uma subclasse de `server_health.notify.Notifier` com
`configured()` e `send(message, route=..., mention=..., alert=..., attachment=...)`,
adicionada em `server_health/notify/__init__.py`. A mensagem é um dict no formato de um
embed do Discord, mais `_type`, `_level` e `_tag`; `server_health.text.embed_text` a
transforma em texto puro. Veja `notify/webhook.py` para um curto.

## Testes

`python3 -m unittest discover -s tests`. Um teste de módulo em geral copia
`tests/fixtures/basic`, grava em `cmd/` as saídas de comando de que precisa e roda uma
rodada pelo `tests/helpers.py`.
