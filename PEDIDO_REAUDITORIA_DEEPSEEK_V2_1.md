# PEDIDO DE REAUDITORIA INDEPENDENTE — FOREX BOT PRO V2.1

Você realizou uma auditoria adversarial da V2. Agora envio um pacote **integral de código da V2.1** para reavaliar os bloqueadores anteriores. Leia `LEIA_PRIMEIRO_AUDITORIA_DEEPSEEK.md`, os arquivos originais, todos os testes e `EVIDENCIAS/`. Não altere o código.

## Tarefa

1. Compare V2 com V2.1, usando o commit-base V2 `aaa71a728c4d1dee8c9aef0eb1c65ee2a85da271` e o snapshot testado da V2.1 `10c4aa78f3d48ee427152b715f8046fec18c9f92`. Separe precisamente correções, regressões e limitações assumidas.
2. Reavalie **cada achado da auditoria anterior**: matemática de risco e pip para EUR/USD, equity versus balance, stop/TP sob gaps, idempotência `last_bar`, sinal só com candle fechado, saída não retroativa, lacunas incluindo fim de semana, concorrência e git push, escrita atômica do estado cifrado e do resumo, compatibilidade AES-GCM com estado legado, rotação de chave, 429/5xx e qualidade dos dados Twelve Data, XSS e integridade do painel, custos simulados e definição de dia UTC.
3. Inspecione os logs reais anexados: **Pages falhou com HTTP 502, mas um deploy posterior terminou com sucesso**; não atribua falha a permissões sem evidência. Diferencie a execução V2 ativa dos testes V2.1 offline.
4. Valide o agendamento e a proteção de branch: `paper` só deve usar API e escrever estado na main. Faça revisão de alcance de permissões, condições, dependências e controles de corrida; não confunda workflow de paper com o workflow do GitHub Pages.
5. Execute os 32 testes, se possível. Compare com as saídas locais e do GitHub Actions fornecidas. Crie testes adversariais adicionais para tentar quebrar correções **aparentemente** verdes: transições de candles, tempo UTC exato, gaps na inicialização e depois dela, operações repetidas, JSON malformado, ciphertext truncado, exceções no `os.replace`, saldo negativo, sobreposição de jobs e recusa do Git push.
6. Quantifique riscos: a perda nominal de US$0,50 é apenas **planejada**. Uma perda pior por gap ou slippage deve ser registrada, não artificialmente capada. Para EUR/USD com conta USD, verifique unidades EUR × variação em USD/EUR, sem inventar conversões desnecessárias.
7. Identifique se a candidata é realmente segura para **paper trading prospectivo**; se não, indique exatamente o bloqueador comprovado. Para dinheiro real, mantenha critério independente: testes verdes não garantem segurança nem rentabilidade.

## Entrega esperada

- Veredito separado para **demonstração**, **paper prospectivo** e **dinheiro real**.
- Tabela de achados com ID, severidade, código/linha exata, passo a passo da reprodução, impacto e correção mínima.
- Diferenças concretas entre as versões; verificação de cada problema anteriormente relatado como **corrigido**, **parcial**, **persistente**, **não comprovado** ou **não aplicável**.
- Demonstrações numéricas, testes reproduzíveis e patches em diff unificado se necessário.
- Testes de fato executados versus apenas propostos (sem inventar).
- Bloqueadores para promoção da V2.1, riscos residuais, limites de dados e recomendação de estratégia de deployment seguro que preserve `state.enc` vivo na main.
- Evite julgamentos por nomes de variáveis: avalie comportamento observável e seja crítico também com a própria auditoria anterior.

**Restrições:** não solicitar secrets, não requerer estado cifrado real, não executar ordens, não alterar repositório, não aprovar dinheiro real com base em poucas operações. `v2/state.json` nesta cópia é a saída pública da V2 herdada da main — **não é uma prova de operação prospectiva da V2.1**.
