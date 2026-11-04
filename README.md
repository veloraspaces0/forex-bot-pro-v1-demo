# Forex Bot Pro V2 — EUR/USD com dados de mercado e paper trading

Projeto experimental, sem ordens reais e sem conexão com corretoras.

## Acessar no iPhone

- **Painel V2:** https://veloraspaces0.github.io/forex-bot-pro-v1-demo/
- **V1 com preços artificiais:** https://veloraspaces0.github.io/forex-bot-pro-v1-demo/v1.html
- **GitHub Actions V2:** https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/workflows/forex-v2.yml

No Safari: Compartilhar > Adicionar à Tela de Início.

## Ativar a coleta de preços reais

1. Crie uma conta no provedor [Twelve Data](https://twelvedata.com/), e copie a chave de API. Confirme que o seu plano dá acesso a EUR/USD em 15 minutos.
2. Neste repositório, abra **Settings > Secrets and variables > Actions > New repository secret**.
3. Nome exato: **TWELVE_DATA_API_KEY**. Cole a chave no campo de valor e salve. **Nunca** coloque a chave em arquivos, mensagens ou prints.
4. Abra **Actions > Forex Bot Pro V2 - Paper EURUSD > Run workflow** (se o botão aparecer) ou aguarde o próximo horário agendado.
5. A primeira coleta só inicializa indicadores, sem criar operações históricas. Operações posteriores são estimativas de paper trading.
6. Se o workflow não conseguir publicar o estado, verifique **Settings > Actions > General > Workflow permissions** e permita escrita para o GITHUB_TOKEN quando disponível.

Sem a chave, a V2 permanece em **AGUARDANDO_API** e não inventa candles.

## Configuração e funcionamento

- Par: EUR/USD. Fonte: Twelve Data /time_series.
- Candles de 15 minutos; apenas velas fechadas (UTC) são processadas.
- Agendamento: minutos 7, 22, 37 e 52 de cada hora (UTC). **GitHub Actions pode atrasar ou deixar de executar**; não é servidor de execução garantida.
- Saldo inicial: **US$ 100,00 simulados**.
- Risco nominal por operação: **0,5%** do saldo, sujeito a limite de alavancagem simulado de 20x.
- Bloqueio de novas entradas após queda de **2%** da referência de patrimônio do início do dia UTC.
- EMA 9/21, filtro RSI 14, ATR 14. Sinal ao fechamento; entrada estimada na abertura da vela seguinte.
- Stop Loss = 1,5 ATR (mínimo de 3,5 pips); Take Profit = 2x a distância do stop.
- Spread **hipotético** fixo de 1,2 pip. Não representa cotação executável.
- Se a mesma vela tocar stop e alvo, o motor escolhe o **stop primeiro**, de forma conservadora.
- Se faltar uma ou mais velas, o robô cancela sinais pendentes e pausa novas entradas até reconstruir sequência. Stops em gaps podem ser piores do que o planejado.
- O motor não executa ordens em corretoras. Não modela swaps, comissões, liquidez, slippage real ou todos os custos de operação.

## Arquitetura

- v2/engine.py — motor determinístico, sem acesso à rede.
- v2/run.py — cliente Twelve Data, validação e persistência segura.
- v2/state.json — histórico público, candles e estado persistente.
- v2/index.html — painel responsivo somente leitura; sem chave exposta.
- v2/tests/test_engine.py — testes de risco, idempotência e falhas de dados.
- .github/workflows/forex-v2.yml — execução periódica com teste antes do processamento.
- v1.html — simulador V1 original com preços artificiais.

## Rodar testes localmente

    python -m unittest discover -s v2/tests -v

Para rodar a coleta localmente, configure TWELVE_DATA_API_KEY no ambiente e execute:

    python v2/run.py

**Aviso:** backtest, simulação e resultados passados não comprovam lucratividade. Nunca utilize dados do painel como cotação executável. O estado da V2 é público no repositório; a chave de API permanece em Secrets do GitHub.
