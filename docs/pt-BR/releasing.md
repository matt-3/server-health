# Publicando uma versão

1. Os testes passam: `python3 -m unittest discover -s tests`, e o teste do instalador num
   contêiner (`sudo bash tests/install_test.sh` numa máquina descartável).
2. O `__version__` em `server_health/__init__.py` recebe o novo número. Versionamento
   semântico: uma correção sobe o último número; uma métrica, configuração ou mensagem
   nova, o do meio; uma chave de config ou de TOML que deixa de funcionar, o primeiro.
3. Uma entrada no `CHANGELOG.md` com o que muda para quem instala, e a versão no
   `versions.json` com o `level` dela: `security` ou `critical` (os servidores recebem um
   aviso até atualizar), `feature` ou `fix` (só no resumo diário deles), e um `summary`
   de uma linha em inglês e em português. Os servidores leem este arquivo da `main`:
   faça o push dele junto com a tag, nunca antes dela.
4. Commit, depois a tag `vX.Y.Z` com o mesmo número, e o push da tag. O CI da tag confere
   se a tag e o `__version__` batem.
5. Em cada servidor, a [atualização](installation.md#atualização) com a nova versão. As
   apps que conferem o TOML no CI também sobem a versão fixada delas.
