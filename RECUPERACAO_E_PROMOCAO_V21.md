# V2.1 — recuperação manual de lacunas e promoção segura (PAPER ONLY)

**Status: só branch de testes; este documento não é autorização de promover o robô.** O código **não** implementa trades reais, não envia ordens e não administra uma conta de corretora.

## 1. Quando aparecer `data_gap` / `gap_halt=true`

O robô interrompe novas simulações. Ele não inventa candles e **não retoma automaticamente**. Causas possíveis: falha do provedor, candle faltante, horário especial/feriado não modelado, atraso de GitHub Actions. Ao contrário de uma ordem real, stops são apenas simulados e ficam sem avaliação durante interrupções.

1. Verifique o workflow e o timestamp do último candle público. Confira dados originais da Twelve Data e se existe uma **posição simulada aberta**. Se houver posição aberta, **NÃO** execute a recuperação: o resultado na lacuna é indeterminado, requer reconciliação manual independente e eventual encerramento do experimento; o utilitário recusa esse estado.
2. **Pause as execuções paper na main** antes de preparar qualquer promoção/recuperação. Use um checkout/cópia local segura e **nunca modifique o arquivo remoto enquanto GitHub Actions puder gravá-lo**.
3. Copie `v2/state.enc` da main para uma pasta privada fora do Git. Use somente sua chave já existente por variável de ambiente (seja `V2_STATE_ENCRYPTION_KEY` dedicada, seja a chave antiga Twelve Data que cifrava o estado); **não envie a chave nem o arquivo cifrado em chats**. Não faça rotação da chave durante o processo.
4. Revise um JSON de `time_series` EUR/USD 15min, UTC, com OHLC originais suficientes para **40 candles consecutivos encerrados**, atualizados e válidos. Guarde esse JSON **fora do repositório**; jamais publique o campo `apikey` ou histórico OHLC bruto. Este procedimento exige **pelo menos 10 horas de histórico contínuo recente**, podendo precisar esperar após feriados/aberturas.
5. Execute **primeiro em DRY-RUN**, com todos os caminhos em área privada:

   ```bash
   python v2/recover_gap.py --state /privado/copia/state.enc \
     --snapshot /privado/cotacoes-revisadas.json \
     --reason "Conferido feriado ou perda de coleta em simulacao"
   ```

6. Somente após concordar com a perda de continuidade do experimento, utilizar **offline**:

   ```bash
   python v2/recover_gap.py --state /privado/copia/state.enc \
     --snapshot /privado/cotacoes-revisadas.json \
     --reason "Conferido feriado ou perda de coleta em simulacao" \
     --backup /privado/backups/state-ANTES-DA-RECUPERACAO.enc \
     --acknowledge-skipped-paper-trades --apply \
     --public /privado/copia/state.json
   ```

   O backup precisa ser **novo e fora do repositório**. O utilitário verifica a integridade da cifragem, rejeita posições abertas, candles não contíguos ou desatualizados, exige progresso do relógio, faz gravação atômica e registra `RECOVERY_GAP_SKIPPED` no histórico. Saldo/trades passados são preservados, mas **não há replay de candles perdidos nem imputação de stop**. Recomeça a observar novos candles; o intervalo pulado invalida a continuidade integral da amostra prospectiva.
7. **Não há push automático:** qualquer substituição de estado na main exige revisão de backup, reconciliação do último commit remoto, desativação/pausa do workflow e procedimento operacional controlado. Não faça `git push --force` nem `rebase` automático para resolver conflitos. Se não puder auditar os dados, mantenha o bloqueio.

## 2. Migração V2 → V2.1 antes de promoção

- A fixture pública de teste `tests/fixtures/v2_legacy_synthetic_state.enc.txt` foi cifrada **pelo formato original da V2** com chave fictícia; ela valida compatibilidade do esquema antigo (`gap_halt` ausente) e o algoritmo PBKDF2/AES-GCM.
- **Não comprova** que a V2.1 decifra o `state.enc` operacional **com a chave real**. Antes de alterar a main, faça um teste de decifragem somente em cópia privada: confira saldo, trades, posição, `last_bar` e eventos **sem expor valores ou arquivos**. Se falhar, **não promova**.
- Preserve exatamente os secrets atuais. Se o estado V2 foi cifrado usando a chave da Twelve Data, introduzir uma nova `V2_STATE_ENCRYPTION_KEY` sem migração torna esse estado ilegível.
- O artefato criptografado **da main** e o `state.json` público são dinâmicos; jamais mescle versões congeladas vindas de uma branch de testes.

## 3. Promoção (ainda NÃO executada)

- Inspecione o diff, confirme a suite offline no CI e revise os logs de `verify` com `paper=skipped` na branch.
- Pause/suspenda de modo controlado o paper na main, guarde backup cifrado e verifique privadamente a compatibilidade real.
- Promova **somente** código, testes, frontend e documentação, com `v2/state.enc` e `v2/state.json` mantidos da main. Não faça merge cego, porque a branch foi cortada de um estado anterior.
- Nos primeiros ciclos, valide data da última execução, `balance`, `closed_trades`, `last_candle_at`, `gap_halt`, `risk_violations` e reconciliação dos eventos. Tenha rollback de código sem restaurar dados antigos por cima de novos.
- Relate qualquer falha na coleta ou hiato, sem reconstituir ganhos e perdas ausentes artificialmente. Um resultado de paper não fornece garantia de desempenho real.

## 4. Custo/modelo e limites

A entrada ainda é simulada usando a última cotação recebida, spread fixo; custos, ordem intrabar e paradas instantâneas não são replicados. O horário do limite diário é **UTC** (herdado). A recuperação não resolve ausência de cotações, eventos de volatilidade, risco de execução nem gaps de mercado. O correto é registrar as limitações, não converter resultado simulado em expectativa de lucro.
