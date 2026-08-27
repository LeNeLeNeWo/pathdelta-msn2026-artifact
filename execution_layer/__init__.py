"""
Execution Layer - Infrastructure Operations

This module provides abstractions for running infrastructure operations
including Docker interactions, Kathara lifecycle management, and command execution.

All subprocess and Docker operations should go through this layer.
"""

from execution_layer.executor import (
    KatharaExecutor,
    ExecutionResult,
    ExecutionError,
    DockerUnavailableError,
    KatharaUnavailableError,
    ExecutionTimeoutError,
    DOCKER_TIMEOUT,
    KATHARA_TIMEOUT,
)

__all__ = [
    "KatharaExecutor",
    "ExecutionResult",
    "ExecutionError",
    "DockerUnavailableError",
    "KatharaUnavailableError",
    "ExecutionTimeoutError",
    "DOCKER_TIMEOUT",
    "KATHARA_TIMEOUT",
]
