"""MCP server command for deepctl."""

import os
from typing import Any, Dict, List, Optional

from deepctl_core import AuthManager, BaseCommand, Config, DeepgramClient
from mcp.server.fastmcp import Context, FastMCP
from rich.console import Console

from .gnosis import GnosisClient
from .models import MCPServerResult, TransportType

console = Console()


class McpCommand(BaseCommand):
    """MCP server command for interacting with Deepgram's AI assistant."""

    name = "mcp"
    help = "Run an MCP server that connects to Deepgram's AI assistant service"
    short_help = "Run MCP server for Deepgram AI"

    # MCP doesn't require existing auth to start, but can use it
    requires_auth = False
    requires_project = False
    ci_friendly = True

    def get_arguments(self) -> List[Dict[str, Any]]:
        """Get command arguments and options."""
        return [
            {
                "names": ["--transport", "-t"],
                "help": (
                    "Transport mode: stdio (default), sse, "
                    "or streamable-http"
                ),
                "type": str,
                "default": "stdio",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--port", "-p"],
                "help": "Port number for HTTP server (default: 8000)",
                "type": int,
                "default": 8000,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--host"],
                "help": "Host address for HTTP server (default: 127.0.0.1)",
                "type": str,
                "default": "127.0.0.1",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--api-key"],
                "help": (
                    "Override API key for Deepgram AI service (falls back to "
                    "profile or DEEPGRAM_API_KEY env var)"
                ),
                "type": str,
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--gnosis-url"],
                "help": "Base URL for Deepgram AI service",
                "type": str,
                "default": "https://gnosis.deepgram.com",
                "required": False,
                "is_option": True,
            },
            {
                "names": ["--debug"],
                "help": "Enable debug logging",
                "is_flag": True,
                "is_option": True,
            },
        ]

    def handle(
        self,
        config: Config,
        auth_manager: AuthManager,
        client: DeepgramClient,
        **kwargs: Any,
    ) -> Any:
        """Handle MCP server command."""
        transport = kwargs.get("transport", "stdio").lower()
        port = kwargs.get("port", 8000)
        host = kwargs.get("host", "127.0.0.1")
        gnosis_api_key = kwargs.get("api_key") or os.getenv("DEEPGRAM_API_KEY")
        # Fallback to stored credentials if no key provided
        if not gnosis_api_key:
            gnosis_api_key = auth_manager.get_api_key()
        gnosis_url = kwargs.get("gnosis_url", "https://gnosis.deepgram.com")
        debug = kwargs.get("debug", False)

        # Validate transport type
        valid_transports = ["stdio", "sse", "streamable-http"]
        if transport not in valid_transports:
            console.print(
                f"[red]Invalid transport type:[/red] {transport}. "
                f"Must be one of: {', '.join(valid_transports)}"
            )
            return MCPServerResult(
                status="error", message=f"Invalid transport type: {transport}"
            )

        # Store configuration in environment for the MCP server
        if isinstance(gnosis_api_key, str) and gnosis_api_key:
            # Expose for child processes / downstream code
            os.environ["DEEPGRAM_API_KEY"] = gnosis_api_key
        os.environ["DEEPGRAM_GNOSIS_URL"] = gnosis_url
        if debug:
            os.environ["DEEPGRAM_MCP_DEBUG"] = "1"

        # Create and run the MCP server
        mcp_server = create_mcp_server()

        try:
            if transport == "stdio":
                console.print(
                    "[blue]Starting MCP server in stdio mode...[/blue]"
                )
                mcp_server.run()
            elif transport == "sse":
                console.print(
                    f"[blue]Starting MCP SSE server on {host}:{port}...[/blue]"
                )
                mcp_server.run(transport="sse")
            elif transport == "streamable-http":
                console.print(
                    f"[blue]Starting MCP Streamable HTTP server on "
                    f"{host}:{port}...[/blue]"
                )
                mcp_server.run(transport="streamable-http")

            return MCPServerResult(
                status="success",
                message="MCP server stopped",
                transport=TransportType(transport.replace("-", "")),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )

        except KeyboardInterrupt:
            console.print("\n[yellow]MCP server stopped by user[/yellow]")
            return MCPServerResult(
                status="cancelled",
                message="MCP server stopped by user",
                transport=TransportType(transport.replace("-", "")),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )
        except Exception as e:
            console.print(f"[red]Error running MCP server:[/red] {e}")
            return MCPServerResult(
                status="error",
                message=str(e),
                transport=TransportType(transport.replace("-", "")),
                port=port if transport != "stdio" else None,
                host=host if transport != "stdio" else None,
            )


def create_mcp_server() -> FastMCP:
    """Create the MCP server with Deepgram AI assistant tools."""
    # Create FastMCP instance
    mcp = FastMCP("Deepgram AI Assistant")

    # Get configuration from environment
    gnosis_api_key = os.getenv("DEEPGRAM_API_KEY")
    gnosis_url = os.getenv(
        "DEEPGRAM_GNOSIS_URL", "https://gnosis.deepgram.com"
    )
    debug = os.getenv("DEEPGRAM_MCP_DEBUG", "").lower() in ("1", "true", "yes")

    # Debug logging to diagnose URL issue
    if debug:
        import sys

        print(
            f"[DEBUG] DEEPGRAM_GNOSIS_URL from env: {gnosis_url}",
            file=sys.stderr,
        )

    # Create a shared Gnosis client instance
    try:
        gnosis_client = GnosisClient(
            api_key=gnosis_api_key,
            base_url=gnosis_url,
            debug=debug,
        )
    except ValueError:
        # Client will be None if no API key is available
        gnosis_client = None

    @mcp.tool()
    async def ask_question(question: str, ctx: Context[Any, Any, Any]) -> str:
        """Ask questions about Deepgram products and services.

        This is the **catch-all** helper for natural-language queries about
        anything related to Deepgram. Use it for general product or feature
        questions, onboarding advice ("How do I get started with live
        transcription?"), pricing, SDK capabilities, best practices, model
        details, or any other information request that does **not** obviously
        belong to one of the specialised tools below.

        Example questions that should route to this tool:
        • "What is the difference between pre-recorded and live transcription?"
        • "Does Deepgram offer speaker diarization?"
        • "How accurate is Nova-2 on telephone audio?"

        Args:
            question: The question to ask about Deepgram (products, services,
                SDKs, APIs, pricing, etc.)
            ctx: MCP context used by FastMCP
        """
        if not gnosis_client:
            return (
                "Error: Deepgram API key not configured. Please set "
                "DEEPGRAM_API_KEY, use the --api-key flag, or store a "
                "credential."
            )

        if debug:
            await ctx.info(f"Asking question: {question}")

        try:
            return await gnosis_client.ask_question(
                question,
                system_prompt=(
                    "You are a helpful assistant that answers questions about "
                    "Deepgram products and services."
                ),
            )
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def check_api_spec(
        api_type: str = "rest",
        endpoint: str = "",
        ctx: Optional[Context[Any, Any, Any]] = None,
    ) -> str:
        """Retrieve Deepgram **API reference** details.

        Use this tool when the user explicitly asks for endpoint specifics,
        HTTP verbs, WebSocket event schemas, request/response bodies,
        authentication headers, rate limits, or error codes—anything that
        belongs to the formal API specification.

        Example queries that should trigger this tool:
        • "Show me the REST API spec for /v1/listen"
        • "What fields does the WebSocket start message accept?"
        • "Which status codes can the Transcription API return?"

        Args:
            api_type: Either 'rest' or 'websocket'. Defaults to 'rest'.
            endpoint: Optional specific endpoint path (e.g., "/v1/listen").
                Leave blank for an overview.
            ctx: MCP context
        """
        if not gnosis_client:
            return (
                "Error: Deepgram API key not configured. Please set "
                "DEEPGRAM_API_KEY, use the --api-key flag, or store a "
                "credential."
            )

        if ctx and debug:
            await ctx.info(f"Checking API spec: {api_type} {endpoint}")

        prompt = (
            f"Provide the API specification for Deepgram's {api_type.upper()} "
        )
        if endpoint:
            prompt += f"endpoint: {endpoint}"
        else:
            prompt += "endpoints"

        try:
            return await gnosis_client.ask_question(
                prompt,
                system_prompt=(
                    "You are a technical assistant that provides detailed API "
                    "specifications for Deepgram."
                ),
            )
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def get_code_example(
        language: str,
        use_case: str,
        ctx: Optional[Context[Any, Any, Any]] = None,
    ) -> str:
        """Return **ready-to-run code samples** that integrate Deepgram.

        Use this when the user asks for sample code or snippets in a particular
        language or SDK, e.g. "Give me a Python example for real-time
        transcription" or "Show a JavaScript snippet for batch processing".

        Args:
            language: Target programming language (python, javascript,
                typescript, go, java, csharp, etc.)
            use_case: The scenario for the snippet (e.g., "real-time
                transcription", "batch processing").
            ctx: MCP context
        """
        if not gnosis_client:
            return (
                "Error: Deepgram API key not configured. Please set "
                "DEEPGRAM_API_KEY, use the --api-key flag, or store a "
                "credential."
            )

        if ctx and debug:
            await ctx.info(f"Getting code example: {language} for {use_case}")

        prompt = f"Provide a {language} code example for: {use_case}"

        try:
            return await gnosis_client.ask_question(
                prompt,
                system_prompt=(
                    f"You are a code assistant that provides {language} "
                    f"code examples for Deepgram."
                ),
            )
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def search_docs(
        query: str,
        category: str = "all",
        ctx: Optional[Context[Any, Any, Any]] = None,
    ) -> str:
        """Keyword search across official Deepgram documentation.

        Ideal for requests that mention "documentation", "docs", "guide",
        or when the user wants a how-to or tutorial section. The search can
        be narrowed to specific categories like guides, SDK references, or
        API reference.

        Example queries:
        • "Find the docs page about audio formats"
        • "Search the guides for diarization"

        Args:
            query: Search terms to look for.
            category: One of 'guides', 'api-reference', 'sdks', or 'all'.
                Defaults to 'all'.
            ctx: MCP context
        """
        if not gnosis_client:
            return (
                "Error: Deepgram API key not configured. Please set "
                "DEEPGRAM_API_KEY, use the --api-key flag, or store a "
                "credential."
            )

        if ctx and debug:
            await ctx.info(f"Searching docs: {query} in {category}")

        search_prompt = f"Search Deepgram documentation for: {query}"
        if category != "all":
            search_prompt += f" (category: {category})"

        try:
            return await gnosis_client.ask_question(
                search_prompt,
                system_prompt=(
                    "You are a documentation assistant that helps find "
                    "relevant information in Deepgram's docs."
                ),
            )
        except Exception as e:
            return f"Error: {str(e)}"

    return mcp
