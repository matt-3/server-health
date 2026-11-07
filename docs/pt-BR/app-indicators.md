# Apps e os indicadores delas

Cada app pode ter um arquivo TOML que diz onde estão as coisas dela (quando ela não é uma
app do Dokku) e o que só ela sabe que importa: o fluxo que as pessoas sentem quando para,
um registro preso num estado intermediário, uma chamada externa falhando e os números do
dia para o resumo. Guarde-o no repositório da app (o SQL muda com as migrations dela); no
servidor ele mora em `/etc/server-health/apps.d/<app>.toml`. Um exemplo comentado:
[`examples/my-app.toml`](../../examples/my-app.toml).

## Uma app fora do Dokku

Uma app do Dokku é encontrada sozinha. Qualquer outra app (Docker Compose, Kamal, um
serviço do systemd) é monitorada quando o TOML dela declara pelo menos um destes:

```toml
app = "shop"                                # o nome dela nas mensagens e nas chaves das métricas
health_url = "https://shop.example.com/up"  # o módulo http confere (e o tls, o certificado)
nginx_log = "/var/log/nginx/shop.access.log" # o módulo nginx lê (formato combined)
database_url = "env:SHOP_DATABASE_URL"      # os módulos postgres, rails e indicators usam
# database_container = "shop-postgres-1"    # ou: psql dentro deste contêiner, com `docker exec`
```

O `database_url` recebe uma URL `postgres://user@host:5432/db`. **Nunca ponha a senha no
TOML**: o arquivo pode ser lido por qualquer usuário da máquina, e o `--check-app` o
recusa. Ponha a URL inteira no arquivo de config (só root) e aponte para ela:

```bash
# /etc/default/server-health
SHOP_DATABASE_URL=postgres://monitor:secret@127.0.0.1:5432/shop?sslmode=disable
```

Um usuário somente leitura basta (e é melhor): o monitor só lê. Com
`database_container`, o psql roda dentro do contêiner como `postgres` (ou como o usuário
e o banco do `database_url`, sem senha), do jeito que a imagem oficial permite no socket
local dela.

## O TOML

```toml
app = "my-app"              # o nome da app no Dokku, ou qualquer nome para uma app fora dele (obrigatório)
name = "My App"             # opcional: as mensagens mostram "My App (my-app)"
database = "my-app-db"      # serviço Postgres do Dokku; padrão: o ligado à app
health_path = "/up"         # opcional: o caminho do módulo http para esta app
jobs_dashboard = "/admin/jobs"  # opcional: citado nos alertas de jobs com falha
storage_mount = true        # opcional: alerta se a app não tiver storage mount no Dokku
max_body_size = "50m"       # opcional: alerta se o nginx ou o Thruster aceitarem menos
health_url = "https://..."  # fora do Dokku: veja acima (também nginx_log, database_url, database_container)

[[indicator]]
key = "orders_stuck"        # letras minúsculas, dígitos e _
label = "Orders stuck in processing"
sql = "SELECT count(*) FROM orders WHERE status = 'processing' AND updated_at < now() - interval '15 minutes'"
warn_at = 1                 # valor >= 1 é aviso
critical_at = 5             # valor >= 5 é crítico
direction = "above"         # ou "below": alerta quando o valor cai até o limite
when = "always"             # "always": toda rodada, pode alertar; "summary": só no resumo diário, nunca alerta
persist_min = 0             # o alerta só conta depois de tantos minutos seguidos
active_hours = "09-23"      # opcional: fora destas horas (SERVER_HEALTH_TZ) nunca alerta
format = "number"           # number, bytes, duration (segundos) ou cents
explanation = "o que fazer quando alertar (vai na mensagem)"
```

## Regras

- **A consulta devolve um número**, começa com `SELECT` ou `WITH` e não tem `;` no
  meio.
- **Ela roda numa sessão somente leitura** (`default_transaction_read_only`), com
  timeout de 5 s e espera de lock de 1 s. Uma consulta que falha vira aviso daquele
  indicador (depois de 10 min), e o resto segue.
- **Só contagens e somas.** Nada de e-mail, nome, texto escrito por usuário, IP ou valor
  por usuário: o resultado vai para o Discord.
- **A regra de tempo mora no SQL** (`created_at < now() - interval '10 minutes'`).
  O `persist_min` é para o que oscila (um job que roda de madrugada), não substitui a
  janela.
- **As colunas `datetime` do Rails guardam UTC sem fuso.** Comparar com uma hora local
  pede `(created_at AT TIME ZONE 'UTC') AT TIME ZONE 'America/Sao_Paulo'`.
- **Os indicadores de resumo se somam no relatório semanal** (`number` e `cents` são
  somados; `bytes` e `duration` mostram o último valor). Um rótulo que diz "(24h)" fica
  errado lá.
- Um erro no TOML (chave desconhecida, `sql` que escreve, um indicador de resumo com
  limiar) derruba só aquele indicador e aparece como "Config do monitor" no relatório.

## Conferindo um TOML

No servidor, antes de instalar uma mudança:

```bash
sudo server-health --check-app /tmp/my-app.toml
```

Ele valida o arquivo e roda cada consulta uma vez, mostrando o valor, o nível e o tempo,
ou o erro do Postgres. Sem Dokku (no seu computador, no CI) ele só valida o arquivo:
código de saída 0 quando válido, 1 ou 3 quando não.

Para conferir no CI da app com a mesma versão que os servidores rodam:

```yaml
# GitHub Actions
- uses: actions/setup-python@v5
  with: { python-version: "3.12" }
- run: |
    curl -fsSL https://github.com/matt-3/server-health/archive/refs/tags/v1.0.0.tar.gz | tar -xz -C /tmp
    SERVER_HEALTH_CONFIG=/dev/null PYTHONPATH=/tmp/server-health-1.0.0 \
      python3 -m server_health --check-app scripts/monitor/my-app.toml
```

```yaml
# GitLab CI
monitor_toml:
  image: python:3.12-slim
  variables:
    SERVER_HEALTH_VERSION: "1.0.0"
  script:
    - python3 -c "import urllib.request, tarfile, io; tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen('https://github.com/matt-3/server-health/archive/refs/tags/v${SERVER_HEALTH_VERSION}.tar.gz').read())).extractall('/tmp')"
    - SERVER_HEALTH_CONFIG=/dev/null PYTHONPATH=/tmp/server-health-${SERVER_HEALTH_VERSION} python3 -m server_health --check-app scripts/monitor/my-app.toml
```
