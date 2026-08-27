"""
Verifier module for FRR configuration verification.

This module provides the Verifier class that uses the Execution Layer
for all infrastructure operations.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from execution_layer import (
    KatharaExecutor,
    ExecutionResult,
    DockerUnavailableError,
    KatharaUnavailableError,
    ExecutionTimeoutError,
)
from verification_layer.assertions import check_all_neighbors_established
from verification_layer.lab_generator import generate_lab_conf


# FRR Docker image for syntax verification
FRR_DOCKER_IMAGE = "frrouting/frr:v8.4.0"

# Default convergence wait time
DEFAULT_CONVERGENCE_WAIT = 60


@dataclass
class VerificationResult:
    """Result of a verification operation."""
    is_valid: bool
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_tuple(self) -> Tuple[bool, str]:
        """Convert to legacy tuple format for backward compatibility."""
        return (self.is_valid, self.error_message)


@dataclass
class SteeringVerificationResult:
    """
    Result of steering verification.
    
    This dataclass captures the outcome of verifying that traffic steering
    intents are effective at the data plane level.
    
    Attributes:
        is_valid: True if the verification passed, False otherwise.
        intent_type: Type of intent being verified (always "steering").
        expected_nexthop: The expected nexthop (IP or interface) for steering.
        actual_nexthop: The actual nexthop(s) found in the routing table.
        error_message: Descriptive error message if verification failed.
        details: Additional diagnostic information (e.g., all nexthops found).
    
    Requirements: 5.4
    """
    is_valid: bool
    intent_type: str = "steering"
    expected_nexthop: Optional[str] = None
    actual_nexthop: Optional[str] = None
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ECMPVerificationResult:
    """
    Result of ECMP verification.
    
    This dataclass captures the outcome of verifying that ECMP (Equal-Cost
    Multi-Path) routing is correctly configured with the expected number
    of paths.
    
    Attributes:
        is_valid: True if actual_count >= expected_count, False otherwise.
        expected_count: Minimum expected number of ECMP paths.
        actual_count: Actual number of nexthops found in the RIB.
        nexthops: List of all nexthops from the RIB.
        error_message: Descriptive error message if verification failed.
    
    Requirements: 2.3, 2.4
    """
    is_valid: bool
    expected_count: int
    actual_count: int
    nexthops: List[Dict[str, Any]] = field(default_factory=list)
    error_message: str = ""


@dataclass
class BackupPathVerificationResult:
    """
    Result of backup path verification.
    
    This dataclass captures the outcome of verifying that backup path
    routing is correctly configured with proper metric differentiation
    between primary and backup paths.
    
    Attributes:
        is_valid: True if backup has higher metric than primary, False otherwise.
        primary_nexthop: Nexthop with lowest metric (primary path).
        primary_metric: Metric of the primary path.
        backup_nexthop: Nexthop with higher metric (backup path).
        backup_metric: Metric of the backup path.
        error_message: Descriptive error message if verification failed.
    
    Requirements: 3.3, 3.4
    """
    is_valid: bool
    primary_nexthop: Optional[str] = None
    primary_metric: Optional[int] = None
    backup_nexthop: Optional[str] = None
    backup_metric: Optional[int] = None
    error_message: str = ""


@dataclass
class BGPVerificationResult:
    """
    Result of BGP steering verification.
    
    This dataclass captures the outcome of verifying BGP steering intents
    using BGP-specific commands (show bgp ipv4 unicast).
    
    Attributes:
        is_valid: True if verification passed, False otherwise.
        bestpath_exists: True if a BGP bestpath was found for the prefix.
        expected_local_pref: Expected local-preference value (if specified).
        actual_local_pref: Actual local-preference from BGP bestpath.
        expected_nexthop: Expected nexthop (if specified).
        actual_nexthop: Actual nexthop from BGP bestpath.
        rib_cross_check: True if RIB cross-check was performed and passed.
        error_message: Descriptive error message if verification failed.
        details: Additional diagnostic information.
    
    Requirements: 1.1, 1.2
    """
    is_valid: bool
    bestpath_exists: bool = False
    expected_local_pref: Optional[int] = None
    actual_local_pref: Optional[int] = None
    expected_nexthop: Optional[str] = None
    actual_nexthop: Optional[str] = None
    rib_cross_check: bool = False
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OSPFVerificationResult:
    """
    Result of OSPF steering verification.
    
    This dataclass captures the outcome of verifying OSPF steering intents
    using RIB commands (show ip route).
    
    Attributes:
        is_valid: True if verification passed, False otherwise.
        route_exists: True if an OSPF route was found for the prefix.
        expected_metric: Expected OSPF metric/cost value (if specified).
        actual_metric: Actual metric from OSPF route.
        expected_nexthop: Expected nexthop (if specified).
        actual_nexthop: Actual nexthop from OSPF route.
        error_message: Descriptive error message if verification failed.
        details: Additional diagnostic information.
    
    Requirements: 2.1
    """
    is_valid: bool
    route_exists: bool = False
    expected_metric: Optional[int] = None
    actual_metric: Optional[int] = None
    expected_nexthop: Optional[str] = None
    actual_nexthop: Optional[str] = None
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FailoverVerificationResult:
    """
    Result of failure-driven backup path verification.
    
    This dataclass captures the outcome of verifying backup path activation
    by simulating primary path failure. It tracks the state before failure,
    after failure, and after restoration.
    
    Attributes:
        is_valid: True if backup path was activated after primary failure.
        primary_nexthop: The primary nexthop (active before failure).
        backup_nexthop: The backup nexthop (expected to be active after failure).
        primary_active_before_failure: True if primary was active before shutdown.
        backup_active_after_failure: True if backup became active after shutdown.
        primary_restored: True if primary interface was restored after test.
        error_message: Descriptive error message if verification failed.
        details: Additional diagnostic information.
    
    Requirements: 3.1
    """
    is_valid: bool
    primary_nexthop: Optional[str] = None
    backup_nexthop: Optional[str] = None
    primary_active_before_failure: bool = False
    backup_active_after_failure: bool = False
    primary_restored: bool = False
    error_message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ECMPInclusionResult:
    """
    Result of ECMP set inclusion verification.
    
    This dataclass captures the outcome of verifying that ECMP (Equal-Cost
    Multi-Path) routing includes all expected exit nexthops. Unlike count-based
    ECMP verification, this checks set inclusion (expected ⊆ actual).
    
    Attributes:
        is_valid: True if all expected exits are present in actual nexthops.
        expected_exits: List of expected exit nexthops.
        actual_exits: List of actual nexthops found in the RIB.
        missing_exits: List of expected exits not found in actual nexthops.
        extra_exits: List of actual exits not in expected set (informational).
        error_message: Descriptive error message if verification failed.
    
    Requirements: 4.1
    """
    is_valid: bool
    expected_exits: List[str] = field(default_factory=list)
    actual_exits: List[str] = field(default_factory=list)
    missing_exits: List[str] = field(default_factory=list)
    extra_exits: List[str] = field(default_factory=list)
    error_message: str = ""



import re

class BasicFRRVerifier:
    """
    A basic, regex-based verifier for FRR configurations.
    Used when Docker is unavailable to provide a best-effort syntax check.
    """
    
    # Whitelist of valid command patterns (regex)
    PATTERNS = [
        r"^!.*",  # Comments
        r"^\s*$", # Empty lines
        # BGP
        r"^router bgp \d+$",
        r"^\s+bgp router-id \d+\.\d+\.\d+\.\d+$",
        r"^\s+bgp log-neighbor-changes$",
        r"^\s+no bgp ebgp-requires-policy$",
        r"^\s+neighbor \S+ remote-as \d+$",
        r"^\s+neighbor \S+ update-source \S+$",
        r"^\s+neighbor \S+ description .*$",
        r"^\s+address-family ipv4 unicast$",
        r"^\s+network \d+\.\d+\.\d+\.\d+/\d+$",
        r"^\s+neighbor \S+ activate$",
        r"^\s+neighbor \S+ next-hop-self$",
        r"^\s+neighbor \S+ route-map \S+ (in|out)$",
        r"^\s+neighbor \S+ soft-reconfiguration inbound$",
        r"^\s+redistribute connected$",
        r"^\s+redistribute static$",
        r"^\s+redistribute ospf$",
        r"^\s+maximum-paths \d+$",
        r"^\s+exit-address-family$",
        r"^\s+exit$",
        # OSPF
        r"^router ospf$",
        r"^\s+ospf router-id \d+\.\d+\.\d+\.\d+$",
        r"^\s+network \d+\.\d+\.\d+\.\d+/\d+ area \d+$",
        r"^\s+passive-interface \S+$",
        # Interfaces
        r"^interface \S+$",
        r"^\s+ip address \d+\.\d+\.\d+\.\d+/\d+$",
        r"^\s+ip ospf cost \d+$",
        r"^\s+description .*$",
        r"^\s+shutdown$",
        r"^\s+no shutdown$",
        # Route Maps & Access Lists
        r"^route-map \S+ permit \d+$",
        r"^route-map \S+ deny \d+$",
        r"^\s+match ip address prefix-list \S+$",
        r"^\s+match community \S+$",
        r"^\s+set partial-local-preference \d+$", # Custom flavor?
        r"^\s+set local-preference \d+$",
        r"^\s+set weight \d+$",
        r"^\s+set metric \d+$",
        r"^\s+set community .*$",
        r"^\s+set ip next-hop \d+\.\d+\.\d+\.\d+$",
        # Prefix Lists
        # Supports: seq N perm/deny PREFIX [ge N] [le N]
        r"^ip prefix-list \S+ seq \d+ (permit|deny) \d+\.\d+\.\d+\.\d+/\d+( ge \d+)?( le \d+)?$",
        r"^ip prefix-list \S+ (permit|deny) \d+\.\d+\.\d+\.\d+/\d+( ge \d+)?( le \d+)?$",
        # Static Routes
        r"^ip route \d+\.\d+\.\d+\.\d+/\d+ \S+( \d+)?$",
        r"^ip route \d+\.\d+\.\d+\.\d+/\d+ \d+\.\d+\.\d+\.\d+( \d+)?$",
        # Other
        r"^log file \S+$",
        r"^service password-encryption$",
        r"^hostname \S+$",
        r"^line vty$",
    ]

    @classmethod
    def verify(cls, config_content: str) -> Tuple[bool, str]:
        lines = config_content.strip().split('\n')
        compiled_patterns = [re.compile(p) for p in cls.PATTERNS]
        
        for i, line in enumerate(lines):
            line = line.rstrip() # Keep indentation
            if not line: continue
            
            matched = False
            for p in compiled_patterns:
                if p.match(line):
                    matched = True
                    break
            
            if not matched:
                # Basic check failed
                return (False, f"Line {i+1}: Unrecognized command format: '{line.strip()}'")
                
        return (True, "")


class Verifier:
    """
    High-level verifier for FRR configurations.
    
    Uses KatharaExecutor for all infrastructure operations.
    """
    
    def __init__(self, executor: Optional[KatharaExecutor] = None):
        """
        Initialize verifier with optional executor.
        
        Args:
            executor: KatharaExecutor instance. If None, creates default.
        """
        self.executor = executor or KatharaExecutor()
    
    def verify_syntax_static(
        self,
        config_path: Path,
        allow_autofix: bool = True,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Verify FRR configuration syntax using Docker vtysh -C.
        
        Args:
            config_path: Path to FRR configuration file.
        
        Returns:
            (True, "") if syntax is valid.
            (False, error_message) if syntax errors found.
        """
        # Validate config file exists
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        # Helper to run check
        def run_check(path_to_check):
            abs_path = str(path_to_check.resolve())
            service = os.environ.get("MSN2026_FRR_SYNTAX_SERVICE")
            if service:
                resolved = path_to_check.resolve()
                if resolved.parent != Path("/tmp"):
                    raise ValueError("Persistent FRR verifier accepts only isolated /tmp inputs")
                checked = subprocess.run(
                    ["docker", "exec", service, "vtysh", "-C", "-f", f"/host-tmp/{resolved.name}"],
                    capture_output=True, text=True, timeout=120,
                )
                return ExecutionResult(
                    success=checked.returncode == 0,
                    stdout=checked.stdout,
                    stderr=checked.stderr,
                    return_code=checked.returncode,
                )
            return self.executor.run_temp_container(
                image=FRR_DOCKER_IMAGE,
                command=[
                    "-lc",
                    "printf 'service integrated-vtysh-config\\n' >/etc/frr/vtysh.conf && "
                    "vtysh -C -f /etc/frr/frr.conf",
                ],
                mounts={
                    abs_path: "/etc/frr/frr.conf",
                },
                entrypoint="/bin/sh",
            )

        # 1. First attempt: Check original
        result = run_check(config_path)
        
        if result.success:
            return (True, "", None)
            
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        
        # 2. Retry with aggressive wrapping only when explicitly allowed.
        if allow_autofix and "Unknown command" in error_msg:
            original_content = config_path.read_text(encoding="utf-8")
            # Create a new temp file with wrapped content
            wrapped_content = f"router bgp 65000\n{original_content}\n exit\n"
            
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as tmp_wrap:
                tmp_wrap.write(wrapped_content)
                tmp_wrap_path = Path(tmp_wrap.name)
                
            try:
                result_retry = run_check(tmp_wrap_path)
                if result_retry.success:
                    # It worked! Return the fixed content
                    return (True, "", wrapped_content)
            finally:
                try:
                    tmp_wrap_path.unlink()
                except OSError:
                    pass

        return (False, error_msg, None)

    def verify_syntax_string(
        self,
        config_content: str,
        allow_autofix: bool = True,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Verify FRR configuration syntax from string content.
        
        Args:
            config_content: FRR configuration as string.
        
        Returns:
            (True, "") if syntax is valid.
            (False, error_message) if syntax errors found or Docker unavailable.
        """
        # Create temporary file with config content
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.conf',
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            tmp_file.write(config_content)
            tmp_path = Path(tmp_file.name)
        
        try:
            return self.verify_syntax_static(tmp_path, allow_autofix=allow_autofix)
        finally:
            # Clean up temp file
            try:
                tmp_path.unlink()
            except OSError:
                pass  # Ignore cleanup errors
    
    def verify_connectivity_dynamic(
        self,
        device_configs: Dict[str, Path],
        topology: Dict[str, Any],
        convergence_wait: int = DEFAULT_CONVERGENCE_WAIT,
    ) -> Tuple[bool, str]:
        """
        Verify network connectivity via Kathara emulation.
        
        This function:
        1. Generates a Kathara lab.conf from the topology
        2. Starts the virtual network lab
        3. Waits for protocol convergence
        4. Checks BGP state on edge routers
        5. ALWAYS cleans up containers (even on failure/exception)
        
        Args:
            device_configs: Map of device_name -> config_path.
            topology: Topology dictionary with nodes and lans.
            convergence_wait: Seconds to wait for protocol convergence.
        
        Returns:
            (True, "") if all BGP neighbors are established.
            (False, error_message) if verification fails.
        """
        lab_dir = None
        try:
            # Create temporary directory for lab files
            lab_dir = Path(tempfile.mkdtemp(prefix="kathara_lab_"))
            
            # Generate lab configuration
            generate_lab_conf(topology, device_configs, lab_dir)
            
            # Start the lab (this will raise if Kathara unavailable)
            try:
                start_result = self.executor.start_lab(lab_dir)
            except KatharaUnavailableError:
                return (False, "Kathara not available")
            except ExecutionTimeoutError:
                return (False, "Kathara operation timeout")
            
            if not start_result.success:
                return (False, f"Lab start failed: {start_result.stderr}")
            
            # Wait for convergence
            time.sleep(convergence_wait)
            
            # Get edge routers from topology
            nodes = topology.get("nodes", {})
            roles = topology.get("roles", {})
            
            # Try to get edge routers from roles, otherwise infer from node roles
            edge_routers = roles.get("edge_routers", [])
            if not edge_routers:
                edge_routers = [
                    name for name, info in nodes.items()
                    if info.get("role") == "edge"
                ]
            
            # If still no edge routers, check all nodes
            if not edge_routers:
                edge_routers = list(nodes.keys())
            
            # Check BGP state on edge routers
            return self._check_bgp_state(lab_dir, edge_routers)
            
        except KatharaUnavailableError:
            return (False, "Kathara not available")
        except ExecutionTimeoutError:
            return (False, "Kathara operation timeout")
        except Exception as e:
            return (False, f"Dynamic verification error: {e}")
        finally:
            # ALWAYS clean up, regardless of success or failure
            if lab_dir:
                self.executor.stop_lab(lab_dir)
                # Also try to remove the temp directory
                try:
                    shutil.rmtree(lab_dir, ignore_errors=True)
                except Exception:
                    pass
    
    def _check_bgp_state(
        self,
        lab_dir: Path,
        edge_routers: list,
    ) -> Tuple[bool, str]:
        """
        Check BGP neighbor state on edge routers.
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            edge_routers: List of edge router device names to check.
        
        Returns:
            (True, "") if all neighbors are Established.
            (False, error_message) if any neighbor is not Established.
        """
        for device in edge_routers:
            try:
                result = self.executor.exec_command(
                    device=device,
                    command="vtysh -c 'show ip bgp summary json'",
                    lab_dir=lab_dir,
                )
                
                if not result.success:
                    return (False, f"Failed to get BGP state on {device}: {result.stderr}")
                
                # Parse JSON response
                try:
                    bgp_data = json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    return (False, f"Invalid BGP JSON response from {device}: {e}")
                
                # Check BGP neighbor states using assertions module
                is_ok, error_msg = check_all_neighbors_established(bgp_data, device)
                if not is_ok:
                    return (False, error_msg)
                    
            except ExecutionTimeoutError:
                return (False, f"Timeout checking BGP state on {device}")
            except KatharaUnavailableError as e:
                return (False, f"Error executing kathara on {device}: {e}")
        
        return (True, "")
    
    def verify_steering(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_nexthop: str,
    ) -> SteeringVerificationResult:
        """
        Verify that traffic steering intent is effective at the data plane.
        
        Executes `vtysh -c "show ip route <prefix> json"` and parses JSON
        to verify nexthop matches expected path.
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix/IP to verify.
            expected_nexthop: Expected nexthop (interface or IP) for steering.
        
        Returns:
            SteeringVerificationResult with verification outcome.
        
        Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
        """
        return self._verify_steering_path(
            lab_dir, source_device, target_prefix, expected_nexthop
        )
    
    def _verify_steering_path(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_nexthop: str,
    ) -> SteeringVerificationResult:
        """
        Verify steering intent via RIB/FIB check.
        
        Executes `vtysh -c "show ip route <prefix> json"` and verifies
        that at least one nexthop matches the expected path.
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to check routing for.
            expected_nexthop: Expected nexthop (IP address or interface name).
        
        Returns:
            SteeringVerificationResult with match status.
        
        Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
        """
        try:
            # Execute show ip route command
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show ip route {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            if not result.success:
                return SteeringVerificationResult(
                    is_valid=False,
                    intent_type="steering",
                    expected_nexthop=expected_nexthop,
                    error_message=f"Failed to get route info: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                route_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return SteeringVerificationResult(
                    is_valid=False,
                    intent_type="steering",
                    expected_nexthop=expected_nexthop,
                    error_message=f"Invalid JSON response: {e}",
                    details={"raw_output": result.stdout},
                )
            
            # Extract nexthops from route data
            # FRR JSON format: {prefix: [{nexthops: [{ip, interfaceName, ...}]}]}
            actual_nexthops = self._extract_nexthops(route_data, target_prefix)
            
            if not actual_nexthops:
                return SteeringVerificationResult(
                    is_valid=False,
                    intent_type="steering",
                    expected_nexthop=expected_nexthop,
                    error_message=f"No route found for {target_prefix}",
                    details={"route_data": route_data},
                )
            
            # Check if expected nexthop matches any actual nexthop
            # Match by interface name or IP address
            for nh in actual_nexthops:
                nh_ip = nh.get("ip", "")
                nh_iface = nh.get("interfaceName", "")
                
                if expected_nexthop in (nh_ip, nh_iface):
                    return SteeringVerificationResult(
                        is_valid=True,
                        intent_type="steering",
                        expected_nexthop=expected_nexthop,
                        actual_nexthop=nh_ip or nh_iface,
                        details={"all_nexthops": actual_nexthops},
                    )
            
            # No match found
            actual_str = ", ".join(
                nh.get("ip") or nh.get("interfaceName", "unknown")
                for nh in actual_nexthops
            )
            return SteeringVerificationResult(
                is_valid=False,
                intent_type="steering",
                expected_nexthop=expected_nexthop,
                actual_nexthop=actual_str,
                error_message=f"Nexthop mismatch: expected {expected_nexthop}, got {actual_str}",
                details={"all_nexthops": actual_nexthops},
            )
            
        except ExecutionTimeoutError:
            return SteeringVerificationResult(
                is_valid=False,
                intent_type="steering",
                expected_nexthop=expected_nexthop,
                error_message=f"Timeout verifying route on {source_device}",
            )
        except Exception as e:
            return SteeringVerificationResult(
                is_valid=False,
                intent_type="steering",
                expected_nexthop=expected_nexthop,
                error_message=f"Verification error: {e}",
            )
    
    def verify_ecmp(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_count: int,
    ) -> ECMPVerificationResult:
        """
        Verify ECMP intent by checking nexthop count in RIB.
        
        Executes `vtysh -c "show ip route <prefix> json"` on the source device
        and verifies that the number of nexthops meets or exceeds the expected
        count for ECMP load balancing.
        
        Args:
            lab_dir: Path to Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to check.
            expected_count: Minimum expected number of ECMP paths.
        
        Returns:
            ECMPVerificationResult with validation outcome.
        
        Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
        """
        try:
            # Execute show ip route command (Requirement 2.1)
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show ip route {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            # Handle command failure (Requirement 2.5)
            if not result.success:
                return ECMPVerificationResult(
                    is_valid=False,
                    expected_count=expected_count,
                    actual_count=0,
                    nexthops=[],
                    error_message=f"Failed to get route info: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                route_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return ECMPVerificationResult(
                    is_valid=False,
                    expected_count=expected_count,
                    actual_count=0,
                    nexthops=[],
                    error_message=f"Invalid JSON response: {e}",
                )
            
            # Extract nexthops using existing helper (Requirement 2.2)
            nexthops = self._extract_nexthops(route_data, target_prefix)
            actual_count = len(nexthops)
            
            # Compare actual count vs expected count (Requirements 2.3, 2.4)
            if actual_count >= expected_count:
                # Valid: actual count meets or exceeds expected (Requirement 2.3)
                return ECMPVerificationResult(
                    is_valid=True,
                    expected_count=expected_count,
                    actual_count=actual_count,
                    nexthops=nexthops,
                )
            else:
                # Invalid: actual count is less than expected (Requirement 2.4)
                return ECMPVerificationResult(
                    is_valid=False,
                    expected_count=expected_count,
                    actual_count=actual_count,
                    nexthops=nexthops,
                    error_message=f"ECMP count mismatch: expected >= {expected_count}, got {actual_count}",
                )
            
        except ExecutionTimeoutError:
            return ECMPVerificationResult(
                is_valid=False,
                expected_count=expected_count,
                actual_count=0,
                nexthops=[],
                error_message=f"Timeout verifying ECMP on {source_device}",
            )
        except Exception as e:
            return ECMPVerificationResult(
                is_valid=False,
                expected_count=expected_count,
                actual_count=0,
                nexthops=[],
                error_message=f"ECMP verification error: {e}",
            )
    
    def verify_ecmp_inclusion(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_exits: List[str],
    ) -> ECMPInclusionResult:
        """
        Verify ECMP intent by checking set inclusion of expected exits.
        
        Executes `vtysh -c "show ip route <prefix> json"` on the source device
        and verifies that ALL expected exit nexthops are present in the actual
        nexthop set (set inclusion: expected ⊆ actual).
        
        Args:
            lab_dir: Path to Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to check.
            expected_exits: List of expected exit nexthops (IP addresses).
        
        Returns:
            ECMPInclusionResult with validation outcome.
        
        Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
        """
        try:
            # Execute show ip route command (Requirement 4.2)
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show ip route {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            # Handle command failure
            if not result.success:
                return ECMPInclusionResult(
                    is_valid=False,
                    expected_exits=expected_exits,
                    actual_exits=[],
                    missing_exits=expected_exits.copy(),
                    error_message=f"Failed to get route info: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                route_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return ECMPInclusionResult(
                    is_valid=False,
                    expected_exits=expected_exits,
                    actual_exits=[],
                    missing_exits=expected_exits.copy(),
                    error_message=f"Invalid JSON response: {e}",
                )
            
            # Extract all active nexthops from RIB response (Requirement 4.2)
            nexthops = self._extract_nexthops(route_data, target_prefix)
            
            # Extract nexthop IPs/interfaces as strings
            actual_exits: List[str] = []
            for nh in nexthops:
                nh_ip = nh.get("ip")
                nh_iface = nh.get("interfaceName")
                if nh_ip:
                    actual_exits.append(nh_ip)
                elif nh_iface:
                    actual_exits.append(nh_iface)
            
            # Compute set difference to find missing exits (Requirement 4.3)
            expected_set = set(expected_exits)
            actual_set = set(actual_exits)
            
            missing_exits = list(expected_set - actual_set)
            extra_exits = list(actual_set - expected_set)
            
            # Return is_valid=True iff expected ⊆ actual (Requirement 4.3, 4.4)
            if not missing_exits:
                # All expected exits are present
                return ECMPInclusionResult(
                    is_valid=True,
                    expected_exits=expected_exits,
                    actual_exits=actual_exits,
                    missing_exits=[],
                    extra_exits=extra_exits,
                )
            else:
                # Some expected exits are missing (Requirement 4.4, 4.6)
                return ECMPInclusionResult(
                    is_valid=False,
                    expected_exits=expected_exits,
                    actual_exits=actual_exits,
                    missing_exits=missing_exits,
                    extra_exits=extra_exits,
                    error_message=f"Missing expected exits: {', '.join(missing_exits)}",
                )
            
        except ExecutionTimeoutError:
            return ECMPInclusionResult(
                is_valid=False,
                expected_exits=expected_exits,
                actual_exits=[],
                missing_exits=expected_exits.copy(),
                error_message=f"Timeout verifying ECMP inclusion on {source_device}",
            )
        except Exception as e:
            return ECMPInclusionResult(
                is_valid=False,
                expected_exits=expected_exits,
                actual_exits=[],
                missing_exits=expected_exits.copy(),
                error_message=f"ECMP inclusion verification error: {e}",
            )
    
    def verify_backup_path(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_backup_nexthop: Optional[str] = None,
    ) -> BackupPathVerificationResult:
        """
        Verify backup path intent by checking metric differentiation in RIB.
        
        Executes `vtysh -c "show ip route <prefix> json"` on the source device
        and verifies that there is proper metric differentiation between
        primary (lowest metric) and backup (higher metric) paths.
        
        Args:
            lab_dir: Path to Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to check.
            expected_backup_nexthop: Optional expected backup nexthop to validate.
        
        Returns:
            BackupPathVerificationResult with validation outcome.
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
        """
        try:
            # Execute show ip route command (Requirement 3.1)
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show ip route {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            # Handle command failure (Requirement 3.6)
            if not result.success:
                return BackupPathVerificationResult(
                    is_valid=False,
                    error_message=f"Failed to get route info: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                route_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return BackupPathVerificationResult(
                    is_valid=False,
                    error_message=f"Invalid JSON response: {e}",
                )
            
            # Extract nexthops with metrics (Requirement 3.2)
            nexthops_with_metrics = self._extract_nexthops_with_metrics(
                route_data, target_prefix
            )
            
            # Handle edge case: single nexthop (no backup path)
            if len(nexthops_with_metrics) < 2:
                return BackupPathVerificationResult(
                    is_valid=False,
                    error_message="No backup path found: only single nexthop available",
                )
            
            # Sort by metric to identify primary (lowest) and backup (higher)
            sorted_nexthops = sorted(nexthops_with_metrics, key=lambda x: x[1])
            
            primary_nexthop, primary_metric = sorted_nexthops[0]
            backup_nexthop, backup_metric = sorted_nexthops[1]
            
            # Handle edge case: equal metrics (ECMP, no backup differentiation)
            # (Requirement 3.5)
            if primary_metric == backup_metric:
                return BackupPathVerificationResult(
                    is_valid=False,
                    primary_nexthop=primary_nexthop,
                    primary_metric=primary_metric,
                    backup_nexthop=backup_nexthop,
                    backup_metric=backup_metric,
                    error_message="No backup differentiation: all nexthops have equal metrics",
                )
            
            # Verify backup has higher metric than primary (Requirements 3.3, 3.4)
            # If expected_backup_nexthop is provided, also verify it matches
            if expected_backup_nexthop is not None:
                # Find the expected backup in the nexthops
                expected_found = False
                for nh, metric in nexthops_with_metrics:
                    if nh == expected_backup_nexthop:
                        expected_found = True
                        if metric <= primary_metric:
                            return BackupPathVerificationResult(
                                is_valid=False,
                                primary_nexthop=primary_nexthop,
                                primary_metric=primary_metric,
                                backup_nexthop=nh,
                                backup_metric=metric,
                                error_message=f"Expected backup {expected_backup_nexthop} does not have higher metric than primary",
                            )
                        break
                
                if not expected_found:
                    return BackupPathVerificationResult(
                        is_valid=False,
                        primary_nexthop=primary_nexthop,
                        primary_metric=primary_metric,
                        error_message=f"Expected backup nexthop {expected_backup_nexthop} not found in route",
                    )
            
            # Valid: backup has higher metric than primary
            return BackupPathVerificationResult(
                is_valid=True,
                primary_nexthop=primary_nexthop,
                primary_metric=primary_metric,
                backup_nexthop=backup_nexthop,
                backup_metric=backup_metric,
            )
            
        except ExecutionTimeoutError:
            return BackupPathVerificationResult(
                is_valid=False,
                error_message=f"Timeout verifying backup path on {source_device}",
            )
        except Exception as e:
            return BackupPathVerificationResult(
                is_valid=False,
                error_message=f"Backup path verification error: {e}",
            )
    
    def _extract_nexthops(
        self,
        route_data: Dict[str, Any],
        target_prefix: str,
    ) -> List[Dict[str, Any]]:
        """
        Extract nexthops from FRR route JSON response.
        
        FRR JSON format varies by version, handle common formats:
        - {prefix: [{nexthops: [...]}]} - List format (FRR 7.x+)
        - {prefix: {nexthops: [...]}} - Dict format (older FRR)
        - {"routes": {prefix: {nexthops: [...]}}} - Nested format
        - {prefix: [{...route_info..., nexthops: [...]}]} - Route info with nexthops
        - Direct nexthops array at top level
        
        Args:
            route_data: Parsed JSON response from show ip route command.
            target_prefix: The target prefix being queried.
        
        Returns:
            List of nexthop dictionaries containing 'ip' and/or 'interfaceName'.
        
        Requirements: 5.2
        """
        nexthops: List[Dict[str, Any]] = []
        
        # Handle empty input
        if not route_data:
            return nexthops
        
        # Check if route_data itself has a top-level nexthops array
        if "nexthops" in route_data and isinstance(route_data["nexthops"], list):
            nexthops.extend(route_data["nexthops"])
            return nexthops
        
        # Try to find the prefix in the response
        for key, value in route_data.items():
            # Skip non-route keys that are metadata
            if key in ("routerId", "localAsn", "vrfId", "vrfName", "tableVersion"):
                continue
            
            # Key might be the prefix or a container
            if isinstance(value, list):
                # Format: {prefix: [{nexthops: [...]}]}
                # or: {prefix: [{...route_info..., nexthops: [...]}]}
                for entry in value:
                    if isinstance(entry, dict):
                        if "nexthops" in entry:
                            nexthops.extend(entry["nexthops"])
                        # Also check for 'nexthop' (singular) in some FRR versions
                        elif "nexthop" in entry:
                            nh = entry["nexthop"]
                            if isinstance(nh, dict):
                                nexthops.append(nh)
                            elif isinstance(nh, list):
                                nexthops.extend(nh)
            elif isinstance(value, dict):
                if "nexthops" in value:
                    # Format: {prefix: {nexthops: [...]}}
                    nexthops.extend(value["nexthops"])
                elif "nexthop" in value:
                    # Handle singular 'nexthop' key
                    nh = value["nexthop"]
                    if isinstance(nh, dict):
                        nexthops.append(nh)
                    elif isinstance(nh, list):
                        nexthops.extend(nh)
                else:
                    # Recurse into nested structure (e.g., {"routes": {...}})
                    nexthops.extend(self._extract_nexthops(value, target_prefix))
        
        return nexthops
    
    def _extract_nexthops_with_metrics(
        self,
        route_data: Dict[str, Any],
        target_prefix: str,
    ) -> List[Tuple[str, int]]:
        """
        Extract nexthops with their metric values from FRR route JSON response.
        
        Extends nexthop extraction to include metric values for backup path
        verification. Handles FRR JSON format variations for the metric field.
        
        FRR JSON formats for metrics:
        - nexthop["metric"] - Direct metric on nexthop
        - route_entry["metric"] - Metric at route level (applies to all nexthops)
        - nexthop["weight"] - Alternative metric field in some FRR versions
        
        Args:
            route_data: Parsed JSON response from show ip route command.
            target_prefix: The target prefix being queried.
        
        Returns:
            List of tuples (nexthop_ip_or_interface, metric).
            If no metric found, defaults to 0.
        
        Requirements: 3.2
        """
        result: List[Tuple[str, int]] = []
        
        # Handle empty input
        if not route_data:
            return result
        
        # Check if route_data itself has a top-level nexthops array with metric
        if "nexthops" in route_data and isinstance(route_data["nexthops"], list):
            route_metric = route_data.get("metric", 0)
            for nh in route_data["nexthops"]:
                if isinstance(nh, dict):
                    nh_id = nh.get("ip") or nh.get("interfaceName", "unknown")
                    # Prefer nexthop-level metric, fall back to route-level
                    metric = nh.get("metric", nh.get("weight", route_metric))
                    result.append((nh_id, int(metric) if metric is not None else 0))
            return result
        
        # Try to find the prefix in the response
        for key, value in route_data.items():
            # Skip non-route keys that are metadata
            if key in ("routerId", "localAsn", "vrfId", "vrfName", "tableVersion"):
                continue
            
            # Key might be the prefix or a container
            if isinstance(value, list):
                # Format: {prefix: [{nexthops: [...], metric: N}]}
                for entry in value:
                    if isinstance(entry, dict):
                        route_metric = entry.get("metric", 0)
                        if "nexthops" in entry:
                            for nh in entry["nexthops"]:
                                if isinstance(nh, dict):
                                    nh_id = nh.get("ip") or nh.get("interfaceName", "unknown")
                                    # Prefer nexthop-level metric, fall back to route-level
                                    metric = nh.get("metric", nh.get("weight", route_metric))
                                    result.append((nh_id, int(metric) if metric is not None else 0))
                        elif "nexthop" in entry:
                            nh = entry["nexthop"]
                            if isinstance(nh, dict):
                                nh_id = nh.get("ip") or nh.get("interfaceName", "unknown")
                                metric = nh.get("metric", nh.get("weight", route_metric))
                                result.append((nh_id, int(metric) if metric is not None else 0))
            elif isinstance(value, dict):
                route_metric = value.get("metric", 0)
                if "nexthops" in value:
                    # Format: {prefix: {nexthops: [...], metric: N}}
                    for nh in value["nexthops"]:
                        if isinstance(nh, dict):
                            nh_id = nh.get("ip") or nh.get("interfaceName", "unknown")
                            metric = nh.get("metric", nh.get("weight", route_metric))
                            result.append((nh_id, int(metric) if metric is not None else 0))
                elif "nexthop" in value:
                    nh = value["nexthop"]
                    if isinstance(nh, dict):
                        nh_id = nh.get("ip") or nh.get("interfaceName", "unknown")
                        metric = nh.get("metric", nh.get("weight", route_metric))
                        result.append((nh_id, int(metric) if metric is not None else 0))
                else:
                    # Recurse into nested structure
                    result.extend(self._extract_nexthops_with_metrics(value, target_prefix))
        
        return result
    
    def verify_steering_bgp(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_nexthop: Optional[str] = None,
        expected_local_pref: Optional[int] = None,
    ) -> BGPVerificationResult:
        """
        Verify BGP steering intent using BGP-specific commands.
        
        Primary: vtysh -c "show bgp ipv4 unicast <prefix> json"
        Secondary: vtysh -c "show ip route <prefix> json" (cross-check)
        
        Validates:
        - Bestpath exists for the prefix
        - Local-pref matches expected (if specified)
        - Nexthop matches expected exit (if specified)
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to verify.
            expected_nexthop: Expected nexthop (IP address) for steering.
            expected_local_pref: Expected local-preference value.
        
        Returns:
            BGPVerificationResult with verification outcome.
        
        Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
        """
        try:
            # Execute BGP-specific show command (Requirement 1.1)
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show bgp ipv4 unicast {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            if not result.success:
                return BGPVerificationResult(
                    is_valid=False,
                    bestpath_exists=False,
                    expected_local_pref=expected_local_pref,
                    expected_nexthop=expected_nexthop,
                    error_message=f"Failed to execute BGP show command: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                bgp_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return BGPVerificationResult(
                    is_valid=False,
                    bestpath_exists=False,
                    expected_local_pref=expected_local_pref,
                    expected_nexthop=expected_nexthop,
                    error_message=f"Invalid BGP JSON response: {e}",
                    details={"raw_output": result.stdout},
                )
            
            # Extract bestpath information (Requirement 1.2)
            bestpath_info = self._extract_bgp_bestpath(bgp_data, target_prefix)
            
            if bestpath_info is None:
                # No bestpath found (Requirement 1.6)
                return BGPVerificationResult(
                    is_valid=False,
                    bestpath_exists=False,
                    expected_local_pref=expected_local_pref,
                    expected_nexthop=expected_nexthop,
                    error_message=f"No BGP bestpath found for prefix {target_prefix}",
                    details={"bgp_data": bgp_data},
                )
            
            actual_local_pref = bestpath_info.get("local_pref")
            actual_nexthop = bestpath_info.get("nexthop")
            
            # Verify local-pref if specified (Requirement 1.3)
            if expected_local_pref is not None:
                if actual_local_pref != expected_local_pref:
                    return BGPVerificationResult(
                        is_valid=False,
                        bestpath_exists=True,
                        expected_local_pref=expected_local_pref,
                        actual_local_pref=actual_local_pref,
                        expected_nexthop=expected_nexthop,
                        actual_nexthop=actual_nexthop,
                        error_message=f"Local-pref mismatch: expected {expected_local_pref}, got {actual_local_pref}",
                        details={"bestpath_info": bestpath_info},
                    )
            
            # Verify nexthop if specified (Requirement 1.4)
            if expected_nexthop is not None:
                if actual_nexthop != expected_nexthop:
                    return BGPVerificationResult(
                        is_valid=False,
                        bestpath_exists=True,
                        expected_local_pref=expected_local_pref,
                        actual_local_pref=actual_local_pref,
                        expected_nexthop=expected_nexthop,
                        actual_nexthop=actual_nexthop,
                        error_message=f"Nexthop mismatch: expected {expected_nexthop}, got {actual_nexthop}",
                        details={"bestpath_info": bestpath_info},
                    )
            
            # Cross-check with RIB as secondary evidence (Requirement 1.5)
            rib_cross_check = False
            try:
                rib_result = self.executor.exec_command(
                    device=source_device,
                    command=f'vtysh -c "show ip route {target_prefix} json"',
                    lab_dir=lab_dir,
                )
                if rib_result.success:
                    rib_data = json.loads(rib_result.stdout)
                    rib_nexthops = self._extract_nexthops(rib_data, target_prefix)
                    # Check if actual_nexthop appears in RIB
                    for nh in rib_nexthops:
                        if nh.get("ip") == actual_nexthop:
                            rib_cross_check = True
                            break
            except (json.JSONDecodeError, Exception):
                # RIB cross-check is secondary, don't fail on errors
                pass
            
            # All checks passed
            return BGPVerificationResult(
                is_valid=True,
                bestpath_exists=True,
                expected_local_pref=expected_local_pref,
                actual_local_pref=actual_local_pref,
                expected_nexthop=expected_nexthop,
                actual_nexthop=actual_nexthop,
                rib_cross_check=rib_cross_check,
                details={"bestpath_info": bestpath_info},
            )
            
        except ExecutionTimeoutError:
            return BGPVerificationResult(
                is_valid=False,
                bestpath_exists=False,
                expected_local_pref=expected_local_pref,
                expected_nexthop=expected_nexthop,
                error_message=f"Timeout verifying BGP steering on {source_device}",
            )
        except Exception as e:
            return BGPVerificationResult(
                is_valid=False,
                bestpath_exists=False,
                expected_local_pref=expected_local_pref,
                expected_nexthop=expected_nexthop,
                error_message=f"BGP verification error: {e}",
            )
    
    def _extract_bgp_bestpath(
        self,
        bgp_data: Dict[str, Any],
        target_prefix: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract bestpath information from FRR BGP JSON response.
        
        FRR JSON format for show bgp ipv4 unicast <prefix> json:
        {
          "prefix": "10.0.0.0/24",
          "paths": [
            {
              "bestpath": true,
              "valid": true,
              "locPrf": 200,
              "nexthops": [
                {
                  "ip": "192.168.1.1",
                  "hostname": "peer1",
                  "afi": "ipv4",
                  "used": true
                }
              ],
              "peer": {
                "peerId": "192.168.1.1",
                "routerId": "1.1.1.1"
              }
            }
          ]
        }
        
        Args:
            bgp_data: Parsed JSON response from show bgp ipv4 unicast command.
            target_prefix: The target prefix being queried.
        
        Returns:
            Dictionary with bestpath info: {"local_pref": int, "nexthop": str}
            or None if no bestpath found.
        
        Requirements: 1.2
        """
        if not bgp_data:
            return None
        
        # Handle direct paths array at top level
        paths = bgp_data.get("paths", [])
        
        # Also check for nested structure under prefix key
        if not paths:
            for key, value in bgp_data.items():
                if isinstance(value, dict) and "paths" in value:
                    paths = value.get("paths", [])
                    break
                elif isinstance(value, list):
                    # Some FRR versions return paths directly under prefix
                    paths = value
                    break
        
        # Find the bestpath entry
        for path in paths:
            if not isinstance(path, dict):
                continue
            
            # Check if this is the bestpath
            if path.get("bestpath", False) or path.get("best", False):
                # Extract local-pref (FRR uses "locPrf" or "localPref")
                local_pref = path.get("locPrf") or path.get("localPref") or path.get("local_pref")
                
                # Extract nexthop from nexthops array
                nexthop = None
                nexthops = path.get("nexthops", [])
                for nh in nexthops:
                    if isinstance(nh, dict):
                        # Prefer the "used" nexthop
                        if nh.get("used", False) or len(nexthops) == 1:
                            nexthop = nh.get("ip")
                            break
                
                # If no nexthop found in nexthops array, try peer info
                if nexthop is None:
                    peer = path.get("peer", {})
                    if isinstance(peer, dict):
                        nexthop = peer.get("peerId")
                
                return {
                    "local_pref": local_pref,
                    "nexthop": nexthop,
                    "path_data": path,
                }
        
        return None
    
    def verify_steering_ospf(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_nexthop: Optional[str] = None,
        expected_metric: Optional[int] = None,
    ) -> OSPFVerificationResult:
        """
        Verify OSPF steering intent using RIB commands.
        
        Command: vtysh -c "show ip route <prefix> json"
        
        Validates:
        - Route exists for the prefix
        - Metric matches expected (if specified)
        - Nexthop matches expected exit (if specified)
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to verify.
            expected_nexthop: Expected nexthop (IP address) for steering.
            expected_metric: Expected OSPF metric/cost value.
        
        Returns:
            OSPFVerificationResult with verification outcome.
        
        Requirements: 2.1, 2.2, 2.3, 2.4
        """
        try:
            # Execute RIB show command (Requirement 2.1)
            result = self.executor.exec_command(
                device=source_device,
                command=f'vtysh -c "show ip route {target_prefix} json"',
                lab_dir=lab_dir,
            )
            
            if not result.success:
                return OSPFVerificationResult(
                    is_valid=False,
                    route_exists=False,
                    expected_metric=expected_metric,
                    expected_nexthop=expected_nexthop,
                    error_message=f"Failed to execute OSPF show command: {result.stderr}",
                )
            
            # Parse JSON response
            try:
                route_data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                return OSPFVerificationResult(
                    is_valid=False,
                    route_exists=False,
                    expected_metric=expected_metric,
                    expected_nexthop=expected_nexthop,
                    error_message=f"Invalid OSPF JSON response: {e}",
                    details={"raw_output": result.stdout},
                )
            
            # Extract OSPF route information
            ospf_route_info = self._extract_ospf_route(route_data, target_prefix)
            
            if ospf_route_info is None:
                # No OSPF route found (Requirement 2.4)
                return OSPFVerificationResult(
                    is_valid=False,
                    route_exists=False,
                    expected_metric=expected_metric,
                    expected_nexthop=expected_nexthop,
                    error_message=f"No OSPF route found for prefix {target_prefix}",
                    details={"route_data": route_data},
                )
            
            actual_metric = ospf_route_info.get("metric")
            actual_nexthop = ospf_route_info.get("nexthop")
            
            # Verify metric if specified (Requirement 2.2)
            if expected_metric is not None:
                if actual_metric != expected_metric:
                    return OSPFVerificationResult(
                        is_valid=False,
                        route_exists=True,
                        expected_metric=expected_metric,
                        actual_metric=actual_metric,
                        expected_nexthop=expected_nexthop,
                        actual_nexthop=actual_nexthop,
                        error_message=f"Metric mismatch: expected {expected_metric}, got {actual_metric}",
                        details={"route_info": ospf_route_info},
                    )
            
            # Verify nexthop if specified (Requirement 2.3)
            if expected_nexthop is not None:
                if actual_nexthop != expected_nexthop:
                    return OSPFVerificationResult(
                        is_valid=False,
                        route_exists=True,
                        expected_metric=expected_metric,
                        actual_metric=actual_metric,
                        expected_nexthop=expected_nexthop,
                        actual_nexthop=actual_nexthop,
                        error_message=f"Nexthop mismatch: expected {expected_nexthop}, got {actual_nexthop}",
                        details={"route_info": ospf_route_info},
                    )
            
            # All checks passed
            return OSPFVerificationResult(
                is_valid=True,
                route_exists=True,
                expected_metric=expected_metric,
                actual_metric=actual_metric,
                expected_nexthop=expected_nexthop,
                actual_nexthop=actual_nexthop,
                details={"route_info": ospf_route_info},
            )
            
        except ExecutionTimeoutError:
            return OSPFVerificationResult(
                is_valid=False,
                route_exists=False,
                expected_metric=expected_metric,
                expected_nexthop=expected_nexthop,
                error_message=f"Timeout verifying OSPF steering on {source_device}",
            )
        except Exception as e:
            return OSPFVerificationResult(
                is_valid=False,
                route_exists=False,
                expected_metric=expected_metric,
                expected_nexthop=expected_nexthop,
                error_message=f"OSPF verification error: {e}",
            )
    
    def _extract_ospf_route(
        self,
        route_data: Dict[str, Any],
        target_prefix: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Extract OSPF route information from FRR RIB JSON response.
        
        FRR JSON format for show ip route <prefix> json:
        {
          "10.0.0.0/24": [
            {
              "protocol": "ospf",
              "metric": 20,
              "nexthops": [
                {
                  "ip": "192.168.1.1",
                  "interfaceName": "eth0",
                  "active": true
                }
              ]
            }
          ]
        }
        
        Args:
            route_data: Parsed JSON response from show ip route command.
            target_prefix: The target prefix being queried.
        
        Returns:
            Dictionary with route info: {"metric": int, "nexthop": str, "protocol": str}
            or None if no OSPF route found.
        
        Requirements: 2.1
        """
        if not route_data:
            return None
        
        # Try to find OSPF routes in the response
        for key, value in route_data.items():
            # Skip metadata keys
            if key in ("routerId", "localAsn", "vrfId", "vrfName", "tableVersion"):
                continue
            
            if isinstance(value, list):
                # Format: {prefix: [{protocol: "ospf", metric: N, nexthops: [...]}]}
                for entry in value:
                    if isinstance(entry, dict):
                        protocol = entry.get("protocol", "").lower()
                        # Accept OSPF routes (including ospf, ospf6, etc.)
                        if "ospf" in protocol or protocol == "o":
                            metric = entry.get("metric", 0)
                            nexthop = None
                            
                            # Extract nexthop from nexthops array
                            nexthops = entry.get("nexthops", [])
                            for nh in nexthops:
                                if isinstance(nh, dict):
                                    # Prefer active nexthop
                                    if nh.get("active", True) or len(nexthops) == 1:
                                        nexthop = nh.get("ip")
                                        if nexthop:
                                            break
                            
                            return {
                                "metric": int(metric) if metric is not None else 0,
                                "nexthop": nexthop,
                                "protocol": protocol,
                                "route_data": entry,
                            }
            elif isinstance(value, dict):
                # Format: {prefix: {protocol: "ospf", metric: N, nexthops: [...]}}
                protocol = value.get("protocol", "").lower()
                if "ospf" in protocol or protocol == "o":
                    metric = value.get("metric", 0)
                    nexthop = None
                    
                    nexthops = value.get("nexthops", [])
                    for nh in nexthops:
                        if isinstance(nh, dict):
                            if nh.get("active", True) or len(nexthops) == 1:
                                nexthop = nh.get("ip")
                                if nexthop:
                                    break
                    
                    return {
                        "metric": int(metric) if metric is not None else 0,
                        "nexthop": nexthop,
                        "protocol": protocol,
                        "route_data": value,
                    }
        
        return None
    
    def verify_backup_path_with_failover(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        primary_exit_device: str,
        primary_exit_interface: str,
        expected_backup_nexthop: str,
        protocol: str = "bgp",
        convergence_wait: int = DEFAULT_CONVERGENCE_WAIT,
    ) -> FailoverVerificationResult:
        """
        Verify backup path activation by simulating primary path failure.
        
        This method performs failure-driven verification by:
        1. Verifying primary path is active before failure
        2. Shutting down the primary exit interface
        3. Waiting for convergence
        4. Verifying backup path is now active
        5. Restoring the primary exit interface
        
        Args:
            lab_dir: Path to the Kathara lab directory.
            source_device: Device to verify routing from.
            target_prefix: Target prefix to verify.
            primary_exit_device: Device where primary exit interface is located.
            primary_exit_interface: Interface to shutdown for failure simulation.
            expected_backup_nexthop: Expected backup nexthop after primary failure.
            protocol: Protocol type ("bgp" or "ospf").
            convergence_wait: Seconds to wait for protocol convergence after failure.
        
        Returns:
            FailoverVerificationResult with verification outcome.
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
        """
        primary_restored = False
        primary_nexthop = None
        
        try:
            # Step 1: Verify primary path is active before failure (Requirement 3.2)
            if protocol.lower() == "bgp":
                pre_result = self.verify_steering_bgp(
                    lab_dir=lab_dir,
                    source_device=source_device,
                    target_prefix=target_prefix,
                )
                primary_active = pre_result.is_valid and pre_result.bestpath_exists
                primary_nexthop = pre_result.actual_nexthop
            else:  # OSPF
                pre_result = self.verify_steering_ospf(
                    lab_dir=lab_dir,
                    source_device=source_device,
                    target_prefix=target_prefix,
                )
                primary_active = pre_result.is_valid and pre_result.route_exists
                primary_nexthop = pre_result.actual_nexthop
            
            if not primary_active:
                return FailoverVerificationResult(
                    is_valid=False,
                    primary_nexthop=primary_nexthop,
                    backup_nexthop=expected_backup_nexthop,
                    primary_active_before_failure=False,
                    error_message=f"Primary path not active before failure simulation: {getattr(pre_result, 'error_message', 'unknown error')}",
                    details={"pre_failure_result": pre_result.__dict__ if hasattr(pre_result, '__dict__') else str(pre_result)},
                )
            
            # Step 2: Shutdown primary exit interface (Requirement 3.3)
            shutdown_cmd = f'vtysh -c "conf t" -c "interface {primary_exit_interface}" -c "shutdown"'
            shutdown_result = self.executor.exec_command(
                device=primary_exit_device,
                command=shutdown_cmd,
                lab_dir=lab_dir,
            )
            
            if not shutdown_result.success:
                return FailoverVerificationResult(
                    is_valid=False,
                    primary_nexthop=primary_nexthop,
                    backup_nexthop=expected_backup_nexthop,
                    primary_active_before_failure=True,
                    error_message=f"Failed to shutdown primary interface: {shutdown_result.stderr}",
                    details={"shutdown_result": shutdown_result.__dict__ if hasattr(shutdown_result, '__dict__') else str(shutdown_result)},
                )
            
            # Step 3: Wait for convergence (Requirement 3.4)
            time.sleep(convergence_wait)
            
            # Step 4: Re-run oracle to verify backup is active (Requirement 3.4)
            if protocol.lower() == "bgp":
                post_result = self.verify_steering_bgp(
                    lab_dir=lab_dir,
                    source_device=source_device,
                    target_prefix=target_prefix,
                    expected_nexthop=expected_backup_nexthop,
                )
                backup_active = post_result.is_valid and post_result.actual_nexthop == expected_backup_nexthop
            else:  # OSPF
                post_result = self.verify_steering_ospf(
                    lab_dir=lab_dir,
                    source_device=source_device,
                    target_prefix=target_prefix,
                    expected_nexthop=expected_backup_nexthop,
                )
                backup_active = post_result.is_valid and post_result.actual_nexthop == expected_backup_nexthop
            
            # Step 5: Restore primary exit interface (Requirement 3.5)
            restore_cmd = f'vtysh -c "conf t" -c "interface {primary_exit_interface}" -c "no shutdown"'
            restore_result = self.executor.exec_command(
                device=primary_exit_device,
                command=restore_cmd,
                lab_dir=lab_dir,
            )
            primary_restored = restore_result.success
            
            # Return result based on backup activation (Requirement 3.6)
            if backup_active:
                return FailoverVerificationResult(
                    is_valid=True,
                    primary_nexthop=primary_nexthop,
                    backup_nexthop=expected_backup_nexthop,
                    primary_active_before_failure=True,
                    backup_active_after_failure=True,
                    primary_restored=primary_restored,
                    details={
                        "pre_failure_result": pre_result.__dict__ if hasattr(pre_result, '__dict__') else str(pre_result),
                        "post_failure_result": post_result.__dict__ if hasattr(post_result, '__dict__') else str(post_result),
                    },
                )
            else:
                return FailoverVerificationResult(
                    is_valid=False,
                    primary_nexthop=primary_nexthop,
                    backup_nexthop=expected_backup_nexthop,
                    primary_active_before_failure=True,
                    backup_active_after_failure=False,
                    primary_restored=primary_restored,
                    error_message=f"Backup path not activated after primary failure. Expected: {expected_backup_nexthop}, Got: {getattr(post_result, 'actual_nexthop', 'unknown')}",
                    details={
                        "pre_failure_result": pre_result.__dict__ if hasattr(pre_result, '__dict__') else str(pre_result),
                        "post_failure_result": post_result.__dict__ if hasattr(post_result, '__dict__') else str(post_result),
                    },
                )
            
        except ExecutionTimeoutError as e:
            return FailoverVerificationResult(
                is_valid=False,
                primary_nexthop=primary_nexthop,
                backup_nexthop=expected_backup_nexthop,
                primary_restored=primary_restored,
                error_message=f"Timeout during failover verification: {e}",
            )
        except Exception as e:
            return FailoverVerificationResult(
                is_valid=False,
                primary_nexthop=primary_nexthop,
                backup_nexthop=expected_backup_nexthop,
                primary_restored=primary_restored,
                error_message=f"Failover verification error: {e}",
            )
    
    def verify_steering_auto(
        self,
        lab_dir: Path,
        source_device: str,
        target_prefix: str,
        expected_nexthop: Optional[str] = None,
        protocol: str = "auto",
        **kwargs,
    ) -> Union[BGPVerificationResult, OSPFVerificationResult, SteeringVerificationResult]:
        """
        Unified steering verification dispatcher.
        
        Dispatches to protocol-specific verification methods based on protocol parameter.
        This provides a single entry point for steering verification that automatically
        routes to the appropriate protocol-specific oracle.
        
        Args:
            lab_dir: Path to Kathara lab directory.
            source_device: Device to verify from.
            target_prefix: Target prefix to verify.
            expected_nexthop: Expected nexthop (optional).
            protocol: "auto", "bgp", or "ospf".
            **kwargs: Additional protocol-specific parameters:
                - For BGP: expected_local_pref (int)
                - For OSPF: expected_metric (int)
        
        Returns:
            Protocol-specific verification result:
            - BGPVerificationResult for protocol="bgp"
            - OSPFVerificationResult for protocol="ospf"
            - SteeringVerificationResult for protocol="auto" (RIB-only fallback)
        
        Raises:
            ValueError: If protocol is not one of "auto", "bgp", "ospf".
        
        Dispatch logic:
            - "bgp" → verify_steering_bgp()
            - "ospf" → verify_steering_ospf()
            - "auto" → verify_steering() (RIB-only fallback)
        
        Requirements: 2.1, 2.2, 2.3
        """
        protocol_lower = protocol.lower()
        
        if protocol_lower not in ("auto", "bgp", "ospf"):
            raise ValueError(
                f"Invalid protocol '{protocol}'. Must be one of: 'auto', 'bgp', 'ospf'"
            )
        
        if protocol_lower == "bgp":
            # Dispatch to BGP-specific verification (Requirement 2.2)
            expected_local_pref = kwargs.get("expected_local_pref")
            return self.verify_steering_bgp(
                lab_dir=lab_dir,
                source_device=source_device,
                target_prefix=target_prefix,
                expected_nexthop=expected_nexthop,
                expected_local_pref=expected_local_pref,
            )
        elif protocol_lower == "ospf":
            # Dispatch to OSPF-specific verification (Requirement 2.3)
            expected_metric = kwargs.get("expected_metric")
            return self.verify_steering_ospf(
                lab_dir=lab_dir,
                source_device=source_device,
                target_prefix=target_prefix,
                expected_nexthop=expected_nexthop,
                expected_metric=expected_metric,
            )
        else:
            # "auto" - RIB-only fallback
            if expected_nexthop is None:
                # For auto mode without expected_nexthop, return a basic result
                return SteeringVerificationResult(
                    is_valid=False,
                    intent_type="steering",
                    error_message="expected_nexthop is required for 'auto' protocol mode",
                )
            return self.verify_steering(
                lab_dir=lab_dir,
                source_device=source_device,
                target_prefix=target_prefix,
                expected_nexthop=expected_nexthop,
            )
    
    def is_docker_available(self) -> bool:
        """Check if Docker is available."""
        return self.executor.is_docker_available()
    
    def is_kathara_available(self) -> bool:
        """Check if Kathara is available."""
        return self.executor.is_kathara_available()
    
    def cleanup(self, lab_dir: Optional[Path] = None) -> None:
        """Clean up Kathara containers."""
        self.executor.stop_lab(lab_dir)
