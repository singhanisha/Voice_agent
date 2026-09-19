"""
LLM module public interface.

Usage:
    from llm import generate_response

    response = generate_response("Hello, are you interested in our property?")
"""

from .llm import generate_voice_response as generate_response

__all__ = ["generate_response"]
