# PACOTE COMPLETO PARA AUDITORIA EXTERNA — FOREX BOT PRO V2.1

**Objetivo:** permitir ao DeepSeek executar auditoria rigorosa, adversarial e reproduzível comparando a V2.1 com achados anteriores, sem tocar no robô da `main`.

## Proveniência e integridade do snapshot

- Repositório: https://github.com/veloraspaces0/forex-bot-pro-v1-demo
- Código de referência da candidata V2.1: branch `forex-bot-pro-v2-1-hardening`; commit **`10c4aa78f3d48ee427152b715f8046fec18c9f92`**.
- Esta branch de auditoria, `auditoria-v2-1-deepseek-2026-11-04`, foi criada **a partir desse commit**. Foram removidos da cópia apenas `v2/state.enc` e `v2/__pycache__/paper_bot.cpython-312.pyc`; foram acrescentados arquivos de documentação e evidências em `EVIDENCIAS/`, sem alterações à implementação de trading.
- A V2 ativa da `main` não foi atualizada para V2.1.
- Para comparar com o motor anterior, use o código V2 no commit `aaa71a728c4d1dee8c9aef0eb1c65ee2a85da271`. A diferença pode ser inspecionada no GitHub em `compare/aaa71a728c4d1dee8c9aef0eb1c65ee2a85da271...10c4aa78f3d48ee427152b715f8046fec18c9f92`.

## Conteúdo original completo para auditar

- `v2/paper_bot.py`: motor prospectivo de paper trading, dados Twelve Data, risco e indicadores.
- `v2/runner.py`: estado cifrado e resumo público.
- `.github/workflows/forex-v2.yml`: testes, gatilhos e limites de permissões.
- `tests/test_paper_bot.py` e `tests/test_v21_hardening.py`: suíte atual.
- `v2/index.html`, `index.html`, `v1.html`: painéis.
- `v2/state.json`: estado público V2 herdado da `main`, **não produzido por uma execução prospectiva da V2.1**.
- `README.md`, `README_V2_1.md`, `.gitignore`, `v2/run.py`.
- `v2/engine.py`, `v2/tests/test_engine.py`: código legado, não faz parte do workflow atual.

## Evidências anexadas neste mesmo pacote

- `EVIDENCIAS/LOG_PAGES_ERRO_HTTP_502.txt`: log REAL do job `deploy`, execução [41815869264](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/runs/41815869264), falhou com HTTP **502 (Server Error)**, não com uma mensagem comprovada de permissão.
- `EVIDENCIAS/LOG_PAGES_PUBLICACAO_SUCESSO.txt`: log REAL do job `deploy`, execução [41822769385](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/runs/41822769385), terminou com sucesso sem modificação proposital do Pages.
- `EVIDENCIAS/LOG_V2_PAPER_MAIN_SUCESSO.txt`: log REAL de execução na `main` com motor **V2 anterior**, execução [41822743778](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/runs/41822743778). **Não é um teste prospectivo V2.1.**
- `EVIDENCIAS/LOG_V21_VERIFICACAO_32_TESTES.txt`: log REAL da candidata V2.1, execução [41824661367](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/runs/41824661367), **32/32 testes offline passaram** no GitHub Actions (Python 3.12). O job `paper` foi `skipped` na branch de desenvolvimento.
- `EVIDENCIAS/TESTES_LOCAIS_PYTHON_3_13_5.txt`: teste local executado em container Linux com código correspondente ao snapshot; **32/32 testes passaram**, Python 3.13.5, cryptography 46.0.4.
- `EVIDENCIAS/STATE_PUBLICO_V2_2026-11-04_104137Z.json` e `EVIDENCIAS/STATE_PUBLICO_V2_2026-11-04_114000Z.json`: resumos públicos de duas execuções distintas do motor **V2**. A ausência de novas operações entre as duas amostras deve ser considerada na reconciliação.
- `EVIDENCIAS/GIT_LOG_V21_20_COMMITS.txt`: histórico recente da branch de implementação.

## O que NÃO foi enviado (e por quê)

- `v2/state.enc`: estado operacional cifrado foi retirado, por prudência; não solicitar, compartilhar ou publicar. Testes de migração/criptografia usam fixtures sintéticas, não o estado real.
- `TWELVE_DATA_API_KEY`, `V2_STATE_ENCRYPTION_KEY`, credenciais ou logs que as exibam: nunca anexar.
- `pip freeze` do ambiente CI: **não foi coletado pelo workflow**. O workflow usa Python 3.12 e instala `cryptography>=43,<49`. O log registra a versão efetivamente instalada; não inventar `pip freeze`. Localmente: Python 3.13.5 + cryptography 46.0.4.
- Não existem duas execuções prospectivas V2.1 para cotejar, porque a branch está deliberadamente restrita a testes offline e a `main` ainda opera a V2.

## Parâmetros e expectativas

- EUR/USD; 15min; conta virtual US$100; risco **planejado**, não máximo garantido, de 0,5% por operação; bloqueio diário 2%.
- Indicadores EMA 9/21, RSI 14 e ATR 14. Spread estimado fixo e fills paper não replicam mercado.
- Nenhuma ordem real, nenhuma corretora conectada. Cotações Twelve Data apenas na execução da `main`.
- Separar corretamente: `last_bar` pode ser marcador de idempotência sem chamar-se `last_processed_candle_time`. O código deve ser julgado pelo comportamento demonstrável, não pelo nome da variável.
- Regra temporal precisa ser julgada conforme a disponibilidade real de dados e tempo de entrada. Não afirmar que utilizar o último candle fechado seja *look-ahead* automaticamente; verificar timestamps e dados efetivamente acessados.
- O workflow do bot não é o workflow dinâmico gerenciado pelo GitHub Pages. Não exigir `pages: write` no workflow de paper trading sem explicar por quê.
- Em simulação, **não limitar artificialmente o P/L de um gap para parecer que a perda máxima foi respeitada**. Registrar perdas acima do risco planejado e sinalizá-las.
- Exigir evidências precisas, casos reproduzíveis, condições de falha e recomendações, inclusive onde a V2.1 permanece limitada.

## Como executar

```bash
python -m pip install 'cryptography>=43,<49'
python -m unittest discover -s tests -v
```

A suíte é offline, não requer API key e não faz trades reais.

**Não promover V2.1 à main, não rodar o runner com chave real e não modificar códigos durante a auditoria.** O objetivo é produzir relatório independente antes da decisão de implantação.
