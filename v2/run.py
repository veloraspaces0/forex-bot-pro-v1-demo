"""Compatibilidade: redireciona a execução V2 para o runner com dados criptografados.

Nao salva candles ou operacoes brutas em JSON publico.
"""
from runner import main

if __name__ == "__main__":
    main()
