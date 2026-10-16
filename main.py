"""Ponto de entrada da interface gr?fica BTC Paper Trader."""

from formatting import formatar_reais
from gui import AppBTC
from motor import FUSO_LOCAL, MotorSimulador

if __name__ == "__main__":
    app = AppBTC()
    app.mainloop()
