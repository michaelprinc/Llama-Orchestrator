"""
llama-orchestrator CLI - Typer-based command line interface.

Commands:
    up <name>           Start an instance
    down <name>         Stop an instance
    restart <name>      Restart an instance
    ps                  List all instances
    health <name>       Check instance health
    logs <name>         View instance logs
    describe <name>     Show full config + status
    dashboard           Live TUI dashboard
    config validate     Validate configuration
    daemon start        Start background daemon
    binary install      Install llama.cpp binary
    binary list         List installed binaries
    binary info         Show binary details
    binary remove       Remove a binary
    binary latest       Show latest available version
    gui                  Launch Windows desktop GUI
"""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from llama_orchestrator import __version__
from llama_orchestrator.cli_exit_codes import ExitCode

# Initialize Typer app
app = typer.Typer(
    name="llama-orch",
    help=(
        "Docker-like CLI orchestration for llama.cpp server instances. "
        "Standard exit codes: 2 usage, 10-19 config, 20-39 instance/process, 50-69 binary/daemon."
    ),
    add_completion=False,
    no_args_is_help=True,
)

# Sub-apps for grouped commands
config_app = typer.Typer(help="Configuration management")
daemon_app = typer.Typer(help="Daemon management")
binary_app = typer.Typer(help="Binary version management")

app.add_typer(config_app, name="config")
app.add_typer(daemon_app, name="daemon")
app.add_typer(binary_app, name="binary")

# Rich console for pretty output
console = Console()


def _raise_exit(code: ExitCode) -> None:
    """Raise a Typer exit with a standard exit code."""
    raise typer.Exit(int(code))


def _process_error_code(message: str, default: ExitCode) -> ExitCode:
    """Map common process error messages to standard exit codes."""
    lower_message = message.lower()
    if "already running" in lower_message:
        return ExitCode.INSTANCE_ALREADY_RUNNING
    if "not found" in lower_message:
        return ExitCode.INSTANCE_NOT_FOUND
    if "port" in lower_message and ("in use" in lower_message or "already used" in lower_message):
        return ExitCode.PORT_IN_USE
    return default


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        console.print(f"llama-orchestrator v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-v", callback=version_callback, is_eager=True),
    ] = None,
) -> None:
    """
    llama-orchestrator: Manage multiple llama.cpp server instances.

    Standard exit codes: 2 usage, 10-19 config, 20-39 instance/process,
    50-69 binary/daemon.
    """
    pass


# =============================================================================
# Instance Management Commands
# =============================================================================


@app.command()
def up(
    name: Annotated[str, typer.Argument(help="Instance name to start")],
    detach: Annotated[bool, typer.Option("--detach/--no-detach", "-d", help="Run in background")] = True,
) -> None:
    """
    Start a llama.cpp server instance.
    
    Example:
        llama-orch up gpt-oss
    """
    from llama_orchestrator.config import get_instance_config
    from llama_orchestrator.engine import (
        ProcessError,
        build_command,
        format_command,
        start_instance,
        validate_executable,
    )
    from rich.panel import Panel
    
    # Load config first for UUID-aware binary resolution
    try:
        config = get_instance_config(name)
    except FileNotFoundError:
        console.print(Panel(
            f"[red]Instance '{name}' not found.[/red]\n\n"
            "Check that the instance config exists in instances/{name}/config.json",
            title="❌ Instance Not Found",
            border_style="red"
        ))
        _raise_exit(ExitCode.INSTANCE_NOT_FOUND)
    
    # Check executable with config for UUID-based resolution
    exe_valid, exe_msg = validate_executable(config)
    if not exe_valid:
        console.print(Panel(
            f"[red]{exe_msg}[/red]\n\n"
            "Please install a binary with 'llama-orch binary install'\n"
            "or ensure llama-server.exe is in the bin/ directory.",
            title="❌ Executable Not Found",
            border_style="red"
        ))
        _raise_exit(ExitCode.BINARY_NOT_FOUND)
    
    console.print(f"[green]Starting instance:[/green] {name}")
    
    try:
        state = start_instance(name, detach=detach)
        console.print(Panel(
            f"[green]Instance '{name}' started successfully![/green]\n\n"
            f"PID: {state.pid}\n"
            f"Status: {state.status.value}\n"
            f"Health: {state.health.value} (loading...)",
            title="✅ Instance Started",
            border_style="green"
        ))
        console.print("\n[dim]Check status with:[/dim] [cyan]llama-orch ps[/cyan]")
        console.print("[dim]View logs with:[/dim] [cyan]llama-orch logs " + name + "[/cyan]")
    except ProcessError as e:
        console.print(Panel(
            f"[red]Failed to start instance:[/red]\n{e.message}",
            title="❌ Start Failed",
            border_style="red"
        ))
        _raise_exit(_process_error_code(e.message, ExitCode.PROCESS_START_FAILED))


@app.command()
def down(
    name: Annotated[str, typer.Argument(help="Instance name to stop")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Force stop")] = False,
) -> None:
    """
    Stop a llama.cpp server instance.
    
    Example:
        llama-orch down gpt-oss
        llama-orch down gpt-oss --force
    """
    from llama_orchestrator.engine import ProcessError, stop_instance
    from rich.panel import Panel
    
    console.print(f"[red]Stopping instance:[/red] {name}")
    
    try:
        state = stop_instance(name, force=force)
        console.print(Panel(
            f"[green]Instance '{name}' stopped successfully![/green]\n\n"
            f"Status: {state.status.value}",
            title="✅ Instance Stopped",
            border_style="green"
        ))
    except ProcessError as e:
        console.print(Panel(
            f"[red]Failed to stop instance:[/red]\n{e.message}",
            title="❌ Stop Failed",
            border_style="red"
        ))
        _raise_exit(_process_error_code(e.message, ExitCode.PROCESS_STOP_FAILED))


@app.command()
def restart(
    name: Annotated[str, typer.Argument(help="Instance name to restart")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Force stop before restart")] = False,
) -> None:
    """
    Restart a llama.cpp server instance.
    
    Example:
        llama-orch restart gpt-oss
    """
    from llama_orchestrator.engine import ProcessError, restart_instance
    from rich.panel import Panel
    
    console.print(f"[blue]Restarting instance:[/blue] {name}")
    
    try:
        state = restart_instance(name, force=force)
        console.print(Panel(
            f"[green]Instance '{name}' restarted successfully![/green]\n\n"
            f"PID: {state.pid}\n"
            f"Restart count: {state.restart_count}",
            title="✅ Instance Restarted",
            border_style="green"
        ))
    except ProcessError as e:
        console.print(Panel(
            f"[red]Failed to restart instance:[/red]\n{e.message}",
            title="❌ Restart Failed",
            border_style="red"
        ))
        _raise_exit(_process_error_code(e.message, ExitCode.PROCESS_START_FAILED))


@app.command()
def ps(
    all_instances: Annotated[
        bool, typer.Option("--all", "-a", help="Show all instances including stopped")
    ] = False,
) -> None:
    """
    List all llama.cpp server instances.
    
    Example:
        llama-orch ps
        llama-orch ps --all
    """
    from llama_orchestrator.engine import InstanceStatus, list_instances
    
    instances = list_instances()
    
    if not instances:
        console.print("[dim]No instances configured.[/dim]")
        console.print("Use 'llama-orch init <name> --model <path>' to create one.")
        return
    
    # Filter if not showing all
    if not all_instances:
        instances = {
            k: v for k, v in instances.items() 
            if v.status != InstanceStatus.STOPPED
        }
        if not instances:
            console.print("[dim]No running instances.[/dim]")
            console.print("Use 'llama-orch ps --all' to show all instances.")
            return
    
    table = Table(title="llama-orchestrator Instances")
    
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("PID", style="magenta")
    table.add_column("Port", style="green")
    table.add_column("Backend", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Health", style="bold")
    table.add_column("Uptime", style="dim")
    
    for name, state in sorted(instances.items()):
        # Try to get config for port/backend info
        port = "-"
        backend = "-"
        try:
            from llama_orchestrator.config import get_instance_config
            config = get_instance_config(name)
            port = str(config.server.port)
            backend = config.gpu.backend
        except Exception:
            pass
        
        # Status styling
        status_style = {
            InstanceStatus.RUNNING: "green",
            InstanceStatus.STARTING: "yellow",
            InstanceStatus.STOPPING: "yellow",
            InstanceStatus.STOPPED: "dim",
            InstanceStatus.ERROR: "red",
        }.get(state.status, "white")
        
        status_text = f"[{status_style}]{state.status_symbol} {state.status.value}[/{status_style}]"
        health_text = f"{state.health_symbol} {state.health.value}"
        
        table.add_row(
            name,
            str(state.pid) if state.pid else "-",
            port,
            backend,
            status_text,
            health_text,
            state.uptime_str,
        )
    
    console.print(table)


@app.command()
def health(
    name: Annotated[str | None, typer.Argument(help="Instance name to check")] = None,
    all_instances: Annotated[
        bool, typer.Option("--all", "-a", help="Check all instances")
    ] = False,
    watch: Annotated[
        bool, typer.Option("--watch", "-w", help="Continuously monitor health")
    ] = False,
) -> None:
    """
    Check health status of an instance.
    
    Example:
        llama-orch health gpt-oss
        llama-orch health --all
    """
    from llama_orchestrator.config import discover_instances, get_instance_config
    from llama_orchestrator.health import check_instance_health
    from rich.panel import Panel
    
    if name is None and not all_instances:
        console.print("[yellow]Specify an instance name or use --all[/yellow]")
        _raise_exit(ExitCode.USAGE_ERROR)
    
    instances_to_check = [name] if name else [n for n, _ in discover_instances()]
    
    if not instances_to_check:
        console.print("[dim]No instances found.[/dim]")
        return
    
    table = Table(title="Health Status")
    table.add_column("Instance", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Response Time", style="dim")
    table.add_column("Details", style="dim")
    
    for inst_name in instances_to_check:
        try:
            result = check_instance_health(inst_name)
            
            # Status styling
            if result.is_healthy:
                status_text = "[green]● HEALTHY[/green]"
            elif result.is_loading:
                status_text = "[yellow]◐ LOADING[/yellow]"
            else:
                status_text = f"[red]✗ {result.status.value.upper()}[/red]"
            
            response_time = f"{result.response_time_ms:.1f}ms" if result.response_time_ms else "-"
            details = result.error_message or ""
            
            if result.slots_idle is not None:
                details = f"Slots: {result.slots_idle} idle, {result.slots_processing} busy"
            
            table.add_row(inst_name, status_text, response_time, details)
            
        except FileNotFoundError:
            table.add_row(inst_name, "[dim]NOT FOUND[/dim]", "-", "Config missing")
        except Exception as e:
            table.add_row(inst_name, "[red]ERROR[/red]", "-", str(e))
    
    console.print(table)


@app.command()
def logs(
    name: Annotated[str, typer.Argument(help="Instance name")],
    tail: Annotated[int, typer.Option("--tail", "-n", help="Number of lines")] = 100,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow log output")] = False,
    stream: Annotated[str, typer.Option("--stream", help="Log stream: stdout, stderr, or both")] = "stdout",
    stderr: Annotated[bool, typer.Option("--stderr", help="Show stderr instead")] = False,
) -> None:
    """
    View logs of an instance.
    
    Example:
        llama-orch logs gpt-oss --tail 50
        llama-orch logs gpt-oss --follow
        llama-orch logs gpt-oss --stream both
    """
    import time
    from llama_orchestrator.config import get_instance_config, get_logs_dir
    from llama_orchestrator.engine.detach import get_latest_logs
    
    try:
        config = get_instance_config(name)
    except FileNotFoundError:
        console.print(f"[red]Instance '{name}' not found.[/red]")
        _raise_exit(ExitCode.INSTANCE_NOT_FOUND)

    selected_stream = stream.lower().strip()
    if stderr and selected_stream == "stdout":
        selected_stream = "stderr"

    valid_streams = {"stdout", "stderr", "both"}
    if selected_stream not in valid_streams:
        console.print(f"[red]Invalid stream '{stream}'. Use stdout, stderr, or both.[/red]")
        _raise_exit(ExitCode.USAGE_ERROR)

    stream_names = ["stdout", "stderr"] if selected_stream == "both" else [selected_stream]
    
    # Determine log file path
    logs_dir = get_logs_dir() / name

    def get_active_log_file(log_type: str) -> Path | None:
        fixed = logs_dir / f"{log_type}.log"
        if fixed.exists():
            return fixed

        pattern = f"{log_type}.*.log"
        matches = sorted(logs_dir.glob(pattern), reverse=True)
        return matches[0] if matches else None

    active_files = {log_type: get_active_log_file(log_type) for log_type in stream_names}

    if not any(active_files.values()):
        missing_paths = ", ".join(str(logs_dir / f"{log_type}.log") for log_type in stream_names)
        console.print(f"[yellow]No log file found at {missing_paths}[/yellow]")
        console.print("[dim]Instance may not have been started yet.[/dim]")
        _raise_exit(ExitCode.CONFIG_NOT_FOUND)

    console.print(f"[dim]Showing {selected_stream} logs for instance '{name}'[/dim]")
    for log_type in stream_names:
        if active_files[log_type] is not None:
            console.print(f"[dim]{log_type}: {active_files[log_type]}[/dim]")
    console.print()

    def print_log_line(log_type: str, line: str) -> None:
        if selected_stream == "both":
            console.print(f"[{ 'cyan' if log_type == 'stdout' else 'yellow' }]{log_type}[/]: {line.rstrip()}")
        else:
            console.print(line.rstrip())
    
    if follow:
        # Follow mode - stream new lines
        console.print("[dim]Following log output (Ctrl+C to stop)...[/dim]\n")

        try:
            latest_logs = get_latest_logs(name, tail)
            for log_type in stream_names:
                for line in latest_logs.get(log_type, []):
                    print_log_line(log_type, line)

            handles = {}
            try:
                for log_type, path in active_files.items():
                    if path is None:
                        continue
                    handle = open(path, 'r', encoding='utf-8', errors='replace')
                    handle.seek(0, 2)
                    handles[log_type] = handle

                while True:
                    saw_output = False
                    for log_type, handle in handles.items():
                        line = handle.readline()
                        while line:
                            saw_output = True
                            print_log_line(log_type, line)
                            line = handle.readline()

                    if not saw_output:
                        time.sleep(0.1)
            finally:
                for handle in handles.values():
                    handle.close()
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped following logs.[/dim]")
    else:
        latest_logs = get_latest_logs(name, tail)

        if not any(latest_logs.get(log_type) for log_type in stream_names):
            console.print("[dim]Log file is empty.[/dim]")
        else:
            for log_type in stream_names:
                for line in latest_logs.get(log_type, []):
                    print_log_line(log_type, line)


@app.command()
def describe(
    name: Annotated[str, typer.Argument(help="Instance name")],
    output_json: Annotated[bool, typer.Option("--json", help="Output as JSON")] = False,
) -> None:
    """
    Show detailed information about an instance.
    
    Example:
        llama-orch describe gpt-oss
        llama-orch describe gpt-oss --json
    """
    from llama_orchestrator.config import get_instance_config
    from llama_orchestrator.cli_describe import build_description, format_description_rich
    from rich.panel import Panel
    
    try:
        config = get_instance_config(name)
    except FileNotFoundError:
        console.print(f"[red]Instance '{name}' not found.[/red]")
        _raise_exit(ExitCode.INSTANCE_NOT_FOUND)
    
    description = build_description(name, config=config)
    
    if output_json:
        import json

        console.print(json.dumps(description.to_dict(), indent=2, default=str))
    else:
        console.print(
            Panel(
                format_description_rich(description),
                title=f"Instance: {name}",
                border_style="blue",
            )
        )


def _build_dashboard_table() -> Table:
    """Build the dashboard instances table."""
    from llama_orchestrator.config import discover_instances, get_instance_config
    from llama_orchestrator.engine import list_instances
    from llama_orchestrator.engine.state import InstanceStatus

    table = Table(
        title="llama-orchestrator Dashboard",
        caption="Press Ctrl+C to exit",
        expand=True,
    )

    table.add_column("Instance", style="cyan", no_wrap=True)
    table.add_column("PID", style="magenta", justify="right")
    table.add_column("Port", style="green", justify="right")
    table.add_column("Backend", style="yellow")
    table.add_column("Model", style="dim", max_width=30)
    table.add_column("Status", style="bold", justify="center")
    table.add_column("Health", style="bold", justify="center")
    table.add_column("Uptime", style="dim", justify="right")

    instances = list_instances()
    instance_configs = {name for name, _ in discover_instances()}

    # Merge config info with state info
    all_names = set(instances.keys()) | instance_configs

    for name in sorted(all_names):
        state = instances.get(name)

        # Get config info
        try:
            config = get_instance_config(name)
            port = str(config.server.port)
            backend = config.gpu.backend
            model = config.model.path.name[:30]
        except Exception:
            port = "-"
            backend = "-"
            model = "-"

        # State info
        if state:
            pid = str(state.pid) if state.pid else "-"

            status_style = {
                InstanceStatus.RUNNING: "green",
                InstanceStatus.STARTING: "yellow",
                InstanceStatus.STOPPING: "yellow",
                InstanceStatus.STOPPED: "dim",
                InstanceStatus.ERROR: "red",
            }.get(state.status, "white")

            status_text = f"[{status_style}]{state.status_symbol} {state.status.value}[/{status_style}]"
            health_text = f"{state.health_symbol} {state.health.value}"
            uptime = state.uptime_str
        else:
            pid = "-"
            status_text = "[dim]○ stopped[/dim]"
            health_text = "? unknown"
            uptime = "-"

        table.add_row(
            name,
            pid,
            port,
            backend,
            model,
            status_text,
            health_text,
            uptime,
        )

    if not all_names:
        table.add_row(
            "[dim]No instances configured[/dim]",
            "", "", "", "", "", "", ""
        )

    return table


def _build_dashboard_events_panel(events_for: str | None = None):
    """Build the dashboard recent-events panel."""
    from datetime import datetime

    from rich.panel import Panel

    from llama_orchestrator.engine import get_recent_events

    events = get_recent_events(instance_name=events_for, limit=10)
    if not events:
        return Panel("[dim]No recent events.[/dim]", title="Recent Events", border_style="dim")

    lines = []
    for event in events[:10]:
        timestamp = event.get("ts")
        if isinstance(timestamp, int | float):
            ts_text = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
        else:
            ts_text = str(timestamp or "")[:8]
        instance_name = event.get("instance_name") or "system"
        lines.append(f"[{ts_text}] {instance_name}: {event.get('event_type')} - {event.get('message')}")

    title = "Recent Events" if events_for is None else f"Recent Events ({events_for})"
    return Panel("\n".join(lines), title=title, border_style="blue")


def _build_dashboard_layout(events_for: str | None = None):
    """Build the full dashboard layout."""
    from rich.layout import Layout

    layout = Layout()
    layout.split_row(
        Layout(_build_dashboard_table(), name="instances", ratio=3),
        Layout(_build_dashboard_events_panel(events_for), name="events", ratio=2),
    )
    return layout


@app.command()
def dashboard(
    events_for: Annotated[Optional[str], typer.Option("--events-for", help="Filter dashboard events by instance")] = None,
) -> None:
    """
    Launch live TUI dashboard.
    
    Shows all instances with live status updates.
    Press 'q' to quit, 'r' to refresh, 'h' for help.
    
    Example:
        llama-orch dashboard
    """
    import time
    from rich.live import Live
    
    console.print("[bold]Starting dashboard...[/bold]")
    console.print("[dim]Press Ctrl+C to exit[/dim]\n")
    
    try:
        with Live(_build_dashboard_layout(events_for), console=console, refresh_per_second=1) as live:
            while True:
                time.sleep(1)
                live.update(_build_dashboard_layout(events_for))
    except KeyboardInterrupt:
        console.print("\n[dim]Dashboard closed.[/dim]")


@app.command()
def gui() -> None:
    """
    Launch the desktop GUI for model instance management.

    Example:
        llama-orch gui
    """
    try:
        from llama_orchestrator.gui import launch_gui
    except Exception as e:
        console.print(f"[red]Failed to load GUI:[/red] {e}")
        _raise_exit(ExitCode.GENERAL_ERROR)

    launch_gui()


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Instance name to create")],
    model: Annotated[Path, typer.Option("--model", "-m", help="Path to GGUF model")],
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = 8001,
    backend: Annotated[str, typer.Option("--backend", "-b", help="GPU backend")] = "vulkan",
    device: Annotated[int, typer.Option("--device", "-d", help="GPU device ID")] = 0,
    layers: Annotated[int, typer.Option("--layers", "-l", help="GPU layers to offload")] = 0,
    context: Annotated[int, typer.Option("--context", "-c", help="Context size")] = 4096,
    threads: Annotated[int, typer.Option("--threads", "-t", help="CPU threads")] = 8,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite existing config")] = False,
) -> None:
    """
    Initialize a new instance configuration.
    
    Example:
        llama-orch init gpt-oss --model ../models/gpt-oss.gguf --port 8001
        llama-orch init my-model -m models/model.gguf -p 8002 -b vulkan -d 1 -l 30
    """
    from llama_orchestrator.config import (
        GpuConfig,
        InstanceConfig,
        ModelConfig,
        ServerConfig,
        get_instances_dir,
        save_config,
    )
    from rich.panel import Panel
    
    instances_dir = get_instances_dir()
    instance_dir = instances_dir / name
    config_path = instance_dir / "config.json"
    
    # Check if already exists
    if config_path.exists() and not force:
        console.print(f"[red]Instance '{name}' already exists.[/red]")
        console.print(f"Use --force to overwrite, or choose a different name.")
        _raise_exit(ExitCode.INSTANCE_ALREADY_EXISTS)
    
    # Validate backend
    valid_backends = ("cpu", "vulkan", "cuda", "metal", "hip")
    if backend not in valid_backends:
        console.print(f"[red]Invalid backend '{backend}'.[/red]")
        console.print(f"Valid options: {', '.join(valid_backends)}")
        _raise_exit(ExitCode.CONFIG_INVALID)
    
    # Create config
    try:
        config = InstanceConfig(
            name=name,
            model=ModelConfig(
                path=model,
                context_size=context,
                threads=threads,
            ),
            server=ServerConfig(
                port=port,
            ),
            gpu=GpuConfig(
                backend=backend,  # type: ignore
                device_id=device,
                layers=layers,
            ),
        )
    except Exception as e:
        console.print(f"[red]Invalid configuration: {e}[/red]")
        _raise_exit(ExitCode.CONFIG_INVALID)
    
    # Save config
    saved_path = save_config(config)
    
    console.print(Panel(
        f"[green]Instance '{name}' created successfully![/green]\n\n"
        f"Config: {saved_path}\n"
        f"Model: {model}\n"
        f"Port: {port}\n"
        f"Backend: {backend}" + (f" (device {device}, {layers} layers)" if backend != "cpu" else ""),
        title="✅ Instance Created",
        border_style="green"
    ))
    
    console.print("\n[dim]Next steps:[/dim]")
    console.print(f"  1. Review config: [cyan]llama-orch describe {name}[/cyan]")
    console.print(f"  2. Validate: [cyan]llama-orch config validate[/cyan]")
    console.print(f"  3. Start: [cyan]llama-orch up {name}[/cyan]")


# =============================================================================
# Config Commands
# =============================================================================


@config_app.command("validate")
def config_validate(
    path: Annotated[
        Optional[Path], 
        typer.Argument(help="Config file path (optional)")
    ] = None,
    check_runtime: Annotated[
        bool, typer.Option("--runtime", "-r", help="Check runtime conditions (port availability)")
    ] = False,
) -> None:
    """
    Validate instance configuration.
    
    Example:
        llama-orch config validate
        llama-orch config validate instances/gpt-oss/config.json
        llama-orch config validate --runtime
    """
    from llama_orchestrator.config import (
        ConfigLoadError,
        load_config,
        validate_all_instances,
        validate_instance,
    )
    from rich.panel import Panel
    
    if path:
        console.print(f"[blue]Validating config:[/blue] {path}")
        try:
            config = load_config(path)
            result = validate_instance(config, check_runtime=check_runtime)
        except ConfigLoadError as e:
            console.print(Panel(
                f"[red]Failed to load config:[/red]\n{e.message}",
                title="❌ Validation Failed",
                border_style="red"
            ))
            _raise_exit(ExitCode.CONFIG_NOT_FOUND)
    else:
        console.print("[blue]Validating all instance configs...[/blue]")
        result = validate_all_instances(check_runtime=check_runtime)
    
    # Print results
    if result.issues:
        for issue in result.issues:
            console.print(str(issue))
    
    if result.is_valid:
        console.print("\n[green]✅ Validation passed[/green]")
        if result.warning_count > 0:
            console.print(f"[yellow]   ({result.warning_count} warnings)[/yellow]")
    else:
        console.print(f"\n[red]❌ Validation failed ({result.error_count} errors)[/red]")
        _raise_exit(ExitCode.CONFIG_INVALID)


@config_app.command("lint")
def config_lint(
    output_json: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
) -> None:
    """
    Run full validation (lint) on all configurations.
    
    Includes additional best practice checks beyond basic validation.
    
    Example:
        llama-orch config lint
        llama-orch config lint --json
    """
    import json
    
    from llama_orchestrator.config import (
        ConfigLoadError,
        ValidationResult,
        lint_config,
        load_all_instances,
    )
    from rich.panel import Panel
    from rich.table import Table
    
    console.print("[blue]Linting all configurations...[/blue]\n")
    
    result = ValidationResult()
    
    try:
        configs = load_all_instances()
    except ConfigLoadError as e:
        console.print(Panel(
            f"[red]Failed to load configs:[/red]\n{e.message}",
            title="❌ Lint Failed",
            border_style="red"
        ))
        _raise_exit(ExitCode.CONFIG_NOT_FOUND)
    
    if not configs:
        console.print("[yellow]No instances configured.[/yellow]")
        console.print("Use 'llama-orch init <name> --model <path>' to create one.")
        return
    
    # Lint each config
    for name, config in configs.items():
        console.print(f"[dim]Linting {name}...[/dim]")
        instance_result = lint_config(config)
        result.merge(instance_result)
    
    console.print()
    
    if output_json:
        issues_data = [
            {
                "instance": i.instance,
                "field": i.field,
                "severity": i.severity,
                "message": i.message,
                "suggestion": i.suggestion,
            }
            for i in result.issues
        ]
        console.print(json.dumps({"issues": issues_data, "valid": result.is_valid}, indent=2))
    else:
        if result.issues:
            table = Table(title="Lint Results")
            table.add_column("Severity", style="bold")
            table.add_column("Instance", style="cyan")
            table.add_column("Field", style="yellow")
            table.add_column("Message")
            
            for issue in result.issues:
                severity_style = {
                    "error": "red",
                    "warning": "yellow", 
                    "info": "blue"
                }[issue.severity]
                table.add_row(
                    f"[{severity_style}]{issue.severity.upper()}[/{severity_style}]",
                    issue.instance,
                    issue.field,
                    issue.message
                )
            
            console.print(table)
        
        console.print()
        if result.is_valid:
            console.print(f"[green]✅ Lint passed[/green] ({len(configs)} instances checked)")
            if result.warning_count > 0:
                console.print(f"[yellow]   {result.warning_count} warnings[/yellow]")
        else:
            console.print(f"[red]❌ Lint failed ({result.error_count} errors, {result.warning_count} warnings)[/red]")
            _raise_exit(ExitCode.CONFIG_INVALID)


# =============================================================================
# Daemon Commands
# =============================================================================


@daemon_app.command("start")
def daemon_start(
    foreground: Annotated[bool, typer.Option("--foreground", "-f", help="Run in foreground")] = False,
) -> None:
    """
    Start the orchestrator daemon.
    
    The daemon monitors all instances and triggers auto-restarts
    based on health check policies.
    
    Example:
        llama-orch daemon start
        llama-orch daemon start --foreground
    """
    from llama_orchestrator.daemon import is_daemon_running, start_daemon
    from rich.panel import Panel
    
    if is_daemon_running():
        console.print("[yellow]Daemon is already running.[/yellow]")
        console.print("Use 'llama-orch daemon status' to check status.")
        _raise_exit(ExitCode.DAEMON_ALREADY_RUNNING)
    
    mode = "foreground" if foreground else "background"
    console.print(f"[green]Starting daemon ({mode})...[/green]")
    
    if foreground:
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
        try:
            start_daemon(foreground=True)
        except KeyboardInterrupt:
            console.print("\n[dim]Daemon stopped.[/dim]")
    else:
        start_daemon(foreground=False)
        console.print(Panel(
            "[green]Daemon started successfully![/green]\n\n"
            "The daemon will monitor all instances and trigger auto-restarts.\n"
            "Use 'llama-orch daemon status' to check status.\n"
            "Use 'llama-orch daemon stop' to stop.",
            title="✅ Daemon Started",
            border_style="green"
        ))


@daemon_app.command("stop")
def daemon_stop() -> None:
    """
    Stop the orchestrator daemon.
    
    Example:
        llama-orch daemon stop
    """
    from llama_orchestrator.daemon import is_daemon_running, stop_daemon
    from rich.panel import Panel
    
    if not is_daemon_running():
        console.print("[yellow]Daemon is not running.[/yellow]")
        _raise_exit(ExitCode.DAEMON_NOT_RUNNING)
    
    console.print("[red]Stopping daemon...[/red]")
    
    if stop_daemon():
        console.print(Panel(
            "[green]Daemon stopped successfully![/green]",
            title="✅ Daemon Stopped",
            border_style="green"
        ))
    else:
        console.print("[red]Failed to stop daemon.[/red]")
        _raise_exit(ExitCode.DAEMON_STOP_FAILED)


@daemon_app.command("status")
def daemon_status() -> None:
    """
    Show daemon status.
    
    Example:
        llama-orch daemon status
    """
    from llama_orchestrator.daemon import get_daemon_status
    from rich.panel import Panel
    
    status = get_daemon_status()
    
    if status.running:
        info = f"""
[green]● Daemon is running[/green]

  PID:                 {status.pid}
  Instances monitored: {status.instances_monitored}
"""
        console.print(Panel(info.strip(), title="Daemon Status", border_style="green"))
    else:
        console.print(Panel(
            "[dim]○ Daemon is not running[/dim]\n\n"
            "Use 'llama-orch daemon start' to start.",
            title="Daemon Status",
            border_style="dim"
        ))


@daemon_app.command("install")
def daemon_install(
    service_name: Annotated[str, typer.Option("--service-name", help="Windows service name")] = "llama-orchestrator",
) -> None:
    """Install the orchestrator daemon as a Windows service."""
    from rich.panel import Panel

    from llama_orchestrator.daemon.win_service import install_windows_service

    try:
        install_windows_service(service_name=service_name)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        _raise_exit(ExitCode.BINARY_NOT_FOUND)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        _raise_exit(ExitCode.PERMISSION_DENIED)
    except Exception as e:
        console.print(f"[red]Failed to install service:[/red] {e}")
        _raise_exit(ExitCode.DAEMON_START_FAILED)

    console.print(Panel(
        f"[green]Windows service '{service_name}' installed successfully.[/green]",
        title="✅ Service Installed",
        border_style="green",
    ))


@daemon_app.command("uninstall")
def daemon_uninstall(
    service_name: Annotated[str, typer.Option("--service-name", help="Windows service name")] = "llama-orchestrator",
) -> None:
    """Uninstall the orchestrator Windows service."""
    from rich.panel import Panel

    from llama_orchestrator.daemon.win_service import uninstall_windows_service

    try:
        uninstall_windows_service(service_name=service_name)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        _raise_exit(ExitCode.BINARY_NOT_FOUND)
    except Exception as e:
        console.print(f"[red]Failed to uninstall service:[/red] {e}")
        _raise_exit(ExitCode.DAEMON_STOP_FAILED)

    console.print(Panel(
        f"[green]Windows service '{service_name}' uninstalled successfully.[/green]",
        title="✅ Service Removed",
        border_style="green",
    ))


# =============================================================================
# Binary Version Management Commands
# =============================================================================


@binary_app.command("install")
def binary_install(
    version: Annotated[str, typer.Argument(help="Version to install (e.g., b7572 or 'latest')")] = "latest",
    variant: Annotated[
        str, typer.Option("--variant", "-var", help="Binary variant (e.g., win-vulkan-x64)")
    ] = "win-vulkan-x64",
) -> None:
    """
    Install a llama.cpp server binary from GitHub releases.
    
    Downloads and extracts the binary to bins/<uuid>/ directory.
    The binary is registered with a unique UUID for unambiguous identification.
    
    Available variants for Windows:
        win-cpu-x64, win-vulkan-x64, win-cuda-12.4-x64, 
        win-cuda-13.1-x64, win-hip-radeon-x64, win-sycl-x64
    
    Example:
        llama-orch binary install
        llama-orch binary install b7572 --variant win-vulkan-x64
        llama-orch binary install latest --variant win-cuda-12.4-x64
    """
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    
    from llama_orchestrator.binaries import BinaryManager
    from llama_orchestrator.config import get_project_root
    
    project_root = get_project_root()
    manager = BinaryManager(project_root)
    
    console.print(f"[cyan]Installing llama.cpp binary[/cyan]")
    console.print(f"  Version: {version}")
    console.print(f"  Variant: {variant}")
    console.print()
    
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Downloading and installing...", total=None)
            
            try:
                binary = manager.install(
                    version=version if version != "latest" else None,
                    variant=variant,
                )
                progress.update(task, description="Installation complete!")
            except Exception as e:
                progress.update(task, description=f"[red]Error: {e}[/red]")
                raise
        
        console.print()
        console.print(Panel(
            f"[green]Binary installed successfully![/green]\n\n"
            f"  UUID:    [cyan]{binary.id}[/cyan]\n"
            f"  Version: {binary.version}\n"
            f"  Variant: {binary.variant}\n"
            f"  Path:    {binary.path}\n\n"
            f"[dim]Use this UUID in your instance config.json:[/dim]\n"
            f'  "binary": {{"binary_id": "{binary.id}"}}',
            title="✅ Binary Installed",
            border_style="green"
        ))
    except Exception as e:
        console.print(Panel(
            f"[red]Failed to install binary:[/red]\n{e}",
            title="❌ Install Failed",
            border_style="red"
        ))
        _raise_exit(ExitCode.BINARY_INSTALL_FAILED)


@binary_app.command("list")
def binary_list() -> None:
    """
    List all installed llama.cpp binaries.
    
    Shows all binaries in the bins/ directory with their UUIDs,
    versions, variants, and installation dates.
    
    Example:
        llama-orch binary list
    """
    from llama_orchestrator.binaries.registry import load_registry
    from llama_orchestrator.config import get_bins_dir
    
    bins_dir = get_bins_dir()
    registry = load_registry(bins_dir)
    
    if not registry.binaries:
        console.print("[dim]No binaries installed.[/dim]")
        console.print("Use 'llama-orch binary install' to install one.")
        return
    
    table = Table(title="Installed llama.cpp Binaries")
    
    table.add_column("UUID", style="cyan", no_wrap=True, max_width=36)
    table.add_column("Version", style="green")
    table.add_column("Variant", style="yellow")
    table.add_column("Installed", style="dim")
    table.add_column("Path", style="dim", max_width=40)
    
    for binary in sorted(registry.binaries, key=lambda b: b.installed_at, reverse=True):
        table.add_row(
            str(binary.id),
            binary.version,
            binary.variant,
            binary.installed_at.strftime("%Y-%m-%d %H:%M"),
            str(binary.path) if binary.path else "-",
        )
    
    console.print(table)
    console.print()
    console.print(f"[dim]Total: {len(registry.binaries)} binary/binaries[/dim]")


@binary_app.command("info")
def binary_info(
    binary_id: Annotated[str, typer.Argument(help="Binary UUID or partial UUID")],
) -> None:
    """
    Show detailed information about an installed binary.
    
    Example:
        llama-orch binary info 550e8400-e29b-41d4-a716-446655440000
        llama-orch binary info 550e8400  # partial UUID match
    """
    from uuid import UUID
    
    from rich.panel import Panel
    
    from llama_orchestrator.binaries.registry import load_registry
    from llama_orchestrator.config import get_bins_dir
    
    bins_dir = get_bins_dir()
    registry = load_registry(bins_dir)
    
    # Find matching binary (exact or partial UUID match)
    matches = []
    for binary in registry.binaries:
        if str(binary.id) == binary_id:
            matches = [binary]
            break
        if str(binary.id).startswith(binary_id):
            matches.append(binary)
    
    if not matches:
        console.print(f"[red]No binary found matching '{binary_id}'[/red]")
        _raise_exit(ExitCode.BINARY_NOT_FOUND)
    
    if len(matches) > 1:
        console.print(f"[yellow]Multiple binaries match '{binary_id}':[/yellow]")
        for m in matches:
            console.print(f"  - {m.id}")
        console.print("\nPlease provide a more specific UUID.")
        _raise_exit(ExitCode.USAGE_ERROR)
    
    binary = matches[0]
    
    info = f"""
[cyan]UUID:[/cyan]         {binary.id}
[cyan]Version:[/cyan]      {binary.version}
[cyan]Variant:[/cyan]      {binary.variant}
[cyan]Path:[/cyan]         {binary.path}
[cyan]Download URL:[/cyan] {binary.download_url or 'N/A'}
[cyan]Installed:[/cyan]    {binary.installed_at.strftime("%Y-%m-%d %H:%M:%S")}
[cyan]SHA256:[/cyan]       {binary.sha256 or 'N/A'}
"""
    
    console.print(Panel(
        info.strip(),
        title=f"Binary {binary.version}-{binary.variant}",
        border_style="cyan"
    ))


@binary_app.command("remove")
def binary_remove(
    binary_id: Annotated[str, typer.Argument(help="Binary UUID to remove")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """
    Remove an installed llama.cpp binary.
    
    Removes the binary directory and unregisters it from the registry.
    
    Example:
        llama-orch binary remove 550e8400-e29b-41d4-a716-446655440000
        llama-orch binary remove 550e8400 --force
    """
    import shutil
    from uuid import UUID
    
    from rich.panel import Panel
    
    from llama_orchestrator.binaries.registry import load_registry, remove_binary
    from llama_orchestrator.config import get_bins_dir
    
    bins_dir = get_bins_dir()
    registry = load_registry(bins_dir)
    
    # Find matching binary
    matches = []
    for binary in registry.binaries:
        if str(binary.id) == binary_id:
            matches = [binary]
            break
        if str(binary.id).startswith(binary_id):
            matches.append(binary)
    
    if not matches:
        console.print(f"[red]No binary found matching '{binary_id}'[/red]")
        _raise_exit(ExitCode.BINARY_NOT_FOUND)
    
    if len(matches) > 1:
        console.print(f"[yellow]Multiple binaries match '{binary_id}':[/yellow]")
        for m in matches:
            console.print(f"  - {m.id} ({m.version}-{m.variant})")
        console.print("\nPlease provide a more specific UUID.")
        _raise_exit(ExitCode.USAGE_ERROR)
    
    binary = matches[0]
    
    # Confirm deletion
    if not force:
        console.print(f"[yellow]About to remove binary:[/yellow]")
        console.print(f"  UUID:    {binary.id}")
        console.print(f"  Version: {binary.version}")
        console.print(f"  Variant: {binary.variant}")
        console.print()
        
        confirm = typer.confirm("Are you sure you want to remove this binary?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)
    
    # Remove directory
    if binary.path and binary.path.exists():
        shutil.rmtree(binary.path)
        console.print(f"[dim]Removed directory: {binary.path}[/dim]")
    
    # Remove from registry
    remove_binary(bins_dir, binary.id)
    
    console.print(Panel(
        f"[green]Binary removed successfully![/green]\n\n"
        f"  UUID:    {binary.id}\n"
        f"  Version: {binary.version}\n"
        f"  Variant: {binary.variant}",
        title="✅ Binary Removed",
        border_style="green"
    ))


@binary_app.command("latest")
def binary_latest(
    variant: Annotated[
        str, typer.Option("--variant", "-var", help="Binary variant")
    ] = "win-vulkan-x64",
) -> None:
    """
    Show the latest available llama.cpp version from GitHub.
    
    Example:
        llama-orch binary latest
        llama-orch binary latest --variant win-cuda-12.4-x64
    """
    from rich.panel import Panel
    
    from llama_orchestrator.binaries import GitHubClient
    from llama_orchestrator.binaries.schema import build_download_url
    
    console.print("[cyan]Fetching latest release from GitHub...[/cyan]")
    
    try:
        client = GitHubClient()
        release = client.get_latest_release()
        download_url = build_download_url(release.version, variant)
        
        console.print(Panel(
            f"[green]Latest llama.cpp release:[/green]\n\n"
            f"  Version:      [cyan]{release.version}[/cyan]\n"
            f"  Published:    {release.published_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"  Variant:      {variant}\n"
            f"  Download URL: {download_url}\n\n"
            f"[dim]Install with:[/dim]\n"
            f"  llama-orch binary install {release.version} --variant {variant}",
            title="Latest Release",
            border_style="cyan"
        ))
    except Exception as e:
        console.print(f"[red]Failed to fetch latest release:[/red] {e}")
        _raise_exit(ExitCode.BINARY_DOWNLOAD_FAILED)


if __name__ == "__main__":
    app()
