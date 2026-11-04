# Forex Bot Pro V2.1 — hardening candidate (PAPER ONLY)

**Branch de desenvolvimento:** `forex-bot-pro-v2-1-hardening`. **Não mesclada na `main`.** A V2 continua em funcionamento sem mudanças até a aprovação dos testes.

## O que mudou

- **Persistência durável:** gravações atômicas (arquivo temporário no mesmo diretório, `fsync`, `os.replace`) de `v2/state.enc` e `v2/state.json`. Caso a substituição falhe, o estado anterior permanece intacto; ao reiniciar, corrupção do estado cifrado causa falha fechada, **sem reset automático**.
- **Compatibilidade:** arquivo cifrado V2 ainda usa o mesmo cabeçalho `FX2`, PBKDF2 e AES-GCM; aceita o `\n` no final do arquivo legado. Novos campos no estado são adicionados sem alterar sua versão (`version=2`). Não altere `V2_STATE_ENCRYPTION_KEY` nem `TWELVE_DATA_API_KEY` sem migração da chave se os dados foram cifrados com ela.
- **Robustez de dados:** valida preços e timestamps, rejeita cotações não finitas, OHLC incoerente e duplicatas conflitantes; realiza até 3 tentativas em 429/5xx e falhas de rede/JSON, com espera progressiva. Nunca replica preço inventado após erro.
- **Idempotência:** só processa candles mais novos que `last_bar`; valida lacunas de 15 minutos **dentro** de todo o novo lote, e não apenas no início da série. Exclui os horários de fechamento regular do fim de semana FX (17h Nova York sexta a domingo); feriados e horários extraordinários exigem avaliação manual.
- **Parada de segurança:** quando há candle ausente em horário negociável, `gap_halt=true` bloqueia novas decisões de forma persistente até a análise manual. Não se autocorrige silenciosamente nem reinicia o estado.
- **Candle encerrado:** sinais são calculados apenas com candles que passaram do fechamento do intervalo de 15 minutos há 90 s. Entrada paper requer uma barra **atual** em formação, não um candle de preço antigo. Entradas em candle incompleto **não o usam para indicador**.
- **Risco:** conserva capital virtual inicial US$100, EUR/USD, EMA 9/21, RSI 14, ATR 14, limite 0,5% e teto diário 2% (dia UTC), posição única, spread sintético 1,2 pip e limite de alavancagem 20x. O tamanho usa `min(balance,equity)` antes de dimensionar a operação; gaps podem exceder 0,5% e agora são sinalizados como `RISK_EXCEEDED`.
- **GitHub Actions:** em branches não-main, executa **apenas testes offline**, sem acesso à API/segredos e sem gravar estado. Na main, a execução paper mantém `git push` sem força e falha se houver conflito. Nada publica dados brutos no painel.

## Testes

```
python -m pip install 'cryptography>=43,<49'
python -m unittest discover -s tests -v
```

A nova suíte usa respostas de API falsas, sem internet. Inclui retry 429, HTTP 403, JSON inválido, candles incompletos/duplicados, gaps de mercado, fim de semana, gap latch, STOP e gap BUY/SELL, prioridade do STOP, idempotência, estados cifrados V2 antigos, truncamento e atomicidade.

## Limitações e precauções

1. **Não é um robô de corretora** e não envia ordens reais. GitHub Actions roda de forma oportunista, não em 24/7 contínuo.
2. Uma barra OHLC não contém a sequência intrabar; a V2.1 evita usar para a saída a barra em andamento no momento da entrada, mas **não modela o stop durante a parte restante dessa mesma barra**. Isso torna o paper trading impróprio para avaliar execução real.
3. Spread fixo não representa spread real, slippage, swap, comissão, latência ou risco extremo de gap. O aviso `RISK_EXCEEDED` é observacional, **não limita artificialmente prejuízos**.
4. A escolha da janela diária **continua em UTC** para preservar a regra de risco da V2 e não modificar o experimento em curso; não representa o dia da corretora (17h Nova York).
5. Limites de 20x simulados não equivalem às regras de margem, tamanho mínimo e liquidação de uma corretora. Não use o protótipo para avaliar a viabilidade de operar US$100 em corretora específica.
6. O segredo padrão de cifragem é derivado da API key Twelve Data quando `V2_STATE_ENCRYPTION_KEY` não está definido. Rotação da API key pode tornar o histórico ilegível; **não altere chaves sem migração separada e backup seguro**.
7. O estado completo permanece cifrado em repositório público; a segurança depende da chave. Não forneça a chave a IAs, em chats, prints ou commits.
8. A rotina detecta lacunas e **bloqueia**, não inventa candles, não completa feriados e não reprocessa trades antigos automaticamente. Recuperação exige auditoria do estado e dados originais.
9. Dois arquivos atômicos **não são uma transação conjunta**. Se cair entre o commit do estado cifrado e o resumo público, o painel pode ficar desatualizado até a próxima execução. Uma persistência transacional real precisaria de backend/armazenamento apropriado.
10. GitHub Pages pode falhar independentemente do robô; o histórico de ações mostrou HTTP 502 do provedor de deploy, sem evidência de falta de permissão.

## Procedimento de promoção (não executado)

1. Revisar diff/commits da branch; rodar testes no GitHub Actions e confirmar **success**.
2. Fazer backup seguro do `v2/state.enc` e checar integridade do estado antes de tocar a main.
3. Somente então decidir se cria PR/merge seletivo **dos arquivos de código e testes**, preservando sempre o estado vivo mais recente da main.
4. Confirmar que o primeiro ciclo mantém saldo/histórico; acompanhar o painel e flags, com plano de rollback pronto.
5. Reexecutar auditoria independente e testes prospectivos antes de pensar em qualquer execução real.

**Não mescle o arquivo `v2/state.enc` nem `v2/state.json` da branch no repositório principal:** o estado da main evolui automaticamente. Ao mesclar, selecionar apenas código/testes/docs e proteger o estado vivo.

## Consolidação pós-reauditoria (ainda candidata, sem mexer na main)

- Testes offline adicionais em `tests/test_v21_consolidation.py` cobrem a migração sintética V2→V2.1 com **ciphertext gerado pelo formato original da V2**, idempotência com crescimento da série e repetição, limite diário com caso positivo e negativo, aproximação do orçamento de risco, borda de 15min, erros `HTTP 200 / status:error`, TLS e recuperação manual fail-closed.
- A **compatibilidade do estado real de produção ainda exige prova privada**; uma fixture V2 sintética não é decifragem do estado operacional real.
- Novo utilitário `v2/recover_gap.py` opera **offline, em cópia local**, dry-run por padrão, e exige backup novo externo ao repositório, motivo, reconhecimento de perda de continuidade e 40 candles recentes consecutivos para pular um gap sem criar trades. Ele **recusa posição aberta**. **Não realiza push GitHub nem limpa gap_halt automaticamente**.
- O painel público passa a incluir `planned_risk_usd` e `risk_exceeded` por operação, além do contador geral. Dados privados de OHLC não são publicados.
- Em `enter()`, se `gap_halt` impedir entrada, um evento explícito é registrado. A primeira barra elegível para saída será sempre **posterior** ao candle em que a entrada ocorreu (inclusive em entrada exatamente no limite de 15min).
- Critério e procedimento de recuperação/promoção com cópias privadas: ver `RECUPERACAO_E_PROMOCAO_V21.md`.
