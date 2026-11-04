# Forex Bot Pro V1 — Simulador para iPhone

Protótipo web totalmente local: abra `index.html` em um navegador ou publique-o com GitHub Pages.

## Publicação no GitHub Pages

1. No repositório, abra **Settings → Pages**.
2. Em **Build and deployment**, selecione **Deploy from a branch**.
3. Escolha **main** e **/(root)**; clique em **Save**.
4. Após a implantação, abra: https://veloraspaces0.github.io/forex-bot-pro-v1-demo/

No Safari do iPhone, use **Compartilhar → Adicionar à Tela de Início** para criar um atalho.

## Configuração do simulador

- Par: **EUR/USD**; candles de **15 minutos**.
- Saldo inicial: **US$ 100,00**.
- Risco planejado por trade: **0,5% do saldo**.
- Perda diária: bloqueio de novas entradas após **2%** de queda em relação ao início do dia simulado.
- Estratégia: cruzamentos EMA 9/21, RSI 14 e ATR 14; stop e alvo automáticos.
- Dados salvos via `localStorage` no dispositivo; exportação CSV disponível.
- Os candles são **sintéticos**; bot somente simula operações e **não se conecta a corretoras**.
- O avanço automático depende de a aba permanecer ativa. Sem servidor 24/7.
- O risco de 0,5% é *planejado*, não garantido: gaps simulados podem excedê-lo; comissões, swaps e condições reais não são representados.

## Arquivos

- `index.html`: toda a interface e o motor da simulação, sem dependências externas.

**Aviso:** projeto experimental para estudo e teste, não uma recomendação de investimento e não demonstra rentabilidade com preços reais.
