"""Motor do simulador de paper trading."""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from requests import RequestException

from config import CONFIG
from fetcher import Cotacao, buscar_cotacao_btc_brl
from market_insights import (
    AnalystArticle,
    buscar_historico_btc_brl,
    buscar_sentimento_analistas,
    calcular_previsao,
)
from models import Estado
from simulator import (
    executar_compra,
    executar_venda,
    executar_venda_quantidade,
    registrar_deposito,
    registrar_saque,
)
from strategy import decidir_acao

FUSO_LOCAL = ZoneInfo("America/Sao_Paulo")


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
        self._cotacao_atual: Cotacao | None = None
        self._cotacao_pronta = threading.Event()
        self._parar_cotacao = threading.Event()
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
        self.thread_cotacao = threading.Thread(
            target=self._loop_cotacao, daemon=True
        )

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
                with open(CONFIG["state_file"], encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("O estado precisa ser um objeto JSON.")
                return Estado.from_dict(data)
            except (
                json.JSONDecodeError,
                OSError,
                TypeError,
                UnicodeError,
                ValueError,
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
                with open(CONFIG["history_file"], encoding="utf-8") as f:
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
            with open(CONFIG["cash_flow_file"], encoding="utf-8") as f:
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
            with open(path, encoding="utf-8") as f:
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
            with open(path, encoding="utf-8") as f:
                transaction = json.load(f)
            state = transaction["state"]
            history = transaction["history"]
            if (
                not isinstance(state, dict)
                or not isinstance(history, list)
                or any(not isinstance(trade, dict) for trade in history)
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
            KeyError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            UnicodeError,
            ValueError,
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
        now = (
            timestamp if isinstance(timestamp, datetime) else datetime.now(timezone.utc)
        )
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        if self.equity_history and not force_sample:
            anterior = datetime.fromisoformat(self.equity_history[-1]["timestamp"])
            if anterior.tzinfo is None:
                anterior = anterior.replace(tzinfo=timezone.utc)
            if anterior.astimezone(timezone.utc).replace(
                minute=0, second=0, microsecond=0
            ) == now.replace(minute=0, second=0, microsecond=0):
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
            benchmark_btc = max(0.0, float(previous["benchmark_btc"]) + flow / preco)
        else:
            units = equity
            nav = 1.0 if equity > 0 else 0.0
            benchmark_btc = equity / preco

        self.equity_history.append(
            {
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
            }
        )
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
            remaining = debounce_seconds - (time.monotonic() - requested_at)
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
            raise RuntimeError(
                "A cotação não está atualizada; aguarde uma fonte online."
            )
        if atualizado_em.tzinfo is None:
            atualizado_em = atualizado_em.replace(tzinfo=timezone.utc)
        idade = (
            datetime.now(timezone.utc) - atualizado_em.astimezone(timezone.utc)
        ).total_seconds()
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
            self._pending_equity_flow = (
                getattr(self, "_pending_equity_flow", 0.0) + valor_brl
            )
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
            self._pending_equity_flow = (
                getattr(self, "_pending_equity_flow", 0.0) - valor_brl
            )
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
            Inicia as threads de cotação e de estratégia.
        """
        if not self.thread.is_alive():
            try:
                self.rodando = True
                self._parar_cotacao.clear()
                self.thread_cotacao.start()
                self.thread.start()
            except Exception as exc:
                self.rodando = False
                self._parar_cotacao.set()
                if self.thread_cotacao.is_alive():
                    self.thread_cotacao.join(timeout=2)
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
        self._parar_cotacao.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.thread_cotacao.is_alive():
            self.thread_cotacao.join(timeout=2)

    def _loop_cotacao(self):
        """Atualiza a cotação online sem antecipar o ciclo da estratégia."""
        while self.rodando:
            try:
                cotacao = buscar_cotacao_btc_brl()
                with self.lock:
                    self._cotacao_atual = cotacao if cotacao.atual else None
                    self.preco_atual = cotacao.preco_brl
                    self.fonte_preco = cotacao.fonte
                    self.preco_atualizado = cotacao.atual
                    self.ultimo_update = cotacao.timestamp
                    self.ultimo_erro = ""
                self._cotacao_pronta.set()
            except Exception as exc:
                print(f"[ERRO na cotação] {exc}")
                with self.lock:
                    self._cotacao_atual = None
                    self.preco_atualizado = False
                    self.ultimo_erro = str(exc)
                self._cotacao_pronta.set()

            intervalo = CONFIG.get("intervalo_cotacao_segundos", 5)
            self._parar_cotacao.wait(intervalo)

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
            Atualiza estado de mercado, executa trades automáticos, grava
            estado/históricos e aguarda o intervalo de estratégia em ``CONFIG``.
        """
        while self.rodando:
            if not self._cotacao_pronta.wait(timeout=1):
                continue
            if not self.rodando:
                break
            try:
                with self.lock:
                    cotacao = self._cotacao_atual
                    erro_cotacao = self.ultimo_erro
                if cotacao is None:
                    raise RuntimeError(
                        erro_cotacao or "Cotação online indisponível."
                    )

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
                    except (
                        RequestException,
                        ValueError,
                        KeyError,
                        TypeError,
                        IndexError,
                    ) as exc:
                        erro_analise = f"Histórico indisponível: {exc}"
                        print(f"[AVISO] {erro_analise}")

                    deve_atualizar_analistas = (
                        atualizado_analistas is None
                        or (
                            datetime.now(timezone.utc) - atualizado_analistas
                        ).total_seconds()
                        >= 900
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
                            erro_analise = (
                                f"{erro_analise} Analistas indisponíveis: {exc}".strip()
                            )
                            print(f"[AVISO] {erro_analise}")

                    if historico_cotacao:
                        try:
                            previsao_dia = calcular_previsao(
                                historico_cotacao,
                                cotacao.preco_brl,
                                sentimento_analistas,
                            )
                        except ValueError as exc:
                            erro_analise = (
                                f"{erro_analise} Projeção indisponível: {exc}".strip()
                            )
                            previsao_dia = []

                if not self.rodando:
                    break

                with self.lock:
                    cotacao_operacao = self._cotacao_atual
                    acao = (
                        decidir_acao(self.estado, cotacao_operacao.preco_brl)
                        if (
                            cotacao_operacao is not None
                            and cotacao_operacao.atual
                            and not self.erro_persistencia
                        )
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
                            self.estado,
                            cotacao_operacao.preco_brl,
                            acao["valor_brl"],
                            acao["motivo"],
                        )
                        self._registrar_trade(trade_executado)
                    elif acao["acao"] == "VENDA":
                        trade_executado = executar_venda(
                            self.estado,
                            cotacao_operacao.preco_brl,
                            acao["percentual"],
                            acao["motivo"],
                        )
                        self._registrar_trade(trade_executado)

                    if trade_executado is not None and estado_anterior is not None:
                        self._persistir_trade(estado_anterior, historico_anterior)

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
                            cotacao_operacao.preco_brl
                            if cotacao_operacao is not None
                            else cotacao.preco_brl,
                            cotacao_operacao.timestamp
                            if cotacao_operacao is not None
                            else cotacao.timestamp,
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
