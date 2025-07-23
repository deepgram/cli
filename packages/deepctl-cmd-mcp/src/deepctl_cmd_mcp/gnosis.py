"""Gnosis API client for Deepgram's AI assistant service."""

import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field


class GnosisRequest(BaseModel):
    """Request model for Gnosis API."""

    messages: List[Dict[str, str]] = Field(
        ..., description="List of messages in the conversation"
    )
    model: str = Field(
        default="deepgram", description="Model to use for generation"
    )
    temperature: float = Field(
        default=0.7, description="Temperature for generation"
    )
    max_tokens: Optional[int] = Field(
        default=None, description="Maximum tokens to generate"
    )


class GnosisResponse(BaseModel):
    """Response model from Gnosis API."""

    choices: List[Dict[str, Any]] = Field(
        default_factory=list, description="Response choices"
    )
    usage: Optional[Dict[str, Any]] = Field(
        default=None, description="Token usage information"
    )
    model: Optional[str] = Field(default=None, description="Model used")
    id: Optional[str] = Field(default=None, description="Response ID")
    created: Optional[int] = Field(
        default=None, description="Creation timestamp"
    )


class GnosisClient:
    """Client for interacting with Deepgram's Gnosis API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://gnosis.deepgram.com",
        timeout: float = 30.0,
        debug: bool = False,
    ):
        """Initialize the Gnosis client.

        Args:
            api_key: Deepgram API key. If not provided, will check
                DEEPGRAM_API_KEY env var.
            base_url: Base URL for Gnosis API.
            timeout: Request timeout in seconds.
            debug: Enable debug logging.
        """
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.debug = debug

        if not self.api_key:
            raise ValueError(
                "API key is required. Provide it as a parameter or set "
                "DEEPGRAM_API_KEY environment variable."
            )

    async def call(
        self,
        messages: List[Dict[str, str]],
        model: str = "deepgram",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Make a request to the Gnosis API.

        Args:
            messages: List of message dictionaries with 'role' and 'content'
                keys.
            model: Model to use for generation.
            temperature: Temperature for generation.
            max_tokens: Maximum tokens to generate.

        Returns:
            The response content from Gnosis.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
            Exception: For other errors during the request.
        """
        request = GnosisRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if self.debug:
            print(
                f"[DEBUG] Request: {request.model_dump_json()}",
                file=sys.stderr,
            )

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=request.model_dump(),
                    headers={
                        "Authorization": f"token {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()

                if self.debug:
                    print(
                        f"[DEBUG] Response: {response.text}", file=sys.stderr
                    )

                gnosis_response = GnosisResponse(**response.json())
                if gnosis_response.choices and gnosis_response.choices[0].get(
                    "message", {}
                ).get("content"):
                    return str(
                        gnosis_response.choices[0]["message"]["content"]
                    )

                return "No response from Deepgram AI"
            except httpx.HTTPStatusError as e:
                if self.debug:
                    print(f"[DEBUG] HTTP Error: {e}", file=sys.stderr)
                    print(
                        f"[DEBUG] Response: {e.response.text}", file=sys.stderr
                    )
                return (
                    f"HTTP Error {e.response.status_code}: {e.response.text}"
                )
            except Exception as e:
                error_msg = f"Error calling Deepgram AI: {str(e)}"
                if self.debug:
                    import traceback

                    print(f"[DEBUG] {error_msg}", file=sys.stderr)
                    traceback.print_exc()
                return error_msg

    async def ask_question(
        self, question: str, system_prompt: Optional[str] = None
    ) -> str:
        """Ask a question to Gnosis with an optional system prompt.

        Args:
            question: The question to ask.
            system_prompt: Optional system prompt to set context.

        Returns:
            The response from Gnosis.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": question})

        return await self.call(messages)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> str:
        """Have a conversation with Gnosis.

        Args:
            messages: List of messages in the conversation.
            system_prompt: Optional system prompt to prepend.

        Returns:
            The response from Gnosis.
        """
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        return await self.call(all_messages)


async def main() -> None:
    """Main function for standalone CLI usage."""
    import argparse

    # Create parser
    parser = argparse.ArgumentParser(
        prog="deepctl_cmd_mcp.gnosis",
        description="Interact with Deepgram's AI assistant API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ask a single question
  python -m deepctl_cmd_mcp.gnosis "What is Deepgram?"

  # Ask with a custom system prompt
  python -m deepctl_cmd_mcp.gnosis "Explain streaming" \\
    --system-prompt "You are a technical expert"

  # Interactive chat mode
  python -m deepctl_cmd_mcp.gnosis --chat

  # JSON output for integration
  python -m deepctl_cmd_mcp.gnosis "What models are available?" \\
    --json-output

  # Debug mode to see API calls
  python -m deepctl_cmd_mcp.gnosis "Test question" --debug
""",
    )

    parser.add_argument(
        "question",
        nargs="?",
        help="Question to ask Deepgram AI (omit for interactive chat mode)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://gnosis.deepgram.com",
        help="Base URL for Deepgram AI API",
    )
    parser.add_argument(
        "--api-key",
        help="Deepgram API key (defaults to DEEPGRAM_API_KEY env var)",
    )
    parser.add_argument(
        "--system-prompt",
        help="System prompt to set context",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Enable interactive chat mode",
    )
    parser.add_argument(
        "--model",
        default="deepgram",
        help="Model to use (default: deepgram)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Temperature for generation (default: 0.7)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="Maximum tokens to generate",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="Output raw JSON response",
    )

    args = parser.parse_args()

    # Check if we have a question or chat mode
    if not args.question and not args.chat:
        parser.print_help()
        sys.exit(1)

    try:
        client = GnosisClient(
            api_key=args.api_key,
            base_url=args.url,
            debug=args.debug,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.chat:
            # Interactive chat mode
            print("Deepgram AI Chat ('exit' to quit, 'clear' to reset)")
            print("-" * 60)

            history: list[dict[str, str]] = []
            if args.system_prompt:
                print(f"System: {args.system_prompt}")
                print("-" * 60)

            while True:
                try:
                    user_input = input("\nYou: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if user_input.lower() in ["exit", "quit"]:
                    print("\nGoodbye!")
                    break
                elif user_input.lower() == "clear":
                    history = []
                    print("\nChat history cleared.")
                    continue
                elif not user_input:
                    continue

                # Add to history
                history.append({"role": "user", "content": user_input})

                # Get response
                response = await client.chat(
                    history, system_prompt=args.system_prompt
                )

                # Add response to history
                history.append({"role": "assistant", "content": response})

                # Display response
                print(f"\nDeepgram AI: {response}")

        else:
            # Single question mode
            if args.json_output:
                # For JSON output, get the raw response
                messages = []
                if args.system_prompt:
                    messages.append(
                        {"role": "system", "content": args.system_prompt}
                    )
                messages.append({"role": "user", "content": args.question})

                # We need to modify the client to return the full response
                # For now, just get the text response
                response = await client.ask_question(
                    args.question, args.system_prompt
                )
                result = {
                    "question": args.question,
                    "response": response,
                    "model": args.model,
                }
                print(json.dumps(result, indent=2))
            else:
                response = await client.ask_question(
                    args.question, args.system_prompt
                )
                print(response)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
