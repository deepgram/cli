#!/usr/bin/env python3
"""Example usage of the GnosisClient for programmatic access to Deepgram's Gnosis API."""

import asyncio
import os
from deepctl_cmd_mcp import GnosisClient


async def basic_usage():
    """Demonstrate basic usage of the GnosisClient."""
    print("=== Basic Usage Example ===\n")

    # Initialize client (will use DEEPGRAM_API_KEY env var if not provided)
    client = GnosisClient()

    # Ask a simple question
    response = await client.ask_question("What is Deepgram's Nova model?")
    print("Q: What is Deepgram's Nova model?")
    print(f"A: {response}\n")


async def custom_system_prompt():
    """Demonstrate using custom system prompts."""
    print("=== Custom System Prompt Example ===\n")

    client = GnosisClient()

    # Ask with a technical expert system prompt
    response = await client.ask_question(
        "Explain the difference between streaming and batch transcription",
        system_prompt="You are a technical expert who provides detailed, developer-focused explanations."
    )
    print("Q: Explain the difference between streaming and batch transcription")
    print(f"A: {response}\n")


async def multi_turn_conversation():
    """Demonstrate multi-turn conversations."""
    print("=== Multi-Turn Conversation Example ===\n")

    client = GnosisClient()

    # Build a conversation
    messages = [
        {"role": "user", "content": "What audio formats does Deepgram support?"},
    ]

    # First response
    response1 = await client.chat(messages)
    print("User: What audio formats does Deepgram support?")
    print(f"Assistant: {response1}\n")

    # Add the response to conversation history
    messages.append({"role": "assistant", "content": response1})
    messages.append(
        {"role": "user", "content": "Which format provides the best quality?"})

    # Continue the conversation
    response2 = await client.chat(messages)
    print("User: Which format provides the best quality?")
    print(f"Assistant: {response2}\n")


async def api_specification_example():
    """Demonstrate getting API specifications."""
    print("=== API Specification Example ===\n")

    client = GnosisClient()

    # Get REST API spec
    response = await client.ask_question(
        "Show me the REST API specification for the /v1/listen endpoint",
        system_prompt="You are a technical documentation expert. Provide detailed API specifications."
    )
    print("Q: Show me the REST API specification for the /v1/listen endpoint")
    print(f"A: {response}\n")


async def code_example():
    """Demonstrate getting code examples."""
    print("=== Code Example Request ===\n")

    client = GnosisClient()

    # Get a Python code example
    response = await client.ask_question(
        "Provide a Python code example for real-time transcription using Deepgram",
        system_prompt="You are a code assistant that provides complete, runnable Python examples."
    )
    print("Q: Provide a Python code example for real-time transcription")
    print(f"A: {response}\n")


async def error_handling_example():
    """Demonstrate error handling."""
    print("=== Error Handling Example ===\n")

    try:
        # Try to create client without API key
        os.environ.pop("DEEPGRAM_API_KEY", None)  # Remove env var if exists
        client = GnosisClient()
    except ValueError as e:
        print(f"Expected error when no API key provided: {e}")

    # Now with proper API key
    client = GnosisClient(api_key=os.getenv(
        "DEEPGRAM_API_KEY", "your-api-key-here"))

    try:
        # Make a request
        response = await client.ask_question("What is Deepgram?")
        print(f"\nSuccessful response: {response[:100]}...")
    except Exception as e:
        print(f"Error during API call: {e}")


async def main():
    """Run all examples."""
    examples = [
        basic_usage,
        custom_system_prompt,
        multi_turn_conversation,
        api_specification_example,
        code_example,
        error_handling_example,
    ]

    for example in examples:
        try:
            await example()
            print("-" * 80 + "\n")
        except Exception as e:
            print(f"Error in {example.__name__}: {e}\n")
            print("-" * 80 + "\n")


if __name__ == "__main__":
    print("Deepgram Gnosis Client Examples")
    print("================================\n")
    print("Make sure DEEPGRAM_API_KEY is set in your environment.\n")

    asyncio.run(main())
