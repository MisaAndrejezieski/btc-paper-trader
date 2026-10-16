"""
BTC Paper Trader — GUI integrada com o simulador.
O simulador roda em thread separada; a GUI atualiza a cada 2 segundos.
Tudo em um único processo: `python main.py`
"""

import json
import os
import queue
import re
import tempfile
import threading
import time
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo
from tkinter import TclError, filedialog, messagebox
import webbrowser

import customtkinter as ctk
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import FuncFormatter, MaxNLocator
from requests import RequestException

from customtkinter.windows.widgets.appearance_mode.appearance_mode_tracker import (
    AppearanceModeTracker,
)
from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
from config import CONFIG
from fetcher import Cotacao, buscar_cotacao_btc_brl
from market_insights import (
    AnalystArticle,
    buscar_historico_btc_brl,
    buscar_historico_btc_brl_periodo,
    buscar_sentimento_analistas,
    calcular_previsao,
)
from models import Estado
from backtest import simular_backtest
from simulator import (
    executar_compra,
    executar_venda,
    executar_venda_quantidade,
    registrar_deposito,
    registrar_saque,
)
from strategy import decidir_acao
from trading_analytics import (
    calcular_metricas_risco,
    gerar_csv_trades,
)
from theme import (CORES, FONTE_LABEL, FONTE_PEQUENA, FONTE_SUBTITULO,
                   FONTE_TITULO, FONTE_VALOR, FONTE_VALOR_MEDIO)

FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


def formatar_reais(valor: float, sinal: bool = False) -> str:
    """Formata valor monetário como reais no padrão brasileiro.

    Args:
        valor: Montante em BRL.
        sinal: Se verdadeiro, sempre inclui sinal ``+`` ou ``-``.

    Returns:
        str: Valor com ponto de milhar e vírgula decimal.

    Raises:
        ValueError: Se o valor não puder ser formatado numericamente.

    Side Effects:
        Nenhum.
    """
    formato = f"{valor:+,.2f}" if sinal else f"{valor:,.2f}"
    return formato.replace(",", "X").replace(".", ",").replace("X", ".")


# =========================================================================
# MOTOR DO SIMULADOR (roda em thread separada)
# =========================================================================

class MotorSimulador:
    """Coordena cotações, decisões da estratégia, execução e persistência.

    Mantém estado e históricos em memória, executa o ciclo de mercado em uma
    thread daemon e serializa alterações de estado com ``lock``. Arquivos
    configurados em ``CONFIG`` são carregados ao inicializar e escritos por
    substituição atômica durante transações e checkpoints.
    """

    def __init__(self):
        """Carrega dados, recupera journal pendente e prepara a thread do motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se houver conflito de migração ou falha de recovery.
            ValueError: Se arquivos JSON existentes estiverem inválidos.
            OSError: Se operações de migração ou recuperação falharem.

        Side Effects:
            Pode migrar arquivos legados para ``data/`` e regravar estado,
            trades ou fluxo de caixa ao recuperar transação pendente. Não inicia
            a thread até :meth:`iniciar`.
        """
        self._migrar_arquivos_legados()
        self._recuperar_transacao_trade()
        self.estado = self._carregar_estado()
        self.historico = self._carregar_historico()
        self.fluxo_caixa = self._carregar_fluxo_caixa()
        self.equity_history = self._carregar_equity()
        self._pending_equity_flow = 0.0
        self._equity_save_timer = None
        self._equity_save_requested_at = None
        self._ultimo_equity_save_at = None
        self.erro_patrimonio = ""
        self.erro_persistencia = ""
        self.preco_atual = None
        self.fonte_preco = ""
        self.preco_atualizado = False
        self.ultimo_update = None
        self.ultimo_erro = ""
        self.historico_cotacao = []
        self.previsao_dia = []
        self.sentimento_analistas = 0.0
        self.artigos_analistas: list[AnalystArticle] = []
        self.atualizado_mercado = None
        self.atualizado_analistas = None
        self.erro_analise = ""
        self.rodando = True
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    @staticmethod
    def _migrar_arquivos_legados():
        """Move arquivos legados da raiz do projeto para o diretório de dados.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se existirem cópias legada e atual com conteúdos diferentes.
            OSError: Se criação, comparação, remoção ou movimentação de arquivos falhar.

        Side Effects:
            Move os arquivos conhecidos para os caminhos em ``CONFIG``; cópias
            idênticas legadas são removidas e conflitos preservam ambos os arquivos.
        """
        diretorio = os.path.dirname(os.path.abspath(__file__))
        for config_key in (
            "state_file",
            "history_file",
            "trade_journal_file",
            "cash_flow_file",
            "equity_file",
        ):
            destino = os.path.join(diretorio, CONFIG[config_key])
            legado = os.path.join(diretorio, os.path.basename(destino))
            if not os.path.isfile(legado):
                continue
            if os.path.exists(destino):
                with open(legado, "rb") as source, open(destino, "rb") as current:
                    same_content = source.read() == current.read()
                if not same_content:
                    raise RuntimeError(
                        f"Há dados diferentes em {legado} e {destino}. "
                        "Nenhum arquivo foi alterado; resolva a duplicação antes "
                        "de abrir o simulador."
                    )
                os.remove(legado)
                continue
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            os.replace(legado, destino)

    def _carregar_estado(self) -> Estado:
        """Carrega estado JSON existente ou cria estado com capital inicial.

        Args:
            Nenhum.

        Returns:
            Estado: Estado validado e desserializado.

        Raises:
            ValueError: Se o arquivo estiver inválido ou não representar um Estado.
            OSError: É encapsulado em ``ValueError`` com o caminho preservado.

        Side Effects:
            Lê ``CONFIG["state_file"]``; não grava o arquivo existente.
        """
        if os.path.exists(CONFIG["state_file"]):
            try:
                with open(CONFIG["state_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("O estado precisa ser um objeto JSON.")
                return Estado.from_dict(data)
            except (
                json.JSONDecodeError, OSError, TypeError, UnicodeError, ValueError
            ) as exc:
                raise ValueError(
                    f"Não foi possível carregar {CONFIG['state_file']}; "
                    f"o arquivo foi preservado: {exc}"
                ) from exc
        estado = Estado()
        estado.capital_brl = CONFIG["capital_inicial_brl"]
        estado.patrimonio_inicial_brl = CONFIG["capital_inicial_brl"]
        return estado

    def _carregar_historico(self):
        """Carrega e valida a lista de trades persistida.

        Args:
            Nenhum.

        Returns:
            list[dict]: Trades carregados, ou lista vazia se o arquivo não existir.

        Raises:
            ValueError: Se o JSON não for uma lista de objetos.
            OSError: Se a leitura falhar; convertido em ``ValueError`` contextual.

        Side Effects:
            Lê ``CONFIG["history_file"]``.
        """
        if os.path.exists(CONFIG["history_file"]):
            try:
                with open(CONFIG["history_file"], "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list) or any(
                    not isinstance(trade, dict) for trade in data
                ):
                    raise ValueError("O histórico precisa ser uma lista de trades.")
                return data
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
                raise ValueError(
                    f"Não foi possível carregar {CONFIG['history_file']}; "
                    f"o arquivo foi preservado: {exc}"
                ) from exc
        return []

    def _carregar_fluxo_caixa(self):
        """Carrega e valida os depósitos e saques persistidos.

        Args:
            Nenhum.

        Returns:
            list[dict]: Eventos de fluxo de caixa, ou lista vazia se ausente.

        Raises:
            ValueError: Se o conteúdo não for uma lista de objetos.
            OSError: Se a leitura falhar; convertido em ``ValueError`` contextual.

        Side Effects:
            Lê ``CONFIG["cash_flow_file"]``.
        """
        if not os.path.exists(CONFIG["cash_flow_file"]):
            return []
        try:
            with open(CONFIG["cash_flow_file"], "r", encoding="utf-8") as f:
                fluxo_caixa = json.load(f)
            if not isinstance(fluxo_caixa, list) or any(
                not isinstance(evento, dict) for evento in fluxo_caixa
            ):
                raise ValueError("O fluxo de caixa precisa ser uma lista de eventos.")
            return fluxo_caixa
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Não foi possível carregar {CONFIG['cash_flow_file']}; "
                f"o arquivo foi preservado: {exc}"
            ) from exc

    def _carregar_equity(self):
        """Carrega amostras históricas de patrimônio do arquivo configurado.

        Args:
            Nenhum.

        Returns:
            list[dict]: Amostras de equity, ou lista vazia se o arquivo não existir.

        Raises:
            ValueError: Se o JSON não for uma lista de objetos.
            OSError: Se a leitura falhar; convertido em ``ValueError`` contextual.

        Side Effects:
            Lê ``CONFIG["equity_file"]``.
        """
        path = CONFIG["equity_file"]
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or any(
                not isinstance(point, dict) for point in data
            ):
                raise ValueError("O histórico de patrimônio precisa ser uma lista.")
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            raise ValueError(
                f"Não foi possível carregar {path}; o arquivo foi preservado: {exc}"
            ) from exc
        return data

    @staticmethod
    def _salvar_json_atomico(path: str, data, ensure_ascii: bool = True):
        """Grava JSON em temporário e substitui o destino atomicamente.

        Args:
            path: Caminho de destino.
            data: Objeto compatível com a serialização JSON.
            ensure_ascii: Controla escaping de caracteres não ASCII.

        Returns:
            None.

        Raises:
            OSError: Se diretório, escrita, fsync ou substituição falhar.
            TypeError: Se ``data`` contiver valores não serializáveis.
            ValueError: Se a serialização JSON não puder representar os dados.

        Side Effects:
            Cria o diretório pai, grava um temporário, sincroniza os dados e
            substitui o arquivo de destino; temporário incompleto é removido.
        """
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=f".{os.path.basename(path)}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = f.name
                json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass

    def _recuperar_transacao_trade(self):
        """Reaplica ao disco os dados de um journal transacional pendente.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se o journal estiver inválido ou alguma gravação falhar.

        Side Effects:
            Lê ``trade_journal_file``; se válido, grava estado e histórico
            (e fluxo de caixa quando incluído) atomicamente e remove o journal.
        """
        path = CONFIG["trade_journal_file"]
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                transaction = json.load(f)
            state = transaction["state"]
            history = transaction["history"]
            if not isinstance(state, dict) or not isinstance(history, list) or any(
                not isinstance(trade, dict) for trade in history
            ):
                raise ValueError("O diário de recuperação contém dados inválidos.")
            cash_flow = transaction.get("cash_flow")
            if cash_flow is not None and (
                not isinstance(cash_flow, list)
                or any(not isinstance(evento, dict) for evento in cash_flow)
            ):
                raise ValueError("O fluxo de caixa do diário contém dados inválidos.")
            Estado.from_dict(state)
            self._salvar_json_atomico(CONFIG["state_file"], state)
            self._salvar_json_atomico(
                CONFIG["history_file"], history, ensure_ascii=False
            )
            if cash_flow is not None:
                self._salvar_json_atomico(
                    CONFIG["cash_flow_file"], cash_flow, ensure_ascii=False
                )
            os.remove(path)
        except (
            KeyError, json.JSONDecodeError, OSError, TypeError, UnicodeError, ValueError
        ) as exc:
            raise RuntimeError(
                f"Não foi possível recuperar a transação pendente em {path}; "
                f"os arquivos foram preservados: {exc}"
            ) from exc

    def _salvar_estado(self):
        """Persiste o estado atual no caminho configurado.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            OSError: Se a gravação atômica falhar.
            TypeError | ValueError: Se o estado não for serializável em JSON.

        Side Effects:
            Substitui ``CONFIG["state_file"]``.
        """
        self._salvar_json_atomico(CONFIG["state_file"], self.estado.to_dict())

    def _salvar_historico(self):
        """Persiste a lista atual de trades em JSON.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            OSError: Se a gravação atômica falhar.
            TypeError | ValueError: Se o histórico não for serializável.

        Side Effects:
            Substitui ``CONFIG["history_file"]``.
        """
        self._salvar_json_atomico(
            CONFIG["history_file"], self.historico, ensure_ascii=False
        )

    def _salvar_fluxo_caixa(self):
        """Persiste depósitos e saques em JSON.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            OSError: Se a gravação atômica falhar.
            TypeError | ValueError: Se o fluxo não for serializável.

        Side Effects:
            Substitui ``CONFIG["cash_flow_file"]``.
        """
        self._salvar_json_atomico(
            CONFIG["cash_flow_file"], self.fluxo_caixa, ensure_ascii=False
        )

    def _salvar_equity(self):
        """Persiste o histórico integral de amostras de patrimônio.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            OSError: Se a gravação atômica falhar.
            TypeError | ValueError: Se o histórico não for serializável.

        Side Effects:
            Substitui ``CONFIG["equity_file"]``.
        """
        self._salvar_json_atomico(
            CONFIG["equity_file"], self.equity_history, ensure_ascii=False
        )

    def _persistir_trade(self, estado_anterior: Estado, historico_anterior: list):
        """Grava journal, estado e histórico como uma transação recuperável.

        Args:
            estado_anterior: Snapshot para rollback se o journal não puder ser criado.
            historico_anterior: Histórico anterior para rollback pré-journal.

        Returns:
            None.

        Raises:
            OSError: Se persistência parcial deixar transação pendente.
            TypeError | ValueError: Se serialização ou gravação inicial falhar.

        Side Effects:
            Cria ``trade_journal_file``, grava estado e histórico e remove o
            journal após sucesso. Se a fase de journal falhar, restaura memória;
            se arquivos finais falharem, mantém o journal e sinaliza erro.
        """
        path = CONFIG["trade_journal_file"]
        transaction = {
            "state": self.estado.to_dict(),
            "history": self.historico,
        }
        try:
            self._salvar_json_atomico(path, transaction, ensure_ascii=False)
        except (OSError, TypeError, ValueError):
            self.estado = estado_anterior
            self.historico = historico_anterior
            raise

        try:
            self._salvar_estado()
            self._salvar_historico()
            os.remove(path)
        except (OSError, TypeError, ValueError) as exc:
            self.erro_persistencia = (
                f"Trade pendente de gravação; os dados serão recuperados ao "
                f"reiniciar o simulador ({path}): {exc}"
            )
            raise OSError(self.erro_persistencia) from exc

    def _persistir_fluxo_caixa(
        self,
        estado_anterior: Estado,
        fluxo_caixa_anterior: list,
    ):
        """Persiste saldo, trades e fluxo de caixa em uma transação recuperável.

        Args:
            estado_anterior: Snapshot para rollback se o journal não for criado.
            fluxo_caixa_anterior: Fluxo anterior usado para rollback pré-journal.

        Returns:
            None.

        Raises:
            OSError: Se persistência parcial deixar transação pendente.
            TypeError | ValueError: Se serialização ou gravação inicial falhar.

        Side Effects:
            Grava journal, estado, trades e fluxo de caixa; remove o journal
            após sucesso. Mantém-no em falhas de gravação final para recovery.
        """
        path = CONFIG["trade_journal_file"]
        transaction = {
            "state": self.estado.to_dict(),
            "history": self.historico,
            "cash_flow": self.fluxo_caixa,
        }
        try:
            self._salvar_json_atomico(path, transaction, ensure_ascii=False)
        except (OSError, TypeError, ValueError):
            self.estado = estado_anterior
            self.fluxo_caixa = fluxo_caixa_anterior
            raise

        try:
            self._salvar_estado()
            self._salvar_historico()
            self._salvar_fluxo_caixa()
            os.remove(path)
        except (OSError, TypeError, ValueError) as exc:
            self.erro_persistencia = (
                f"Fluxo de caixa pendente de gravação; os dados serão "
                f"recuperados ao reiniciar o simulador ({path}): {exc}"
            )
            raise OSError(self.erro_persistencia) from exc

    def _validar_persistencia(self):
        """Impede novas operações quando há uma transação pendente.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se ``erro_persistencia`` indicar dados pendentes.

        Side Effects:
            Nenhum; apenas consulta o estado do motor.
        """
        if getattr(self, "erro_persistencia", ""):
            raise RuntimeError(
                "Há uma transação pendente de gravação. Reinicie o simulador "
                "para recuperar os arquivos antes de operar novamente."
            )

    def _registrar_amostra_equity(
        self,
        preco: float,
        timestamp,
        force_sample: bool = False,
    ):
        """Atualiza NAV/benchmark em memória e solicita persistência da amostra.

        Args:
            preco: Cotação em BRL/BTC usada para valorar caixa e BTC.
            timestamp: Instante da amostra; datetime ou valor ausente (usa UTC atual).
            force_sample: Se verdadeiro, permite amostra no mesmo minuto e
                persiste sem debounce.

        Returns:
            None.

        Raises:
            ValueError: Se os timestamps previamente gravados forem inválidos.
            OSError: Não é propagado pela persistência normal; fica em
                ``erro_patrimonio``. Falha ao iniciar um timer pode propagar
                ``RuntimeError`` da infraestrutura de threads.

        Side Effects:
            Acrescenta ou limita a lista ``equity_history`` a 15.000 pontos,
            aplica fluxo pendente aos units/NAV e agenda ou executa gravação
            em ``equity_file``.
        """
        if not hasattr(self, "equity_history") or not isfinite(preco) or preco <= 0:
            return
        now = timestamp if isinstance(timestamp, datetime) else datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        if self.equity_history and not force_sample:
            anterior = datetime.fromisoformat(self.equity_history[-1]["timestamp"])
            if anterior.tzinfo is None:
                anterior = anterior.replace(tzinfo=timezone.utc)
            if anterior.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) == now.replace(
                minute=0, second=0, microsecond=0
            ):
                if self.erro_patrimonio:
                    self._persistir_equity(force_sample=True)
                return

        equity = self.estado.capital_brl + self.estado.capital_btc * preco
        flow = getattr(self, "_pending_equity_flow", 0.0)
        if self.equity_history:
            previous = self.equity_history[-1]
            units = float(previous["units"])
            previous_nav = float(previous["nav"])
            if previous_nav > 0:
                units += flow / previous_nav
            if units <= 0 and equity > 0:
                units = equity
            nav = equity / units if units > 0 else 0.0
            benchmark_btc = max(
                0.0, float(previous["benchmark_btc"]) + flow / preco
            )
        else:
            units = equity
            nav = 1.0 if equity > 0 else 0.0
            benchmark_btc = equity / preco

        self.equity_history.append({
            "timestamp": now.isoformat(),
            "price": preco,
            "cash": self.estado.capital_brl,
            "btc": self.estado.capital_btc,
            "equity": equity,
            "nav": nav,
            "units": units,
            "benchmark_btc": benchmark_btc,
            "benchmark": benchmark_btc * preco,
            "flow": flow,
        })
        self.equity_history = self.equity_history[-15000:]
        self._pending_equity_flow = 0.0
        self._persistir_equity(force_sample=force_sample)

    def _agendar_persistencia_equity(self, delay: float):
        """Agenda callback daemon para concluir o debounce do equity.

        Args:
            delay: Atraso em segundos até a tentativa de gravação.

        Returns:
            None.

        Raises:
            RuntimeError: Se o sistema não permitir iniciar o timer.
            ValueError: Se o atraso não for aceito por ``threading.Timer``.

        Side Effects:
            Substitui ``_equity_save_timer`` e inicia uma thread timer.
        """
        timer = threading.Timer(delay, self._finalizar_debounce_equity)
        timer.daemon = True
        self._equity_save_timer = timer
        timer.start()

    def _finalizar_debounce_equity(self):
        """Grava a última amostra quando termina a janela de debounce.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            OSError: Falhas de escrita são registradas em ``erro_patrimonio``,
                não propagadas por :meth:`_persistir_equity`.

        Side Effects:
            Adquire o lock do motor, pode reagendar o timer e grava equity no
            arquivo configurado quando a janela termina.
        """
        lock = getattr(self, "lock", None)
        if lock is None:
            return
        with lock:
            requested_at = getattr(self, "_equity_save_requested_at", None)
            if requested_at is None:
                return
            debounce_seconds = max(
                0.0, float(CONFIG.get("equity_debounce_segundos", 30))
            )
            remaining = debounce_seconds - (
                time.monotonic() - requested_at
            )
            if remaining > 0:
                self._agendar_persistencia_equity(remaining)
                return
            self._equity_save_timer = None
            self._equity_save_requested_at = None
            self._persistir_equity(force_sample=True)

    def _persistir_equity(self, force_sample: bool = False):
        """Persiste equity imediatamente ou após debounce trailing.

        Args:
            force_sample: Se verdadeiro, cancela timer pendente e grava agora.
                Caso contrário, reinicia a marca temporal da janela e garante
                um callback agendado.

        Returns:
            None.

        Raises:
            RuntimeError | ValueError: Se o timer não puder ser criado.
            OSError: Capturado e registrado em ``erro_patrimonio``.

        Side Effects:
            Com debounce, controla timer e grava ``equity_file`` após
            ``equity_debounce_segundos`` sem novas solicitações. Em modo
            forçado atualiza ``_ultimo_equity_save_at`` após sucesso.
        """
        if not force_sample:
            self._equity_save_requested_at = time.monotonic()
            if getattr(self, "_equity_save_timer", None) is None:
                debounce_seconds = max(
                    0.0, float(CONFIG.get("equity_debounce_segundos", 30))
                )
                if debounce_seconds == 0:
                    self._equity_save_requested_at = None
                    return self._persistir_equity(force_sample=True)
                self._agendar_persistencia_equity(debounce_seconds)
            return

        timer = getattr(self, "_equity_save_timer", None)
        if timer is not None:
            timer.cancel()
        self._equity_save_timer = None
        self._equity_save_requested_at = None
        try:
            self._salvar_equity()
        except OSError as exc:
            self.erro_patrimonio = str(exc)
            print(f"[AVISO] Não foi possível salvar a curva de patrimônio: {exc}")
        else:
            self.erro_patrimonio = ""
            self._ultimo_equity_save_at = datetime.now(timezone.utc)

    def _registrar_trade(self, trade):
        """Anexa ao histórico a representação em dicionário de um trade.

        Args:
            trade: Trade com método ``to_dict`` ou ``None``.

        Returns:
            None.

        Raises:
            AttributeError: Se o objeto não possuir ``to_dict``.

        Side Effects:
            Acrescenta um registro a ``self.historico``; não grava em disco.
        """
        if trade is None:
            return
        self.historico.append(trade.to_dict())

    def _validar_cotacao_para_operacao(self) -> float:
        """Valida atualidade e valor da última cotação antes de operar.

        Args:
            Nenhum.

        Returns:
            float: Preço atual em BRL/BTC.

        Raises:
            RuntimeError: Se não houver cotação online atual ou se estiver fora
                da janela configurada.

        Side Effects:
            Uma cotação expirada invalida ``preco_atualizado``.
        """
        atualizado_em = getattr(self, "ultimo_update", None)
        if (
            not self.preco_atualizado
            or self.preco_atual is None
            or not isinstance(atualizado_em, datetime)
        ):
            raise RuntimeError("A cotação não está atualizada; aguarde uma fonte online.")
        if atualizado_em.tzinfo is None:
            atualizado_em = atualizado_em.replace(tzinfo=timezone.utc)
        idade = (datetime.now(timezone.utc) - atualizado_em.astimezone(timezone.utc)).total_seconds()
        if idade < 0 or idade > CONFIG.get("cotacao_max_age_segundos", 600):
            self.preco_atualizado = False
            raise RuntimeError(
                "A cotação está desatualizada; aguarde uma nova cotação online."
            )
        return self.preco_atual

    def comprar_manual(self, valor_brl: float):
        """Executa e persiste uma compra manual sob lock.

        Args:
            valor_brl: Valor bruto em BRL, positivo e não superior ao caixa.

        Returns:
            Trade: Registro da compra simulada.

        Raises:
            RuntimeError: Se a cotação ou persistência não permitir a operação.
            ValueError: Se o valor for inválido ou exceder o caixa.
            OSError: Se a transação falhar ao persistir.

        Side Effects:
            Altera estado e histórico, grava journal/estado/trades e registra
            uma amostra equity forçada.
        """
        with self.lock:
            self._validar_persistencia()
            preco = self._validar_cotacao_para_operacao()
            if not isfinite(valor_brl) or valor_brl <= 0:
                raise ValueError("Informe um valor de compra maior que zero.")
            if valor_brl > self.estado.capital_brl:
                raise ValueError("O valor da compra excede seu saldo em reais.")
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            historico_anterior = list(self.historico)
            trade = executar_compra(self.estado, preco, valor_brl, "manual")
            self._registrar_trade(trade)
            self._persistir_trade(estado_anterior, historico_anterior)
            self._registrar_amostra_equity(
                preco, getattr(self, "ultimo_update", None), force_sample=True
            )
            return trade

    def vender_manual(self, quantidade_btc: float):
        """Executa e persiste uma venda manual de quantidade exata de BTC.

        Args:
            quantidade_btc: Quantidade a vender em BTC.

        Returns:
            Trade: Registro da venda.

        Raises:
            RuntimeError: Se a cotação ou persistência não permitir a operação.
            ValueError: Se a quantidade for inválida ou exceder o saldo BTC.
            OSError: Se a transação falhar ao persistir.

        Side Effects:
            Atualiza estado e histórico, grava transação e registra amostra
            equity forçada.
        """
        with self.lock:
            self._validar_persistencia()
            preco = self._validar_cotacao_para_operacao()
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            historico_anterior = list(self.historico)
            trade = executar_venda_quantidade(
                self.estado, preco, quantidade_btc, "manual"
            )
            self._registrar_trade(trade)
            self._persistir_trade(estado_anterior, historico_anterior)
            self._registrar_amostra_equity(
                preco, getattr(self, "ultimo_update", None), force_sample=True
            )
            return trade

    def depositar_manual(self, valor_brl: float):
        """Registra depósito manual e atualiza o patrimônio.

        Args:
            valor_brl: Valor em reais, finito e positivo.

        Returns:
            CashFlow: Evento de depósito persistido.

        Raises:
            RuntimeError: Se houver persistência pendente.
            ValueError: Se o valor for inválido.
            OSError: Se falhar a persistência transacional.

        Side Effects:
            Atualiza caixa e lista de fluxo, grava journal/estado/fluxos e,
            quando há preço atual, registra amostra equity com fluxo.
        """
        with self.lock:
            self._validar_persistencia()
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            fluxo_caixa_anterior = list(self.fluxo_caixa)
            evento = registrar_deposito(self.estado, valor_brl)
            self.fluxo_caixa.append(evento.to_dict())
            self._persistir_fluxo_caixa(estado_anterior, fluxo_caixa_anterior)
            self._pending_equity_flow = getattr(self, "_pending_equity_flow", 0.0) + valor_brl
            if getattr(self, "preco_atual", None) is not None:
                self._registrar_amostra_equity(
                    self.preco_atual,
                    getattr(self, "ultimo_update", None),
                    force_sample=True,
                )
            return evento

    def sacar_manual(self, valor_brl: float):
        """Registra saque manual sem vender BTC.

        Args:
            valor_brl: Valor em reais, finito, positivo e disponível em caixa.

        Returns:
            CashFlow: Evento de saque persistido.

        Raises:
            RuntimeError: Se houver persistência pendente.
            ValueError: Se valor for inválido ou superior ao caixa.
            OSError: Se falhar a persistência transacional.

        Side Effects:
            Atualiza caixa e lista de fluxo, grava journal/estado/fluxos e,
            quando há preço atual, registra amostra equity com fluxo negativo.
        """
        with self.lock:
            self._validar_persistencia()
            estado_anterior = Estado.from_dict(self.estado.to_dict())
            fluxo_caixa_anterior = list(self.fluxo_caixa)
            evento = registrar_saque(self.estado, valor_brl)
            self.fluxo_caixa.append(evento.to_dict())
            self._persistir_fluxo_caixa(estado_anterior, fluxo_caixa_anterior)
            self._pending_equity_flow = getattr(self, "_pending_equity_flow", 0.0) - valor_brl
            if getattr(self, "preco_atual", None) is not None:
                self._registrar_amostra_equity(
                    self.preco_atual,
                    getattr(self, "ultimo_update", None),
                    force_sample=True,
                )
            return evento

    def iniciar(self):
        """Inicia a thread daemon do ciclo de cotação se ainda não estiver ativa.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se a thread não puder ser iniciada; mensagem fica em
                ``erro_persistencia``.

        Side Effects:
            Inicia ``self.thread``.
        """
        if not self.thread.is_alive():
            try:
                self.thread.start()
            except Exception as exc:
                self.erro_persistencia = f"Não foi possível iniciar o motor: {exc}"
                raise RuntimeError(self.erro_persistencia) from exc

    def parar(self):
        """Sinaliza encerramento do loop e aguarda a thread por até dois segundos.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError: Se a operação de join da thread falhar.

        Side Effects:
            Define ``rodando=False`` e pode bloquear a thread chamadora por
            até dois segundos.
        """
        self.rodando = False
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _loop(self):
        """Executa o ciclo contínuo de cotação, estratégia e persistência.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            As exceções esperadas por iteração são capturadas; falhas ficam em
            ``ultimo_erro`` e invalidam a cotação, sem encerrar o loop.

        Side Effects:
            Consulta fontes online, atualiza estado de mercado, executa trades
            automáticos, grava estado/históricos e aguarda o intervalo definido
            em ``CONFIG`` enquanto ``rodando`` for verdadeiro.
        """
        while self.rodando:
            try:
                cotacao: Cotacao = buscar_cotacao_btc_brl()

                with self.lock:
                    historico_cotacao = self.historico_cotacao
                    previsao_dia = self.previsao_dia
                    atualizado_mercado = self.atualizado_mercado
                    sentimento_analistas = self.sentimento_analistas
                    artigos_analistas = self.artigos_analistas
                    atualizado_analistas = self.atualizado_analistas
                erro_analise = ""
                if cotacao.atual:
                    try:
                        historico_cotacao = buscar_historico_btc_brl()
                        atualizado_mercado = datetime.now(timezone.utc)
                    except (RequestException, ValueError, KeyError, TypeError, IndexError) as exc:
                        erro_analise = f"Histórico indisponível: {exc}"
                        print(f"[AVISO] {erro_analise}")

                    deve_atualizar_analistas = (
                        atualizado_analistas is None
                        or (
                            datetime.now(timezone.utc) - atualizado_analistas
                        ).total_seconds() >= 900
                    )
                    if deve_atualizar_analistas:
                        try:
                            (
                                sentimento_analistas,
                                artigos_analistas,
                                erro_fontes,
                            ) = buscar_sentimento_analistas()
                            atualizado_analistas = datetime.now(timezone.utc)
                            erro_analise = " ".join(
                                part for part in (erro_analise, erro_fontes) if part
                            )
                        except (RuntimeError, RequestException, ValueError) as exc:
                            erro_analise = f"{erro_analise} Analistas indisponíveis: {exc}".strip()
                            print(f"[AVISO] {erro_analise}")

                    if historico_cotacao:
                        try:
                            previsao_dia = calcular_previsao(
                                historico_cotacao,
                                cotacao.preco_brl,
                                sentimento_analistas,
                            )
                        except ValueError as exc:
                            erro_analise = f"{erro_analise} Projeção indisponível: {exc}".strip()
                            previsao_dia = []

                if not self.rodando:
                    break

                with self.lock:
                    acao = (
                        decidir_acao(self.estado, cotacao.preco_brl)
                        if cotacao.atual and not self.erro_persistencia
                        else {"acao": "NADA"}
                    )
                    trade_executado = None
                    estado_anterior = (
                        Estado.from_dict(self.estado.to_dict())
                        if acao["acao"] != "NADA"
                        else None
                    )
                    historico_anterior = list(self.historico)

                    if acao["acao"] == "COMPRA":
                        trade_executado = executar_compra(
                            self.estado, cotacao.preco_brl,
                            acao["valor_brl"], acao["motivo"]
                        )
                        self._registrar_trade(trade_executado)
                    elif acao["acao"] == "VENDA":
                        trade_executado = executar_venda(
                            self.estado, cotacao.preco_brl,
                            acao["percentual"], acao["motivo"]
                        )
                        self._registrar_trade(trade_executado)

                    if trade_executado is not None and estado_anterior is not None:
                        self._persistir_trade(estado_anterior, historico_anterior)

                    self.preco_atual = cotacao.preco_brl
                    self.fonte_preco = cotacao.fonte
                    self.preco_atualizado = cotacao.atual
                    self.ultimo_update = cotacao.timestamp
                    self.ultimo_erro = ""
                    self.historico_cotacao = historico_cotacao
                    self.previsao_dia = previsao_dia
                    self.atualizado_mercado = atualizado_mercado
                    self.sentimento_analistas = sentimento_analistas
                    self.artigos_analistas = artigos_analistas
                    self.atualizado_analistas = atualizado_analistas
                    self.erro_analise = erro_analise
                    if not self.erro_persistencia:
                        if trade_executado is None:
                            self._salvar_estado()
                            self._salvar_historico()
                        self._registrar_amostra_equity(
                            cotacao.preco_brl,
                            cotacao.timestamp,
                            force_sample=trade_executado is not None,
                        )

            except Exception as e:
                print(f"[ERRO no motor] {e}")
                with self.lock:
                    self.ultimo_erro = str(e)
                    self.preco_atualizado = False

            for _ in range(int(CONFIG["intervalo_loop_minutos"] * 60)):
                if not self.rodando:
                    break
                time.sleep(1)


# =========================================================================
# GUI
# =========================================================================

class AppBTC(ctk.CTk):
    """Janela CustomTkinter do paper trader e adaptador entre UI e motor.

    A classe agenda atualizações da interface, representa cotações e patrimônio,
    e delega operações ao :class:`MotorSimulador`. A interface cria e atualiza
    widgets e figuras Matplotlib; o motor mantém a thread de mercado.
    """

    def __init__(self):
        """Constrói a janela, seus painéis, gráficos e instância do motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            RuntimeError | ValueError | OSError: Falhas de abertura dos dados
                são mostradas em diálogo e encerram a janela; erro não é
                propagado após essa recuperação.
            TclError: Se a criação/configuração de widgets falhar fora do
                caminho de tratamento de inicialização do motor.

        Side Effects:
            Cria widgets e figuras, carrega arquivos via ``MotorSimulador``,
            inicia sua thread e agenda o primeiro callback de atualização.
        """
        super().__init__()

        self._after_id = None
        self._ultimo_grafico_renderizado = None
        self._ultimo_patrimonio_renderizado = None
        self._backtest_em_execucao = False
        self._backtest_resultados = queue.Queue()
        self._metricas_cache_key = None
        self._metricas_cache = None
        self._cache_fluxo_key = None
        self._cache_depositos = 0.0
        self._cache_saques = 0.0
        self._toast_after_id = None
        self._closing = False
        self.title("BTC Paper Trader")
        largura_tela = self.winfo_screenwidth()
        altura_tela = self.winfo_screenheight()
        largura_inicial = min(1240, max(760, largura_tela - 80))
        altura_inicial = min(900, max(560, altura_tela - 100))
        self.geometry(f"{largura_inicial}x{altura_inicial}")
        self.minsize(min(760, largura_tela - 40), min(560, altura_tela - 80))
        self.configure(fg_color=CORES["bg_principal"])
        ctk.set_appearance_mode("dark")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.conteudo = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=CORES["verde_escuro"],
            scrollbar_button_hover_color=CORES["verde_matrix"],
        )
        self.conteudo.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self.conteudo.grid_columnconfigure(0, weight=1)
        self.conteudo.grid_rowconfigure(4, weight=3, minsize=330)
        self.conteudo.grid_rowconfigure(6, weight=2, minsize=220)
        self.conteudo.grid_rowconfigure(7, weight=2, minsize=220)
        self.conteudo.grid_rowconfigure(8, weight=2, minsize=220)

        # Motor
        try:
            self.motor = MotorSimulador()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror(
                "Não foi possível abrir os dados do simulador",
                f"Nenhum dado foi substituído.\n\n{exc}",
                parent=self,
            )
            self.destroy()
            return
        self._notified_trade_count = len(self.motor.historico)
        self.motor.iniciar()

        # Grid principal: coluna única, com painéis roláveis
        # ----- LINHA 0: Título -----
        ctk.CTkLabel(
            self.conteudo, text="⚡ BTC PAPER TRADER",
            font=FONTE_TITULO, text_color=CORES["verde_matrix"]
        ).grid(row=0, column=0, pady=(15, 5), sticky="w", padx=20)

        # ----- LINHA 1: Preço + Patrimônio -----
        self.frame_topo = ctk.CTkFrame(self.conteudo, fg_color="transparent")
        self.frame_topo.grid(row=1, column=0, sticky="ew", padx=20, pady=5)
        self.frame_topo.grid_columnconfigure((0, 1), weight=1)

        self.card_preco = self._criar_card(self.frame_topo, "PREÇO BTC", "R$ —", "—")
        self.card_preco.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.card_patrimonio = self._criar_card(self.frame_topo, "PATRIMÔNIO", "R$ —", "—")
        self.card_patrimonio.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # ----- LINHA 2: Posição -----
        self.frame_posicao = ctk.CTkFrame(self.conteudo, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_posicao.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        self.frame_posicao.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(
            self.frame_posicao, text="POSIÇÃO",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=(10, 5))

        self.lbl_brl = self._criar_label_posicao(self.frame_posicao, "BRL", "—", 0, 0)
        self.lbl_btc = self._criar_label_posicao(self.frame_posicao, "BTC", "—", 0, 1)
        self.lbl_preco_medio = self._criar_label_posicao(self.frame_posicao, "PREÇO MÉDIO", "—", 0, 2)
        self.lbl_variacao = self._criar_label_posicao(self.frame_posicao, "VARIAÇÃO", "—", 0, 3)

        # ----- LINHA 3: Trades manuais e fluxo de caixa -----
        self.frame_manual = ctk.CTkFrame(self.conteudo, fg_color=CORES["bg_card"], corner_radius=10)
        self.frame_manual.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.frame_manual.grid_columnconfigure((0, 1), weight=1, uniform="ordem")

        ctk.CTkLabel(
            self.frame_manual, text="TRADES MANUAIS (paper trading)",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(8, 4))

        self.grupo_compra = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_compra.grid(row=1, column=0, sticky="ew", padx=(15, 10), pady=(0, 10))
        self.grupo_compra.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_compra, text="Comprar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_compra = ctk.CTkEntry(
            self.grupo_compra, placeholder_text="ex.: 100,00", width=130
        )
        self.entrada_compra.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_compra, text="Comprar BTC", command=self._comprar_manual,
            fg_color=CORES["verde_matrix"], hover_color=CORES["verde_escuro"], width=130
        ).grid(row=0, column=2, sticky="e")

        self.grupo_venda = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_venda.grid(row=1, column=1, sticky="ew", padx=(10, 15), pady=(0, 10))
        self.grupo_venda.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_venda, text="Vender (BTC)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_venda = ctk.CTkEntry(
            self.grupo_venda, placeholder_text="ex.: 0,0001", width=130
        )
        self.entrada_venda.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_venda, text="Vender BTC", command=self._vender_manual,
            fg_color="#a63d40", hover_color="#7d2d30", width=130
        ).grid(row=0, column=2, sticky="e")

        ctk.CTkLabel(
            self.frame_manual, text="FLUXO DE CAIXA (não é trade)",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=15, pady=(0, 4))

        self.grupo_deposito = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_deposito.grid(row=3, column=0, sticky="ew", padx=(15, 10), pady=(0, 10))
        self.grupo_deposito.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_deposito, text="Depositar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_deposito = ctk.CTkEntry(
            self.grupo_deposito, placeholder_text="ex.: 500,00", width=130
        )
        self.entrada_deposito.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_deposito, text="Depositar", command=self._depositar_manual,
            fg_color=CORES["verde_matrix"], hover_color=CORES["verde_escuro"], width=130
        ).grid(row=0, column=2, sticky="e")

        self.grupo_saque = ctk.CTkFrame(self.frame_manual, fg_color="transparent")
        self.grupo_saque.grid(row=3, column=1, sticky="ew", padx=(10, 15), pady=(0, 10))
        self.grupo_saque.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.grupo_saque, text="Sacar (R$)", font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"]
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.entrada_saque = ctk.CTkEntry(
            self.grupo_saque, placeholder_text="ex.: 200,00", width=130
        )
        self.entrada_saque.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ctk.CTkButton(
            self.grupo_saque, text="Sacar", command=self._sacar_manual,
            fg_color="#a63d40", hover_color="#7d2d30", width=130
        ).grid(row=0, column=2, sticky="e")

        # ----- LINHA 4: Cotação e expectativa de mercado -----
        self.frame_graficos = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_graficos.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        self.frame_graficos.grid_columnconfigure(0, weight=1)
        self.frame_graficos.grid_rowconfigure(1, weight=1, minsize=260)

        ctk.CTkLabel(
            self.frame_graficos,
            text="BTC/BRL · PREÇO OBSERVADO E CENÁRIO",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        self.fig, self.axes = plt.subplots(1, 2, figsize=(12, 3.25), dpi=90)
        self.fig.patch.set_facecolor(CORES["bg_card"])
        self.canvas_graficos = FigureCanvasTkAgg(self.fig, master=self.frame_graficos)
        self.canvas_graficos.get_tk_widget().configure(width=1)
        self.canvas_graficos.get_tk_widget().grid(
            row=1, column=0, sticky="nsew", padx=14, pady=(4, 0)
        )

        self.lbl_previsao_status = ctk.CTkLabel(
            self.frame_graficos,
            text="Aguardando dados de mercado...",
            font=FONTE_LABEL,
            text_color=CORES["verde_pastel"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_previsao_status.grid(
            row=2, column=0, sticky="ew", padx=16, pady=(5, 2)
        )

        self.lbl_modelo_status = ctk.CTkLabel(
            self.frame_graficos,
            text=(
                "Estimativa experimental baseada em momentum recente, "
                "volatilidade observada e sinal limitado de notícias."
            ),
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_modelo_status.grid(
            row=3, column=0, sticky="ew", padx=16, pady=(0, 4)
        )

        # ----- Painel independente de notícias -----
        self.frame_noticias = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_noticias.grid(
            row=5, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_noticias.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_noticias,
            text="NOTÍCIAS E PERSPECTIVAS DE ANALISTAS",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 2))
        self.lbl_noticias_status = ctk.CTkLabel(
            self.frame_noticias,
            text="Aguardando consulta aos feeds...",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
            wraplength=max(400, largura_inicial - 100),
        )
        self.lbl_noticias_status.grid(
            row=1, column=0, sticky="ew", padx=15, pady=(0, 6)
        )
        self.frame_fontes_analistas = ctk.CTkFrame(
            self.frame_noticias, fg_color="transparent"
        )
        self.frame_fontes_analistas.grid(
            row=2, column=0, sticky="ew", padx=12, pady=(0, 8)
        )
        self.frame_fontes_analistas.grid_columnconfigure(0, weight=1)
        self.botoes_fontes_analistas = []
        self.widgets_fontes_analistas = []

        self.frame_controles_backtest = ctk.CTkFrame(
            self.frame_graficos, fg_color="transparent"
        )
        self.frame_controles_backtest.grid(
            row=5, column=0, sticky="ew", padx=15, pady=(0, 10)
        )
        self.frame_controles_backtest.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(
            self.frame_controles_backtest,
            text="BACKTEST",
            font=FONTE_LABEL,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, padx=(0, 8))
        self.periodo_backtest = ctk.CTkOptionMenu(
            self.frame_controles_backtest,
            values=["7 dias", "30 dias", "90 dias", "1 ano"],
            width=115,
        )
        self.periodo_backtest.grid(row=0, column=1, padx=(0, 8))
        self.botao_backtest = ctk.CTkButton(
            self.frame_controles_backtest,
            text="Rodar backtest",
            command=self._rodar_backtest,
            width=130,
            fg_color=CORES["verde_matrix"],
            hover_color=CORES["verde_escuro"],
        )
        self.botao_backtest.grid(row=0, column=2, padx=(0, 10))
        self.lbl_backtest_status = ctk.CTkLabel(
            self.frame_controles_backtest,
            text="Simulação inicia com 50% em BTC e 50% em caixa.",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
        )
        self.lbl_backtest_status.grid(row=0, column=3, sticky="ew")

        # ----- LINHA 6: Resultado do backtest -----
        self.frame_resultado_backtest = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_resultado_backtest.grid(
            row=6, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_resultado_backtest.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_resultado_backtest,
            text="BACKTEST · ESTRATÉGIA VS. BUY-AND-HOLD",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))
        self.fig_backtest, self.ax_backtest = plt.subplots(
            figsize=(11, 2.4), dpi=90
        )
        self.fig_backtest.patch.set_facecolor(CORES["bg_card"])
        self.canvas_backtest = FigureCanvasTkAgg(
            self.fig_backtest, master=self.frame_resultado_backtest
        )
        self.canvas_backtest.get_tk_widget().configure(width=1)
        self.canvas_backtest.get_tk_widget().grid(
            row=1, column=0, sticky="ew", padx=14, pady=(4, 10)
        )
        self.frame_resultado_backtest.grid_remove()

        # ----- LINHA 7: Patrimônio histórico -----
        self.frame_patrimonio = ctk.CTkFrame(
            self.conteudo, fg_color=CORES["bg_card"], corner_radius=10
        )
        self.frame_patrimonio.grid(
            row=7, column=0, sticky="ew", padx=20, pady=(0, 10)
        )
        self.frame_patrimonio.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.frame_patrimonio,
            text="PATRIMÔNIO AO LONGO DO TEMPO",
            font=FONTE_SUBTITULO,
            text_color=CORES["verde_pastel"],
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))
        self.fig_patrimonio, self.ax_patrimonio = plt.subplots(
            figsize=(11, 2.4), dpi=90
        )
        self.fig_patrimonio.patch.set_facecolor(CORES["bg_card"])
        self.canvas_patrimonio = FigureCanvasTkAgg(
            self.fig_patrimonio, master=self.frame_patrimonio
        )
        self.canvas_patrimonio.get_tk_widget().configure(width=1)
        self.canvas_patrimonio.get_tk_widget().grid(
            row=1, column=0, sticky="ew", padx=14, pady=(4, 0)
        )
        self.lbl_patrimonio_status = ctk.CTkLabel(
            self.frame_patrimonio,
            text="A curva será formada conforme novas amostras forem registradas.",
            font=FONTE_PEQUENA,
            text_color=CORES["texto_secundario"],
            anchor="w",
        )
        self.lbl_patrimonio_status.grid(
            row=2, column=0, sticky="ew", padx=16, pady=(0, 8)
        )

        # ----- LINHA 8: Trades + Métricas (lado a lado) -----
        self.frame_baixo = ctk.CTkFrame(self.conteudo, fg_color="transparent")
        self.frame_baixo.grid(row=8, column=0, sticky="nsew", padx=20, pady=10)
        self.frame_baixo.grid_columnconfigure(0, weight=3, uniform="painel")
        self.frame_baixo.grid_columnconfigure(1, weight=2, uniform="painel")
        self.frame_baixo.grid_rowconfigure(0, weight=1)

        # Card trades
        self.card_trades = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_trades.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.card_trades.grid_columnconfigure((0, 1), weight=1)
        self.card_trades.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.card_trades, text="ÚLTIMOS TRADES",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 5))
        ctk.CTkButton(
            self.card_trades,
            text="Exportar CSV",
            command=self._exportar_trades_csv,
            width=105,
            height=28,
            fg_color=CORES["bg_destaque"],
            hover_color=CORES["verde_escuro"],
        ).grid(row=0, column=1, sticky="e", padx=15, pady=(8, 4))

        self.txt_trades = ctk.CTkTextbox(
            self.card_trades, font=FONTE_PEQUENA,
            fg_color=CORES["bg_secundario"],
            text_color=CORES["texto_primario"],
            border_width=0, wrap="none"
        )
        self.txt_trades.grid(
            row=2, column=0, columnspan=2, sticky="nsew", padx=15, pady=(0, 15)
        )
        self.txt_trades.configure(state="disabled")

        # Card métricas
        self.card_metricas = ctk.CTkFrame(self.frame_baixo, fg_color=CORES["bg_card"], corner_radius=10)
        self.card_metricas.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.card_metricas.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            self.card_metricas, text="MÉTRICAS",
            font=FONTE_SUBTITULO, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(10, 5))

        self.metricas_labels = {}
        linhas_metricas = [
            ("compras", "Compras"),
            ("vendas", "Vendas"),
            ("taxas", "Taxas pagas"),
            ("depositos", "Depósitos"),
            ("saques", "Saques"),
            ("capital_aportado", "Capital líquido"),
            ("lucro", "Lucro/prejuízo"),
            ("rentabilidade", "Rentabilidade"),
            ("drawdown", "Drawdown máximo"),
            ("sharpe", "Sharpe anualizado"),
            ("win_rate", "Win rate"),
            ("profit_factor", "Profit factor"),
            ("holding_time", "Tempo médio em posição"),
            ("exposure", "Exposição média em BTC"),
        ]
        for r in range(len(linhas_metricas)):
            self.card_metricas.grid_rowconfigure(r + 1, weight=1)

        for i, (chave, rotulo) in enumerate(linhas_metricas, start=1):
            ctk.CTkLabel(
                self.card_metricas, text=f"{rotulo}:",
                font=FONTE_LABEL, text_color=CORES["texto_secundario"]
            ).grid(row=i, column=0, sticky="w", padx=(15, 10), pady=2)
            lbl = ctk.CTkLabel(
                self.card_metricas, text="—",
                font=FONTE_LABEL, text_color=CORES["texto_primario"]
            )
            lbl.grid(row=i, column=1, sticky="w", padx=(0, 15), pady=2)
            self.metricas_labels[chave] = lbl

        # ----- Status -----
        self.lbl_status = ctk.CTkLabel(
            self, text="Iniciando...",
            font=FONTE_PEQUENA, text_color=CORES["texto_apagado"]
        )
        self.lbl_status.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        self.bind("<Configure>", self._ajustar_largura_textos, add="+")

        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.atualizar_gui()

    # -----------------------------------------------------------------
    def _ajustar_largura_textos(self, event):
        """Ajusta canvases e quebra de texto quando a janela é redimensionada.

        Args:
            event: Evento Tkinter de configuração da janela.

        Returns:
            None.

        Raises:
            TclError: Se algum widget Tcl tiver sido destruído durante o ajuste.
            ValueError: Se os widgets fornecerem dimensões não conversíveis.

        Side Effects:
            Reconfigura dimensões de widgets e larguras de quebra de texto.
        """
        if event.widget is not self:
            return
        self.conteudo.configure(
            width=max(400, self.winfo_width() - 16),
            height=max(300, self.winfo_height() - 60),
        )
        largura = max(400, self.winfo_width() - 48)
        largura_grafico = max(480, self.winfo_width() - 104)
        canvas_grafico = self.canvas_graficos.get_tk_widget()
        if int(canvas_grafico.cget("width")) != largura_grafico:
            canvas_grafico.configure(width=largura_grafico)
        for canvas in (self.canvas_backtest, self.canvas_patrimonio):
            widget = canvas.get_tk_widget()
            if int(widget.cget("width")) != largura_grafico:
                widget.configure(width=largura_grafico)
        self.lbl_previsao_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_modelo_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_noticias_status.configure(
            wraplength=max(400, self.conteudo.winfo_width() - 64)
        )
        self.lbl_status.configure(wraplength=largura)

    def _criar_card(self, parent, titulo, valor, variacao):
        """Cria um cartão de resumo com valor principal e variação.

        Args:
            parent: Widget pai Tkinter.
            titulo: Texto do título.
            valor: Texto inicial do valor.
            variacao: Texto inicial da variação/legenda.

        Returns:
            CTkFrame: Cartão com atributos ``lbl_valor`` e ``lbl_var``.

        Raises:
            TclError: Se a criação ou disposição de widgets falhar.

        Side Effects:
            Cria e posiciona widgets filhos no pai informado.
        """
        card = ctk.CTkFrame(parent, fg_color=CORES["bg_card"], corner_radius=10)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text=titulo, font=FONTE_LABEL, text_color=CORES["verde_pastel"]
        ).grid(row=0, column=0, sticky="w", padx=15, pady=(10, 0))

        lbl_valor = ctk.CTkLabel(
            card, text=valor, font=FONTE_VALOR, text_color=CORES["texto_primario"]
        )
        lbl_valor.grid(row=1, column=0, sticky="w", padx=15, pady=(0, 0))

        lbl_var = ctk.CTkLabel(
            card, text=variacao, font=FONTE_LABEL, text_color=CORES["texto_secundario"]
        )
        lbl_var.grid(row=2, column=0, sticky="w", padx=15, pady=(0, 10))

        card.lbl_valor = lbl_valor
        card.lbl_var = lbl_var
        return card

    def _criar_label_posicao(self, parent, titulo, valor, row, col):
        """Cria rótulo de título e valor para uma coluna de posição.

        Args:
            parent: Widget pai.
            titulo: Legenda do dado.
            valor: Valor textual inicial.
            row: Linha-base da grade.
            col: Coluna da grade.

        Returns:
            CTkLabel: Widget que exibe o valor e pode ser atualizado depois.

        Raises:
            TclError: Se a criação/disposição do widget falhar.

        Side Effects:
            Cria e posiciona um frame e dois rótulos.
        """
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row + 1, column=col, sticky="w", padx=15, pady=(0, 15))

        ctk.CTkLabel(
            frame, text=titulo, font=FONTE_PEQUENA, text_color=CORES["texto_secundario"]
        ).pack(anchor="w")

        lbl = ctk.CTkLabel(
            frame, text=valor, font=FONTE_VALOR_MEDIO, text_color=CORES["texto_primario"]
        )
        lbl.pack(anchor="w")
        return lbl

    def _cancelar_callbacks_pendentes(self):
        """Cancela callbacks Tk agendados por esta janela e seus descendentes.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros Tcl esperados durante destruição são ignorados.

        Side Effects:
            Consulta ``after info`` e cancela timers associados a widgets
            pertencentes à janela.
        """
        try:
            callback_ids = self.tk.splitlist(self.tk.call("after", "info"))
        except TclError:
            return
        widgets = [self]
        for widget in widgets:
            try:
                widgets.extend(widget.winfo_children())
            except TclError:
                continue
        for callback_id in callback_ids:
            try:
                script = self.tk.splitlist(
                    self.tk.call("after", "info", callback_id)
                )[0]
            except (RuntimeError, TclError):
                continue
            owner = next(
                (
                    widget for widget in widgets
                    if script in (widget._tclCommands or ())
                ),
                None,
            )
            if owner is not None:
                try:
                    owner.after_cancel(callback_id)
                except (RuntimeError, TclError):
                    continue

    def _desregistrar_rastreadores_customtkinter(self):
        """Remove esta janela dos rastreadores globais do CustomTkinter.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros de remoção por ausência já são tratados internamente.

        Side Effects:
            Altera listas/flags globais de rastreamento de escala e aparência.
        """
        ScalingTracker.remove_window(self._set_scaling, self)
        ScalingTracker.window_dpi_scaling_dict.pop(self, None)
        if not ScalingTracker.window_widgets_dict:
            ScalingTracker.update_loop_running = False
        try:
            AppearanceModeTracker.app_list.remove(self)
        except ValueError:
            pass
        if not AppearanceModeTracker.app_list:
            AppearanceModeTracker.update_loop_running = False

    def _ao_fechar(self):
        """Executa uma única vez o encerramento ordenado da interface.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            TclError: Se operações de teardown de widgets falharem.
            RuntimeError: Se o motor não puder ser interrompido.

        Side Effects:
            Cancela callbacks, interrompe o motor, fecha figuras Matplotlib e
            destrói a janela.
        """
        if self._closing:
            return
        self._closing = True
        self._after_id = None
        self._toast_after_id = None
        self._desregistrar_rastreadores_customtkinter()
        self._cancelar_callbacks_pendentes()
        self.motor.parar()
        plt.close(self.fig)
        plt.close(self.fig_backtest)
        plt.close(self.fig_patrimonio)
        self.destroy()

    def _exportar_trades_csv(self):
        """Solicita destino ao usuário e exporta trades para CSV com BOM UTF-8.

        Args:
            Nenhum.

        Returns:
            None; cancela silenciosamente se o diálogo não fornecer destino.

        Raises:
            Falhas ``OSError`` de gravação são exibidas em diálogo e tratadas.

        Side Effects:
            Abre diálogo de arquivo, lê histórico sob lock e grava CSV no
            destino escolhido; mostra diálogo de resultado.
        """
        destino = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar histórico de trades",
            defaultextension=".csv",
            filetypes=[("Arquivo CSV", "*.csv")],
            initialfile="trades_btc.csv",
        )
        if not destino:
            return
        try:
            with self.motor.lock:
                csv_texto = gerar_csv_trades(list(self.motor.historico))
            with open(destino, "w", encoding="utf-8-sig", newline="") as arquivo:
                arquivo.write(csv_texto)
        except OSError as exc:
            messagebox.showerror(
                "Não foi possível exportar", str(exc), parent=self
            )
            return
        messagebox.showinfo(
            "Exportação concluída",
            f"Trades exportados para:\n{destino}",
            parent=self,
        )

    def _rodar_backtest(self):
        """Valida condições e inicia o backtest em uma thread auxiliar.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros de rede, parsing ou simulação são capturados pela thread e
            enviados à fila de resultados, não propagados ao callback.

        Side Effects:
            Lê estado sob lock, atualiza widgets e inicia worker daemon que
            consulta histórico CoinGecko e calcula resultados.
        """
        if self._backtest_em_execucao:
            return
        with self.motor.lock:
            preco = (
                self.motor.preco_atual
                if self.motor.preco_atualizado else None
            )
            capital = (
                self.motor.estado.capital_brl
                + self.motor.estado.capital_btc * preco
                if preco is not None else 0.0
            )
        if capital <= 0:
            self.lbl_backtest_status.configure(
                text="Backtest indisponível: aguarde cotação online e patrimônio válido.",
                text_color=CORES["amarelo_pastel"],
            )
            return

        periodo_texto = self.periodo_backtest.get()
        dias = {"7 dias": 7, "30 dias": 30, "90 dias": 90, "1 ano": 365}.get(
            periodo_texto
        )
        if dias is None:
            self.lbl_backtest_status.configure(
                text="Selecione um período de backtest válido.",
                text_color=CORES["amarelo_pastel"],
            )
            return

        self._backtest_em_execucao = True
        self.botao_backtest.configure(state="disabled")
        self.lbl_backtest_status.configure(
            text=f"Buscando {periodo_texto} de dados históricos na CoinGecko...",
            text_color=CORES["texto_secundario"],
        )

        def trabalho():
            """Busca e simula o período em thread de fundo.

            Returns:
                None.

            Side Effects:
                Consulta o histórico remoto e coloca o resultado ou erro na
                fila ``_backtest_resultados`` para consumo pela interface.
            """
            try:
                history = buscar_historico_btc_brl_periodo(dias)
                result = simular_backtest(history, capital)
            except (
                RequestException, OSError, RuntimeError, ValueError,
                KeyError, TypeError, IndexError,
            ) as exc:
                self._backtest_resultados.put((None, str(exc), periodo_texto))
            else:
                self._backtest_resultados.put((result, "", periodo_texto))

        threading.Thread(target=trabalho, daemon=True).start()

    def _finalizar_backtest(self, resultado, erro: str, periodo: str):
        """Atualiza widgets e gráfico com o resultado assíncrono do backtest.

        Args:
            resultado: Dicionário de resultado de ``simular_backtest`` ou ``None``.
            erro: Mensagem de erro; string vazia representa sucesso.
            periodo: Rótulo do período selecionado.

        Returns:
            None.

        Raises:
            KeyError | TypeError | ValueError: Se um resultado de sucesso não
                tiver o formato esperado.
            TclError: Se widgets ou canvas não estiverem disponíveis.

        Side Effects:
            Reabilita o botão, atualiza status, desenha no eixo do backtest e
            solicita renderização do canvas.
        """
        self._backtest_em_execucao = False
        self.botao_backtest.configure(state="normal")
        if erro:
            self.lbl_backtest_status.configure(
                text=f"Backtest não concluído: {erro}",
                text_color=CORES["amarelo_pastel"],
            )
            return
        self.frame_resultado_backtest.grid()
        pontos = resultado["points"]
        datas = [self._para_horario_local(p["timestamp"]) for p in pontos]
        estrategia = [p["strategy"] for p in pontos]
        buy_hold = [p["buy_hold"] for p in pontos]
        eixo = self.ax_backtest
        eixo.clear()
        eixo.set_facecolor(CORES["bg_secundario"])
        eixo.tick_params(colors=CORES["texto_secundario"], labelsize=8)
        eixo.grid(True, alpha=0.16, color=CORES["verde_matrix"])
        eixo.plot(datas, estrategia, color=CORES["verde_matrix"], label="Estratégia")
        eixo.plot(
            datas, buy_hold, color=CORES["azul_pastel"],
            linestyle="--", label="Buy-and-hold",
        )
        eixo.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m", tz=FUSO_LOCAL))
        eixo.xaxis.set_major_locator(
            mdates.AutoDateLocator(minticks=4, maxticks=8, tz=FUSO_LOCAL)
        )
        eixo.yaxis.set_major_formatter(
            FuncFormatter(lambda value, pos: f"R$ {value / 1000:.0f} mil")
        )
        eixo.legend(
            loc="upper left", fontsize=8, frameon=False,
            labelcolor=CORES["texto_secundario"],
        )
        for spine in eixo.spines.values():
            spine.set_color(CORES["verde_escuro"])
        self.fig_backtest.subplots_adjust(left=0.08, right=0.99, top=0.95, bottom=0.18)
        self.canvas_backtest.draw_idle()
        diferenca = resultado["final_strategy_brl"] - resultado["final_buy_hold_brl"]
        self.lbl_backtest_status.configure(
            text=(
                f"{periodo}: estratégia R$ {formatar_reais(resultado['final_strategy_brl'])} "
                f"({resultado['strategy_return_pct']:+.2f}%) · "
                f"buy-and-hold R$ {formatar_reais(resultado['final_buy_hold_brl'])} "
                f"({resultado['buy_hold_return_pct']:+.2f}%) · "
                f"diferença R$ {formatar_reais(diferenca, sinal=True)} · "
                f"{resultado['trade_count']} operações. "
                "Capital simulado 50% BTC/50% caixa; resultado passado não prevê retorno."
            ),
            text_color=CORES["texto_secundario"],
        )

    def _mostrar_notificacao_trade(self, trade: dict):
        """Mostra toast e sinal sonoro para uma operação automática.

        Args:
            trade: Registro de trade com tipo, valor BRL e motivo.

        Returns:
            None.

        Raises:
            TclError: Se a recriação ou disposição do widget também falhar após
                o tratamento do widget antigo destruído.
            ValueError: Se ``valor_brl`` não puder ser convertido em número.

        Side Effects:
            Cria/reconfigura label, toca campainha, cancela toast anterior e
            agenda o desaparecimento após sete segundos.
        """
        def criar_toast():
            """Cria um label novo para exibir a notificação.

            Returns:
                ctk.CTkLabel: Widget de toast associado à janela atual.

            Raises:
                TclError: Se o interpretador Tcl ou a janela não estiverem
                    disponíveis.

            Side Effects:
                Cria um widget filho da janela, sem exibi-lo ainda.
            """
            return ctk.CTkLabel(
                self,
                text="",
                fg_color=CORES["bg_destaque"],
                text_color=CORES["verde_pastel"],
                corner_radius=8,
                font=FONTE_LABEL,
            )

        if not hasattr(self, "lbl_toast"):
            self.lbl_toast = criar_toast()
        tipo = str(trade.get("tipo", "trade")).upper()
        valor = float(trade.get("valor_brl", 0) or 0)
        motivo = str(trade.get("motivo", "")).replace("_", " ")
        texto = f"  🔔 {tipo} automática · {motivo} · R$ {formatar_reais(valor)}  "
        try:
            self.lbl_toast.configure(text=texto)
            self.lbl_toast.place(relx=0.98, rely=0.02, anchor="ne")
        except TclError:
            self.lbl_toast = criar_toast()
            self.lbl_toast.configure(text=texto)
            self.lbl_toast.place(relx=0.98, rely=0.02, anchor="ne")
        self.bell()
        if self._toast_after_id is not None:
            self.after_cancel(self._toast_after_id)
        self._toast_after_id = self.after(7000, self.lbl_toast.place_forget)

    def _criar_link_noticia(self, parent, text: str, url: str, index: int):
        """Cria rótulo de notícia clicável que abre o endereço no navegador.

        Args:
            parent: Widget pai do link.
            text: Texto já formatado da notícia.
            url: Endereço HTTP(S) a abrir quando clicado.
            index: Índice usado para organizar o widget na grade.

        Returns:
            CTkLabel: Rótulo criado.

        Raises:
            TclError: Se a criação/configuração do widget falhar.
            webbrowser.Error: Se o navegador falhar ao abrir ao receber clique.

        Side Effects:
            Cria e posiciona widget; registra callback que abre o navegador.
        """
        label = ctk.CTkLabel(
            parent,
            text=text,
            anchor="w",
            justify="left",
            height=58,
            wraplength=max(400, self.conteudo.winfo_width() - 110),
            fg_color=CORES["bg_secundario"],
            corner_radius=6,
            text_color=CORES["azul_pastel"],
            font=FONTE_PEQUENA,
            cursor="hand2",
        )
        label.grid(row=index, column=0, sticky="ew", padx=3, pady=(0, 5))
        label.bind(
            "<Button-1>",
            lambda _event, article_url=url: webbrowser.open(article_url),
        )
        return label

    @staticmethod
    def _ler_numero(texto: str, tipo: str = "brl") -> float:
        """Interpreta entrada numérica em convenção BRL ou quantidade BTC.

        Args:
            texto: Campo digitado; BRL aceita agrupamento ``.`` e decimal ``,``,
                além de ponto decimal com uma ou duas casas.
            tipo: ``"brl"`` ou ``"btc"``.

        Returns:
            float: Valor numérico sem conversão de unidade.

        Raises:
            ValueError: Se texto, separadores ou tipo forem inválidos ou o
                valor resultante não for finito.

        Side Effects:
            Nenhum.
        """
        texto = texto.strip().replace("R$", "").replace(" ", "")
        if not texto or tipo not in ("brl", "btc"):
            raise ValueError("Informe um número válido.")

        sinal = ""
        if texto[0] in "+-":
            sinal, texto = texto[0], texto[1:]
        if not texto:
            raise ValueError("Informe um número válido.")

        if tipo == "brl":
            if texto.count(",") > 1:
                raise ValueError("Informe um valor em reais válido.")
            if "," in texto:
                inteiro, centavos = texto.split(",", 1)
                if len(centavos) not in (1, 2):
                    raise ValueError("Informe no máximo duas casas decimais em reais.")
                if "." in inteiro and not re.fullmatch(
                    r"\d{1,3}(?:\.\d{3})+", inteiro
                ):
                    raise ValueError("Separadores de milhar inválidos.")
                if "." not in inteiro and inteiro and not inteiro.isdigit():
                    raise ValueError("Informe um valor em reais válido.")
                inteiro = inteiro.replace(".", "") or "0"
                texto = f"{inteiro}.{centavos}"
            elif "." in texto:
                if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", texto):
                    texto = texto.replace(".", "")
                elif (
                    texto.count(".") == 1
                    and len(texto.rsplit(".", 1)[1]) in (1, 2)
                    and all(parte.isdigit() for parte in texto.split("."))
                ):
                    pass
                else:
                    raise ValueError("Informe um valor em reais válido.")
            elif not texto.isdigit():
                raise ValueError("Informe um valor em reais válido.")
        else:
            if "," in texto and "." in texto:
                raise ValueError("Use apenas um separador decimal para BTC.")
            if texto.count(",") > 1 or texto.count(".") > 1:
                raise ValueError("Informe uma quantidade de BTC válida.")
            texto = texto.replace(",", ".")
            if texto.startswith("."):
                texto = "0" + texto
            if texto.endswith(".") or not all(
                parte.isdigit() for parte in texto.split(".")
            ):
                raise ValueError("Informe uma quantidade de BTC válida.")

        valor = float(sinal + texto)
        if not isfinite(valor):
            raise ValueError("Informe um número válido.")
        return valor

    def _comprar_manual(self):
        """Lê o campo de compra e encaminha a ordem ao motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros ``OSError``, ``ValueError`` e ``RuntimeError`` são exibidos
            ao usuário e tratados.

        Side Effects:
            Pode executar e persistir uma compra; limpa o campo e mostra diálogo.
        """
        try:
            valor = self._ler_numero(self.entrada_compra.get(), "brl")
            trade = self.motor.comprar_manual(valor)
            self.entrada_compra.delete(0, "end")
            messagebox.showinfo(
                "Compra simulada",
                f"Compra de R$ {formatar_reais(trade.valor_brl)} registrada "
                f"a R$ {formatar_reais(trade.preco_brl)}/BTC.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível comprar", str(e), parent=self)

    def _vender_manual(self):
        """Lê quantidade BTC e solicita venda manual ao motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros ``OSError``, ``ValueError`` e ``RuntimeError`` são exibidos
            ao usuário e tratados.

        Side Effects:
            Pode executar e persistir venda; limpa o campo e mostra diálogo.
        """
        try:
            quantidade = self._ler_numero(self.entrada_venda.get(), "btc")
            trade = self.motor.vender_manual(quantidade)
            self.entrada_venda.delete(0, "end")
            messagebox.showinfo(
                "Venda simulada",
                f"Venda de {trade.quantidade_btc:.8f} BTC registrada "
                f"a R$ {formatar_reais(trade.preco_brl)}/BTC.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível vender", str(e), parent=self)

    def _depositar_manual(self):
        """Lê valor em BRL e registra depósito manual no motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros ``OSError``, ``ValueError`` e ``RuntimeError`` são exibidos
            ao usuário e tratados.

        Side Effects:
            Pode alterar e persistir caixa/fluxo; limpa o campo e mostra diálogo.
        """
        try:
            valor = self._ler_numero(self.entrada_deposito.get(), "brl")
            evento = self.motor.depositar_manual(valor)
            self.entrada_deposito.delete(0, "end")
            messagebox.showinfo(
                "Depósito registrado",
                f"Depósito de R$ {formatar_reais(evento.valor_brl)} registrado.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível depositar", str(e), parent=self)

    def _sacar_manual(self):
        """Lê valor em BRL e registra saque manual no motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            Erros ``OSError``, ``ValueError`` e ``RuntimeError`` são exibidos
            ao usuário e tratados.

        Side Effects:
            Pode alterar e persistir caixa/fluxo; limpa o campo e mostra diálogo.
        """
        try:
            valor = self._ler_numero(self.entrada_saque.get(), "brl")
            evento = self.motor.sacar_manual(valor)
            self.entrada_saque.delete(0, "end")
            messagebox.showinfo(
                "Saque registrado",
                f"Saque de R$ {formatar_reais(evento.valor_brl)} registrado.",
                parent=self,
            )
        except (OSError, ValueError, RuntimeError) as e:
            messagebox.showerror("Não foi possível sacar", str(e), parent=self)

    @staticmethod
    def _para_horario_local(timestamp: str | datetime) -> datetime:
        """Converte timestamp ISO ou datetime para o fuso de São Paulo.

        Args:
            timestamp: String ISO 8601 ou objeto datetime; valores sem fuso são
                interpretados como UTC.

        Returns:
            datetime: Instante timezone-aware em ``America/Sao_Paulo``.

        Raises:
            ValueError: Se uma string não puder ser interpretada como ISO 8601.
            TypeError: Se o argumento não for string nem datetime.

        Side Effects:
            Nenhum.
        """
        dt = (
            timestamp
            if isinstance(timestamp, datetime)
            else datetime.fromisoformat(timestamp)
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(FUSO_LOCAL)

    # -----------------------------------------------------------------
    def atualizar_gui(self):
        """Atualiza widgets a partir de um snapshot consistente do motor.

        Args:
            Nenhum.

        Returns:
            None.

        Raises:
            TclError: Se widgets forem destruídos durante a atualização.

        Side Effects:
            Adquire brevemente o lock do motor, atualiza painéis/gráficos,
            consome resultados de backtest e agenda nova chamada em dois
            segundos (ou 100 ms se o lock estiver ocupado).
        """
        if self._closing or not self.winfo_exists():
            return

        while True:
            try:
                resultado, erro, periodo = self._backtest_resultados.get_nowait()
            except queue.Empty:
                break
            self._finalizar_backtest(resultado, erro, periodo)

        if not self.motor.lock.acquire(timeout=0.05):
            self._after_id = self.after(100, self.atualizar_gui)
            return
        try:
            estado = self.motor.estado
            historico = list(self.motor.historico)
            fluxo_caixa = list(self.motor.fluxo_caixa)
            equity_history = list(self.motor.equity_history)
            erro_patrimonio = self.motor.erro_patrimonio
            erro_persistencia = self.motor.erro_persistencia
            preco = self.motor.preco_atual
            ultimo_update = self.motor.ultimo_update
            fonte_preco = self.motor.fonte_preco
            preco_atualizado = self.motor.preco_atualizado
            ultimo_erro = self.motor.ultimo_erro
            historico_cotacao = list(self.motor.historico_cotacao)
            previsao_dia = list(self.motor.previsao_dia)
            sentimento_analistas = self.motor.sentimento_analistas
            artigos_analistas = list(self.motor.artigos_analistas)
            atualizado_mercado = self.motor.atualizado_mercado
            atualizado_analistas = self.motor.atualizado_analistas
            erro_analise = self.motor.erro_analise
        finally:
            self.motor.lock.release()

        if preco and estado:
            self._atualizar_tela(
                estado, historico, preco, fonte_preco, fluxo_caixa, equity_history
            )
            novas_operacoes = historico[self._notified_trade_count:]
            for trade in novas_operacoes:
                if trade.get("motivo") not in ("manual", "aporte"):
                    self._mostrar_notificacao_trade(trade)
            self._notified_trade_count = len(historico)
            ts = (
                self._para_horario_local(ultimo_update).strftime("%d/%m %H:%M:%S")
                if ultimo_update else "—"
            )
            if preco_atualizado:
                status = f"● Cotação atualizada: {ts} (Carambeí)  |  Fonte: {fonte_preco}"
                cor_status = CORES["verde_matrix"]
            else:
                status = (
                    f"⚠ Cotação indisponível/desatualizada (última: {ts}, "
                    f"{fonte_preco})  |  Operações bloqueadas"
                )
                cor_status = CORES["amarelo_pastel"]
            if erro_patrimonio:
                status += f"  |  Erro ao salvar curva: {erro_patrimonio}"
                cor_status = CORES["amarelo_pastel"]
            if erro_persistencia:
                status += f"  |  {erro_persistencia}"
                cor_status = CORES["amarelo_pastel"]
            self.lbl_status.configure(text=status, text_color=cor_status)
        else:
            self.lbl_status.configure(
                text=f"⏳ Aguardando cotação online. {ultimo_erro}",
                text_color=CORES["amarelo_pastel"]
            )

        chave_graficos = (
            ultimo_update,
            atualizado_mercado,
            atualizado_analistas,
            len(historico_cotacao),
            len(previsao_dia),
            len(artigos_analistas),
            erro_analise,
        )
        if chave_graficos != self._ultimo_grafico_renderizado:
            self._atualizar_graficos(
                historico_cotacao,
                previsao_dia,
                artigos_analistas,
                sentimento_analistas,
                preco,
                atualizado_mercado,
                atualizado_analistas,
                erro_analise,
            )
            self._ultimo_grafico_renderizado = chave_graficos

        chave_patrimonio = (
            len(equity_history),
            equity_history[-1].get("timestamp") if equity_history else None,
            equity_history[-1].get("equity") if equity_history else None,
        )
        if chave_patrimonio != self._ultimo_patrimonio_renderizado:
            self._atualizar_grafico_patrimonio(equity_history, historico)
            self._ultimo_patrimonio_renderizado = chave_patrimonio

        if not self._closing:
            self._after_id = self.after(2000, self.atualizar_gui)

    def _atualizar_graficos(
        self,
        historico,
        previsao,
        artigos,
        sentimento,
        preco_atual,
        atualizado_mercado,
        atualizado_analistas,
        erro,
    ):
        """Redesenha gráficos de mercado, previsão e painel de notícias.

        Args:
            historico: Pares ``(datetime, preço BRL/BTC)`` observados.
            previsao: Pontos de previsão com timestamp, valor esperado e faixa.
            artigos: Notícias recentes tipadas como ``AnalystArticle``.
            sentimento: Sinal agregado lexical no intervalo aproximado [-1, 1].
            preco_atual: Última cotação BRL/BTC ou ``None``.
            atualizado_mercado: Instante de atualização do histórico, se houver.
            atualizado_analistas: Instante de atualização das notícias, se houver.
            erro: Mensagem de indisponibilidade da análise, se existente.

        Returns:
            None.

        Raises:
            TclError: Se o canvas/widgtes da interface não estiverem disponíveis.
            ValueError: Se dados recebidos tiverem timestamps incompatíveis.

        Side Effects:
            Limpa e redesenha eixos Matplotlib, atualiza canvases e rótulos
            informativos de mercado, previsão e analistas.
        """
        ax_real, ax_previsao = self.axes
        for ax in self.axes:
            ax.clear()
            ax.set_facecolor(CORES["bg_secundario"])
            ax.tick_params(colors=CORES["texto_secundario"], labelsize=8)
            ax.grid(True, alpha=0.16, color=CORES["verde_matrix"])
            ax.yaxis.set_major_formatter(
                FuncFormatter(
                    lambda valor, pos: (
                        f"R$ {valor / 1000:.0f} mil" if pos is not None else ""
                    )
                )
            )
            ax.xaxis.set_major_formatter(
                mdates.DateFormatter("%H:%M", tz=FUSO_LOCAL)
            )
            ax.xaxis.set_major_locator(
                mdates.AutoDateLocator(minticks=3, maxticks=4, tz=FUSO_LOCAL)
            )
            ax.yaxis.set_major_locator(MaxNLocator(4))
            for spine in ax.spines.values():
                spine.set_color(CORES["verde_escuro"])

        ax_real.set_title(
            "COTAÇÃO REAL · ÚLTIMAS 24H",
            color=CORES["verde_pastel"], fontsize=10, loc="left",
        )
        if historico:
            horarios = [ts.astimezone(FUSO_LOCAL) for ts, _ in historico]
            valores = [valor for _, valor in historico]
            ax_real.plot(
                horarios, valores, color=CORES["verde_matrix"],
                linewidth=1.8, label="BTC/BRL",
            )
            ax_real.fill_between(
                horarios, valores, min(valores),
                color=CORES["verde_matrix"], alpha=0.10,
            )
            if preco_atual is not None:
                agora_local = datetime.now(FUSO_LOCAL)
                ax_real.scatter(
                    [agora_local], [preco_atual], color=CORES["amarelo_pastel"],
                    s=24, zorder=3, label="último preço",
                )
        else:
            ax_real.text(
                0.5, 0.5, "Histórico indisponível",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=ax_real.transAxes,
            )

        ax_previsao.set_title(
            "CENÁRIO ESTIMADO · PRÓXIMAS 24H",
            color=CORES["verde_pastel"], fontsize=10, loc="left", pad=9,
        )
        if previsao:
            previsao_local = [p.timestamp.astimezone(FUSO_LOCAL) for p in previsao]
            esperados = [p.expected_brl for p in previsao]
            minimos = [p.low_brl for p in previsao]
            maximos = [p.high_brl for p in previsao]

            if historico:
                contexto = [
                    (ts.astimezone(FUSO_LOCAL), valor)
                    for ts, valor in historico[-6:]
                ]
                ax_previsao.plot(
                    [item[0] for item in contexto],
                    [item[1] for item in contexto],
                    color=CORES["texto_secundario"],
                    linewidth=1.5,
                    linestyle=":",
                    label="real recente",
                    zorder=2,
                )
            ax_previsao.fill_between(
                previsao_local, minimos, maximos,
                color=CORES["azul_pastel"],
                alpha=0.20,
                label="faixa estimada",
                zorder=1,
            )
            preco_final = esperados[-1]
            variacao = (
                (preco_final / preco_atual - 1) * 100
                if preco_atual else 0.0
            )
            cor_cenario = (
                CORES["lucro"] if variacao > 0
                else CORES["prejuizo"] if variacao < 0
                else CORES["azul_pastel"]
            )
            ax_previsao.plot(
                previsao_local, esperados, color=cor_cenario,
                linewidth=2.4, label="cenário central",
                zorder=3,
            )
            inicio = previsao_local[0]
            fim = previsao_local[-1]
            ax_previsao.axvline(
                inicio, color=CORES["amarelo_pastel"],
                linewidth=1, linestyle="--", alpha=0.8,
            )
            if preco_atual is not None:
                ax_previsao.scatter(
                    [inicio], [preco_atual], color=CORES["amarelo_pastel"],
                    edgecolors=CORES["bg_secundario"], linewidths=0.7,
                    s=42, zorder=5, label="agora",
                )
            ax_previsao.scatter(
                [fim], [preco_final], color=cor_cenario, s=34,
                edgecolors=CORES["bg_secundario"], linewidths=0.7,
                zorder=5,
            )
            ax_previsao.annotate(
                f"24h  {variacao:+.1f}%",
                xy=(fim, preco_final),
                xytext=(-8, 10 if variacao >= 0 else -16),
                textcoords="offset points",
                ha="right",
                color=cor_cenario,
                fontsize=8,
                fontweight="bold",
            )
            inicio_contexto = (
                contexto[0][0]
                if historico and contexto else inicio
            )
            ax_previsao.set_xlim(inicio_contexto, fim)
            ax_previsao.xaxis.set_major_formatter(
                mdates.DateFormatter("%Hh", tz=FUSO_LOCAL)
            )
            ax_previsao.xaxis.set_major_locator(
                mdates.HourLocator(interval=6, tz=FUSO_LOCAL)
            )
            ax_previsao.set_xlabel(
                "horário local", color=CORES["texto_secundario"], fontsize=8
            )
            ax_previsao.legend(
                loc="upper left", ncol=1, fontsize=7, frameon=False,
                labelcolor=CORES["texto_secundario"],
            )
            ax_previsao.margins(y=0.18)
            eixo_previsao_status = (
                f"CENÁRIO CENTRAL EM 24H: R$ {formatar_reais(preco_final)} "
                f"({variacao:+.2f}%)   ·   "
                f"FAIXA MODELADA: R$ {formatar_reais(minimos[-1])}–"
                f"R$ {formatar_reais(maximos[-1])}"
            )
            cor_previsao_status = cor_cenario
            faixa_total_pct = (
                (maximos[-1] - minimos[-1]) / preco_atual * 100
                if preco_atual else 0.0
            )
            detalhe_cenario = (
                f"Referência atual: R$ {formatar_reais(preco_atual or 0)}  ·  "
                f"A faixa tem amplitude de {faixa_total_pct:.2f}% do preço atual; "
                "não representa probabilidade nem garantia."
            )
        else:
            ax_previsao.text(
                0.5, 0.5,
                "Ainda não há dados horários suficientes\npara estimar as próximas 24h.",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=ax_previsao.transAxes,
            )
            eixo_previsao_status = "CENÁRIO DE 24H INDISPONÍVEL"
            cor_previsao_status = CORES["amarelo_pastel"]
            detalhe_cenario = (
                "São necessárias ao menos 6 variações horárias válidas. "
                "O preço observado continua disponível no gráfico à esquerda."
            )

        self.fig.subplots_adjust(
            left=0.08, right=0.99, top=0.88, bottom=0.20, wspace=0.16
        )
        self.canvas_graficos.draw_idle()

        if atualizado_analistas:
            horario_analistas = atualizado_analistas.astimezone(
                FUSO_LOCAL
            ).strftime("%d/%m %H:%M")
        else:
            horario_analistas = "sem atualização"
        if atualizado_mercado:
            horario_mercado = atualizado_mercado.astimezone(
                FUSO_LOCAL
            ).strftime("%d/%m %H:%M")
        else:
            horario_mercado = "sem histórico"
        direcao = "positivo" if sentimento > 0.05 else "negativo" if sentimento < -0.05 else "neutro"
        analises_direcionais = sum(
            article.analyst_view and article.sentiment != 0
            for article in artigos
        )
        sentimento_texto = (
            f"sinal das análises de analistas {direcao} ({sentimento:+.2f}, "
            f"{analises_direcionais} matérias direcionais; {horario_analistas})"
        )
        self.lbl_noticias_status.configure(
            text=(
                f"{len(artigos)} notícia(s) recente(s) sobre BTC · "
                f"{analises_direcionais} análise(s) direcional(is) de analistas · "
                f"sinal agregado {direcao} ({sentimento:+.2f}) · "
                f"atualizado: {horario_analistas}. "
                "Amostra limitada aos feeds e últimas 24h; leitura lexical, não consenso."
                + (f" Aviso da consulta: {erro}" if erro and not artigos else "")
            ),
            text_color=(
                CORES["lucro"] if sentimento > 0.05
                else CORES["prejuizo"] if sentimento < -0.05
                else CORES["texto_secundario"]
            ),
        )
        self.lbl_previsao_status.configure(
            text=eixo_previsao_status,
            text_color=cor_previsao_status,
        )
        self.lbl_modelo_status.configure(
            text=(
                f"{detalhe_cenario} Base: momentum das últimas 6h + "
                f"volatilidade recente; {sentimento_texto}. "
                f"Dados de mercado atualizados: {horario_mercado}. "
                "Estimativa experimental, não recomendação."
                + (f" Aviso: {erro}" if erro else "")
            ),
            text_color=CORES["texto_secundario"],
        )

        for widget in self.widgets_fontes_analistas:
            widget.destroy()
        self.widgets_fontes_analistas.clear()
        self.botoes_fontes_analistas.clear()
        if not artigos:
            mensagem_fontes = ctk.CTkLabel(
                self.frame_fontes_analistas,
                text=(
                    "Nenhuma notícia recente sobre BTC foi encontrada nos feeds. "
                    "Eles são consultados periodicamente."
                ),
                font=FONTE_PEQUENA,
                text_color=CORES["texto_apagado"],
                anchor="w",
            )
            mensagem_fontes.grid(
                row=0, column=0, sticky="ew", padx=5
            )
            self.widgets_fontes_analistas.append(mensagem_fontes)
        else:
            for index, article in enumerate(artigos[:5]):
                publicado = article.published_at.astimezone(
                    FUSO_LOCAL
                ).strftime("%d/%m %H:%M")
                if article.analyst_view and article.sentiment:
                    classificacao = (
                        "ANÁLISE DE ANALISTA · "
                        f"tom lexical {article.sentiment:+.2f}"
                    )
                elif article.sentiment:
                    classificacao = (
                        "NOTÍCIA BTC · tendência lexical "
                        f"{'positiva' if article.sentiment > 0 else 'negativa'}"
                    )
                else:
                    classificacao = "NOTÍCIA BTC · sem sinal direcional identificado"
                texto = (
                    f"{classificacao} · {article.publisher} · {publicado}\n"
                    f"{article.title}\n"
                    f"{article.author} · Abrir matéria"
                )
                link = self._criar_link_noticia(
                    self.frame_fontes_analistas, texto, article.url, index
                )
                self.botoes_fontes_analistas.append(link)
                self.widgets_fontes_analistas.append(link)

    def _obter_totais_fluxo_caixa(self, fluxo_caixa):
        """Retorna depósitos/saques somados, reutilizando cache por tamanho.

        Args:
            fluxo_caixa: Lista de eventos com tipo e valor em BRL.

        Returns:
            tuple[float, float]: Totais de depósitos e saques, nessa ordem.

        Raises:
            KeyError: Se um evento precisar ser contado e não tiver tipo/valor.
            TypeError | ValueError: Se o valor de um evento não for numérico.

        Side Effects:
            Atualiza atributos de cache quando o comprimento da lista muda.
        """
        fluxo_key = len(fluxo_caixa)
        if fluxo_key != self._cache_fluxo_key:
            self._cache_depositos = sum(
                float(item["valor_brl"])
                for item in fluxo_caixa if item["tipo"] == "DEPOSITO"
            )
            self._cache_saques = sum(
                float(item["valor_brl"])
                for item in fluxo_caixa if item["tipo"] == "SAQUE"
            )
            self._cache_fluxo_key = fluxo_key
        return self._cache_depositos, self._cache_saques

    def _atualizar_tela(
        self, estado, historico, preco, fonte_preco, fluxo_caixa, equity_history
    ):
        """Atualiza valores, métricas e lista de trades da interface principal.

        Args:
            estado: Snapshot de :class:`Estado`.
            historico: Lista de trades em dicionários.
            preco: Preço BTC/BRL exibido.
            fonte_preco: Identificação da fonte da cotação.
            fluxo_caixa: Eventos de depósitos e saques.
            equity_history: Amostras NAV/equity para métricas e gráfico.

        Returns:
            None.

        Raises:
            KeyError | TypeError | ValueError: Se snapshots não tiverem os
                campos ou valores esperados pelas métricas.
            TclError: Se algum widget tiver sido destruído durante atualização.

        Side Effects:
            Reconfigura rótulos, calcula/cacheia métricas e delega atualização
            do histórico visual de trades.
        """
        capital_brl = estado.capital_brl
        capital_btc = estado.capital_btc
        preco_medio = estado.preco_medio_brl
        depositos, saques = self._obter_totais_fluxo_caixa(fluxo_caixa)
        capital_aportado = estado.patrimonio_inicial_brl + depositos - saques

        valor_btc = capital_btc * preco
        patrimonio = capital_brl + valor_btc
        lucro = patrimonio - capital_aportado
        lucro_pct = (lucro / capital_aportado * 100) if capital_aportado > 0 else 0

        self.card_preco.lbl_valor.configure(
            text=f"R$ {formatar_reais(preco)}"
        )
        self.card_preco.lbl_var.configure(
            text=f"fonte: {fonte_preco}", text_color=CORES["texto_secundario"]
        )

        cor_patrimonio = CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        self.card_patrimonio.lbl_valor.configure(
            text=f"R$ {formatar_reais(patrimonio)}",
            text_color=CORES["texto_primario"]
        )
        self.card_patrimonio.lbl_var.configure(
            text=f"{'▲' if lucro >= 0 else '▼'} {lucro_pct:+.2f}%  "
                 f"(R$ {formatar_reais(lucro, sinal=True)})",
            text_color=cor_patrimonio
        )

        self.lbl_brl.configure(
            text=f"R$ {formatar_reais(capital_brl)}"
        )
        self.lbl_btc.configure(text=f"{capital_btc:.8f}")

        if preco_medio > 0:
            var_pm = (preco - preco_medio) / preco_medio * 100
            cor_pm = CORES["lucro"] if var_pm >= 0 else CORES["prejuizo"]
            self.lbl_preco_medio.configure(
                text=f"R$ {formatar_reais(preco_medio)}",
                text_color=CORES["texto_primario"]
            )
            self.lbl_variacao.configure(text=f"{var_pm:+.2f}%", text_color=cor_pm)
        else:
            self.lbl_preco_medio.configure(text="—", text_color=CORES["texto_primario"])
            self.lbl_variacao.configure(text="—", text_color=CORES["texto_primario"])

        self.metricas_labels["compras"].configure(text=str(estado.total_compras))
        self.metricas_labels["vendas"].configure(text=str(estado.total_vendas))
        self.metricas_labels["taxas"].configure(
            text=f"R$ {formatar_reais(estado.total_taxas_brl)}"
        )
        self.metricas_labels["depositos"].configure(
            text=f"R$ {formatar_reais(depositos)}"
        )
        self.metricas_labels["saques"].configure(
            text=f"R$ {formatar_reais(saques)}"
        )
        self.metricas_labels["capital_aportado"].configure(
            text=f"R$ {formatar_reais(capital_aportado)}"
        )
        self.metricas_labels["lucro"].configure(
            text=f"R$ {formatar_reais(lucro, sinal=True)}",
            text_color=CORES["lucro"] if lucro >= 0 else CORES["prejuizo"]
        )
        self.metricas_labels["rentabilidade"].configure(
            text=f"{lucro_pct:+.2f}%",
            text_color=CORES["lucro"] if lucro_pct >= 0 else CORES["prejuizo"]
        )
        metricas_key = (
            len(historico),
            historico[-1].get("timestamp") if historico else None,
            len(equity_history),
            equity_history[-1].get("timestamp") if equity_history else None,
            equity_history[-1].get("nav") if equity_history else None,
        )
        if metricas_key != self._metricas_cache_key:
            self._metricas_cache = calcular_metricas_risco(
                historico, equity_history
            )
            self._metricas_cache_key = metricas_key
        metricas = self._metricas_cache
        drawdown = metricas["max_drawdown_pct"]
        sharpe = metricas["sharpe_ratio"]
        win_rate = metricas["win_rate_pct"]
        profit_factor = metricas["profit_factor"]
        holding_hours = metricas["average_holding_hours"]
        exposure = metricas["average_exposure_pct"]
        self.metricas_labels["drawdown"].configure(
            text=f"{drawdown:.2f}%" if drawdown is not None else "—"
        )
        self.metricas_labels["sharpe"].configure(
            text=f"{sharpe:.2f}" if sharpe is not None else "—"
        )
        self.metricas_labels["win_rate"].configure(
            text=f"{win_rate:.1f}%" if win_rate is not None else "—"
        )
        self.metricas_labels["profit_factor"].configure(
            text=(
                "∞" if profit_factor == float("inf")
                else f"{profit_factor:.2f}" if profit_factor is not None else "—"
            )
        )
        self.metricas_labels["holding_time"].configure(
            text=(
                f"{holding_hours / 24:.1f} dias"
                if holding_hours is not None else "—"
            )
        )
        self.metricas_labels["exposure"].configure(
            text=f"{exposure:.1f}%" if exposure is not None else "—"
        )

        self._atualizar_trades(historico)

    def _atualizar_grafico_patrimonio(self, equity_history, historico):
        """Desenha equity e benchmark; reduz apenas os pontos apresentados.

        Args:
            equity_history: Lista cronológica de amostras com timestamp,
                patrimônio e benchmark em BRL.
            historico: Trades com timestamp e tipo, usados como marcadores.

        Returns:
            None.

        Raises:
            AssertionError: Em modo debug, se timestamps do histórico não
                estiverem ordenados.
            KeyError | TypeError | ValueError: Se campos de amostra ou datas
                forem inválidos fora dos casos tratados.
            TclError: Se widgets/canvas não estiverem disponíveis.

        Side Effects:
            Limpa e redesenha o eixo de patrimônio, atualiza status e solicita
            renderização. Para mais de 5.000 amostras, plota uma amostra de
            cerca de 500 pontos; os dados de entrada não são alterados.
        """
        eixo = self.ax_patrimonio
        eixo.clear()
        eixo.set_facecolor(CORES["bg_secundario"])
        eixo.tick_params(colors=CORES["texto_secundario"], labelsize=8)
        eixo.grid(True, alpha=0.16, color=CORES["verde_matrix"])
        for spine in eixo.spines.values():
            spine.set_color(CORES["verde_escuro"])

        if not equity_history:
            eixo.text(
                0.5, 0.5, "Curva começa a ser registrada a partir da primeira cotação.",
                ha="center", va="center", color=CORES["texto_apagado"],
                transform=eixo.transAxes,
            )
            self.lbl_patrimonio_status.configure(
                text="Sem amostras registradas ainda; a primeira será salva na próxima cotação."
            )
        else:
            timestamps = [
                self._para_horario_local(point["timestamp"])
                for point in equity_history
            ]
            if __debug__:
                assert timestamps == sorted(timestamps), (
                    "As amostras equity precisam estar ordenadas por timestamp."
                )
            values = [float(point["equity"]) for point in equity_history]
            benchmark = [float(point["benchmark"]) for point in equity_history]
            plot_timestamps = timestamps
            plot_values = values
            plot_benchmark = benchmark
            if len(timestamps) > 5000:
                step = (len(timestamps) + 499) // 500
                sample_indices = list(range(0, len(timestamps), step))
                if sample_indices[-1] != len(timestamps) - 1:
                    sample_indices.append(len(timestamps) - 1)
                plot_timestamps = [timestamps[index] for index in sample_indices]
                plot_values = [values[index] for index in sample_indices]
                plot_benchmark = [benchmark[index] for index in sample_indices]
            eixo.plot(
                plot_timestamps, plot_values, color=CORES["verde_matrix"],
                linewidth=1.8, marker="o",
                markevery=max(1, len(plot_timestamps) // 20),
                markersize=7 if len(plot_timestamps) == 1 else 3.5,
                label="Estratégia",
            )
            eixo.plot(
                plot_timestamps, plot_benchmark, color=CORES["azul_pastel"],
                linestyle="--", linewidth=1.4, marker="s",
                markevery=max(1, len(plot_timestamps) // 20),
                markersize=6 if len(plot_timestamps) == 1 else 3,
                label="Buy-and-hold BTC",
            )
            if len(timestamps) == 1:
                eixo.set_xlim(
                    timestamps[0] - timedelta(hours=6),
                    timestamps[0] + timedelta(hours=6),
                )
                eixo.xaxis.set_major_formatter(
                    mdates.DateFormatter("%H:%M", tz=FUSO_LOCAL)
                )
                eixo.xaxis.set_major_locator(
                    mdates.HourLocator(interval=3, tz=FUSO_LOCAL)
                )
                eixo.annotate(
                    f"1ª amostra · {timestamps[0]:%d/%m %H:%M}",
                    xy=(timestamps[0], values[0]),
                    xytext=(8, 12),
                    textcoords="offset points",
                    color=CORES["verde_pastel"],
                    fontsize=8,
                )
                self.lbl_patrimonio_status.configure(
                    text=(
                        f"1 amostra registrada em {timestamps[0]:%d/%m %H:%M}. "
                        "A linha evolutiva ficará visível após as próximas atualizações horárias."
                    )
                )
            else:
                self.lbl_patrimonio_status.configure(
                    text=(
                        f"{len(timestamps)} amostras · de "
                        f"{timestamps[0]:%d/%m %H:%M} a "
                        f"{timestamps[-1]:%d/%m %H:%M} · registro horário"
                    )
                )
            for trade in historico:
                try:
                    trade_time = self._para_horario_local(trade["timestamp"])
                    index = bisect_left(timestamps, trade_time)
                    if index >= len(timestamps):
                        index = len(timestamps) - 1
                    elif index > 0 and (
                        trade_time - timestamps[index - 1]
                    ) <= (timestamps[index] - trade_time):
                        index -= 1
                except (KeyError, TypeError, ValueError):
                    continue
                if abs((timestamps[index] - trade_time).total_seconds()) > 3600:
                    continue
                is_buy = str(trade.get("tipo", "")).upper() == "COMPRA"
                eixo.scatter(
                    [timestamps[index]], [values[index]],
                    marker="^" if is_buy else "v",
                    color=CORES["verde_matrix"] if is_buy else CORES["prejuizo"],
                    s=32, zorder=4,
                )
            if len(timestamps) > 1:
                eixo.xaxis.set_major_formatter(
                    mdates.DateFormatter("%d/%m %Hh", tz=FUSO_LOCAL)
                )
                eixo.xaxis.set_major_locator(
                    mdates.AutoDateLocator(minticks=4, maxticks=8, tz=FUSO_LOCAL)
                )
            eixo.yaxis.set_major_formatter(
                FuncFormatter(lambda value, pos: f"R$ {value / 1000:.0f} mil")
            )
            eixo.legend(
                loc="upper left", fontsize=8, frameon=False,
                labelcolor=CORES["texto_secundario"],
            )
            self.fig_patrimonio.subplots_adjust(
                left=0.08, right=0.99, top=0.95, bottom=0.18
            )
        self.canvas_patrimonio.draw_idle()

    def _atualizar_trades(self, historico):
        """Renderiza no painel os trades mais recentes.

        Args:
            historico: Trades como dicionários, em ordem cronológica.

        Returns:
            None.

        Raises:
            TclError: Se o widget de texto tiver sido destruído.
            KeyError | TypeError | ValueError: Se os campos usados para
                apresentação não puderem ser formatados.

        Side Effects:
            Substitui o conteúdo do widget de histórico; exibe no máximo os
            quinze registros mais recentes.
        """
        self.txt_trades.configure(state="normal")
        self.txt_trades.delete("1.0", "end")

        if not historico:
            self.txt_trades.insert("end", "Nenhum trade ainda.\n")
        else:
            for t in historico[-15:][::-1]:
                tipo = t.get("tipo", "?")
                preco = float(t.get("preco_brl", 0) or 0)
                valor = float(t.get("valor_brl", 0) or 0)
                motivo = str(t.get("motivo", "")).strip() or "-"
                ts_raw = str(t.get("timestamp", "")).strip()
                try:
                    ts = self._para_horario_local(ts_raw).strftime("%d/%m %H:%M:%S")
                except ValueError:
                    ts = "-"

                sinal = "[+]" if tipo == "COMPRA" else "[-]"
                self.txt_trades.insert(
                    "end",
                    f"{sinal} {ts:<19}  {motivo:<14}  "
                    f"R$ {formatar_reais(valor):>10}  @ R$ {formatar_reais(preco)}\n"
                )

        self.txt_trades.configure(state="disabled")

# =========================================================================
# PONTO DE ENTRADA
# =========================================================================

if __name__ == "__main__":
    app = AppBTC()
    app.mainloop()