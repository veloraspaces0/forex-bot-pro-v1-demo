# Auditoria pós-consolidação V2.1 — snapshot de 04/11/2026

## Proveniência

- Código/testes na branch de desenvolvimento `forex-bot-pro-v2-1-consolidation`, commit **`7f2a5cfcf54741f784eb31eb943ee8b497dd9727`**.
- Esta branch de **revisão somente** foi derivada desse commit. Dela removemos unicamente `v2/state.enc` e `v2/__pycache__/paper_bot.cpython-312.pyc`. **Não mesclar esta branch diretamente em `main`**, pois isso poderia apagar o estado cifrado vivo.
- A versão V2 na `main` continua com código anterior e registros simulado intactos.

## Evidências verificadas

- Execução de CI GitHub Actions [41831696974](https://github.com/veloraspaces0/forex-bot-pro-v1-demo/actions/runs/41831696974), commit `7f2a5cfcf54741f784eb31eb943ee8b497dd9727`: job `verify` **success**, **50 testes passaram**, job `paper` **skipped** na branch.
- Suíte executada também localmente com `python -m unittest discover -s tests -q`: **50 testes OK**.
- A suíte testa: V2 legada sintética cifrada via PBKDF2/AES-GCM (chave de teste pública), risco em ambas direções, day-loss com caso elegível/não elegível, candles duplicados em rodadas com série crescente, entrada em fronteira exata, perda acima do risco com flag por trade, resposta HTTP 200 com erro de provedor, TLS, backup exclusivo e recuperação falha-fechada de gaps.
- A fixture criptografada é **sintética**. Não demonstra que a V2.1 conseguiu decifrar o `state.enc` operacional da main com a chave real.

## Novas proteções

1. `v2/recover_gap.py` só executa **offline** em cópias fora do repositório, dry-run padrão e aplicação com backup inédito, reconhecimento do intervalo pulado e checagem dos últimos 40 candles concluídos e contínuos. Recusa posição simulada aberta e não faz push GitHub.
2. `paper_bot.py` exige que uma barra seja posterior à entrada para avaliar saída, trata erros temporários HTTP 200/429/5xx ou TLS, protege estados antigos com flags e registra entradas recusadas por gap.
3. `runner.py` usa writes atômicos para criptografia/summary, exporta `risk_exceeded` e `planned_risk_usd` por trade. O texto privado efêmero também é escrito atomicamente.
4. `v2/index.html` indica risco ultrapassado em cada operação e evento `recovery_paused` com renderização segura `textContent`.
5. Guia `RECUPERACAO_E_PROMOCAO_V21.md` define a recuperação que não cria trades retroativos e os cuidados para promoção preservando histórico cifrado.

## Limitações explícitas

- Nenhum teste prospectivo com API real foi realizado **pela V2.1 consolidada**; os 50 testes são offline.
- A promoção à main ainda requer teste privado da decifragem do estado operacional, backup do blob cifrado, interrupção controlada dos jobs, mudança somente de arquivos de código/testes/docs e verificação dos primeiros ciclos.
- O spread é constante, a janela de perda diária usa UTC, não há modelagem integral de liquidez/execução intrabar nem ordens reais.
- Recuperar um gap saltando intervalo invalida a continuidade prospectiva do período pulado; esse fato é registrado e não se inventam resultados.
- A V2 anterior permanece ativa na `main` até procedimento separado de promoção. Não comparar os snapshots herdados de `v2/state.json` com a execução V2.1, que ainda não existe.
