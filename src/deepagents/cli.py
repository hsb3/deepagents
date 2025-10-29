#!/usr/bin/env python3
import sys

# Check for CLI dependencies before importing them
def check_cli_dependencies():
    """Check if CLI optional dependencies are installed."""
    missing = []
    
    try:
        import rich
    except ImportError:
        missing.append("rich")
    
    try:
        import requests
    except ImportError:
        missing.append("requests")
    
    try:
        import dotenv
    except ImportError:
        missing.append("python-dotenv")
    
    try:
        import tavily
    except ImportError:
        missing.append("tavily-python")

    try:
        import prompt_toolkit
    except ImportError:
        missing.append("prompt-toolkit")

    if missing:
        print("\n❌ Missing required CLI dependencies!")
        print(f"\nThe following packages are required to use the deepagents CLI:")
        for pkg in missing:
            print(f"  - {pkg}")
        print(f"\nPlease install them with:")
        print(f"  pip install deepagents[cli]")
        print(f"\nOr install all dependencies:")
        print(f"  pip install 'deepagents[cli]'")
        sys.exit(1)

check_cli_dependencies()

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Union, Literal

import requests

from tavily import TavilyClient
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from langchain.agents.middleware import HostExecutionPolicy, InterruptOnConfig
from langchain_core.messages import ToolMessage

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends import CompositeBackend
from deepagents.middleware.agent_memory import AgentMemoryMiddleware
from deepagents.middleware.resumable_shell import ResumableShellToolMiddleware
from rich import box

import dotenv
import re
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter, merge_completers, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, Window, FormattedTextControl
from prompt_toolkit.widgets import Frame

dotenv.load_dotenv()

COLORS = {
    "primary": "#10b981",
    "dim": "#6b7280",
    "user": "#ffffff",
    "agent": "#10b981",
    "thinking": "#34d399",
    "tool": "#fbbf24",
}

DEEP_AGENTS_ASCII = """
 ██████╗  ███████╗ ███████╗ ██████╗
 ██╔══██╗ ██╔════╝ ██╔════╝ ██╔══██╗
 ██║  ██║ █████╗   █████╗   ██████╔╝
 ██║  ██║ ██╔══╝   ██╔══╝   ██╔═══╝
 ██████╔╝ ███████╗ ███████╗ ██║
 ╚═════╝  ╚══════╝ ╚══════╝ ╚═╝

  █████╗   ██████╗  ███████╗ ███╗   ██╗ ████████╗ ███████╗
 ██╔══██╗ ██╔════╝  ██╔════╝ ████╗  ██║ ╚══██╔══╝ ██╔════╝
 ███████║ ██║  ███╗ █████╗   ██╔██╗ ██║    ██║    ███████╗
 ██╔══██║ ██║   ██║ ██╔══╝   ██║╚██╗██║    ██║    ╚════██║
 ██║  ██║ ╚██████╔╝ ███████╗ ██║ ╚████║    ██║    ███████║
 ╚═╝  ╚═╝  ╚═════╝  ╚══════╝ ╚═╝  ╚═══╝    ╚═╝    ╚══════╝
"""

console = Console()

tavily_client = TavilyClient(api_key=os.environ.get("TAVILY_API_KEY")) if os.environ.get("TAVILY_API_KEY") else None


def http_request(
    url: str,
    method: str = "GET",
    headers: Dict[str, str] = None,
    data: Union[str, Dict] = None,
    params: Dict[str, str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Make HTTP requests to APIs and web services.

    Args:
        url: Target URL
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        headers: HTTP headers to include
        data: Request body data (string or dict)
        params: URL query parameters
        timeout: Request timeout in seconds

    Returns:
        Dictionary with response data including status, headers, and content
    """
    try:
        kwargs = {"url": url, "method": method.upper(), "timeout": timeout}

        if headers:
            kwargs["headers"] = headers
        if params:
            kwargs["params"] = params
        if data:
            if isinstance(data, dict):
                kwargs["json"] = data
            else:
                kwargs["data"] = data

        response = requests.request(**kwargs)

        try:
            content = response.json()
        except:
            content = response.text

        return {
            "success": response.status_code < 400,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "content": content,
            "url": response.url,
        }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request timed out after {timeout} seconds",
            "url": url,
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Request error: {str(e)}",
            "url": url,
        }
    except Exception as e:
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": f"Error making request: {str(e)}",
            "url": url,
        }


def web_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Search the web using Tavily for current information and documentation.

    This tool searches the web and returns relevant results. After receiving results,
    you MUST synthesize the information into a natural, helpful response for the user.

    Args:
        query: The search query (be specific and detailed)
        max_results: Number of results to return (default: 5)
        topic: Search topic type - "general" for most queries, "news" for current events
        include_raw_content: Include full page content (warning: uses more tokens)

    Returns:
        Dictionary containing:
        - results: List of search results, each with:
            - title: Page title
            - url: Page URL
            - content: Relevant excerpt from the page
            - score: Relevance score (0-1)
        - query: The original search query

    IMPORTANT: After using this tool:
    1. Read through the 'content' field of each result
    2. Extract relevant information that answers the user's question
    3. Synthesize this into a clear, natural language response
    4. Cite sources by mentioning the page titles or URLs
    5. NEVER show the raw JSON to the user - always provide a formatted response
    """
    if tavily_client is None:
        return {
            "error": "Tavily API key not configured. Please set TAVILY_API_KEY environment variable.",
            "query": query
        }
    
    try:
        search_docs = tavily_client.search(
            query,
            max_results=max_results,
            include_raw_content=include_raw_content,
            topic=topic,
        )
        return search_docs
    except Exception as e:
        return {
            "error": f"Web search error: {str(e)}",
            "query": query
        }


def get_default_coding_instructions() -> str:
    """Get the default coding agent instructions.

    These are the immutable base instructions that cannot be modified by the agent.
    Long-term memory (agent.md) is handled separately by the middleware.
    """
    default_prompt_path = Path(__file__).parent / "default_agent_prompt.md"
    return default_prompt_path.read_text()


def create_model():
    """Create the appropriate model based on available API keys.

    Returns:
        ChatModel instance (OpenAI or Anthropic)

    Raises:
        SystemExit if no API key is configured
    """
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if openai_key:
        from langchain_openai import ChatOpenAI
        model_name = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
        console.print(f"[dim]Using OpenAI model: {model_name}[/dim]")
        return ChatOpenAI(
            model=model_name,
            temperature=0.7,
        )
    elif anthropic_key:
        from langchain_anthropic import ChatAnthropic
        model_name = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
        console.print(f"[dim]Using Anthropic model: {model_name}[/dim]")
        return ChatAnthropic(
            model_name=model_name,
            max_tokens=20000,
        )
    else:
        console.print("[bold red]Error:[/bold red] No API key configured.")
        console.print("\nPlease set one of the following environment variables:")
        console.print("  - OPENAI_API_KEY     (for OpenAI models like gpt-5-mini)")
        console.print("  - ANTHROPIC_API_KEY  (for Claude models)")
        console.print("\nExample:")
        console.print("  export OPENAI_API_KEY=your_api_key_here")
        console.print("\nOr add it to your .env file.")
        sys.exit(1)


config = {"recursion_limit": 1000}

MAX_ARG_LENGTH = 150


def truncate_value(value: str, max_length: int = MAX_ARG_LENGTH) -> str:
    """Truncate a string value if it exceeds max_length."""
    if len(value) > max_length:
        return value[:max_length] + "..."
    return value


def format_tool_message_content(content: Any) -> str:
    """Convert ToolMessage content into a printable string."""
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            else:
                try:
                    parts.append(json.dumps(item))
                except Exception:
                    parts.append(str(item))
        return "\n".join(parts)
    return str(content)


class TokenTracker:
    """Track token usage across the conversation."""

    def __init__(self):
        self.session_input = 0
        self.session_output = 0
        self.last_input = 0
        self.last_output = 0

    def add(self, input_tokens: int, output_tokens: int):
        """Add tokens from a response."""
        self.session_input += input_tokens
        self.session_output += output_tokens
        self.last_input = input_tokens
        self.last_output = output_tokens

    def display_last(self):
        """Display tokens for the last response."""
        # Only show output tokens generated in this turn
        if self.last_output:
            if self.last_output >= 1000:
                console.print(f"  {self.last_output:,} tokens", style="dim")

    def display_session(self):
        """Display cumulative session tokens."""
        total = self.session_input + self.session_output
        console.print(f"\n[bold]Session Token Usage:[/bold]", style=COLORS["primary"])
        console.print(f"  Input:  {self.session_input:,} tokens", style=COLORS["dim"])
        console.print(f"  Output: {self.session_output:,} tokens", style=COLORS["dim"])
        console.print(f"  Total:  {total:,} tokens\n", style=COLORS["dim"])


class FilePathCompleter(Completer):
    """File path completer that triggers on @ symbol with case-insensitive matching."""

    def __init__(self):
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        """Get file path completions when @ is detected."""
        text = document.text_before_cursor

        # Check if we're after an @ symbol
        if '@' in text:
            # Get the part after the last @
            parts = text.split('@')
            if len(parts) >= 2:
                after_at = parts[-1]
                # Create a document for just the path part
                path_doc = Document(after_at, len(after_at))

                # Get all completions from PathCompleter
                all_completions = list(self.path_completer.get_completions(path_doc, complete_event))

                # If user has typed something, filter case-insensitively
                if after_at.strip():
                    # Extract just the filename part for matching (not the full path)
                    search_parts = after_at.split('/')
                    search_term = search_parts[-1].lower() if search_parts else ""

                    # Filter completions case-insensitively
                    filtered_completions = [
                        c for c in all_completions
                        if search_term in c.text.lower()
                    ]
                else:
                    # No search term, show all completions
                    filtered_completions = all_completions

                # Yield filtered completions
                for completion in filtered_completions:
                    yield Completion(
                        text=completion.text,
                        start_position=completion.start_position,
                        display=completion.display,
                        display_meta=completion.display_meta,
                        style=completion.style,
                    )


COMMANDS = {
    'clear': 'Clear screen and reset conversation',
    'help': 'Show help information',
    'tokens': 'Show token usage for current session',
    'quit': 'Exit the CLI',
    'exit': 'Exit the CLI',
}


class CommandCompleter(Completer):
    """Command completer for / commands."""

    def __init__(self):
        self.word_completer = WordCompleter(
            list(COMMANDS.keys()),
            meta_dict=COMMANDS,
            sentence=True,
            ignore_case=True,
        )

    def get_completions(self, document, complete_event):
        """Get command completions when / is at the start."""
        text = document.text

        # Only complete if line starts with /
        if text.startswith('/'):
            # Remove / for word completion
            cmd_text = text[1:]
            adjusted_doc = Document(
                cmd_text,
                document.cursor_position - 1 if document.cursor_position > 0 else 0
            )

            for completion in self.word_completer.get_completions(adjusted_doc, complete_event):
                yield completion


# Common bash commands for autocomplete (only universally available commands)
COMMON_BASH_COMMANDS = {
    'ls': 'List directory contents',
    'ls -la': 'List all files with details',
    'cd': 'Change directory',
    'pwd': 'Print working directory',
    'cat': 'Display file contents',
    'grep': 'Search text patterns',
    'find': 'Find files',
    'mkdir': 'Make directory',
    'rm': 'Remove file',
    'cp': 'Copy file',
    'mv': 'Move/rename file',
    'echo': 'Print text',
    'touch': 'Create empty file',
    'head': 'Show first lines',
    'tail': 'Show last lines',
    'wc': 'Count lines/words',
    'chmod': 'Change permissions',
}


class BashCompleter(Completer):
    """Bash command completer for ! commands."""

    def __init__(self):
        self.word_completer = WordCompleter(
            list(COMMON_BASH_COMMANDS.keys()),
            meta_dict=COMMON_BASH_COMMANDS,
            sentence=True,
            ignore_case=True,
        )

    def get_completions(self, document, complete_event):
        """Get bash command completions when ! is at the start."""
        text = document.text

        # Only complete if line starts with !
        if text.startswith('!'):
            # Remove ! for word completion
            cmd_text = text[1:]
            adjusted_doc = Document(
                cmd_text,
                document.cursor_position - 1 if document.cursor_position > 0 else 0
            )

            for completion in self.word_completer.get_completions(adjusted_doc, complete_event):
                yield completion


def parse_file_mentions(text: str) -> tuple[str, list[Path]]:
    """Extract @file mentions and return cleaned text with resolved file paths."""
    pattern = r'@((?:[^\s@]|(?<=\\)\s)+)'  # Match @filename, allowing escaped spaces
    matches = re.findall(pattern, text)

    files = []
    for match in matches:
        # Remove escape characters
        clean_path = match.replace('\\ ', ' ')
        path = Path(clean_path).expanduser()

        # Try to resolve relative to cwd
        if not path.is_absolute():
            path = Path.cwd() / path

        try:
            path = path.resolve()
            if path.exists() and path.is_file():
                files.append(path)
            else:
                console.print(f"[yellow]Warning: File not found: {match}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Warning: Invalid path {match}: {e}[/yellow]")

    return text, files


def show_interactive_help():
    """Show available commands during interactive session."""
    console.print()
    console.print("[bold]Interactive Commands:[/bold]", style=COLORS["primary"])
    console.print()

    for cmd, desc in COMMANDS.items():
        console.print(f"  /{cmd:<12} {desc}", style=COLORS["dim"])

    console.print()
    console.print("[bold]Editing Features:[/bold]", style=COLORS["primary"])
    console.print("  Enter           Submit your message", style=COLORS["dim"])
    console.print("  Alt+Enter       Insert newline (Option+Enter on Mac, or ESC then Enter)", style=COLORS["dim"])
    console.print("  Ctrl+E          Open in external editor (nano by default)", style=COLORS["dim"])
    console.print("  Arrow keys      Navigate input and history", style=COLORS["dim"])
    console.print("  Ctrl+C          Cancel current input", style=COLORS["dim"])
    console.print()
    console.print("[bold]Special Features:[/bold]", style=COLORS["primary"])
    console.print("  @filename       Type @ to auto-complete files and inject content", style=COLORS["dim"])
    console.print("  /command        Type / to see available commands", style=COLORS["dim"])
    console.print("  !command        Type ! to run bash commands (e.g., !ls, !git status)", style=COLORS["dim"])
    console.print("                  Completions appear automatically as you type", style=COLORS["dim"])
    console.print()


def handle_command(command: str, agent, token_tracker: TokenTracker) -> str | bool:
    """Handle slash commands. Returns 'exit' to exit, True if handled, False to pass to agent."""
    cmd = command.lower().strip().lstrip('/')

    if cmd in ['quit', 'exit', 'q']:
        return 'exit'

    elif cmd == 'clear':
        # Reset agent conversation state
        from langgraph.checkpoint.memory import InMemorySaver
        agent.checkpointer = InMemorySaver()

        # Clear screen and show fresh UI
        console.clear()
        console.print(DEEP_AGENTS_ASCII, style=f"bold {COLORS['primary']}")
        console.print()
        console.print("... Fresh start! Screen cleared and conversation reset.", style=COLORS["agent"])
        console.print()
        return True

    elif cmd == 'help':
        show_interactive_help()
        return True

    elif cmd == 'tokens':
        token_tracker.display_session()
        return True

    else:
        console.print()
        console.print(f"[yellow]Unknown command: /{cmd}[/yellow]")
        console.print(f"[dim]Type /help for available commands.[/dim]")
        console.print()
        return True

    return False


def execute_bash_command(command: str) -> bool:
    """Execute a bash command and display output. Returns True if handled."""
    cmd = command.strip().lstrip('!')

    if not cmd:
        return True

    try:
        console.print()
        console.print(f"[dim]$ {cmd}[/dim]")

        # Execute the command
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path.cwd()
        )

        # Display output
        if result.stdout:
            console.print(result.stdout, style=COLORS["dim"])
        if result.stderr:
            console.print(result.stderr, style="red")

        # Show return code if non-zero
        if result.returncode != 0:
            console.print(f"[dim]Exit code: {result.returncode}[/dim]")

        console.print()
        return True

    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out after 30 seconds[/red]")
        console.print()
        return True
    except Exception as e:
        console.print(f"[red]Error executing command: {e}[/red]")
        console.print()
        return True


def create_prompt_session(assistant_id: str) -> PromptSession:
    """Create a configured PromptSession with all features."""

    # Set default editor if not already set
    if 'EDITOR' not in os.environ:
        os.environ['EDITOR'] = 'nano'

    # Create key bindings
    kb = KeyBindings()

    # Bind regular Enter to submit (intuitive behavior)
    @kb.add('enter')
    def _(event):
        """Enter submits the input, unless completion menu is active."""
        buffer = event.current_buffer

        # If completion menu is showing, apply the current completion
        if buffer.complete_state:
            # Get the current completion (the highlighted one)
            current_completion = buffer.complete_state.current_completion

            # If no completion is selected (user hasn't navigated), auto-select the first one
            if not current_completion:
                completions = buffer.complete_state.completions
                if completions:
                    current_completion = completions[0]

            if current_completion:
                # Apply the completion
                buffer.apply_completion(current_completion)
            else:
                # No completions available, close menu
                buffer.complete_state = None
        else:
            # Don't submit if buffer is empty or only whitespace
            if buffer.text.strip():
                # Normal submit
                buffer.validate_and_handle()
            # If empty, do nothing (don't submit)

    # Alt+Enter for newlines (press ESC then Enter, or Option+Enter on Mac)
    @kb.add('escape', 'enter')
    def _(event):
        """Alt+Enter inserts a newline for multi-line input."""
        event.current_buffer.insert_text('\n')

    # Ctrl+E to open in external editor
    @kb.add('c-e')
    def _(event):
        """Open the current input in an external editor (nano by default)."""
        event.current_buffer.open_in_editor()

    # Create history file path
    history_file = Path.home() / ".deepagents" / assistant_id / "history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    # Create the session
    session = PromptSession(
        message=HTML(f'<style fg="{COLORS["user"]}">></style> '),
        multiline=True,  # Keep multiline support but Enter submits
        history=FileHistory(str(history_file)),
        key_bindings=kb,
        completer=merge_completers([CommandCompleter(), BashCompleter(), FilePathCompleter()]),
        editing_mode=EditingMode.EMACS,
        complete_while_typing=True,  # Show completions as you type
        mouse_support=False,
        enable_open_in_editor=True,  # Allow Ctrl+X Ctrl+E to open external editor
    )

    return session


def render_todo_list(todos: list[dict]) -> None:
    """Render todo list as a rich Panel with checkboxes."""
    if not todos:
        return

    lines = []
    for todo in todos:
        status = todo.get("status", "pending")
        content = todo.get("content", "")

        if status == "completed":
            icon = "☑"
            style = "green"
        elif status == "in_progress":
            icon = "⏳"
            style = "yellow"
        else:  # pending
            icon = "☐"
            style = "dim"

        lines.append(f"[{style}]{icon} {content}[/{style}]")

    panel = Panel(
        "\n".join(lines),
        title="[bold]Task List[/bold]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1)
    )
    console.print(panel)


def prompt_for_shell_approval(action_request: dict) -> dict:
    """Prompt user to approve/reject a shell command with arrow key navigation."""
    import sys
    import tty
    import termios

    # Display command info first
    console.print()
    console.print(Panel(
        f"[bold yellow]⚠️  Shell Command Requires Approval[/bold yellow]\n\n"
        f"{action_request.get('description', 'No description available')}",
        border_style="yellow",
        box=box.ROUNDED,
        padding=(0, 1)
    ))
    console.print()

    options = ["approve", "reject"]
    selected = 0  # Start with approve selected

    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        try:
            tty.setraw(fd)

            while True:
                # Clear and redraw menu
                sys.stdout.write('\r\033[K')  # Clear line

                # Display options with ANSI color codes
                for i, option in enumerate(options):
                    if i == selected:
                        if option == "approve":
                            # Green bold with arrow
                            sys.stdout.write('\033[1;32m→ ✓ Approve\033[0m')
                        else:
                            # Red bold with arrow
                            sys.stdout.write('\033[1;31m→ ✗ Reject\033[0m')
                    else:
                        if option == "approve":
                            # Dim white
                            sys.stdout.write('\033[2m  ✓ Approve\033[0m')
                        else:
                            # Dim white
                            sys.stdout.write('\033[2m  ✗ Reject\033[0m')

                    if i < len(options) - 1:
                        sys.stdout.write('  ')

                sys.stdout.flush()

                # Read key
                char = sys.stdin.read(1)

                if char == '\x1b':  # ESC sequence (arrow keys)
                    next1 = sys.stdin.read(1)
                    next2 = sys.stdin.read(1)
                    if next1 == '[':
                        if next2 == 'C':  # Right arrow
                            selected = (selected + 1) % len(options)
                        elif next2 == 'D':  # Left arrow
                            selected = (selected - 1) % len(options)
                        elif next2 == 'B':  # Down arrow
                            selected = (selected + 1) % len(options)
                        elif next2 == 'A':  # Up arrow
                            selected = (selected - 1) % len(options)
                elif char == '\r' or char == '\n':  # Enter
                    sys.stdout.write('\n')
                    break
                elif char == '\x03':  # Ctrl+C
                    sys.stdout.write('\n')
                    raise KeyboardInterrupt()
                elif char.lower() == 'a':
                    selected = 0
                    sys.stdout.write('\n')
                    break
                elif char.lower() == 'r':
                    selected = 1
                    sys.stdout.write('\n')
                    break

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    except (termios.error, AttributeError):
        # Fallback for non-Unix systems
        console.print("  [bold green]✓ (A)pprove[/bold green]  (default)")
        console.print("  [bold red]✗ (R)eject[/bold red]")
        choice = input("\nChoice (A/R, default=Approve): ").strip().lower()
        if choice == 'r' or choice == 'reject':
            selected = 1
        else:
            selected = 0

    console.print()

    # Return decision based on selection
    if selected == 0:
        return {"type": "approve"}
    else:
        return {"type": "reject", "message": "User rejected the command"}


def execute_task(user_input: str, agent, assistant_id: str | None, token_tracker: TokenTracker | None = None):
    """Execute any task by passing it directly to the AI agent."""
    console.print()

    # Parse file mentions and inject content if any
    prompt_text, mentioned_files = parse_file_mentions(user_input)

    if mentioned_files:
        context_parts = [prompt_text, "\n\n## Referenced Files\n"]
        for file_path in mentioned_files:
            try:
                content = file_path.read_text()
                # Limit file content to reasonable size
                if len(content) > 50000:
                    content = content[:50000] + "\n... (file truncated)"
                context_parts.append(f"\n### {file_path.name}\nPath: `{file_path}`\n```\n{content}\n```")
            except Exception as e:
                context_parts.append(f"\n### {file_path.name}\n[Error reading file: {e}]")

        final_input = "\n".join(context_parts)
    else:
        final_input = prompt_text

    config = {
        "configurable": {"thread_id": "main"},
        "metadata": {"assistant_id": assistant_id} if assistant_id else {}
    }

    has_responded = False
    captured_input_tokens = 0
    captured_output_tokens = 0
    current_todos = None  # Track current todo list state

    status = console.status(f"[bold {COLORS['thinking']}]Agent is thinking...", spinner="dots")
    status.start()
    spinner_active = True

    tool_icons = {
        "read_file": "📖",
        "write_file": "✏️",
        "edit_file": "✂️",
        "ls": "📁",
        "glob": "🔍",
        "grep": "🔎",
        "shell": "⚡",
        "web_search": "🌐",
        "http_request": "🌍",
        "task": "🤖",
        "write_todos": "📋",
    }

    # Stream input - may need to loop if there are interrupts
    stream_input = {"messages": [{"role": "user", "content": final_input}]}

    try:
        while True:
            interrupt_occurred = False
            hitl_response = None
            suppress_resumed_output = False

            for chunk in agent.stream(
                stream_input,
                stream_mode=["messages", "updates"],  # Dual-mode for HITL support
                subgraphs=True,
                config=config,
                durability="exit",
            ):
                # Unpack chunk - with subgraphs=True and dual-mode, it's (namespace, stream_mode, data)
                if not isinstance(chunk, tuple) or len(chunk) != 3:
                    continue

                namespace, current_stream_mode, data = chunk

                # Handle UPDATES stream - for interrupts and todos
                if current_stream_mode == "updates":
                    if not isinstance(data, dict):
                        continue

                    # Check for interrupts
                    if "__interrupt__" in data:
                        interrupt_data = data["__interrupt__"]
                        if interrupt_data:
                            interrupt_obj = interrupt_data[0] if isinstance(interrupt_data, tuple) else interrupt_data
                            hitl_request = interrupt_obj.value if hasattr(interrupt_obj, 'value') else interrupt_obj

                            # Stop spinner for approval prompt
                            if spinner_active:
                                status.stop()
                                spinner_active = False

                            # Handle human-in-the-loop approval
                            decisions = []
                            for action_request in hitl_request.get("action_requests", []):
                                decision = prompt_for_shell_approval(action_request)
                                decisions.append(decision)

                            suppress_resumed_output = any(decision.get("type") == "reject" for decision in decisions)
                            hitl_response = {"decisions": decisions}
                            interrupt_occurred = True
                            break

                    # Extract chunk_data from updates for todo checking
                    chunk_data = list(data.values())[0] if data else None
                    if chunk_data and isinstance(chunk_data, dict):
                        # Check for todo updates
                        if "todos" in chunk_data:
                            new_todos = chunk_data["todos"]
                            if new_todos != current_todos:
                                current_todos = new_todos
                                # Stop spinner before rendering todos
                                if spinner_active:
                                    status.stop()
                                    spinner_active = False
                                console.print()
                                render_todo_list(new_todos)
                                console.print()

                # Handle MESSAGES stream - for content and tool calls
                elif current_stream_mode == "messages":
                    # Messages stream returns (message, metadata) tuples
                    if not isinstance(data, tuple) or len(data) != 2:
                        continue


                    message, metadata = data

                    if isinstance(message, ToolMessage):
                        # Tool results are sent to the agent, not displayed to users
                        # Exception: show shell command errors to help with debugging
                        tool_name = getattr(message, "name", "")
                        tool_status = getattr(message, "status", "success")

                        if tool_name == "shell" and tool_status != "success":
                            tool_content = format_tool_message_content(message.content)
                            if tool_content:
                                if spinner_active:
                                    status.stop()
                                    spinner_active = False
                                console.print()
                                console.print(tool_content, style="red")
                                console.print()

                        # For all other tools (web_search, http_request, etc.),
                        # results are hidden from user - agent will process and respond
                        continue

                    # Check if this is an AIMessageChunk
                    if not hasattr(message, 'content_blocks'):
                        # Fallback for messages without content_blocks
                        continue

                    # Extract token usage if available
                    if token_tracker and hasattr(message, 'usage_metadata'):
                        usage = message.usage_metadata
                        if usage:
                            input_toks = usage.get('input_tokens', 0)
                            output_toks = usage.get('output_tokens', 0)
                            if input_toks or output_toks:
                                captured_input_tokens = max(captured_input_tokens, input_toks)
                                captured_output_tokens = max(captured_output_tokens, output_toks)

                    # Process content blocks (this is the key fix!)
                    for block in message.content_blocks:
                        block_type = block.get("type")

                        # Handle text blocks
                        if block_type == "text":
                            text = block.get("text", "")
                            if text:
                                if spinner_active:
                                    status.stop()
                                    spinner_active = False

                                if not has_responded:
                                    console.print("● ", style=COLORS["agent"], end="", markup=False)
                                    has_responded = True

                                # Print the text chunk directly (no cumulative diffing needed)
                                console.print(text, style=COLORS["agent"], end="", markup=False)

                        # Handle reasoning blocks
                        elif block_type == "reasoning":
                            reasoning = block.get("reasoning", "")
                            if reasoning:
                                if spinner_active:
                                    status.stop()
                                    spinner_active = False
                                # Could display reasoning differently if desired
                                # For now, skip it or handle minimally

                        # Handle tool call chunks
                        elif block_type == "tool_call_chunk":
                            tool_name = block.get("name")
                            tool_args = block.get("args", "")
                            tool_id = block.get("id")

                            # Only display when we have a complete tool call (name is present)
                            if tool_name:
                                icon = tool_icons.get(tool_name, "🔧")

                                if spinner_active:
                                    status.stop()

                                # Display tool call
                                if has_responded:
                                    console.print()  # New line after text

                                # Try to parse args if it's a string
                                try:
                                    if isinstance(tool_args, str) and tool_args:
                                        parsed_args = json.loads(tool_args)
                                        args_str = ", ".join(
                                            f"{k}={truncate_value(str(v), 50)}"
                                            for k, v in parsed_args.items()
                                        )
                                    else:
                                        args_str = str(tool_args)
                                except:
                                    args_str = str(tool_args)

                                console.print(f"  {icon} {tool_name}({args_str})", style=f"dim {COLORS['tool']}")

                                if spinner_active:
                                    status.start()

            # After streaming loop - handle interrupt if it occurred
            if interrupt_occurred and hitl_response:
                if suppress_resumed_output:
                    if spinner_active:
                        status.stop()
                        spinner_active = False
                    try:
                        agent.invoke(Command(resume=hitl_response), config=config)
                    except Exception as err:
                        console.print(f"[red]Error resuming after rejection: {err}[/red]")
                    finally:
                        console.print("\nCommand rejected. Returning to prompt.\n", style=COLORS["dim"])
                    return

                # Resume the agent with the human decision
                stream_input = Command(resume=hitl_response)
                # Continue the while loop to restream
            else:
                # No interrupt, break out of while loop
                break

    except KeyboardInterrupt:
        # User pressed Ctrl+C - clean up and exit gracefully
        if spinner_active:
            status.stop()
        console.print("\n[yellow]Interrupted by user[/yellow]\n")
        return

    if spinner_active:
        status.stop()

    if has_responded:
        console.print()

        # Display token usage if available
        if token_tracker and (captured_input_tokens or captured_output_tokens):
            token_tracker.add(captured_input_tokens, captured_output_tokens)
            token_tracker.display_last()

        console.print()


async def simple_cli(agent, assistant_id: str | None):
    """Main CLI loop."""
    console.clear()
    console.print(DEEP_AGENTS_ASCII, style=f"bold {COLORS['primary']}")
    console.print()

    if tavily_client is None:
        console.print(f"[yellow]⚠ Web search disabled:[/yellow] TAVILY_API_KEY not found.", style=COLORS["dim"])
        console.print(f"  To enable web search, set your Tavily API key:", style=COLORS["dim"])
        console.print(f"    export TAVILY_API_KEY=your_api_key_here", style=COLORS["dim"])
        console.print(f"  Or add it to your .env file. Get your key at: https://tavily.com", style=COLORS["dim"])
        console.print()

    console.print("... Ready to code! What would you like to build?", style=COLORS["agent"])
    console.print(f"  [dim]Working directory: {Path.cwd()}[/dim]")
    console.print()
    console.print(f"  Tips: Enter to submit, Alt+Enter for newline, Ctrl+E for editor, /help for commands", style=f"dim {COLORS['dim']}")
    console.print()

    # Create prompt session and token tracker
    session = create_prompt_session(assistant_id)
    token_tracker = TokenTracker()

    while True:
        try:
            user_input = await session.prompt_async()
            user_input = user_input.strip()
        except EOFError:
            break
        except KeyboardInterrupt:
            console.print()
            break

        if not user_input:
            continue

        # Check for slash commands first
        if user_input.startswith('/'):
            result = handle_command(user_input, agent, token_tracker)
            if result == 'exit':
                console.print(f"\nGoodbye!", style=COLORS["primary"])
                break
            elif result:
                # Command was handled, continue to next input
                continue

        # Check for bash commands (!)
        if user_input.startswith('!'):
            execute_bash_command(user_input)
            continue

        # Handle regular quit keywords
        if user_input.lower() in ["quit", "exit", "q"]:
            console.print(f"\nGoodbye!", style=COLORS["primary"])
            break

        execute_task(user_input, agent, assistant_id, token_tracker)


def list_agents():
    """List all available agents."""
    agents_dir = Path.home() / ".deepagents"
    
    if not agents_dir.exists() or not any(agents_dir.iterdir()):
        console.print("[yellow]No agents found.[/yellow]")
        console.print(f"[dim]Agents will be created in ~/.deepagents/ when you first use them.[/dim]", style=COLORS["dim"])
        return
    
    console.print(f"\n[bold]Available Agents:[/bold]\n", style=COLORS["primary"])
    
    for agent_path in sorted(agents_dir.iterdir()):
        if agent_path.is_dir():
            agent_name = agent_path.name
            agent_md = agent_path / "agent.md"
            
            if agent_md.exists():
                console.print(f"  • [bold]{agent_name}[/bold]", style=COLORS["primary"])
                console.print(f"    {agent_path}", style=COLORS["dim"])
            else:
                console.print(f"  • [bold]{agent_name}[/bold] [dim](incomplete)[/dim]", style=COLORS["tool"])
                console.print(f"    {agent_path}", style=COLORS["dim"])
    
    console.print()


def reset_agent(agent_name: str, source_agent: str = None):
    """Reset an agent to default or copy from another agent."""
    agents_dir = Path.home() / ".deepagents"
    agent_dir = agents_dir / agent_name
    
    if source_agent:
        source_dir = agents_dir / source_agent
        source_md = source_dir / "agent.md"
        
        if not source_md.exists():
            console.print(f"[bold red]Error:[/bold red] Source agent '{source_agent}' not found or has no agent.md")
            return
        
        source_content = source_md.read_text()
        action_desc = f"contents of agent '{source_agent}'"
    else:
        source_content = get_default_coding_instructions()
        action_desc = "default"
    
    if agent_dir.exists():
        shutil.rmtree(agent_dir)
        console.print(f"Removed existing agent directory: {agent_dir}", style=COLORS["tool"])
    
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_md = agent_dir / "agent.md"
    agent_md.write_text(source_content)
    
    console.print(f"✓ Agent '{agent_name}' reset to {action_desc}", style=COLORS["primary"])
    console.print(f"Location: {agent_dir}\n", style=COLORS["dim"])


def show_help():
    """Show help information."""
    console.print()
    console.print(DEEP_AGENTS_ASCII, style=f"bold {COLORS['primary']}")
    console.print()
    
    console.print("[bold]Usage:[/bold]", style=COLORS["primary"])
    console.print("  deepagents [--agent NAME]                      Start interactive session")
    console.print("  deepagents list                                List all available agents")
    console.print("  deepagents reset --agent AGENT                 Reset agent to default prompt")
    console.print("  deepagents reset --agent AGENT --target SOURCE Reset agent to copy of another agent")
    console.print("  deepagents help                                Show this help message")
    console.print()
    
    console.print("[bold]Examples:[/bold]", style=COLORS["primary"])
    console.print("  deepagents                              # Start with default agent", style=COLORS["dim"])
    console.print("  deepagents --agent mybot                # Start with agent named 'mybot'", style=COLORS["dim"])
    console.print("  deepagents list                         # List all agents", style=COLORS["dim"])
    console.print("  deepagents reset --agent mybot          # Reset mybot to default", style=COLORS["dim"])
    console.print("  deepagents reset --agent mybot --target other # Reset mybot to copy of 'other' agent", style=COLORS["dim"])
    console.print()
    
    console.print("[bold]Long-term Memory:[/bold]", style=COLORS["primary"])
    console.print("  By default, long-term memory is ENABLED using agent name 'agent'.", style=COLORS["dim"])
    console.print("  Memory includes:", style=COLORS["dim"])
    console.print("  - Persistent agent.md file with your instructions", style=COLORS["dim"])
    console.print("  - /memories/ folder for storing context across sessions", style=COLORS["dim"])
    console.print()
    
    console.print("[bold]Agent Storage:[/bold]", style=COLORS["primary"])
    console.print("  Agents are stored in: ~/.deepagents/AGENT_NAME/", style=COLORS["dim"])
    console.print("  Each agent has an agent.md file containing its prompt", style=COLORS["dim"])
    console.print()
    
    console.print("[bold]Interactive Features:[/bold]", style=COLORS["primary"])
    console.print("  Enter           Submit your message", style=COLORS["dim"])
    console.print("  Alt+Enter       Insert newline for multi-line (Option+Enter or ESC then Enter)", style=COLORS["dim"])
    console.print("  Ctrl+J          Insert newline (alternative)", style=COLORS["dim"])
    console.print("  Arrow keys      Navigate input and command history", style=COLORS["dim"])
    console.print("  @filename       Type @ to auto-complete files and inject content", style=COLORS["dim"])
    console.print("  /command        Type / to see available commands (auto-completes)", style=COLORS["dim"])
    console.print()

    console.print("[bold]Interactive Commands:[/bold]", style=COLORS["primary"])
    console.print("  /help           Show available commands and features", style=COLORS["dim"])
    console.print("  /clear          Clear screen and reset conversation", style=COLORS["dim"])
    console.print("  /tokens         Show token usage for current session", style=COLORS["dim"])
    console.print("  /quit, /exit    Exit the session", style=COLORS["dim"])
    console.print("  quit, exit, q   Exit the session (just type and press Enter)", style=COLORS["dim"])
    console.print()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="DeepAgents - AI Coding Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # List command
    subparsers.add_parser("list", help="List all available agents")
    
    # Help command
    subparsers.add_parser("help", help="Show help information")
    
    # Reset command
    reset_parser = subparsers.add_parser("reset", help="Reset an agent")
    reset_parser.add_argument("--agent", required=True, help="Name of agent to reset")
    reset_parser.add_argument("--target", dest="source_agent", help="Copy prompt from another agent")
    
    # Default interactive mode
    parser.add_argument(
        "--agent",
        default="agent",
        help="Agent identifier for separate memory stores (default: agent).",
    )
    
    return parser.parse_args()


async def main(assistant_id: str):
    """Main entry point."""

    # Create the model (checks API keys)
    model = create_model()

    # Create agent with conditional tools
    tools = [http_request]
    if tavily_client is not None:
        tools.append(web_search)

    shell_middleware = ResumableShellToolMiddleware(
        workspace_root=os.getcwd(),
        execution_policy=HostExecutionPolicy()
    )

    # For long-term memory, point to ~/.deepagents/AGENT_NAME/ with /memories/ prefix
    agent_dir = Path.home() / ".deepagents" / assistant_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_md = agent_dir / "agent.md"
    if not agent_md.exists():
        source_content = get_default_coding_instructions()
        agent_md.write_text(source_content)

    # Long-term backend - rooted at agent directory
    # This handles both /memories/ files and /agent.md
    long_term_backend = FilesystemBackend(root_dir=agent_dir, virtual_mode=True)

    # Composite backend: current working directory for default, agent directory for /memories/
    backend = CompositeBackend(
        default=FilesystemBackend(),
        routes={"/memories/": long_term_backend}
    )

    # Use the same backend for agent memory middleware
    agent_middleware = [AgentMemoryMiddleware(backend=long_term_backend, memory_path="/memories/"), shell_middleware]
    system_prompt = f"""### Current Working Directory

The filesystem backend is currently operating in: `{Path.cwd()}`

### Human-in-the-Loop Tool Approval

Some tool calls require user approval before execution. When a tool call is rejected by the user:
1. Accept their decision immediately - do NOT retry the same command
2. Explain that you understand they rejected the action
3. Suggest an alternative approach or ask for clarification
4. Never attempt the exact same rejected command again

Respect the user's decisions and work with them collaboratively.

### Web Search Tool Usage

When you use the web_search tool:
1. The tool will return search results with titles, URLs, and content excerpts
2. You MUST read and process these results, then respond naturally to the user
3. NEVER show raw JSON or tool results directly to the user
4. Synthesize the information from multiple sources into a coherent answer
5. Cite your sources by mentioning page titles or URLs when relevant
6. If the search doesn't find what you need, explain what you found and ask clarifying questions

The user only sees your text responses - not tool results. Always provide a complete, natural language answer after using web_search."""

    # Configure human-in-the-loop for shell commands
    shell_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject", "edit"],
        "description": lambda tool_call, state, runtime: (
            f"Shell Command: {tool_call['args'].get('command', 'N/A')}\n"
            f"Working Directory: {os.getcwd()}"
        )
    }

    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        backend=backend,
        middleware=agent_middleware,
        interrupt_on={"shell": shell_interrupt_config},
    ).with_config(config)
    
    agent.checkpointer = InMemorySaver()
    
    try:
        await simple_cli(agent, assistant_id)
    except KeyboardInterrupt:
        console.print(f"\n\nGoodbye!", style=COLORS["primary"])
    except Exception as e:
        console.print(f"\n[bold red]❌ Error:[/bold red] {e}\n")


def cli_main():
    """Entry point for console script."""
    try:
        args = parse_args()

        if args.command == "help":
            show_help()
        elif args.command == "list":
            list_agents()
        elif args.command == "reset":
            reset_agent(args.agent, args.source_agent)
        else:
            # API key validation happens in create_model()
            asyncio.run(main(args.agent))
    except KeyboardInterrupt:
        # Clean exit on Ctrl+C - suppress ugly traceback
        console.print("\n\n[yellow]Interrupted[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    cli_main()
