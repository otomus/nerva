"""Container runtime — execute handlers in Docker containers (N-612).

Runs handler logic inside Docker containers via ``docker run``, passing
input as JSON on stdin and collecting output from stdout. Supports
resource limits, network isolation, timeout via container kill, and
per-handler circuit breakers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field

from nerva.context import ExecContext
from nerva.runtime import AgentInput, AgentResult, AgentStatus
from nerva.runtime.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

__all__ = [
    "ContainerRuntime",
    "ContainerConfig",
    "ContainerHandlerConfig",
]

_log = logging.getLogger(__name__)

# -- Constants ---------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: float = 60.0
"""Default timeout for container execution."""

DEFAULT_MEMORY_LIMIT: str = "256m"
"""Default Docker memory limit."""

DEFAULT_CPU_LIMIT: str = "1.0"
"""Default Docker CPU quota (number of CPUs)."""

DEFAULT_NETWORK_MODE: str = "none"
"""Default Docker network mode (isolated by default)."""

MAX_OUTPUT_BYTES: int = 1_048_576  # 1 MB
"""Maximum stdout bytes collected from a container."""


# -- Config ------------------------------------------------------------------


@dataclass(frozen=True)
class ContainerHandlerConfig:
    """Configuration for a single container-based handler.

    Attributes:
        image: Docker image name (e.g. ``"myorg/search:latest"``).
        memory_limit: Docker memory limit (e.g. ``"256m"``).
        cpu_limit: Docker CPU quota as a string (e.g. ``"0.5"``).
        network_mode: Docker network mode (e.g. ``"none"``, ``"bridge"``).
        env: Environment variables passed to the container.
    """

    image: str
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    cpu_limit: str = DEFAULT_CPU_LIMIT
    network_mode: str = DEFAULT_NETWORK_MODE
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerConfig:
    """Global configuration for the container runtime.

    Attributes:
        timeout_seconds: Max execution time per container invocation.
        circuit_breaker: Circuit breaker thresholds applied per handler.
        max_output_bytes: Max stdout bytes to collect before truncation.
        docker_command: Path or name of the docker executable.
    """

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    circuit_breaker: CircuitBreakerConfig | None = None
    max_output_bytes: int = MAX_OUTPUT_BYTES
    docker_command: str = "docker"


# -- Runtime -----------------------------------------------------------------


class ContainerRuntime:
    """Execute handlers inside Docker containers.

    Each handler maps to a Docker image. Input is serialized as JSON to
    the container's stdin; output is collected from stdout. Supports
    memory/CPU limits, network isolation, timeout via container kill,
    and per-handler circuit breakers.

    Args:
        config: Global runtime configuration. Uses defaults when None.
    """

    def __init__(self, config: ContainerConfig | None = None) -> None:
        self._config = config or ContainerConfig()
        self._handlers: dict[str, ContainerHandlerConfig] = {}
        self._breakers: dict[str, CircuitBreaker] = {}

    # -- Registration --------------------------------------------------------

    def register(self, name: str, handler_config: ContainerHandlerConfig) -> None:
        """Register a handler with its Docker container configuration.

        Args:
            name: Unique handler identifier.
            handler_config: Docker image and resource limits.

        Raises:
            ValueError: If *name* is empty or already registered.
            ValueError: If *handler_config.image* is empty.
        """
        if not name:
            raise ValueError("Handler name must not be empty")
        if name in self._handlers:
            raise ValueError(f"Handler '{name}' is already registered")
        if not handler_config.image or not handler_config.image.strip():
            raise ValueError("Container image must not be empty")

        self._handlers[name] = handler_config

    # -- Public API ----------------------------------------------------------

    async def invoke(
        self, handler: str, input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run a handler inside a Docker container.

        Args:
            handler: Handler name (must be registered).
            input: Structured input serialized as JSON to stdin.
            ctx: Execution context for tracing and cancellation.

        Returns:
            AgentResult populated from container stdout, or an error result.
        """
        handler_cfg = self._handlers.get(handler)
        if handler_cfg is None:
            return _not_found_result(handler)

        breaker = self._get_breaker(handler)
        if not breaker.is_allowed():
            return _circuit_open_result(handler)

        ctx.add_event("container.start", handler=handler, image=handler_cfg.image)
        started_at = time.monotonic()

        result = await self._run_container(handler, handler_cfg, input, ctx)

        elapsed = time.monotonic() - started_at
        ctx.add_event(
            "container.end",
            handler=handler,
            status=result.status.value,
            elapsed_seconds=f"{elapsed:.3f}",
        )

        _record_on_breaker(breaker, result.status)
        return result

    async def invoke_chain(
        self, handlers: list[str], input: AgentInput, ctx: ExecContext
    ) -> AgentResult:
        """Run handlers in sequence, piping each output as the next input's message.

        Args:
            handlers: Ordered list of handler names.
            input: Initial input for the first handler.
            ctx: Execution context shared across the chain.

        Returns:
            AgentResult from the last successfully executed handler.

        Raises:
            ValueError: If *handlers* is empty.
        """
        if not handlers:
            raise ValueError("handlers list must not be empty")

        current_input = input
        result = AgentResult(status=AgentStatus.ERROR, handler="", error="no handlers ran")

        for handler_name in handlers:
            result = await self.invoke(handler_name, current_input, ctx)
            if result.status != AgentStatus.SUCCESS:
                return result
            current_input = AgentInput(
                message=result.output,
                args=current_input.args,
                tools=current_input.tools,
                history=current_input.history,
            )

        return result

    async def delegate(
        self, handler: str, input: AgentInput, parent_ctx: ExecContext
    ) -> AgentResult:
        """Invoke a handler from within another handler.

        Args:
            handler: Handler name to delegate to.
            input: Input for the delegated handler.
            parent_ctx: Parent's execution context.

        Returns:
            AgentResult from the delegated handler.
        """
        child_ctx = parent_ctx.child(handler)
        return await self.invoke(handler, input, child_ctx)

    # -- Private: container execution ----------------------------------------

    async def _run_container(
        self,
        handler: str,
        handler_cfg: ContainerHandlerConfig,
        input: AgentInput,
        ctx: ExecContext,
    ) -> AgentResult:
        """Spawn a Docker container, feed stdin, collect stdout.

        Args:
            handler: Handler name.
            handler_cfg: Docker configuration for this handler.
            input: Structured input to serialize as JSON.
            ctx: Execution context.

        Returns:
            AgentResult from parsed container output.
        """
        cmd = _build_docker_command(self._config.docker_command, handler_cfg)
        input_json = json.dumps(asdict(input))

        try:
            stdout, returncode = await self._spawn_and_collect(cmd, input_json)
        except asyncio.TimeoutError:
            return _timeout_result(handler, self._config.timeout_seconds)

        if returncode != 0:
            return _error_result_from_exit(handler, returncode, stdout)

        return _build_success_result(handler, stdout)

    async def _spawn_and_collect(
        self, cmd: list[str], input_json: str
    ) -> tuple[str, int]:
        """Spawn the docker process, write stdin, read stdout with timeout.

        Args:
            cmd: Full docker command with arguments.
            input_json: JSON payload to write to stdin.

        Returns:
            Tuple of (stdout_text, returncode).

        Raises:
            asyncio.TimeoutError: If the container exceeds the timeout.
        """
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                process.communicate(input=input_json.encode()),
                timeout=self._config.timeout_seconds,
            )
        except asyncio.TimeoutError:
            await _kill_process(process)
            raise

        # Truncate oversized output
        if len(stdout_bytes) > self._config.max_output_bytes:
            stdout_bytes = stdout_bytes[: self._config.max_output_bytes]

        returncode = process.returncode if process.returncode is not None else -1
        return stdout_bytes.decode(errors="replace"), returncode

    # -- Circuit breaker helpers ---------------------------------------------

    def _get_breaker(self, handler: str) -> CircuitBreaker:
        """Return the circuit breaker for *handler*, creating one if needed.

        Args:
            handler: Handler name used as the breaker key.

        Returns:
            The CircuitBreaker instance for this handler.
        """
        if handler not in self._breakers:
            self._breakers[handler] = CircuitBreaker(self._config.circuit_breaker)
        return self._breakers[handler]


# -- Pure helpers ------------------------------------------------------------


def _build_docker_command(
    docker_cmd: str, handler_cfg: ContainerHandlerConfig
) -> list[str]:
    """Build the full ``docker run`` command from configuration.

    Args:
        docker_cmd: Docker executable path.
        handler_cfg: Handler-specific container configuration.

    Returns:
        Command list ready for subprocess execution.
    """
    cmd = [
        docker_cmd, "run",
        "--rm",
        "-i",
        f"--memory={handler_cfg.memory_limit}",
        f"--cpus={handler_cfg.cpu_limit}",
        f"--network={handler_cfg.network_mode}",
    ]
    for key, value in handler_cfg.env.items():
        cmd.extend(["-e", f"{key}={value}"])
    cmd.append(handler_cfg.image)
    return cmd


def _record_on_breaker(breaker: CircuitBreaker, status: AgentStatus) -> None:
    """Record success or failure on the circuit breaker.

    Args:
        breaker: The handler's circuit breaker.
        status: The outcome status of the invocation.
    """
    if status == AgentStatus.SUCCESS:
        breaker.record_success()
    else:
        breaker.record_failure()


async def _kill_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess and wait for it to exit.

    Args:
        process: The subprocess to kill.
    """
    try:
        process.kill()
    except ProcessLookupError:
        return
    await process.wait()


def _not_found_result(handler: str) -> AgentResult:
    """Build an error result for an unregistered handler.

    Args:
        handler: The handler name.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        error=f"handler '{handler}' is not registered",
    )


def _circuit_open_result(handler: str) -> AgentResult:
    """Build an error result for a handler whose circuit is open.

    Args:
        handler: The handler that was rejected.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        error=f"circuit open for handler '{handler}'",
    )


def _timeout_result(handler: str, timeout_seconds: float) -> AgentResult:
    """Build a TIMEOUT result.

    Args:
        handler: Handler name.
        timeout_seconds: The timeout that was exceeded.

    Returns:
        AgentResult with TIMEOUT status.
    """
    return AgentResult(
        status=AgentStatus.TIMEOUT,
        handler=handler,
        error=f"container '{handler}' timed out after {timeout_seconds}s",
    )


def _error_result_from_exit(handler: str, returncode: int, output: str) -> AgentResult:
    """Build an ERROR result from a non-zero exit code.

    Args:
        handler: Handler name.
        returncode: Non-zero exit code.
        output: Container stdout.

    Returns:
        AgentResult with ERROR status.
    """
    return AgentResult(
        status=AgentStatus.ERROR,
        handler=handler,
        output=output,
        error=f"container '{handler}' exited with code {returncode}",
    )


def _build_success_result(handler: str, output: str) -> AgentResult:
    """Build a SUCCESS result from container output.

    Attempts to parse JSON from the output for structured data.

    Args:
        handler: Handler name.
        output: Raw container stdout.

    Returns:
        AgentResult with SUCCESS status.
    """
    data: dict[str, str] = {}
    try:
        parsed = json.loads(output.strip())
        if isinstance(parsed, dict):
            data = parsed
    except (json.JSONDecodeError, ValueError):
        pass

    response_text = data.pop("output", "") or data.pop("response", "") or output
    return AgentResult(
        status=AgentStatus.SUCCESS,
        output=response_text,
        data=data,
        handler=handler,
    )
