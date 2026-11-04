# Forex Bot Pro V2 — EUR/USD, paper trading em nuvem

**Estado:** V2 publicada no GitHub Pages. Execucao por GitHub Actions com cotacoes da Twelve Data; nenhuma ordem real ou conexao com corretora.

## Links

- [Painel V2 para iPhone](https://veloraspaces0.github.io/forex-bot-pro-v1-demo/)
- [Workflow agendado](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/workflows/forex-v2.yml)
- [Simulador V1 com dados sinteticos](https://veloraspaces0.github.io/forex-bot-pro-v1-demo/v1.html)

No Safari: Compartilhar > Adicionar a Tela de Inicio.

## Primeira ativacao

O bot usa o secret **TWELVE_DATA_API_KEY**. Sem ele, nao consulta precos nem abre operacoes simuladas.

1. Obtenha sua propria chave no site [Twelve Data](https://twelvedata.com/).
2. No GitHub: Settings > Secrets and variables > Actions > New repository secret.
3. Nome: **TWELVE_DATA_API_KEY**. Valor: sua chave privada. Nunca inclua a chave em commits, prints ou no chat.
4. Abra Actions > Forex Bot Pro V2 - Paper EURUSD > Run workflow, ou aguarde o proximo ciclo.
5. A primeira coleta apenas inicializa os indicadores, sem retroativamente criar trades.

**Se o painel ja exibir MONITORANDO, o secret de dados esta operacional e nao e necessario configurar novamente.**

## Funcionamento

- EUR/USD, 15 minutos; a API deve autorizar esse par e intervalo no seu plano.
- GitHub Actions tenta rodar nos minutos **07, 22, 37 e 52 UTC**, com possiveis atrasos/cancelamentos.
- A V2 e um monitor agendado, **nao um servidor ininterrupto**, e nao deve ser usada para execucao real.
- Capital virtual inicial: **US$ 100**. Risco nominal por trade: **0,5%** do saldo.
- Bloqueio de novas entradas apos perda diaria de **2%** do patrimonio de referencia UTC.
- Indicadores: EMA 9/21, RSI 14 e ATR 14. Stop: 1,5 ATR (minimo 3,5 pips). Alvo: 2 vezes a distancia do stop.
- Apenas candles totalmente encerrados geram sinais; a primeira coleta nao cria trades passados.
- Entrada paper estimada usando a ultima cotacao observada no momento do workflow, mais spread hipotetico fixo de 1,2 pip.
- Saida paper por OHLC dos candles seguintes completos; se stop e alvo ocorrerem na mesma vela, assume-se stop primeiro.
- Nao modela plenamente gaps, slippage, custos de operacao, cotacoes bid/ask reais ou a sequencia intrabar de precos.
- Quando os dados ficarem antigos ou houver lacuna irrecuperavel de candles, o motor nao abre novas operacoes. Erros de API nao geram precos inventados.

## Privacidade e permissao de uso dos dados

O repositorio e publico. Os termos da Twelve Data limitam a redistribuicao de cotacoes: https://twelvedata.com/terms

**Nao publicamos candles brutos.** O motor guarda OHLC e historico integral cifrados em `v2/state.enc` com AES-GCM; a interface GitHub Pages usa apenas `v2/state.json`, que mostra metricas derivadas, resultados paper sem precos de entrada/saida e estados operacionais.

- O segredo de decriptacao padrao e derivado da chave `TWELVE_DATA_API_KEY`.
- Pode-se configurar opcionalmente `V2_STATE_ENCRYPTION_KEY` antes do primeiro ciclo para manter o segredo de cifra independente da chave de API. **Nao mude o segredo de cifra depois de iniciado** sem migrar corretamente o arquivo cifrado, ou o bot nao conseguira retomar o historico.
- O arquivo local `v2/state.private.json` deve ser ignorado pelo Git; o runner o apaga apos a coleta.
- A publicacao da performance derivada nao garante que todos os usos de dados estejam autorizados; consulte as regras do provedor para sua finalidade e licenca.

## Arquivos principais

- `v2/paper_bot.py`: estrategia e gestao de risco.
- `v2/runner.py`: coleta segura, encriptacao do historico e publicacao apenas de metricas derivadas.
- `v2/state.enc`: estado privado cifrado.
- `v2/state.json`: resumo publico para o painel.
- `v2/index.html`: painel iPhone somente leitura.
- `tests/test_paper_bot.py`: testes de risco, bootstrap, gaps e prioridade do stop.
- `.github/workflows/forex-v2.yml`: rotina cloud com testes, cifragem, validacao de dados publicos e commit.

`v2/run.py` e apenas um redirecionamento seguro para o runner cifrado. `v2/engine.py` existe como prototipo legado, mas **nao** e executado pelo workflow ativo.

## Executar testes

    python -m unittest discover -s tests -v

A rotina cloud instala a dependencia `cryptography` para cifragem AES-GCM.

**Aviso:** resultados historicos e simulados nao representam retorno futuro, nem demonstram que a estrategia seja lucrativa. Estimativas de fills paper podem ser significativamente diferentes da realidade.
