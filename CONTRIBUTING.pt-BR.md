# Contribuindo

[English](CONTRIBUTING.md) | Português

Obrigado por ajudar. Relatos de bug, correções, mensagens melhores, módulos novos,
notificadores novos e traduções são todos bem-vindos.

## Antes de começar

- **Um bug:** abra uma issue com o servidor (distribuição, versão do Python), o comando,
  o que você esperava e o que aconteceu. O `sudo server-health --dry-run` e as últimas
  linhas do `/var/log/server-health.log` ajudam muito. Tire webhooks, IPs e qualquer
  coisa privada.
- **Algo grande** (um módulo novo, uma mudança no jeito como os alertas funcionam, uma
  dependência nova): abra uma issue antes, para combinarmos o formato antes de você
  escrever.
- **Correções pequenas** (um erro de digitação, uma mensagem, um limiar errado na
  documentação) podem ir direto para um pull request.

## As regras que o mantêm do jeito que é

- **Só a biblioteca padrão.** Nenhuma dependência do pip: o monitor é instalado nos
  servidores por um script shell e precisa rodar num Ubuntu recém-instalado.
- **Leve.** Ele roda a cada 5 minutos em máquinas pequenas. Nada de daemon, nada de
  comando longo em toda rodada (se algo é lento ou muda devagar, guarde em cache no
  estado, como fazem o `tls` e o `updates`), no máximo uma sessão de banco por app.
- **Ele só lê.** Nenhum comando muda o servidor, as apps ou os bancos; o SQL roda em
  sessões somente leitura.
- **Um alerta precisa valer a pena.** Um alerta novo precisa de um motivo pelo qual uma
  pessoa agiria, de um limiar que não toca num dia normal e de um `detail` que diz o que
  fazer.
- **Nenhum dado pessoal sai do servidor.** Contagens e somas, nunca e-mails, nomes, IPs
  de pessoas ou texto escrito por usuários.
- **Todo texto em inglês e em português.** Use `t()` com chaves nas `strings` do módulo
  para `en` e `pt-BR`; os testes conferem se os dois catálogos têm as mesmas chaves.
- **Código, comentários e nomes em inglês.**

## Rodando os testes

```bash
python3 -m unittest discover -s tests
```

Os testes rodam o monitor inteiro sobre `tests/fixtures/basic` (um `/proc` falso, saídas
de comandos e logs), então nenhum servidor é necessário. Um módulo ou uma verificação
nova vem com testes: copie a fixture num teste (veja `tests/helpers.py`), escreva em
`cmd/` as saídas de comando que ele lê e faça as asserções sobre o relatório ou sobre o
que foi enviado.

O teste do instalador instala de verdade, então roda no CI ou num contêiner:

```bash
docker run --rm -v "$PWD":/src -w /src debian:stable-slim \
  sh -c 'apt-get update -qq && apt-get install -y -qq python3 curl >/dev/null && bash tests/install_test.sh'
```

O CI roda os dois no Python 3.11, 3.12 e 3.13.

## Escrevendo um módulo

Veja [docs/pt-BR/writing-a-module.md](docs/pt-BR/writing-a-module.md). Em resumo: uma
subclasse de `Module` em `server_health/modules/`, listada em
`server_health/modules/__init__.py`, com o `detect`, as fases de que ela precisa, os
limiares e as strings dela; mais uma linha na tabela de módulos dos READMEs e em
`docs/*/modules.md`.

## Pull requests

- Um assunto por pull request, com testes e a documentação que ele muda (nos dois
  idiomas; se você não escreve em português, diga e nós traduzimos).
- Uma linha no `CHANGELOG.md` sob um título "Unreleased".
- As versões (número, tag, `versions.json`) são feitas pelos mantenedores:
  [docs/pt-BR/releasing.md](docs/pt-BR/releasing.md).

Ao contribuir, você concorda que a sua contribuição é licenciada sob a licença
[MIT](LICENSE) do projeto.
