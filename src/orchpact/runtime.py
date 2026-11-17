"""Execution adapters used by the runtime and tests."""
import httpx
from .adapters.providers import Providers
from .services.executor import CrewAIExecutor
from .services.runner import Runner
