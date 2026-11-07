# Experimento — Forex Watch V2.2 (somente leitura)

Monitor experimental e isolado em branch, sem modificar a estratégia V2.1 em `main`.

## Por que testar

No dia 04/11/2026, observamos ciclos agendados em:
- 13:49:07 UTC — sucesso
- 14:43:43 UTC — sucesso
- 15:39:24 UTC — sucesso

Intervalos observados ~54,6 min e ~55,7 min, embora o agendamento da estratégia especifique quatro tentativas por hora. GitHub Actions cron é *best effort* e não é execução com tempo real garantido.

A estratégia tinha posição PAPER SELL aberta às 15:39 UTC; a ausência de ciclos não equivale a uma ordem de stop real: somente a simulação avalia o stop quando o motor processa candles.

## Funções desta V2.2 experimental

- Consulta `v2/state.json` **público** da `main` e metadados **públicos** de GitHub Actions.
- Emite `OK`, `ATENCAO` ou `CRITICO` com justificativas e intervalos entre agendamentos.
- ATENCAO para execução/estado >30 min e para cadence >30 min; CRITICO para >75 min, falha na última execução ou `gap_halt`; com posição simulada aberta, estado >35 min é crítico.
- Não lê `state.enc`, não precisa de Twelve Data nem token do GitHub, não cria trades, não altera arquivos, não envia emails/WhatsApp/notificações. **É um teste de diagnóstico**, não um alerta configurado.
- Uma falha no diagnóstico opcional de rede é apenas informativa no CI; os dez testes offline são o gate real.

## Como testar localmente

```bash
python -m unittest discover -s tests -p test_forex_watch_v22.py -v
python experimentos/forex_watch_v22.py --json
```

Não existe tarefa agendada nem notificação automática configurada por esta branch. Nada muda no robô V2.1 ao executar esses comandos. **Não mesclar automaticamente à main.**

## Ressalvas

As faixas de minutos são operacionais e experimentais, não prometem execução de mercado. Um alerta de atraso não implica perda efetiva nem risco limitado. Dados públicos podem estar defasados por cache e pelo GitHub Pages; é usado o GitHub contents API para ler o commit atual, mas o workflow também pode atrasar.

O usuário decide separadamente se deseja monitoramento recorrente/alertas e qual frequência.
