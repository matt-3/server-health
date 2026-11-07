# Painel

O painel é uma página HTML autocontida (SVG embutido, sem script, sem arquivo externo)
com os últimos 7 dias, hora a hora: memória e espera por memória, load, disco, swap-out,
o tamanho de cada banco, as requisições de cada app (pessoas, robôs, 5xx) e o tempo do
health check, o que está aberto agora e os alertas recentes. Ele segue o tema claro ou
escuro do sistema.

Ele é montado com o que o monitor já guarda (`history.jsonl`, `alerts.jsonl`,
`state.json`): nenhum processo roda para servi-lo, nenhuma porta abre e nenhum login
precisa ser mantido.

## Três jeitos de ver

**Anexado ao relatório semanal.** O relatório semanal chega no Discord com a página
como arquivo (`server-health-<date>.html`): abra no navegador. Desligue com
`SERVER_HEALTH_WEEKLY_DASHBOARD=false`.

**Como arquivo, quando quiser:**

```bash
sudo server-health --dashboard /tmp/dashboard.html
scp deploy@<server>:/tmp/dashboard.html .   # do seu computador, depois abra
```

**Servido no servidor, por um túnel SSH:**

```bash
# no servidor
sudo server-health --serve            # 127.0.0.1:8730; Ctrl+C encerra
# no seu computador
ssh -L 8730:127.0.0.1:8730 deploy@<server>
# abra http://localhost:8730 (cada recarga remonta a página)
```

Ele só escuta em 127.0.0.1: nada fica exposto à internet.

## Os dados

O histórico guarda uma amostra por rodada por 35 dias; a página mostra os últimos 7, em
médias por hora (as requisições são somadas por hora). Um gráfico ainda sem dados diz
isso: as amostras começam na primeira rodada do cron. A página carrega dados do servidor
(nomes das apps, tamanhos, títulos dos alertas), os mesmos dos alertas no Discord:
compartilhe como compartilharia esses.
