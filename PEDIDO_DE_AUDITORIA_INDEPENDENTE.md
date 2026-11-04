# PEDIDO DE AUDITORIA INDEPENDENTE — FOREX BOT PRO V2

**Solicitação:** executar uma auditoria técnica, financeira, estatística e de segurança **independente, crítica e reproduzível** do Forex Bot Pro V2, um monitor de *paper trading* de EUR/USD operado por GitHub Actions. Não presumir qualidade, segurança nem rentabilidade pelo fato de o projeto ter testes ou workflows verdes.

## Escopo e contexto

- Repositório de origem: https://github.com/veloraspaces0/forex-bot-pro-v1-demo
- Snapshot de código analisável: branch `auditoria-v2-2026-11-04`, originada do commit `cf6a6a860d50694a5a2618202a21b8849a0cfab7` da `main` em 2026-11-04 10:41:37 UTC.
- Este pacote conserva o código, testes, GitHub Actions, interface e resumo público. O arquivo cifrado de estado operacional `v2/state.enc` e bytecode `__pycache__` foram **deliberadamente excluídos**. Nenhuma chave deve ser solicitada, colada ou compartilhada.
- Configuração: EUR/USD 15min, capital virtual inicial US$100, risco planejado de 0,5% por operação, limite de perda diária de 2%, EMA 9/21, RSI 14, ATR 14, stop aproximado de 1,5 ATR (mín. 3,5 pips), take profit 2x, spread hipotético de 1,2 pip, alavancagem cap de 20x.
- A Twelve Data é fonte de cotações; **não** há conexão com corretora nem ordens reais.
- O painel V2 é estático, servido por GitHub Pages e lê um `v2/state.json` com métricas derivadas.
- Evidência operacional do snapshot: saldo virtual US$99,50, 1 operação SELL fechada em STOP com P/L −US$0,50, nenhuma posição aberta. Essa amostra **não prova lucratividade**.
- Registro de ambiente a examinar: workflow `Forex Bot Pro V2 - Paper EURUSD` terminou com sucesso em 2026-11-04 10:41:51 UTC; o workflow GitHub Pages subsequente completou build, mas falhou na etapa `Deploy to GitHub Pages` em 2026-11-04 10:42:36 UTC. Investigue causas concretas usando evidências, não suposições.

## Arquivos prioritários

1. `v2/paper_bot.py` — coleta Twelve Data, normalização temporal, indicadores, sinais, execução paper, risco e estado.
2. `v2/runner.py` — cifragem/decifragem AES-GCM, derivação de chave, separação entre estado confidencial e painel público.
3. `.github/workflows/forex-v2.yml` — agendamento, permissões, concorrência, persistência e testes.
4. `tests/test_paper_bot.py` — cobertura dos casos normais e extremos.
5. `v2/index.html`, `index.html` e `v1.html` — dados exibidos, avisos, cache e acesso via iPhone.
6. `v2/state.json` — esquema e consistência das métricas públicas.
7. `v2/engine.py` e `v2/tests/test_engine.py` — implementação legada: verificar isolamento da versão ativa, risco de confusão e regressão.
8. `README.md`, `.gitignore` e `v2/run.py` — documentação, proteção de dados e execução.

## Itens obrigatórios da auditoria

### 1. Matemática financeira e integridade de risco — prioridade crítica
- Derive numericamente a exposição em EUR, cálculo de P/L em USD para EUR/USD, dimensionamento `floor(min(risk_budget/stop_distance, equity*leverage/entry_price))`, arredondamentos e limites de tamanho.
- Verifique BUY e SELL, cálculo de spread na entrada e saída, stop/take-profit em gaps e candles ambíguos, risco efetivo versus risco **planejado** de US$0,50 e sua diferença sob abertura acima/abaixo do stop.
- Examine se a redução de saldo, equity marcada a mercado, limite diário de 2%, troca de data UTC e posições abertas aplicam uma proteção coerente. Verifique risco cumulativo e margem/alavancagem em situações extremas.
- Examine se ganhos/perdas, métricas, acertos, drawdown, e curva do patrimônio no painel reconciliam centavo a centavo com trades e estado.

### 2. Integridade temporal e vieses
- Identifique qualquer *look-ahead bias*, *repainting*, uso de candle incompleto, execução retroativa, preenchimento com preço não disponível na hora do sinal e decisões baseadas em fatos futuros.
- Avalie timestamp UTC de candle aberto/fechado; `STEP=15min`; atraso de 90 segundos; cálculo de `first_exit_bar`; janelas de frescor/idade da cotação.
- Faça testes em bordas de 15 minutos, transição de dia e fim de semana/feriado, lacunas de 1/2/N candles, atraso ou ausência de execução do GitHub Actions, dados fora de ordem e candles duplicados.

### 3. Dados externos e simulação
- Valide respostas Twelve Data: erros, limites/quota, OHLC inconsistente, horário, mercados fechados, precisão de preços, histórico insuficiente e reprocessamentos.
- Verifique se `bars[-1]` pode ser cotação defasada, preço da vela em formação ou um fechamento antigo; quantifique a consequência para fills simulados.
- Identifique custos ausentes: spreads variáveis, swap/financiamento, comissão, slippage, execução de stop, latência e dados bid/ask.
- Conclua com precisão o que as métricas **permitem** ou **não permitem** inferir sobre desempenho futuro.

### 4. Persistência e tolerância a falhas
- Simule reinícios, GitHub Actions duplicados ou atrasados, `git push` rejeitado, conflito de commits, state cifrado corrompido, rotação de chave, falha antes/depois de escrever o resumo, ausência de `TWELVE_DATA_API_KEY` e falha da Twelve Data.
- Verifique idempotência da ingestão, exatamente-once de trades, recuperação após falhas e consistência entre `state.enc` e `state.json`.
- Examine se execução do workflow em branch diferente da `main` pode criar commits ou modificar dados indevidamente.

### 5. Segurança e privacidade
- Avalie a derivação PBKDF2-HMAC-SHA256, AES-GCM/nonce/AAD, persistência em repositório público, permissão `contents: write`, Actions e logs, além da confiança na origem de dependências e Actions.
- Confirme que não há segredo ou OHLC bruto no JSON público, no HTML, nos logs e nos commits; documente limitações dessa evidência. Não solicite nem divulgue chaves.
- Teste schema do JSON, injeção no painel/XSS, dados inesperados, cache desatualizado e falhas de renderização no Safari.

### 6. GitHub Pages e operações
- Diagnostique separadamente o sucesso da execução `Forex Bot Pro V2 - Paper EURUSD` e a falha no deploy do GitHub Pages. Inspecione logs e permissões reais; não alegue causa sem prova.
- Verifique que GitHub Pages mostra a versão mais recente, falha de build/deploy, redirecionamento da raiz para `v2/`, latência e botão de atualização.
- Esclareça que GitHub Actions por cron é um agendador sujeito a atrasos e **não é um servidor de negociação 24/7**.

### 7. Qualidade de testes e método
- Execute os testes existentes, se o ambiente permitir, e identifique lacunas; caso não possa executar, declare essa limitação explicitamente.
- Escreva casos de teste determinísticos e de propriedades para extremos: SL/TP na mesma vela, spread em ambos os lados, stop gap, múltiplas execuções do mesmo candle, gaps, série de perdas, equity negativa, clock skew, múltiplos fusos e credenciais faltantes.
- Proponha testes prospectivos com histórico imutável, dados fora da amostra, *walk-forward*, sensibilidade de spreads e custos, e amostra mínima prudente antes de qualquer consideração de operação real.

## Entrega obrigatória do auditor

1. **Resumo executivo:** veredito verificável: adequado para demonstração / adequado para paper prospectivo / não adequado para dinheiro real, com justificativa.
2. **Tabela de achados** com ID, severidade (CRÍTICO/ALTO/MÉDIO/BAIXO), arquivo e linhas, mecanismo causal, consequência, evidência/caso reproduzível e correção recomendada.
3. **Demonstrações matemáticas** para cada erro de risco, com números concretos, antes/depois e premissas; destaque divergências entre risco planejado e perda possível.
4. **Plano de correções priorizado** separando bloqueadores, mudanças necessárias e melhorias futuras; não alterar a estratégia silenciosamente.
5. **Patches propostos** em diff unificado ou trechos exatos, com testes associados e explicação de compatibilidade/migração de estado.
6. **Resultados dos testes executados** versus testes apenas sugeridos; jamais inventar execuções, preços de mercado, logs ou sucesso.
7. **Riscos residuais** e critérios objetivos de promoção entre fases de demonstração → paper prospectivo → eventual avaliação real.
8. **Perguntas em aberto** apenas quando a ausência de dados impedir verificar algo material. Não assumir acesso à chave de API ou ao estado cifrado.

**Postura esperada:** atuar como auditor adversarial, cético e construtivo. Evitar elogios genéricos e não otimizar apenas para rentabilidade. Priorizar preservação de capital, integridade dos resultados, segurança e reprodutibilidade. Não prometer lucro ou ausência de perdas.
