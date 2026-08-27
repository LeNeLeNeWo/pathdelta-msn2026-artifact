"""
Executor module for Kathara and Docker operations.

This module provides the KatharaExecutor class that abstracts all infrastructure
interactions, providing a clean interface for the Verification Layer.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# Timeout constants
DOCKER_TIMEOUT = 120
KATHARA_TIMEOUT = 300


@dataclass
class ExecutionResult:
    """Result of an execution operation."""
    success: bool
    stdout: str
    stderr: str
    return_code: int


class ExecutionError(Exception):
    """Base exception for execution layer errors."""
    pass


class DockerUnavailableError(ExecutionError):
    """Raised when Docker is not available."""
    pass


class KatharaUnavailableError(ExecutionError):
    """Raised when Kathara is not available."""
    pass


class ExecutionTimeoutError(ExecutionError):
    """Raised when an execution times out."""
    pass


class KatharaExecutor:
    """
    Executor for Kathara and Docker operations.
    
    This class abstracts all infrastructure interactions, providing
    a clean interface for the Verification Layer.
    """
    
    def __init__(
        self,
        docker_timeout: int = DOCKER_TIMEOUT,
        kathara_timeout: int = KATHARA_TIMEOUT,
    ):
        """
        Initialize executor with timeout settings.
        
        Args:
            docker_timeout: Timeout for Docker operations in seconds.
            kathara_timeout: Timeout for Kathara operations in seconds.
        """
        self.docker_timeout = docker_timeout
        self.kathara_timeout = kathara_timeout
    
    def is_docker_available(self) -> bool:
        """
        Check if Docker is available and running.
        
        Returns:
            True if Docker is available and responsive, False otherwise.
        """
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
    
    def is_kathara_available(self) -> bool:
        """
        Check if Kathara is installed and functional.
        
        Executes `kathara --version` and checks the return code.
        
        Returns:
            True if Kathara is available, False otherwise.
        """
        try:
            result = subprocess.run(
                ["kathara", "--version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    
    def start_lab(self, lab_dir: Path) -> ExecutionResult:
        """
        Start a Kathara lab.
        
        Args:
            lab_dir: Path to the lab directory containing lab.conf.
        
        Returns:
            ExecutionResult with operation outcome.
        
        Raises:
            KatharaUnavailableError: If Kathara is not available.
            ExecutionTimeoutError: If operation times out.
        """
        if not self.is_kathara_available():
            raise KatharaUnavailableError("Kathara not available")
        
        try:
            result = subprocess.run(
                ["kathara", "lstart", "-d", str(lab_dir)],
                capture_output=True,
                text=True,
                timeout=self.kathara_timeout,
            )
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        except subprocess.TimeoutExpired as e:
            raise ExecutionTimeoutError(
                f"Kathara lstart timed out after {self.kathara_timeout}s"
            ) from e
        except OSError as e:
            raise KatharaUnavailableError(f"Kathara execution error: {e}") from e
    
    def stop_lab(self, lab_dir: Optional[Path] = None) -> ExecutionResult:
        """
        Stop and clean up a Kathara lab.
        
        Args:
            lab_dir: Optional path to specific lab. If None, cleans all.
        
        Returns:
            ExecutionResult with operation outcome.
        """
        # Skip cleanup if Kathara is not available
        if not self.is_kathara_available():
            return ExecutionResult(
                success=True,
                stdout="",
                stderr="Kathara not available, skipping cleanup",
                return_code=0,
            )
        
        try:
            cmd = ["kathara", "lclean"]
            if lab_dir:
                cmd.extend(["-d", str(lab_dir)])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.kathara_timeout,
            )
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            # Best effort cleanup - don't raise on timeout
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Cleanup timed out",
                return_code=-1,
            )
        except OSError as e:
            # Best effort cleanup - don't raise on error
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                return_code=-1,
            )

    
    def exec_command(
        self,
        device: str,
        command: str,
        lab_dir: Path,
    ) -> ExecutionResult:
        """
        Execute a command inside a Kathara container.
        
        Uses docker exec directly to avoid Kathara's -c argument interception.
        
        Args:
            device: Device name to execute command on.
            command: Command to execute.
            lab_dir: Path to the lab directory.
        
        Returns:
            ExecutionResult with command output.
        
        Raises:
            KatharaUnavailableError: If Kathara is not available.
            ExecutionTimeoutError: If operation times out.
        """
        if not self.is_kathara_available():
            raise KatharaUnavailableError("Kathara not available")
        
        try:
            # Find container name for this device using sudo for docker access
            container_result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={device}"],
                capture_output=True, text=True, timeout=10
            )
            containers = [c for c in container_result.stdout.strip().split('\n') if c and device in c]
            if not containers:
                return ExecutionResult(success=False, stdout="", stderr=f"Container for {device} not found", return_code=1)
            container_name = containers[0]
            
            # Use sudo docker exec with bash -c to properly handle the command
            result = subprocess.run(
                ["docker", "exec", container_name, "bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=self.kathara_timeout,
            )
            return ExecutionResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
            )
        except subprocess.TimeoutExpired as e:
            raise ExecutionTimeoutError(
                f"Docker exec timed out after {self.kathara_timeout}s"
            ) from e
        except OSError as e:
            raise KatharaUnavailableError(f"Docker execution error: {e}") from e

    
    def run_temp_container(
        self,
        image: str,
        command: List[str],
        mounts: Dict[str, str],
        timeout: Optional[int] = None,
        entrypoint: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Run a temporary Docker container with retry for WSL2 transient failures.
        
        Args:
            image: Docker image to use.
            command: Command to execute.
            mounts: Dict mapping host paths to container paths.
            timeout: Optional timeout override.
        
        Returns:
            ExecutionResult with container output.
        
        Raises:
            DockerUnavailableError: If Docker is not available after retries.
            ExecutionTimeoutError: If operation times out after retries.
        """
        # Proceed directly to run to avoid relying on fragile docker info cache
        # The retry loop below handles actual execution failures more gracefully.
        
        effective_timeout = timeout if timeout is not None else self.docker_timeout
        
        def _lightweight_docker_cleanup():
            try:
                subprocess.run(
                    ["docker", "container", "prune", "-f"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except Exception:
                pass
            try:
                subprocess.run(
                    [
                        "bash",
                        "-lc",
                        "ids=$(docker ps -aq --filter label=pathdelta-temp=true); "
                        "if [ -n \"$ids\" ]; then docker rm -f $ids >/dev/null 2>&1; fi",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except Exception:
                pass

        # Retry logic for transient WSL2 Docker failures
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            import time
            container_name = f"pathdelta-temp-{int(time.time() * 1000)}-{attempt}"
            docker_cmd = ["docker", "run", "--rm", "--name", container_name, "--label", "pathdelta-temp=true"]
            if entrypoint:
                docker_cmd.extend(["--entrypoint", entrypoint])

            for host_path, container_path in mounts.items():
                docker_cmd.extend(["-v", f"{host_path}:{container_path}:ro"])

            docker_cmd.append(image)
            docker_cmd.extend(command)

            try:
                result = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                )
                
                # Check for transient file-mount errors (WSL2 race condition)
                stderr_lower = (result.stderr or "").lower()
                stdout_lower = (result.stdout or "").lower()
                combined = stderr_lower + stdout_lower
                
                if result.returncode != 0 and any(t in combined for t in [
                    "can't open config", "no such file", "permission denied",
                ]):
                    # Transient file-mount error — retry after brief wait
                    if attempt < max_retries - 1:
                        import time; time.sleep(3)
                        continue
                
                if result.returncode != 0 and "no space left on device" in combined:
                    if attempt < max_retries - 1:
                        _lightweight_docker_cleanup()
                        time.sleep(3)
                        continue

                return ExecutionResult(
                    success=result.returncode == 0,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    return_code=result.returncode,
                )
            except subprocess.TimeoutExpired as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                raise ExecutionTimeoutError(
                    f"Docker run timed out after {effective_timeout}s (attempt {attempt+1}/{max_retries})"
                ) from e
            except OSError as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                raise DockerUnavailableError(f"Docker execution error: {e}") from e
            finally:
                try:
                    subprocess.run(
                        ["docker", "rm", "-f", container_name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                except Exception:
                    pass
        
        # Should not reach here, but safety net
        raise DockerUnavailableError(f"Docker failed after {max_retries} attempts: {last_error}")
    
    # Cache for Docker availability check
    _docker_available_cache: Optional[bool] = None
    _docker_cache_time: float = 0.0
    _DOCKER_CACHE_TTL: float = 60.0  # Re-check every 60 seconds
    
    def _check_docker_cached(self) -> bool:
        """Check Docker availability with caching (avoids repeated `docker info` calls)."""
        import time as _t
        now = _t.time()
        if (self._docker_available_cache is not None 
                and now - self._docker_cache_time < self._DOCKER_CACHE_TTL):
            return self._docker_available_cache
        
        self._docker_available_cache = self.is_docker_available()
        self._docker_cache_time = now
        return self._docker_available_cache
