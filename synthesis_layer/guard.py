"""
ConstraintGuard: Static analysis layer enforcing Policy Layer parameters.

This module provides security-first validation of LLM-generated configuration
output. The core principle is that the Policy Layer is the authoritative source
of truth - the LLM is merely a stylistic renderer that must conform to computed
parameters.

Key Components:
- SecurityViolationError: Non-recoverable error when LLM violates policy constraints
- SynthesisError: Error raised when synthesis fails after all retries exhausted
- ExtractionResult: Dataclass containing extracted parameters from config text
- ConstraintGuard: Main class for parameter extraction and verification

Security Model:
- Trust Policy, Not LLM: Policy Layer computes exact parameter values
- Fail Fast on Drift: Semantic violations are non-recoverable security failures
- Repair Syntax Only: Only syntax errors trigger the retry loop
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

# Dedicated security logger for audit purposes
security_logger = logging.getLogger("pathdelta.security.guard")


class SecurityViolationError(Exception):
    """
    Raised when LLM output violates Policy Layer constraints.
    
    This is a NON-RECOVERABLE error. The repair loop MUST NOT retry.
    The error contains details about the expected vs actual parameter values
    for audit and debugging purposes.
    
    Attributes:
        expected: Dictionary of expected parameter values from Policy Layer
        actual: Dictionary of actual parameter values extracted from LLM output
        operation_id: Optional identifier for the operation being processed
        device: Optional device name where the violation occurred
    
    Requirements: 2.5, 4.5
    """
    
    def __init__(
        self,
        message: str,
        expected: Dict[str, Any],
        actual: Dict[str, Any],
        operation_id: Optional[str] = None,
        device: Optional[str] = None,
    ):
        super().__init__(message)
        self.expected = expected
        self.actual = actual
        self.operation_id = operation_id
        self.device = device
    
    def __repr__(self) -> str:
        return (
            f"SecurityViolationError("
            f"message={self.args[0]!r}, "
            f"expected={self.expected!r}, "
            f"actual={self.actual!r}, "
            f"operation_id={self.operation_id!r}, "
            f"device={self.device!r})"
        )


class SynthesisError(Exception):
    """
    Raised when synthesis fails after all retries are exhausted.
    
    Contains the history of all attempts for debugging and audit purposes.
    Each attempt includes the stage where it failed and the error message.
    
    Attributes:
        attempts: List of dictionaries containing attempt details:
            - attempt: Attempt number (1-indexed)
            - stage: Stage where failure occurred ("generation" or "syntax")
            - error: Error message
            - patch_text: (optional) The generated patch text that failed
    
    Requirements: 3.4
    """
    
    def __init__(self, message: str, attempts: List[Dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts
    
    def __repr__(self) -> str:
        return (
            f"SynthesisError("
            f"message={self.args[0]!r}, "
            f"attempts={self.attempts!r})"
        )


class FailureKind(Enum):
    """
    Classification of guard verification failures.
    
    Used to determine retry behavior:
    - PASS: Verification succeeded
    - CONTRACT_FAIL: Retryable formatting/contract failures
    - SECURITY_VIOLATION: Non-retryable policy violations
    """
    PASS = "PASS"
    CONTRACT_FAIL = "CONTRACT_FAIL"  # Retryable: empty output, missing markers, missing required field
    SECURITY_VIOLATION = "SECURITY_VIOLATION"  # Non-retryable: semantic drift, wrong values


class Severity(Enum):
    """
    Severity classification for contract violations.
    
    Fail-closed semantics are decoupled from retry policy:
    - Any patch that fails contract verification SHALL NOT proceed to execution (fail-closed)
    - Most contract violations allow regeneration under the same immutable contract (RETRYABLE)
    - Only dangerous/destructive commands trigger immediate hard-stop (HARD_STOP)
    
    Values:
        RETRYABLE: Scope/object/context violations, slot failures - can retry with feedback
        HARD_STOP: Dangerous command detected - fail immediately, no retry allowed
    
    Requirements: 7.2, 7.3, 7.4, 7.5
    """
    RETRYABLE = "RETRYABLE"      # Can retry: scope/object/context violations, slot failures
    HARD_STOP = "HARD_STOP"      # No retry: dangerous commands detected


@dataclass
class MissingSlotsDiff:
    """
    Contract diff for missing template slots (Must Fix 3).
    
    Generated when _fill_skeleton() finds placeholders without
    corresponding op_params values. This triggers RETRYABLE behavior
    in render_with_repair().
    
    Attributes:
        severity: Severity level (always RETRYABLE for missing slots)
        category: Category of the diff ("missing_slots")
        template_id: ID of the template being filled
        missing_slots: List of placeholder names that are missing from op_params
    
    Requirements: 6.2 (modified for Must Fix 3)
    """
    severity: Severity = field(default=Severity.RETRYABLE)
    category: str = "missing_slots"
    template_id: str = ""
    missing_slots: List[str] = field(default_factory=list)
    
    def to_prompt_text(self) -> str:
        """
        Generate structured diff for LLM retry prompt.
        
        Format:
        CONTRACT VIOLATION (RETRYABLE):
        - Category: missing_slots
        - Template: {template_id}
        - Missing slots: {slot1}, {slot2}, ...
        
        Please infer appropriate values for the missing slots.
        REQUIRED/FORBIDDEN constraints remain unchanged.
        
        Returns:
            Formatted string for LLM prompt injection
        """
        lines = [
            "CONTRACT VIOLATION (RETRYABLE):",
            f"- Category: {self.category}",
            f"- Template: {self.template_id}",
            f"- Missing slots: {', '.join(self.missing_slots)}",
            "",
            "Please infer appropriate values for the missing slots.",
            "REQUIRED/FORBIDDEN constraints remain unchanged.",
        ]
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable representation."""
        return {
            "severity": self.severity.value,
            "category": self.category,
            "template_id": self.template_id,
            "missing_slots": self.missing_slots,
        }


class DangerousCommandDetector:
    """
    Detector for high-risk/destructive commands that trigger HARD_STOP.
    
    This detector identifies dangerous command patterns that should never appear
    in LLM-generated patches. When detected, the system immediately fails with
    HARD_STOP severity - no retry is allowed.
    
    Dangerous patterns include:
    - no ... (deletion commands)
    - clear ... (clearing commands)
    - write ... (write to memory/disk)
    - reload (device restart)
    - shutdown (interface shutdown)
    - ip route ... (static routes - can affect global routing)
    - default-information originate (default route injection)
    - match ip address any / match-any (prefix isolation bypass)
    - Empty route-map permit (implicit match-any)
    
    Requirements: 12.1, 12.2, 12.4, 12.5, 12.6
    """
    
    # Default dangerous patterns with descriptions
    # Each tuple: (compiled_pattern, description, reason)
    DEFAULT_PATTERNS: List[tuple] = [
        (
            re.compile(r'^\s*no\s+', re.IGNORECASE | re.MULTILINE),
            "destructive 'no' command",
            "Patch should not contain deletion commands",
        ),
        (
            re.compile(r'^\s*clear\s+', re.IGNORECASE | re.MULTILINE),
            "destructive 'clear' command",
            "Patch should not contain clearing commands",
        ),
        (
            re.compile(r'^\s*write\s+', re.IGNORECASE | re.MULTILINE),
            "write command",
            "Patch should not contain write commands",
        ),
        (
            re.compile(r'^\s*reload\b', re.IGNORECASE | re.MULTILINE),
            "reload command",
            "Patch should not contain reload commands",
        ),
        (
            re.compile(r'^\s*shutdown\b', re.IGNORECASE | re.MULTILINE),
            "shutdown command",
            "Patch should not contain shutdown commands",
        ),
        (
            re.compile(r'^\s*ip\s+route\s+', re.IGNORECASE | re.MULTILINE),
            "static route command",
            "Patch should not contain static route commands",
        ),
        (
            re.compile(r'^\s*default-information\s+originate', re.IGNORECASE | re.MULTILINE),
            "default route injection",
            "Patch should not inject default routes",
        ),
        (
            re.compile(r'match\s+ip\s+address\s+any', re.IGNORECASE),
            "match-any bypass",
            "match-any bypasses prefix isolation",
        ),
    ]
    
    def __init__(self, patterns: Optional[List[tuple]] = None):
        """
        Initialize the detector with patterns.
        
        Args:
            patterns: Optional list of (pattern, description, reason) tuples.
                     If None, uses DEFAULT_PATTERNS.
        
        Requirements: 12.6 (configurable patterns)
        """
        if patterns is not None:
            self._patterns = patterns
        else:
            self._patterns = self.DEFAULT_PATTERNS.copy()
    
    @property
    def patterns(self) -> List[tuple]:
        """Get the current list of dangerous patterns."""
        return self._patterns
    
    def detect(self, patch_text: str) -> Optional[tuple]:
        """
        Detect dangerous commands in patch text.
        
        Scans the patch text for any dangerous command patterns. Returns
        immediately upon finding the first match (fail-fast).
        
        Args:
            patch_text: The LLM-generated patch text to scan
        
        Returns:
            None if no dangerous command found
            Tuple of (description, matched_text, reason) if found
        
        Requirements: 12.1, 12.2, 12.4
        """
        for pattern, description, reason in self._patterns:
            match = pattern.search(patch_text)
            if match:
                matched_text = match.group(0).strip()
                # Log for security audit (Requirement 12.5)
                security_logger.warning(
                    "DANGEROUS COMMAND DETECTED: %s - %s",
                    description,
                    matched_text,
                    extra={
                        "pattern_description": description,
                        "matched_text": matched_text,
                        "reason": reason,
                    },
                )
                return description, matched_text, reason
        return None
    
    def detect_all(self, patch_text: str) -> List[tuple]:
        """
        Detect all dangerous commands in patch text.
        
        Unlike detect(), this method finds ALL dangerous patterns in the text,
        not just the first one. Useful for comprehensive audit logging.
        
        Args:
            patch_text: The LLM-generated patch text to scan
        
        Returns:
            List of (description, matched_text, reason) tuples for all matches.
            Empty list if no dangerous commands found.
        
        Requirements: 12.5 (audit logging)
        """
        results = []
        for pattern, description, reason in self._patterns:
            for match in pattern.finditer(patch_text):
                matched_text = match.group(0).strip()
                results.append((description, matched_text, reason))
                # Log each detection for audit
                security_logger.warning(
                    "DANGEROUS COMMAND DETECTED: %s - %s",
                    description,
                    matched_text,
                    extra={
                        "pattern_description": description,
                        "matched_text": matched_text,
                        "reason": reason,
                    },
                )
        return results
    
    def is_safe(self, patch_text: str) -> bool:
        """
        Check if patch text is safe (contains no dangerous commands).
        
        Args:
            patch_text: The LLM-generated patch text to check
        
        Returns:
            True if no dangerous commands found, False otherwise
        """
        return self.detect(patch_text) is None


@dataclass
class RequiredSlot:
    """
    A slot that must satisfy: presence + uniqueness + equality.
    
    Task A Requirements (8.1-8.8):
    - Presence: slot value can be extracted from patch text
    - Uniqueness: extraction result appears exactly once (prevents speculative outputs)
    - Equality: normalized value exactly matches expected_value
    
    Critical slots requiring uniqueness check:
    - prefix, neighbor, direction, route-map name, local-pref, cost, metric
    
    Attributes:
        slot_name: Name of the slot (e.g., "local_pref", "prefix_list_name")
        expected_value: Expected value from op_params
        aliases: Alias forms for the slot (e.g., ["local-preference", "local-pref"])
        extraction_pattern: Regex pattern to extract value from patch text
        enforce_uniqueness: Whether to enforce uniqueness check (default True for critical slots)
    
    Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8
    """
    slot_name: str           # e.g., "local_pref", "prefix_list_name"
    expected_value: Any      # Expected value from op_params
    aliases: List[str]       # Alias forms (e.g., ["local-preference", "local-pref", "local preference"])
    extraction_pattern: str  # Regex pattern to extract value
    enforce_uniqueness: bool = True  # Whether to enforce uniqueness check
    
    # Critical slots that MUST have uniqueness enabled (Requirement 8.8)
    CRITICAL_SLOTS: Set[str] = field(default_factory=lambda: {
        "prefix", "neighbor", "direction", "route_map_name", 
        "local_pref", "cost", "metric", "prefix_list_name",
        "neighbor_address", "asn"
    }, repr=False)
    
    def __post_init__(self) -> None:
        """Ensure critical slots have uniqueness enabled (Requirement 8.8)."""
        if self.slot_name in self.CRITICAL_SLOTS:
            self.enforce_uniqueness = True
    
    def normalize_value(self, raw_value: str) -> Any:
        """
        Normalize extracted value (case, whitespace, type conversion).
        
        Args:
            raw_value: Raw extracted value from patch text
        
        Returns:
            Normalized value for comparison
        """
        if isinstance(self.expected_value, int):
            try:
                return int(raw_value.strip())
            except (ValueError, TypeError):
                return raw_value.strip()
        return raw_value.strip().lower()
    
    def matches(self, extracted_value: Any) -> bool:
        """
        Check if extracted value matches expected (after normalization).
        
        Args:
            extracted_value: Value extracted from patch text
        
        Returns:
            True if values match after normalization
        """
        normalized = self.normalize_value(str(extracted_value))
        expected_normalized = self.normalize_value(str(self.expected_value))
        return normalized == expected_normalized
    
    def extract_all(self, patch_text: Optional[str]) -> List[str]:
        """
        Extract all occurrences of this slot from patch text.
        
        Uses the extraction_pattern regex to find all matches in the patch text.
        
        Args:
            patch_text: The LLM-generated patch text to scan
        
        Returns:
            List of all extracted values (may be empty if no matches)
        
        Requirements: 8.1 (presence check support)
        """
        if patch_text is None:
            return []
        matches = re.findall(self.extraction_pattern, patch_text, re.IGNORECASE | re.MULTILINE)
        return matches
    
    def verify(self, patch_text: Optional[str]) -> tuple:
        """
        Verify slot satisfies presence + uniqueness + equality.
        
        This method performs three checks in order:
        1. Presence: At least one match must be found
        2. Uniqueness: If enforce_uniqueness is True, exactly one match must be found
        3. Equality: The extracted value must match the expected value
        
        Args:
            patch_text: The LLM-generated patch text to verify
        
        Returns:
            Tuple of (passed, failure_category, details):
            - passed: True if all checks pass
            - failure_category: "missing" | "multi_match" | "mismatch" | None
            - details: Dict with found values, expected, actual, etc.
        
        Requirements: 8.1 (presence), 8.2 (uniqueness), 8.3 (equality), 8.4 (RETRYABLE)
        """
        if patch_text is None:
            return False, "missing", {"expected": self.expected_value, "actual": [], "count": 0}
            
        all_matches = self.extract_all(patch_text)
        
        # Presence check (Requirement 8.1, 8.5)
        if len(all_matches) == 0:
            return False, "missing", {
                "slot": self.slot_name,
                "expected": self.expected_value,
            }
        
        # Uniqueness check (Requirement 8.2, 8.6) - if enabled
        if self.enforce_uniqueness and len(all_matches) > 1:
            return False, "multi_match", {
                "slot": self.slot_name,
                "expected": self.expected_value,
                "found_values": all_matches,
                "count": len(all_matches),
            }
        
        # Equality check (Requirement 8.3, 8.7) - use first match
        extracted = all_matches[0]
        if not self.matches(extracted):
            return False, "mismatch", {
                "slot": self.slot_name,
                "expected": self.expected_value,
                "actual": extracted,
            }
        
        return True, None, None


@dataclass
class ScopeWhitelist:
    """
    Syntax scope whitelist based on OperationType (Task B).
    
    Defines allowed and forbidden command patterns for each operation type.
    This enforces that LLM-generated patches only contain commands appropriate
    for the specific operation being performed.
    
    Attributes:
        operation_type: The type of operation ("PREFIX_LIST", "ROUTE_MAP", "NEIGHBOR_BIND")
        allowed_patterns: List of compiled regex patterns that ARE allowed
        forbidden_patterns: List of (compiled_pattern, rule_name) tuples that are forbidden
    
    Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
    """
    operation_type: str  # "PREFIX_LIST" | "ROUTE_MAP" | "NEIGHBOR_BIND"
    allowed_patterns: List[re.Pattern] = field(default_factory=list)
    forbidden_patterns: List[tuple] = field(default_factory=list)  # List of (pattern, rule_name)
    
    @classmethod
    def for_prefix_list(cls) -> 'ScopeWhitelist':
        """
        Create scope whitelist for PREFIX_LIST operations.
        
        Allows:
        - ip prefix-list definitions
        - Comments (!)
        - Blank lines
        
        Forbids:
        - route-map definitions
        - neighbor commands
        - router bgp commands
        - interface commands
        
        Requirements: 9.1
        
        Returns:
            ScopeWhitelist configured for PREFIX_LIST operations
        """
        return cls(
            operation_type="PREFIX_LIST",
            allowed_patterns=[
                re.compile(r'^\s*ip\s+prefix-list\s+', re.IGNORECASE),
                re.compile(r'^\s*!'),  # Comments
                re.compile(r'^\s*$'),  # Blank lines
            ],
            forbidden_patterns=[
                (re.compile(r'^\s*route-map\s+', re.IGNORECASE), "route-map definition forbidden in PREFIX_LIST"),
                (re.compile(r'^\s*neighbor\s+', re.IGNORECASE), "neighbor command forbidden in PREFIX_LIST"),
                (re.compile(r'^\s*router\s+bgp\s+', re.IGNORECASE), "router bgp forbidden in PREFIX_LIST"),
                (re.compile(r'^\s*interface\s+', re.IGNORECASE), "interface forbidden in PREFIX_LIST"),
            ],
        )
    
    @classmethod
    def for_route_map(cls) -> 'ScopeWhitelist':
        """
        Create scope whitelist for ROUTE_MAP operations.
        
        Allows:
        - route-map definitions
        - match clauses
        - set clauses
        - description clauses
        - Comments (!)
        - Blank lines
        - exit command
        
        Forbids:
        - ip prefix-list definitions
        - neighbor commands
        - router bgp commands
        
        Requirements: 9.2
        
        Returns:
            ScopeWhitelist configured for ROUTE_MAP operations
        """
        return cls(
            operation_type="ROUTE_MAP",
            allowed_patterns=[
                re.compile(r'^\s*route-map\s+', re.IGNORECASE),
                re.compile(r'^\s*match\s+', re.IGNORECASE),
                re.compile(r'^\s*set\s+', re.IGNORECASE),
                re.compile(r'^\s*description\s+', re.IGNORECASE),
                re.compile(r'^\s*!'),  # Comments
                re.compile(r'^\s*$'),  # Blank lines
                re.compile(r'^\s*exit\s*$', re.IGNORECASE),  # exit command
            ],
            forbidden_patterns=[
                (re.compile(r'^\s*ip\s+prefix-list\s+', re.IGNORECASE), "prefix-list definition forbidden in ROUTE_MAP"),
                (re.compile(r'^\s*neighbor\s+', re.IGNORECASE), "neighbor command forbidden in ROUTE_MAP"),
                (re.compile(r'^\s*router\s+bgp\s+', re.IGNORECASE), "router bgp forbidden in ROUTE_MAP"),
            ],
        )
    
    @classmethod
    def for_neighbor_bind(cls) -> 'ScopeWhitelist':
        """
        Create scope whitelist for NEIGHBOR_BIND operations (frr.conf style).
        
        frr.conf style requirements:
        - router bgp <asn> MUST appear exactly 1 time (enforced separately)
        - neighbor <IP> route-map <RM> <direction> MUST appear
        
        Allows:
        - router bgp context line (required, exactly 1)
        - neighbor route-map binding lines
        - Comments (!)
        - Blank lines
        - exit command
        
        Forbids:
        - route-map definitions (permit/deny clauses)
        - ip prefix-list definitions
        
        Requirements: 9.3, 11.1, 11.2, 11.3
        
        Returns:
            ScopeWhitelist configured for NEIGHBOR_BIND operations
        """
        return cls(
            operation_type="NEIGHBOR_BIND",
            allowed_patterns=[
                re.compile(r'^\s*router\s+bgp\s+\d+', re.IGNORECASE),  # Required context (exactly 1)
                re.compile(r'^\s*neighbor\s+\S+\s+route-map\s+', re.IGNORECASE),
                re.compile(r'^\s*!'),  # Comments
                re.compile(r'^\s*$'),  # Blank lines
                re.compile(r'^\s*exit\s*$', re.IGNORECASE),  # exit command
            ],
            forbidden_patterns=[
                (re.compile(r'^\s*route-map\s+\S+\s+(?:permit|deny)', re.IGNORECASE), "route-map definition forbidden in NEIGHBOR_BIND"),
                (re.compile(r'^\s*ip\s+prefix-list\s+', re.IGNORECASE), "prefix-list definition forbidden in NEIGHBOR_BIND"),
            ],
        )
    
    def check_line(self, line: str) -> tuple:
        """
        Check if a line is allowed by this scope whitelist.
        
        The check follows this order:
        1. Check forbidden patterns first (fail-fast)
        2. Check if line matches any allowed pattern
        3. Lines that don't match any pattern are allowed (permissive for unknown lines)
        
        Args:
            line: A single line from the patch text to check
        
        Returns:
            Tuple of (allowed: bool, violation_reason: Optional[str])
            - (True, None) if line is allowed
            - (False, rule_name) if line violates a forbidden pattern
        
        Requirements: 9.4, 9.5
        """
        # Check forbidden patterns first (fail-fast)
        for pattern, rule_name in self.forbidden_patterns:
            if pattern.match(line):
                return False, rule_name
        
        # Check if line matches any allowed pattern
        for pattern in self.allowed_patterns:
            if pattern.match(line):
                return True, None
        
        # Line doesn't match any allowed pattern - but we're permissive for unknown lines
        # Only explicitly forbidden patterns trigger violations
        return True, None
    
    def check_patch(self, patch_text: str) -> List[Dict[str, Any]]:
        """
        Check all lines in a patch text against this scope whitelist.
        
        Args:
            patch_text: The full patch text to check
        
        Returns:
            List of violation dictionaries, each containing:
            - rule: The rule name that was violated
            - token: The line content that violated the rule (truncated)
            - line_number: The line number (1-indexed)
        
        Requirements: 9.4, 9.5
        """
        violations = []
        for line_num, line in enumerate(patch_text.split('\n'), start=1):
            allowed, violation_rule = self.check_line(line)
            if not allowed:
                violations.append({
                    "rule": violation_rule,
                    "token": line.strip()[:50],  # Truncate for readability
                    "line_number": line_num,
                })
        return violations


@dataclass
class ObjectWhitelist:
    """
    Object name whitelist based on PatchPlan/op_params (Task B).
    
    Defines allowed object names for each category. This enforces that
    LLM-generated patches only reference objects that are authorized
    by the operation parameters.
    
    Attributes:
        allowed_prefix_lists: Set of allowed prefix-list names
        allowed_route_maps: Set of allowed route-map names
        allowed_neighbors: Set of allowed neighbor IP addresses
        allowed_asns: Set of allowed ASN values
        allowed_interfaces: Set of allowed interface names
    
    Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
    """
    allowed_prefix_lists: Set[str] = field(default_factory=set)
    allowed_route_maps: Set[str] = field(default_factory=set)
    allowed_neighbors: Set[str] = field(default_factory=set)
    allowed_asns: Set[int] = field(default_factory=set)
    allowed_interfaces: Set[str] = field(default_factory=set)
    
    @classmethod
    def from_op_params(cls, op_params: Dict[str, Any]) -> 'ObjectWhitelist':
        """
        Build object whitelist from op_params.
        
        Extracts allowed object names from the operation parameters dictionary.
        This factory method creates a whitelist that permits only the objects
        explicitly referenced in the operation.
        
        Args:
            op_params: Dictionary of operation parameters from PatchOperation
        
        Returns:
            ObjectWhitelist configured with allowed objects from op_params
        
        Requirements: 10.1
        """
        prefix_lists: Set[str] = set()
        route_maps: Set[str] = set()
        neighbors: Set[str] = set()
        asns: Set[int] = set()
        interfaces: Set[str] = set()
        
        # Extract prefix-list name
        if "prefix_list_name" in op_params:
            prefix_lists.add(op_params["prefix_list_name"])
        
        # Extract route-map name
        if "route_map_name" in op_params:
            route_maps.add(op_params["route_map_name"])
        
        # Extract neighbor address (support both keys)
        if "neighbor_address" in op_params:
            neighbors.add(op_params["neighbor_address"])
        if "neighbor" in op_params:
            neighbors.add(op_params["neighbor"])
        
        # Extract ASN
        if "asn" in op_params:
            try:
                asns.add(int(op_params["asn"]))
            except (ValueError, TypeError):
                pass
        
        # Extract interface
        if "interface" in op_params:
            interfaces.add(op_params["interface"])
        
        # Extract from expected_names list if present (for multi-object operations)
        if "expected_names" in op_params:
            for name in op_params["expected_names"]:
                # Add to both prefix_lists and route_maps since we don't know the type
                prefix_lists.add(name)
                route_maps.add(name)
        
        # Extract from allowed_prefix_lists if explicitly provided
        if "allowed_prefix_lists" in op_params:
            for name in op_params["allowed_prefix_lists"]:
                prefix_lists.add(name)
        
        # Extract from allowed_route_maps if explicitly provided
        if "allowed_route_maps" in op_params:
            for name in op_params["allowed_route_maps"]:
                route_maps.add(name)
        
        # Extract from allowed_neighbors if explicitly provided
        if "allowed_neighbors" in op_params:
            for addr in op_params["allowed_neighbors"]:
                neighbors.add(addr)
        
        # Extract from allowed_asns if explicitly provided
        if "allowed_asns" in op_params:
            for asn in op_params["allowed_asns"]:
                try:
                    asns.add(int(asn))
                except (ValueError, TypeError):
                    pass
        
        return cls(
            allowed_prefix_lists=prefix_lists,
            allowed_route_maps=route_maps,
            allowed_neighbors=neighbors,
            allowed_asns=asns,
            allowed_interfaces=interfaces,
        )
    
    def check_prefix_list(self, name: str) -> bool:
        """
        Check if prefix-list name is allowed.
        
        If the whitelist is empty (no restrictions), any name is allowed.
        Otherwise, the name must be in the allowed set.
        
        Args:
            name: The prefix-list name to check
        
        Returns:
            True if the name is allowed, False otherwise
        
        Requirements: 10.2
        """
        if not self.allowed_prefix_lists:
            return True  # No restrictions if whitelist is empty
        return name in self.allowed_prefix_lists
    
    def check_route_map(self, name: str) -> bool:
        """
        Check if route-map name is allowed.
        
        If the whitelist is empty (no restrictions), any name is allowed.
        Otherwise, the name must be in the allowed set.
        
        Args:
            name: The route-map name to check
        
        Returns:
            True if the name is allowed, False otherwise
        
        Requirements: 10.3
        """
        if not self.allowed_route_maps:
            return True  # No restrictions if whitelist is empty
        return name in self.allowed_route_maps
    
    def check_neighbor(self, address: str) -> bool:
        """
        Check if neighbor address is allowed.
        
        If the whitelist is empty (no restrictions), any address is allowed.
        Otherwise, the address must be in the allowed set.
        
        Args:
            address: The neighbor IP address to check
        
        Returns:
            True if the address is allowed, False otherwise
        
        Requirements: 10.4
        """
        if not self.allowed_neighbors:
            return True  # No restrictions if whitelist is empty
        return address in self.allowed_neighbors
    
    def check_asn(self, asn: int) -> bool:
        """
        Check if ASN is allowed.
        
        If the whitelist is empty (no restrictions), any ASN is allowed.
        Otherwise, the ASN must be in the allowed set.
        
        Args:
            asn: The ASN value to check
        
        Returns:
            True if the ASN is allowed, False otherwise
        
        Requirements: 10.5
        """
        if not self.allowed_asns:
            return True  # No restrictions if whitelist is empty
        return asn in self.allowed_asns
    
    def check_interface(self, name: str) -> bool:
        """
        Check if interface name is allowed.
        
        If the whitelist is empty (no restrictions), any name is allowed.
        Otherwise, the name must be in the allowed set.
        
        Args:
            name: The interface name to check
        
        Returns:
            True if the name is allowed, False otherwise
        """
        if not self.allowed_interfaces:
            return True  # No restrictions if whitelist is empty
        return name in self.allowed_interfaces
    
    def check_patch(self, patch_text: str) -> List[Dict[str, Any]]:
        """
        Check all objects in a patch text against this object whitelist.
        
        Scans the patch text for all object references (prefix-lists, route-maps,
        neighbors, ASNs) and checks each against the whitelist.
        
        Args:
            patch_text: The full patch text to check
        
        Returns:
            List of violation dictionaries, each containing:
            - rule: The whitelist rule that was violated
            - object_type: Type of object ("prefix_list", "route_map", "neighbor", "asn")
            - name: The unauthorized object name/value
        
        Requirements: 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
        """
        violations: List[Dict[str, Any]] = []
        
        # Check prefix-list names (from definitions)
        # Pattern: ip prefix-list <name>
        for match in re.finditer(r'ip\s+prefix-list\s+(\S+)', patch_text, re.IGNORECASE):
            name = match.group(1)
            if not self.check_prefix_list(name):
                violations.append({
                    "rule": "prefix_list_whitelist",
                    "object_type": "prefix_list",
                    "name": name,
                })
        
        # Check route-map names (from definitions)
        # Pattern: route-map <name> permit|deny
        for match in re.finditer(r'route-map\s+(\S+)\s+(?:permit|deny)', patch_text, re.IGNORECASE):
            name = match.group(1)
            if not self.check_route_map(name):
                violations.append({
                    "rule": "route_map_whitelist",
                    "object_type": "route_map",
                    "name": name,
                })
        
        # Check neighbor addresses (from neighbor route-map bindings)
        # Pattern: neighbor <address> route-map
        for match in re.finditer(r'neighbor\s+(\S+)\s+route-map', patch_text, re.IGNORECASE):
            address = match.group(1)
            if not self.check_neighbor(address):
                violations.append({
                    "rule": "neighbor_whitelist",
                    "object_type": "neighbor",
                    "name": address,
                })
        
        # Check ASNs (from router bgp context)
        # Pattern: router bgp <asn>
        for match in re.finditer(r'router\s+bgp\s+(\d+)', patch_text, re.IGNORECASE):
            try:
                asn = int(match.group(1))
                if not self.check_asn(asn):
                    violations.append({
                        "rule": "asn_whitelist",
                        "object_type": "asn",
                        "name": str(asn),
                    })
            except ValueError:
                pass
        
        return violations


@dataclass
class ContractDiff:
    """
    Structured contract violation description.
    
    This class captures all types of contract violations in a structured format
    suitable for LLM prompt injection during retry. It supports the fail-closed
    semantics while enabling retry for most violations.
    
    Categories (Task A - Slot verification failures):
    - missing: slots that failed presence check
    - multi_match: slots that failed uniqueness check (multiple occurrences)
    - mismatch: slots that failed equality check
    
    Categories (Task B - Whitelist violations):
    - scope_violations: syntax scope whitelist violations
    - object_violations: object name whitelist violations
    
    Categories (Task C - Neighbor binding context):
    - neighbor_context_violations: router bgp context violations
    
    Categories (Template-RAG - Must Fix 3):
    - missing_slots: template placeholders without corresponding op_params values
    
    Other categories:
    - forbidden_found: forbidden patterns found
    - prefix_isolation_missing: route-maps without match prefix-list
    - extra_blocks: unexpected configuration blocks
    
    Attributes:
        severity: Severity level (RETRYABLE or HARD_STOP), defaults to RETRYABLE
        template_id: ID of the template used for generation (for audit)
        missing: List of slots that failed presence check
        multi_match: List of slots that failed uniqueness check
        mismatch: List of slots that failed equality check
        missing_slots: List of template placeholders missing from op_params (Must Fix 3)
        scope_violations: List of syntax scope whitelist violations
        object_violations: List of object name whitelist violations
        neighbor_context_violations: List of neighbor binding context violations
        forbidden_found: List of forbidden patterns found
        prefix_isolation_missing: Route-map name missing prefix isolation
        extra_blocks: List of unexpected configuration blocks
    
    Requirements: 5.1-5.8, 7.2, 7.3, 7.4, 8.1, 8.2, 8.3, 8.4
    """
    # Severity field (Requirement 7.2) - defaults to RETRYABLE
    severity: Severity = field(default=Severity.RETRYABLE)
    
    # Template context (Requirement 8.2)
    template_id: Optional[str] = None
    
    # Task A: Slot verification failures (Requirements 5.1, 5.2, 5.3)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    multi_match: List[Dict[str, Any]] = field(default_factory=list)
    mismatch: List[Dict[str, Any]] = field(default_factory=list)
    
    # Template-RAG: Missing slots category (Must Fix 3, Requirement 8.1)
    missing_slots: List[str] = field(default_factory=list)
    
    # Task B: Whitelist violations (Requirements 5.6, 5.7)
    scope_violations: List[Dict[str, Any]] = field(default_factory=list)
    object_violations: List[Dict[str, Any]] = field(default_factory=list)
    
    # Task C: Neighbor binding context violations
    neighbor_context_violations: List[Dict[str, Any]] = field(default_factory=list)
    
    # Other categories (Requirements 5.4, 5.5)
    forbidden_found: List[str] = field(default_factory=list)
    prefix_isolation_missing: Optional[str] = None
    extra_blocks: List[str] = field(default_factory=list)
    
    def has_violations(self) -> bool:
        """
        Check if any violations exist.
        
        Returns:
            True if any violation category has entries, False otherwise.
        """
        return bool(
            self.missing or
            self.multi_match or
            self.mismatch or
            self.missing_slots or
            self.scope_violations or
            self.object_violations or
            self.neighbor_context_violations or
            self.forbidden_found or
            self.prefix_isolation_missing or
            self.extra_blocks
        )
    
    def to_prompt_text(self) -> str:
        """
        Format as structured text for LLM prompt injection.
        
        This method generates a human-readable summary of all contract violations
        that can be appended to the LLM prompt during retry. The format is designed
        to help the LLM understand what went wrong and how to fix it.
        
        Failure categories (Requirement 8.1):
        - "missing required line: <line>"
        - "mismatch: expected <expected>, got <actual>"
        - "forbidden pattern hit: <pattern>"
        - "missing_slots: <slot1>, <slot2>, ..."
        
        Returns:
            Formatted string describing all violations.
        
        Requirements: 5.8, 8.1, 8.3
        """
        lines = ["CONTRACT VIOLATIONS:"]
        
        # Include template_id if present (Requirement 8.2)
        if self.template_id:
            lines.append(f"Template: {self.template_id}")
            lines.append("")
        
        # Template-RAG: Missing slots category (Must Fix 3, Requirement 8.1)
        if self.missing_slots:
            lines.append("MISSING SLOTS (template placeholders without values):")
            lines.append(f"  - missing_slots: {', '.join(self.missing_slots)}")
            lines.append("")
            lines.append("Please infer appropriate values for the missing slots.")
            lines.append("REQUIRED/FORBIDDEN constraints remain unchanged.")
            lines.append("")
        
        # Task A: Slot verification failures
        if self.missing:
            lines.append("MISSING REQUIRED LINES (presence check failed):")
            for m in self.missing:
                slot = m.get("slot", "unknown")
                expected = m.get("expected", "unknown")
                lines.append(f"  - missing required line: {slot} (expected '{expected}')")
        
        if self.multi_match:
            lines.append("MULTIPLE MATCHES (uniqueness violation):")
            for m in self.multi_match:
                slot = m.get("slot", "unknown")
                count = m.get("count", 0)
                found_values = m.get("found_values", [])
                lines.append(f"  - {slot}: found {count} occurrences: {found_values}")
                lines.append(f"    (expected exactly 1 occurrence)")
        
        if self.mismatch:
            lines.append("VALUE MISMATCHES (equality check failed):")
            for m in self.mismatch:
                slot = m.get("slot", "unknown")
                expected = m.get("expected", "unknown")
                actual = m.get("actual", "unknown")
                lines.append(f"  - {slot}: mismatch: expected '{expected}', got '{actual}'")
        
        # Task B: Whitelist violations
        if self.scope_violations:
            lines.append("SCOPE VIOLATIONS (forbidden syntax for this operation type):")
            for v in self.scope_violations:
                rule = v.get("rule", "unknown rule")
                token = v.get("token", "unknown token")
                lines.append(f"  - {rule}: found '{token}'")
        
        if self.object_violations:
            lines.append("OBJECT VIOLATIONS (unauthorized object names):")
            for v in self.object_violations:
                rule = v.get("rule", "unknown rule")
                object_type = v.get("object_type", "unknown type")
                name = v.get("name", "unknown name")
                lines.append(f"  - {rule}: unauthorized {object_type} '{name}'")
        
        # Task C: Neighbor binding context violations
        if self.neighbor_context_violations:
            lines.append("NEIGHBOR CONTEXT VIOLATIONS:")
            for v in self.neighbor_context_violations:
                rule = v.get("rule", "unknown rule")
                detail = v.get("detail", "unknown detail")
                lines.append(f"  - {rule}: {detail}")
        
        # Other categories
        if self.forbidden_found:
            lines.append("FORBIDDEN PATTERNS HIT:")
            for f in self.forbidden_found:
                lines.append(f"  - forbidden pattern hit: {f}")
        
        if self.prefix_isolation_missing:
            lines.append(f"PREFIX ISOLATION MISSING: route-map '{self.prefix_isolation_missing}' lacks 'match ip address prefix-list' clause")
        
        if self.extra_blocks:
            lines.append("UNEXPECTED CONFIGURATION BLOCKS:")
            for block in self.extra_blocks:
                lines.append(f"  - {block}")
        
        # Add severity information
        lines.append("")
        lines.append(f"SEVERITY: {self.severity.value}")
        if self.severity == Severity.RETRYABLE:
            lines.append("ACTION: Please regenerate the patch addressing the above violations.")
            lines.append("The contract constraints (REQUIRED/FORBIDDEN) remain unchanged.")
        else:
            lines.append("ACTION: HARD STOP - This violation cannot be retried.")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for logging/reporting.
        
        Returns JSON-serializable dictionary representation of all violations.
        
        Returns:
            Dictionary representation of all violations.
        
        Requirements: 8.4
        """
        return {
            "severity": self.severity.value,
            "template_id": self.template_id,
            "missing": self.missing,
            "multi_match": self.multi_match,
            "mismatch": self.mismatch,
            "missing_slots": self.missing_slots,
            "scope_violations": self.scope_violations,
            "object_violations": self.object_violations,
            "neighbor_context_violations": self.neighbor_context_violations,
            "forbidden_found": self.forbidden_found,
            "prefix_isolation_missing": self.prefix_isolation_missing,
            "extra_blocks": self.extra_blocks,
            "has_violations": self.has_violations(),
        }


@dataclass
class ForbiddenPattern:
    """
    Pattern that is forbidden in a specific operation type.
    
    When a forbidden pattern is found in the patch text, it triggers a
    RETRYABLE contract error (not HARD_STOP).
    
    Attributes:
        pattern: Regex pattern string to match
        description: Human-readable description of what this pattern represents
        reason: Explanation of why this pattern is forbidden
    """
    pattern: str           # Regex pattern string
    description: str       # Human-readable description
    reason: str            # Why this is forbidden


@dataclass
class DangerousPattern:
    """
    Pattern that triggers HARD_STOP (not retryable).
    
    When a dangerous pattern is found in the patch text, the system
    immediately fails with HARD_STOP severity - no retry is allowed.
    
    Attributes:
        pattern: Regex pattern string to match
        description: Human-readable description of what this pattern represents
        reason: Explanation of why this pattern is dangerous
    """
    pattern: str           # Regex pattern string
    description: str       # Human-readable description
    reason: str            # Why this is dangerous


@dataclass
class PatchContract:
    """
    Immutable contract defining correctness criteria for a patch operation.
    
    This contract is constructed from op_params and remains immutable during
    the entire retry process. It uses structural slot matching with scope
    and object whitelists.
    
    Key Design Principles:
    - Immutable: Contract cannot be modified during retry
    - Structural: Uses slot extraction rather than string matching
    - Fail-Closed: Any violation prevents execution
    - Severity-Based: RETRYABLE vs HARD_STOP classification
    
    Attributes:
        op_id: Operation identifier
        template: Template name (e.g., "prefix_list_entry", "route_map_sequence")
        device: Target device name
        operation_type: Type of operation ("PREFIX_LIST", "ROUTE_MAP", "NEIGHBOR_BIND")
        required_slots: List of RequiredSlot objects that must satisfy presence + uniqueness + equality
        forbidden_patterns: List of ForbiddenPattern objects that must not appear
        dangerous_patterns: List of DangerousPattern objects that trigger HARD_STOP
        scope_whitelist: Optional ScopeWhitelist for syntax scope enforcement (Task B)
        object_whitelist: Optional ObjectWhitelist for object name enforcement (Task B)
        optional_style: List of allowed style elements (comments, descriptions, etc.)
        enforce_prefix_isolation: Whether to enforce prefix isolation for route-maps
        insertion_context: Optional insertion context (e.g., "router bgp <asn>")
        expected_asn: Expected ASN for neighbor binding context validation (Task C)
        expected_neighbor: Expected neighbor IP for neighbor binding context validation (Task C)
        expected_direction: Expected direction (in/out) for neighbor binding context validation (Task C)
    
    Requirements: 9.1-9.3, 10.1, 11.1-11.10
    """
    op_id: str
    template: str
    device: str
    operation_type: str  # "PREFIX_LIST" | "ROUTE_MAP" | "NEIGHBOR_BIND"
    
    # Slots that MUST satisfy presence + uniqueness + equality (Task A)
    required_slots: List[RequiredSlot] = field(default_factory=list)
    
    # Patterns that MUST NOT appear (RETRYABLE)
    forbidden_patterns: List[ForbiddenPattern] = field(default_factory=list)
    
    # Dangerous patterns that trigger HARD_STOP
    dangerous_patterns: List[DangerousPattern] = field(default_factory=list)
    
    # Scope whitelist based on operation type (Task B) - Requirements 9.1-9.3
    scope_whitelist: Optional[ScopeWhitelist] = None
    
    # Object whitelist based on op_params (Task B) - Requirement 10.1
    object_whitelist: Optional[ObjectWhitelist] = None
    
    # Optional style elements (comments, descriptions, blank lines)
    optional_style: List[str] = field(default_factory=list)
    
    # Special constraints
    enforce_prefix_isolation: bool = True
    
    # Insertion context for neighbor binding (e.g., "router bgp <asn>")
    insertion_context: Optional[str] = None
    
    # Neighbor binding context constraints (Task C) - Requirements 11.1-11.10
    expected_asn: Optional[int] = None
    expected_neighbor: Optional[str] = None
    expected_direction: Optional[str] = None


class ContractBuilder:
    """
    Builds PatchContract from PatchOperation.
    
    This builder constructs immutable PatchContract objects based on the
    operation type and parameters. The contract defines all correctness
    criteria that must be satisfied by the LLM-generated patch.
    
    Key Responsibilities:
    - Determine operation type from template name
    - Build appropriate required slots based on op_params
    - Configure scope and object whitelists
    - Set up forbidden and dangerous patterns
    - Integrate template constraints (required_lines, forbidden_patterns)
    
    Requirements: 7.1, 7.2, 7.3, 7.4, 9.1-9.3, 10.1, 11.1-11.10
    """
    
    def build_contract(
        self, 
        op: Any, 
        template: Optional[Any] = None
    ) -> PatchContract:
        """
        Build contract using operation type for template selection.
        
        When a template is provided, its required_lines and forbidden_patterns
        are extracted and merged with default constraints. Template constraints
        take priority on conflict.
        
        Args:
            op: PatchOperation object with template, device, params, and optional id
            template: Optional TemplateDefinition with required_lines and forbidden_patterns
        
        Returns:
            PatchContract configured for the operation type
        
        Requirements: 7.1, 7.2
        """
        op_template = (getattr(op, 'template', '') or "").lower()
        
        # CRITICAL: Check neighbor BEFORE route_map because neighbor_route_map.j2
        # contains both "neighbor" and "route_map" in the name, but it's a NEIGHBOR_BIND
        # operation that should use deterministic rendering (no router bgp context).
        if "prefix_list" in op_template:
            contract = self._build_prefix_list_contract(op)
        elif "neighbor" in op_template:
            # neighbor_route_map.j2, neighbor_bind.j2, etc. are NEIGHBOR_BIND operations
            contract = self._build_neighbor_binding_contract(op)
        elif "route_map" in op_template:
            # route_map_sequence.j2, route_map_localpref.j2, etc. are ROUTE_MAP operations
            contract = self._build_route_map_contract(op)
        else:
            contract = self._build_generic_contract(op)
        
        # If template is provided, merge its constraints
        if template is not None:
            contract = self._apply_template_constraints(contract, template, op)
        
        return contract
    
    def _apply_template_constraints(
        self,
        contract: PatchContract,
        template: Any,
        op: Any,
    ) -> PatchContract:
        """
        Apply template constraints to an existing contract.
        
        Extracts required_lines and forbidden_patterns from the template
        and merges them with the contract's existing constraints.
        Template constraints take priority on conflict.
        
        Args:
            contract: The base PatchContract to modify
            template: TemplateDefinition with required_lines and forbidden_patterns
            op: PatchOperation for parameter substitution
        
        Returns:
            Updated PatchContract with template constraints applied
        
        Requirements: 7.1, 7.2, 7.3, 7.4
        """
        # Extract template constraints
        template_required_lines = getattr(template, 'required_lines', []) or []
        template_forbidden_patterns = getattr(template, 'forbidden_patterns', []) or []
        template_id = getattr(template, 'id', 'unknown')
        
        # Merge constraints (template takes priority)
        merged_required, merged_forbidden = self._merge_constraints(
            template_required_lines=template_required_lines,
            template_forbidden_patterns=template_forbidden_patterns,
            default_required=contract.required_slots,
            default_forbidden=contract.forbidden_patterns,
            op=op,
            template_id=template_id,
        )
        
        # Create new contract with merged constraints
        # Note: We create a new PatchContract since it should be immutable
        return PatchContract(
            op_id=contract.op_id,
            template=contract.template,
            device=contract.device,
            operation_type=contract.operation_type,
            required_slots=merged_required,
            forbidden_patterns=merged_forbidden,
            dangerous_patterns=contract.dangerous_patterns,
            scope_whitelist=contract.scope_whitelist,
            object_whitelist=contract.object_whitelist,
            optional_style=contract.optional_style,
            enforce_prefix_isolation=contract.enforce_prefix_isolation,
            insertion_context=contract.insertion_context,
            expected_asn=contract.expected_asn,
            expected_neighbor=contract.expected_neighbor,
            expected_direction=contract.expected_direction,
        )
    
    def _merge_constraints(
        self,
        template_required_lines: List[str],
        template_forbidden_patterns: List[str],
        default_required: List[RequiredSlot],
        default_forbidden: List[ForbiddenPattern],
        op: Any,
        template_id: str,
    ) -> tuple:
        """
        Merge template constraints with default constraints.
        
        Template constraints take priority on conflict. This method:
        1. Converts template required_lines to RequiredSlot objects
        2. Converts template forbidden_patterns to ForbiddenPattern objects
        3. Merges with existing constraints, with template taking priority
        4. Logs any constraint overrides for audit
        
        Args:
            template_required_lines: Required lines from template (placeholder form)
            template_forbidden_patterns: Forbidden patterns from template (regex)
            default_required: Default RequiredSlot list from contract
            default_forbidden: Default ForbiddenPattern list from contract
            op: PatchOperation for parameter substitution
            template_id: Template ID for logging
        
        Returns:
            Tuple of (merged_required_slots, merged_forbidden_patterns)
        
        Requirements: 7.3, 7.4
        """
        params = getattr(op, 'params', {}) or {}
        
        # Start with default constraints
        merged_required = list(default_required)
        merged_forbidden = list(default_forbidden)
        
        # Track existing slot names for conflict detection
        existing_slot_names = {slot.slot_name for slot in merged_required}
        existing_forbidden_patterns = {fp.pattern for fp in merged_forbidden}
        
        # Convert template required_lines to RequiredSlot objects
        for line in template_required_lines:
            # Fill placeholders with op_params values
            filled_line = self._fill_template_line(line, params)
            
            # Create a slot name from the line (use first word or placeholder name)
            slot_name = self._extract_slot_name_from_line(line)
            
            # Check for conflict with existing slots
            if slot_name in existing_slot_names:
                # Log override for audit (Requirement 7.4)
                security_logger.info(
                    "Template constraint override: slot '%s' from template '%s' overrides default",
                    slot_name,
                    template_id,
                    extra={
                        "template_id": template_id,
                        "slot_name": slot_name,
                        "override_type": "required_slot",
                    },
                )
                # Remove existing slot with same name
                merged_required = [s for s in merged_required if s.slot_name != slot_name]
            
            # Create RequiredSlot from template line
            # Extract expected value from filled line or params
            expected_value = self._extract_expected_value(line, params)
            extraction_pattern = self._line_to_extraction_pattern(filled_line)
            
            if expected_value is not None:
                merged_required.append(RequiredSlot(
                    slot_name=f"template_{slot_name}",
                    expected_value=expected_value,
                    aliases=[],
                    extraction_pattern=extraction_pattern,
                    enforce_uniqueness=True,
                ))
        
        # Convert template forbidden_patterns to ForbiddenPattern objects
        for pattern in template_forbidden_patterns:
            # Check for conflict with existing patterns
            if pattern in existing_forbidden_patterns:
                # Log override for audit (Requirement 7.4)
                security_logger.info(
                    "Template constraint override: pattern '%s' from template '%s' already exists",
                    pattern[:50],
                    template_id,
                    extra={
                        "template_id": template_id,
                        "pattern": pattern,
                        "override_type": "forbidden_pattern",
                    },
                )
                continue  # Skip duplicate patterns
            
            merged_forbidden.append(ForbiddenPattern(
                pattern=pattern,
                description=f"Template forbidden pattern from {template_id}",
                reason=f"Pattern forbidden by template {template_id}",
            ))
        
        return merged_required, merged_forbidden
    
    def _fill_template_line(self, line: str, params: Dict[str, Any]) -> str:
        """
        Fill placeholders in a template line with parameter values.
        
        Args:
            line: Template line with {placeholder} markers
            params: Operation parameters
        
        Returns:
            Line with placeholders filled
        """
        import re as regex_module
        
        def replace_placeholder(match):
            placeholder = match.group(1)
            return str(params.get(placeholder, match.group(0)))
        
        return regex_module.sub(r'\{(\w+)\}', replace_placeholder, line)
    
    def _extract_slot_name_from_line(self, line: str) -> str:
        """
        Extract a slot name from a template line.
        
        Uses the first placeholder name found, or generates a name from the line.
        
        Args:
            line: Template line with {placeholder} markers
        
        Returns:
            Slot name string
        """
        import re as regex_module
        
        # Find first placeholder
        match = regex_module.search(r'\{(\w+)\}', line)
        if match:
            return match.group(1)
        
        # Generate name from first word
        words = line.strip().split()
        if words:
            return words[0].replace('-', '_').lower()
        
        return "unknown_slot"
    
    def _extract_expected_value(self, line: str, params: Dict[str, Any]) -> Optional[Any]:
        """
        Extract expected value from a template line using params.
        
        Args:
            line: Template line with {placeholder} markers
            params: Operation parameters
        
        Returns:
            Expected value or None if no placeholder found
        """
        import re as regex_module
        
        # Find first placeholder and get its value from params
        match = regex_module.search(r'\{(\w+)\}', line)
        if match:
            placeholder = match.group(1)
            return params.get(placeholder)
        
        return None
    
    def _line_to_extraction_pattern(self, filled_line: str) -> str:
        """
        Convert a filled template line to a regex extraction pattern.
        
        Args:
            filled_line: Template line with placeholders filled
        
        Returns:
            Regex pattern for extracting the value
        """
        # Escape special regex characters except for the value we want to capture
        escaped = re.escape(filled_line.strip())
        # The pattern should match the entire line
        return f".*{escaped}.*"
    
    def _build_prefix_list_contract(self, op: Any) -> PatchContract:
        """
        Build contract for prefix-list operations.
        
        Args:
            op: PatchOperation object
        
        Returns:
            PatchContract configured for PREFIX_LIST operations
        """
        params = getattr(op, 'params', {}) or {}
        op_id = getattr(op, 'id', 'unknown')
        device = getattr(op, 'device', 'unknown')
        template = getattr(op, 'template', 'prefix_list_entry')
        
        required_slots = []
        
        # Add prefix-list name slot
        if "prefix_list_name" in params:
            required_slots.append(RequiredSlot(
                slot_name="prefix_list_name",
                expected_value=params["prefix_list_name"],
                aliases=[],
                extraction_pattern=rf'ip\s+prefix-list\s+({re.escape(params["prefix_list_name"])})',
                enforce_uniqueness=True,
            ))
        
        # Add prefix slot if present
        if "prefix" in params:
            required_slots.append(RequiredSlot(
                slot_name="prefix",
                expected_value=params["prefix"],
                aliases=[],
                extraction_pattern=rf'ip\s+prefix-list\s+\S+\s+(?:seq\s+\d+\s+)?(?:permit|deny)\s+({re.escape(params["prefix"])})',
                enforce_uniqueness=True,
            ))
        
        # Forbidden patterns for prefix-list operations
        forbidden = [
            ForbiddenPattern(
                pattern=r'route-map\s+\S+\s+(?:permit|deny)',
                description="route-map definition",
                reason="prefix-list operation should not include route-map definitions",
            ),
            ForbiddenPattern(
                pattern=r'neighbor\s+\S+\s+route-map',
                description="neighbor route-map binding",
                reason="prefix-list operation should not include neighbor bindings",
            ),
        ]
        
        dangerous = self._get_common_dangerous_patterns()
        
        return PatchContract(
            op_id=op_id,
            template=template,
            device=device,
            operation_type="PREFIX_LIST",
            required_slots=required_slots,
            forbidden_patterns=forbidden,
            dangerous_patterns=dangerous,
            scope_whitelist=ScopeWhitelist.for_prefix_list(),
            object_whitelist=ObjectWhitelist.from_op_params(params),
            optional_style=["!", "description", "#"],
            enforce_prefix_isolation=False,  # Not applicable for prefix-list
        )
    
    def _build_route_map_contract(self, op: Any) -> PatchContract:
        """
        Build contract for route-map operations.
        
        Args:
            op: PatchOperation object
        
        Returns:
            PatchContract configured for ROUTE_MAP operations
        """
        params = getattr(op, 'params', {}) or {}
        op_id = getattr(op, 'id', 'unknown')
        device = getattr(op, 'device', 'unknown')
        template = getattr(op, 'template', 'route_map_sequence')
        
        required_slots = []
        
        # Add route-map name slot
        if "route_map_name" in params:
            required_slots.append(RequiredSlot(
                slot_name="route_map_name",
                expected_value=params["route_map_name"],
                aliases=[],
                extraction_pattern=rf'route-map\s+({re.escape(params["route_map_name"])})\s+(?:permit|deny)',
                enforce_uniqueness=True,
            ))
        
        # Add local-pref slot if present
        if "local_pref" in params:
            required_slots.append(RequiredSlot(
                slot_name="local_pref",
                expected_value=params["local_pref"],
                aliases=["local-preference", "local-pref", "local preference"],
                extraction_pattern=r'set\s+(?:local-preference|local-pref|local\s+preference)\s+(\d+)',
                enforce_uniqueness=True,
            ))
        
        # Add metric/cost slot if present
        metric_key = "metric" if "metric" in params else ("cost" if "cost" in params else None)
        if metric_key:
            required_slots.append(RequiredSlot(
                slot_name=metric_key,
                expected_value=params[metric_key],
                aliases=["metric", "cost"],
                extraction_pattern=r'set\s+metric\s+(\d+)',
                enforce_uniqueness=True,
            ))
        
        # Add prefix-list reference slot if present
        if "prefix_list_name" in params:
            required_slots.append(RequiredSlot(
                slot_name="prefix_list_ref",
                expected_value=params["prefix_list_name"],
                aliases=[],
                extraction_pattern=rf'match\s+ip\s+address\s+prefix-list\s+({re.escape(params["prefix_list_name"])})',
                enforce_uniqueness=True,
            ))
        
        # Forbidden patterns for route-map operations
        forbidden = [
            ForbiddenPattern(
                pattern=r'^[\s]*ip\s+prefix-list\s+',
                description="prefix-list definition",
                reason="route-map operation should not include prefix-list definitions",
            ),
            ForbiddenPattern(
                pattern=r'neighbor\s+\S+\s+route-map',
                description="neighbor route-map binding",
                reason="route-map operation should not include neighbor bindings",
            ),
        ]
        
        dangerous = self._get_common_dangerous_patterns()
        
        return PatchContract(
            op_id=op_id,
            template=template,
            device=device,
            operation_type="ROUTE_MAP",
            required_slots=required_slots,
            forbidden_patterns=forbidden,
            dangerous_patterns=dangerous,
            scope_whitelist=ScopeWhitelist.for_route_map(),
            object_whitelist=ObjectWhitelist.from_op_params(params),
            optional_style=["!", "description", "#"],
            enforce_prefix_isolation=True,  # Route-maps require prefix isolation
        )
    
    def _build_neighbor_binding_contract(self, op: Any) -> PatchContract:
        """
        Build contract for neighbor binding operations.
        
        NEIGHBOR_BIND operations produce ONLY the neighbor binding line:
        - neighbor <IP> route-map <RM> <direction> MUST appear
        - router bgp context is NOT required (patch-first minimalism)
        - Direction must be unique and match op_params
        
        Note: Deterministic rendering outputs only the neighbor binding line,
        so we do NOT require router bgp context in the contract.
        
        Args:
            op: PatchOperation object
        
        Returns:
            PatchContract configured for NEIGHBOR_BIND operations
        
        Requirements: 11.1, 11.2, 11.3 (modified for patch-first minimalism)
        """
        params = getattr(op, 'params', {}) or {}
        op_id = getattr(op, 'id', 'unknown')
        device = getattr(op, 'device', 'unknown')
        template = getattr(op, 'template', 'neighbor_route_map')
        
        neighbor_ip = params.get("neighbor_address", params.get("neighbor"))
        rm_name = params.get("route_map_name")
        direction = params.get("direction", "in")
        asn = params.get("asn")
        
        required_slots = []
        
        # NOTE: router bgp context is NOT required for patch-first minimalism
        # Deterministic rendering outputs only the neighbor binding line
        # The router bgp context is assumed to already exist in the config
        
        # REQUIRED_SLOT: neighbor address
        if neighbor_ip:
            required_slots.append(RequiredSlot(
                slot_name="neighbor_address",
                expected_value=neighbor_ip,
                aliases=[],
                extraction_pattern=rf'neighbor\s+({re.escape(neighbor_ip)})\s+route-map',
                enforce_uniqueness=True,
            ))
        
        # REQUIRED_SLOT: route-map name
        if rm_name:
            required_slots.append(RequiredSlot(
                slot_name="route_map_name",
                expected_value=rm_name,
                aliases=[],
                extraction_pattern=rf'neighbor\s+\S+\s+route-map\s+({re.escape(rm_name)})',
                enforce_uniqueness=True,
            ))
        
        # REQUIRED_SLOT: direction (in/out)
        required_slots.append(RequiredSlot(
            slot_name="direction",
            expected_value=direction,
            aliases=[],
            extraction_pattern=r'neighbor\s+\S+\s+route-map\s+\S+\s+(in|out)',
            enforce_uniqueness=True,  # Direction must appear exactly once
        ))
        
        # Forbidden patterns for neighbor binding operations
        # Do NOT forbid router bgp - it's REQUIRED for frr.conf style
        forbidden = [
            ForbiddenPattern(
                pattern=r'route-map\s+\S+\s+(?:permit|deny)\s+\d+',
                description="route-map definition",
                reason="neighbor binding should not include route-map definitions",
            ),
            ForbiddenPattern(
                pattern=r'^[\s]*ip\s+prefix-list\s+',
                description="prefix-list definition",
                reason="neighbor binding should not include prefix-list definitions",
            ),
        ]
        
        dangerous = self._get_common_dangerous_patterns()
        
        # Define insertion context
        insertion_context = f"router bgp {asn}" if asn else None
        
        # Convert asn to int if it's a string
        expected_asn = int(asn) if asn is not None else None
        
        return PatchContract(
            op_id=op_id,
            template=template,
            device=device,
            operation_type="NEIGHBOR_BIND",
            required_slots=required_slots,
            forbidden_patterns=forbidden,
            dangerous_patterns=dangerous,
            scope_whitelist=ScopeWhitelist.for_neighbor_bind(),
            object_whitelist=ObjectWhitelist.from_op_params(params),
            optional_style=["!", "description", "#"],
            enforce_prefix_isolation=False,  # Not applicable for neighbor binding
            insertion_context=insertion_context,
            expected_asn=expected_asn,
            expected_neighbor=neighbor_ip,
            expected_direction=direction,
        )
    
    def _build_generic_contract(self, op: Any) -> PatchContract:
        """
        Build a generic contract for unknown operation types.
        
        Args:
            op: PatchOperation object
        
        Returns:
            PatchContract with minimal constraints
        """
        params = getattr(op, 'params', {}) or {}
        op_id = getattr(op, 'id', 'unknown')
        device = getattr(op, 'device', 'unknown')
        template = getattr(op, 'template', 'generic')
        
        dangerous = self._get_common_dangerous_patterns()
        
        return PatchContract(
            op_id=op_id,
            template=template,
            device=device,
            operation_type="GENERIC",
            required_slots=[],
            forbidden_patterns=[],
            dangerous_patterns=dangerous,
            scope_whitelist=None,
            object_whitelist=ObjectWhitelist.from_op_params(params),
            optional_style=["!", "description", "#"],
            enforce_prefix_isolation=False,
        )
    
    def _get_common_dangerous_patterns(self) -> List[DangerousPattern]:
        """
        Get dangerous patterns common to all operation types.
        
        These patterns trigger HARD_STOP when detected.
        
        Returns:
            List of DangerousPattern objects
        """
        return [
            DangerousPattern(
                pattern=r'^\s*no\s+',
                description="destructive 'no' command",
                reason="Patch should not contain destructive commands",
            ),
            DangerousPattern(
                pattern=r'match\s+ip\s+address\s+any',
                description="match-any bypass",
                reason="match-any bypasses prefix isolation",
            ),
            # Note: "empty match (implicit match-any)" pattern removed because:
            # 1. It incorrectly matches valid route-map headers (the header line ends with seq number)
            # 2. Prefix isolation is already checked in verify_contract Step 8 as RETRYABLE
            # 3. The pattern cannot distinguish between a header with content on next lines
            #    vs a truly empty route-map block
            DangerousPattern(
                pattern=r'^\s*clear\s+',
                description="destructive 'clear' command",
                reason="Patch should not contain clearing commands",
            ),
            DangerousPattern(
                pattern=r'^\s*write\s+',
                description="write command",
                reason="Patch should not contain write commands",
            ),
            DangerousPattern(
                pattern=r'^\s*reload\b',
                description="reload command",
                reason="Patch should not contain reload commands",
            ),
            DangerousPattern(
                pattern=r'^\s*shutdown\b',
                description="shutdown command",
                reason="Patch should not contain shutdown commands",
            ),
            DangerousPattern(
                pattern=r'^\s*ip\s+route\s+',
                description="static route command",
                reason="Patch should not contain static route commands",
            ),
            DangerousPattern(
                pattern=r'^\s*default-information\s+originate',
                description="default route injection",
                reason="Patch should not inject default routes",
            ),
        ]


@dataclass
class VerificationResult:
    """
    Result of guard verification with failure classification.
    
    Attributes:
        ok: True if verification passed, False otherwise
        kind: Classification of the result (PASS, CONTRACT_FAIL, SECURITY_VIOLATION)
        reason: Human-readable description of the result
        expected: Dictionary of expected values (for failures)
        actual: Dictionary of actual values found (for failures)
    """
    ok: bool
    kind: FailureKind
    reason: str
    expected: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """
    Result of parameter extraction from LLM-generated configuration text.
    
    Contains all critical parameters that must be verified against the
    Policy Layer's computed values.
    
    Attributes:
        local_pref: Extracted local-preference value (if present)
        local_pref_form: Form detected ("canonical", "alias", "space", or None)
        metric: Extracted metric/cost value (if present)
        prefix_list_names: List of all prefix-list names found
        route_map_names: List of all route-map names found
        directions: Set of all directions (in/out) found in neighbor bindings
    
    Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 18.1, 18.2, 18.3
    """
    
    local_pref: Optional[int] = None
    local_pref_form: Optional[str] = None  # "canonical", "alias", "space", or None
    metric: Optional[int] = None
    prefix_list_names: List[str] = field(default_factory=list)
    route_map_names: List[str] = field(default_factory=list)
    directions: Set[str] = field(default_factory=set)


class ConstraintGuard:
    """
    Static analysis layer that enforces Policy Layer parameters on LLM output.
    
    The ConstraintGuard extracts critical parameters from generated configuration
    and asserts they exactly match the Policy Layer's computed values.
    
    Security Invariant: If verify() returns True, the patch_text conforms to op_params.
    
    Key Components:
    - extract_parameters(): Extracts critical parameters from config text
    - verify(): Validates extracted parameters against Policy Layer values
    
    Requirements: 1.1-1.5, 2.1-2.6
    """
    
    # Regex patterns for parameter extraction
    # Requirement 1.1, 18.1, 18.2, 18.3: Extract local-preference value (all alias forms)
    # Canonical: set local-preference <value>
    LOCAL_PREF_CANONICAL_PATTERN = re.compile(r'set\s+local-preference\s+(\d+)', re.IGNORECASE)
    # Alias: set local-pref <value>
    LOCAL_PREF_ALIAS_PATTERN = re.compile(r'set\s+local-pref\s+(\d+)', re.IGNORECASE)
    # Space variant: set local preference <value>
    LOCAL_PREF_SPACE_PATTERN = re.compile(r'set\s+local\s+preference\s+(\d+)', re.IGNORECASE)
    
    # Combined pattern for counting (matches any form)
    LOCAL_PREF_PATTERN = re.compile(
        r'set\s+(?:local-preference|local-pref|local\s+preference)\s+(\d+)',
        re.IGNORECASE
    )
    
    # Requirement 1.2: Extract metric value
    METRIC_PATTERN = re.compile(r'set\s+metric\s+(\d+)', re.IGNORECASE)
    
    # Requirement 1.3: Extract prefix-list name (at start of line)
    PREFIX_LIST_PATTERN = re.compile(r'^[\s]*ip\s+prefix-list\s+(\S+)', re.IGNORECASE | re.MULTILINE)
    
    # Requirement 1.4: Extract route-map name from route-map definition (at start of line)
    # This matches "route-map NAME permit/deny SEQ" at the start of a line
    ROUTE_MAP_DEF_PATTERN = re.compile(r'^[\s]*route-map\s+(\S+)\s+(?:permit|deny)', re.IGNORECASE | re.MULTILINE)
    
    # Requirement 1.4 & 1.5: Extract route-map name and direction from neighbor binding
    # This matches "neighbor X.X.X.X route-map NAME in|out"
    NEIGHBOR_ROUTE_MAP_PATTERN = re.compile(r'neighbor\s+\S+\s+route-map\s+(\S+)\s+(in|out)', re.IGNORECASE)
    
    # Requirement 8.1: Pattern to detect `match ip address prefix-list` within route-map blocks
    # This matches "match ip address prefix-list <name>" which is required for prefix isolation
    ROUTE_MAP_MATCH_PREFIX_LIST_PATTERN = re.compile(
        r'match\s+ip\s+address\s+prefix-list\s+(\S+)',
        re.IGNORECASE
    )
    
    # Pattern to extract route-map blocks with their content
    # Captures route-map name and all content until the next route-map or end of config
    ROUTE_MAP_BLOCK_PATTERN = re.compile(
        r'^[\s]*route-map\s+(\S+)\s+(?:permit|deny)\s+\d+\s*((?:(?!^[\s]*route-map\s)[\s\S])*)',
        re.IGNORECASE | re.MULTILINE
    )
    
    def _count_occurrences(self, patch_text: str) -> dict:
        """
        Count occurrences of critical fields in the patch text.
        
        This method counts how many times each critical field type appears
        in the configuration. Used for presence and uniqueness enforcement.
        
        Args:
            patch_text: The LLM-generated configuration snippet
        
        Returns:
            Dictionary with counts for each field type:
            - local_pref_count: Number of local-preference values found
            - local_pref_values: List of all local-preference values found
            - metric_count: Number of metric values found
            - metric_values: List of all metric values found
            - prefix_list_names: List of all prefix-list names found (definitions)
            - prefix_list_refs: List of all prefix-list names referenced (in match clauses)
            - route_map_names: List of all route-map names found (from definitions only)
        
        Requirements: 6.1, 6.2, 6.3, 6.4
        """
        counts: Dict[str, Any] = {
            "local_pref_count": 0,
            "local_pref_values": [],
            "metric_count": 0,
            "metric_values": [],
            "prefix_list_names": [],
            "prefix_list_refs": [],
            "route_map_names": [],
        }
        
        # Count all local-preference occurrences (Requirement 6.1)
        local_pref_matches = list(self.LOCAL_PREF_PATTERN.finditer(patch_text))
        counts["local_pref_count"] = len(local_pref_matches)
        counts["local_pref_values"] = [int(m.group(1)) for m in local_pref_matches]
        
        # Count all metric occurrences (Requirement 6.2)
        metric_matches = list(self.METRIC_PATTERN.finditer(patch_text))
        counts["metric_count"] = len(metric_matches)
        counts["metric_values"] = [int(m.group(1)) for m in metric_matches]
        
        # Get all prefix-list names from definitions (Requirement 6.3)
        counts["prefix_list_names"] = [
            m.group(1) for m in self.PREFIX_LIST_PATTERN.finditer(patch_text)
        ]
        
        # Get all prefix-list names from references (match ip address prefix-list)
        counts["prefix_list_refs"] = [
            m.group(1) for m in self.ROUTE_MAP_MATCH_PREFIX_LIST_PATTERN.finditer(patch_text)
        ]
        
        # Get all route-map names from definitions only (Requirement 6.4)
        counts["route_map_names"] = [
            m.group(1) for m in self.ROUTE_MAP_DEF_PATTERN.finditer(patch_text)
        ]
        
        # Get all route-map names from neighbor bindings (neighbor X route-map Y in|out)
        counts["route_map_refs"] = [
            m.group(1) for m in self.NEIGHBOR_ROUTE_MAP_PATTERN.finditer(patch_text)
        ]
        
        return counts

    def extract_parameters(self, patch_text: str) -> ExtractionResult:
        """
        Extract critical parameters from LLM-generated configuration.
        
        This method uses regex patterns to extract all critical parameters
        from the generated configuration text. These parameters will be
        verified against the Policy Layer's computed values.
        
        Accepts all local-preference alias forms (Requirements: 18.1, 18.2, 18.3):
        - set local-preference <value> (canonical)
        - set local-pref <value> (alias)
        - set local preference <value> (space variant)
        
        Args:
            patch_text: The LLM-generated configuration snippet
        
        Returns:
            ExtractionResult with all extracted parameters:
            - local_pref: First local-preference value found (or None)
            - local_pref_form: Form detected ("canonical", "alias", "space", or None)
            - metric: First metric value found (or None)
            - prefix_list_names: List of all prefix-list names found
            - route_map_names: List of all route-map names found
            - directions: Set of all directions (in/out) found
        
        Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 18.1, 18.2, 18.3
        """
        result = ExtractionResult()
        
        # Requirement 1.1, 18.1, 18.2, 18.3: Extract local-preference (any alias form)
        # Try canonical form first
        lp_match = self.LOCAL_PREF_CANONICAL_PATTERN.search(patch_text)
        if lp_match:
            result.local_pref = int(lp_match.group(1))
            result.local_pref_form = "canonical"
        else:
            # Try alias form
            lp_match = self.LOCAL_PREF_ALIAS_PATTERN.search(patch_text)
            if lp_match:
                result.local_pref = int(lp_match.group(1))
                result.local_pref_form = "alias"
            else:
                # Try space variant
                lp_match = self.LOCAL_PREF_SPACE_PATTERN.search(patch_text)
                if lp_match:
                    result.local_pref = int(lp_match.group(1))
                    result.local_pref_form = "space"
        
        # Requirement 1.2: Extract metric
        metric_match = self.METRIC_PATTERN.search(patch_text)
        if metric_match:
            result.metric = int(metric_match.group(1))
        
        # Requirement 1.3: Extract all prefix-list names (handle multiple occurrences)
        result.prefix_list_names = [
            m.group(1) for m in self.PREFIX_LIST_PATTERN.finditer(patch_text)
        ]
        
        # Requirement 1.4: Extract route-map names from definitions
        route_map_names = [
            m.group(1) for m in self.ROUTE_MAP_DEF_PATTERN.finditer(patch_text)
        ]
        
        # Requirement 1.4 & 1.5: Extract route-map names and directions from neighbor bindings
        directions = set()
        for m in self.NEIGHBOR_ROUTE_MAP_PATTERN.finditer(patch_text):
            route_map_names.append(m.group(1))
            directions.add(m.group(2).lower())
        
        result.route_map_names = route_map_names
        result.directions = directions
        
        return result

    def verify(
        self,
        patch_text: str,
        op_params: Dict[str, Any],
        operation_id: Optional[str] = None,
        device: Optional[str] = None,
    ) -> bool:
        """
        Verify that extracted parameters exactly match Policy Layer values.
        
        This method extracts critical parameters from the LLM-generated configuration
        and asserts they exactly match the Policy Layer's computed values. Any mismatch
        results in a SecurityViolationError being raised.
        
        Args:
            patch_text: The LLM-generated configuration snippet
            op_params: The operation parameters from PatchOperation (Policy Layer values)
            operation_id: Optional operation ID for logging and error reporting
            device: Optional device name for logging and error reporting
        
        Returns:
            True if all parameters match (verification passed)
        
        Raises:
            SecurityViolationError: If any parameter mismatches (NON-RECOVERABLE)
                The error contains expected vs actual values for audit purposes.
        
        Security Invariant: If this method returns True, the patch_text conforms to op_params.
        
        Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
        """
        extracted = self.extract_parameters(patch_text)
        expected: Dict[str, Any] = {}
        actual: Dict[str, Any] = {}
        violations: List[str] = []
        
        # Requirement 6.1-6.6: Check presence and uniqueness of critical fields
        counts = self._count_occurrences(patch_text)
        
        # Requirement 6.1: Check local-preference presence and uniqueness
        if "local_pref" in op_params:
            if counts["local_pref_count"] == 0:
                # Requirement 6.6: Missing field
                expected["local_pref"] = op_params["local_pref"]
                actual["local_pref"] = None
                violations.append(
                    f"missing required field: local-preference (expected {op_params['local_pref']})"
                )
            elif counts["local_pref_count"] > 1:
                # Requirement 6.5: Ambiguity - multiple occurrences
                expected["local_pref"] = op_params["local_pref"]
                actual["local_pref_values"] = counts["local_pref_values"]
                violations.append(
                    f"ambiguity: multiple local-preference values found: {counts['local_pref_values']}"
                )
        
        # Requirement 6.2: Check metric/cost presence and uniqueness
        metric_key = "metric" if "metric" in op_params else ("cost" if "cost" in op_params else None)
        if metric_key is not None:
            if counts["metric_count"] == 0:
                # Requirement 6.6: Missing field
                expected[metric_key] = op_params[metric_key]
                actual[metric_key] = None
                violations.append(
                    f"missing required field: metric (expected {op_params[metric_key]})"
                )
            elif counts["metric_count"] > 1:
                # Requirement 6.5: Ambiguity - multiple occurrences
                expected[metric_key] = op_params[metric_key]
                actual["metric_values"] = counts["metric_values"]
                violations.append(
                    f"ambiguity: multiple metric values found: {counts['metric_values']}"
                )
        
        # Requirement 6.3: Check prefix-list name presence and uniqueness
        # For route-map operations, check for prefix-list references (match ip address prefix-list)
        # For prefix-list operations, check for prefix-list definitions (ip prefix-list)
        if "prefix_list_name" in op_params:
            expected_pl_name = op_params["prefix_list_name"]
            # Check both definitions and references
            matching_pl_defs = [n for n in counts["prefix_list_names"] if n == expected_pl_name]
            matching_pl_refs = [n for n in counts["prefix_list_refs"] if n == expected_pl_name]
            matching_pl_names = matching_pl_defs + matching_pl_refs
            
            if len(matching_pl_names) == 0:
                # Requirement 6.6: Missing field
                expected["prefix_list_name"] = expected_pl_name
                actual["prefix_list_names"] = counts["prefix_list_names"]
                actual["prefix_list_refs"] = counts["prefix_list_refs"]
                violations.append(
                    f"missing required field: prefix-list '{expected_pl_name}'"
                )
            elif len(matching_pl_defs) > 1:
                # Requirement 6.5: Ambiguity - multiple definitions of same prefix-list
                expected["prefix_list_name"] = expected_pl_name
                actual["prefix_list_definitions"] = matching_pl_defs
                violations.append(
                    f"ambiguity: multiple prefix-list definitions found for '{expected_pl_name}': {len(matching_pl_defs)} occurrences"
                )
        
        # Requirement 6.4: Check route-map name presence and uniqueness
        # For neighbor binding operations, check for route-map references
        # For route-map operations, check for route-map definitions
        if "route_map_name" in op_params:
            expected_rm_name = op_params["route_map_name"]
            # Check both definitions and references
            matching_rm_defs = [n for n in counts["route_map_names"] if n == expected_rm_name]
            matching_rm_refs = [n for n in counts.get("route_map_refs", []) if n == expected_rm_name]
            matching_rm_names = matching_rm_defs + matching_rm_refs
            
            if len(matching_rm_names) == 0:
                # Requirement 6.6: Missing field
                expected["route_map_name"] = expected_rm_name
                actual["route_map_names"] = counts["route_map_names"]
                actual["route_map_refs"] = counts.get("route_map_refs", [])
                violations.append(
                    f"missing required field: route-map '{expected_rm_name}'"
                )
            elif len(matching_rm_defs) > 1:
                # Requirement 6.5: Ambiguity - multiple definitions of same route-map
                expected["route_map_name"] = expected_rm_name
                actual["route_map_definitions"] = matching_rm_defs
                violations.append(
                    f"ambiguity: multiple route-map definitions found for '{expected_rm_name}': {len(matching_rm_defs)} occurrences"
                )
        
        # If presence/uniqueness violations found, raise immediately
        if violations:
            security_logger.error(
                "FAIL: ConstraintGuard verification failed - %s",
                "; ".join(violations),
                extra={
                    "operation_id": operation_id,
                    "device": device,
                    "violations": violations,
                    "expected": expected,
                    "actual": actual,
                },
            )
            raise SecurityViolationError(
                message=f"Policy constraint violations: {'; '.join(violations)}",
                expected=expected,
                actual=actual,
                operation_id=operation_id,
                device=device,
            )
        
        # Requirement 7.1-7.4: Check for unexpected objects in patch
        unexpected_objects = self._check_no_extra_objects(patch_text, op_params)
        if unexpected_objects:
            # Requirement 7.3: Raise SecurityViolationError listing unexpected objects
            expected_names_set: Set[str] = set()
            if "prefix_list_name" in op_params:
                expected_names_set.add(op_params["prefix_list_name"])
            if "route_map_name" in op_params:
                expected_names_set.add(op_params["route_map_name"])
            if "expected_names" in op_params:
                expected_names_set.update(op_params["expected_names"])
            
            expected["expected_object_names"] = list(expected_names_set)
            actual["unexpected_objects"] = unexpected_objects
            
            # Requirement 7.4: List the unexpected object names for audit
            violation_msg = f"Unexpected objects in patch: {unexpected_objects}"
            violations.append(violation_msg)
            
            security_logger.error(
                "FAIL: ConstraintGuard verification failed - %s",
                violation_msg,
                extra={
                    "operation_id": operation_id,
                    "device": device,
                    "violations": violations,
                    "expected": expected,
                    "actual": actual,
                },
            )
            raise SecurityViolationError(
                message=f"Policy constraint violations: {violation_msg}",
                expected=expected,
                actual=actual,
                operation_id=operation_id,
                device=device,
            )
        
        # Requirement 8.1-8.4: Check prefix isolation for route-maps
        # Only check if enforce_prefix_isolation is True in op_params (default: True for BGP)
        enforce_isolation = op_params.get("enforce_prefix_isolation", True)
        if enforce_isolation:
            violating_route_maps = self._check_prefix_isolation(patch_text)
            if violating_route_maps:
                # Requirement 8.4: Raise SecurityViolationError with "prefix isolation" in message
                expected["prefix_isolation"] = "all route-maps must have match ip address prefix-list"
                actual["violating_route_maps"] = violating_route_maps
                
                violation_msg = (
                    f"prefix isolation violation: route-map(s) {violating_route_maps} "
                    f"do not have 'match ip address prefix-list' clause"
                )
                violations.append(violation_msg)
                
                security_logger.error(
                    "FAIL: ConstraintGuard verification failed - %s",
                    violation_msg,
                    extra={
                        "operation_id": operation_id,
                        "device": device,
                        "violations": violations,
                        "expected": expected,
                        "actual": actual,
                    },
                )
                raise SecurityViolationError(
                    message=f"Policy constraint violations: {violation_msg}",
                    expected=expected,
                    actual=actual,
                    operation_id=operation_id,
                    device=device,
                )
        
        # Reset for value matching checks
        expected = {}
        actual = {}
        violations = []
        
        # Requirement 2.1: Check local-preference
        if "local_pref" in op_params and extracted.local_pref is not None:
            expected["local_pref"] = op_params["local_pref"]
            actual["local_pref"] = extracted.local_pref
            if extracted.local_pref != op_params["local_pref"]:
                violations.append(
                    f"local-preference mismatch: expected {op_params['local_pref']}, "
                    f"got {extracted.local_pref}"
                )
        
        # Requirement 2.2: Check metric/cost
        # Support both "metric" and "cost" keys from op_params
        metric_key = "metric" if "metric" in op_params else ("cost" if "cost" in op_params else None)
        if metric_key is not None and extracted.metric is not None:
            expected[metric_key] = op_params[metric_key]
            actual[metric_key] = extracted.metric
            if extracted.metric != op_params[metric_key]:
                violations.append(
                    f"{metric_key} mismatch: expected {op_params[metric_key]}, "
                    f"got {extracted.metric}"
                )
        
        # Requirement 2.3: Check prefix-list name (if specified)
        if "prefix_list_name" in op_params and extracted.prefix_list_names:
            expected["prefix_list_name"] = op_params["prefix_list_name"]
            actual["prefix_list_names"] = extracted.prefix_list_names
            if op_params["prefix_list_name"] not in extracted.prefix_list_names:
                violations.append(
                    f"prefix-list name mismatch: expected {op_params['prefix_list_name']}, "
                    f"got {extracted.prefix_list_names}"
                )
        
        # Requirement 2.4: Check direction (if specified)
        if "direction" in op_params and extracted.directions:
            expected["direction"] = op_params["direction"]
            actual["directions"] = list(extracted.directions)
            if op_params["direction"].lower() not in extracted.directions:
                violations.append(
                    f"direction mismatch: expected {op_params['direction']}, "
                    f"got {extracted.directions}"
                )
        
        # Requirement 8.4: Use dedicated logger "pathdelta.security.guard"
        # Requirement 8.5: Include operation_id and device in all log entries
        
        # Requirement 2.5: Raise SecurityViolationError on any mismatch
        if violations:
            # Requirement 8.3: Log FAIL with mismatch details on failure
            security_logger.error(
                "FAIL: ConstraintGuard verification failed - %s",
                "; ".join(violations),
                extra={
                    "operation_id": operation_id,
                    "device": device,
                    "violations": violations,
                    "expected": expected,
                    "actual": actual,
                },
            )
            # Requirement 2.6: This is a hard failure - no retry allowed
            raise SecurityViolationError(
                message=f"Policy constraint violations: {'; '.join(violations)}",
                expected=expected,
                actual=actual,
                operation_id=operation_id,
                device=device,
            )
        
        # Requirement 8.1: Log extracted parameters
        # Requirement 8.2: Log PASS with parameter summary on success
        param_summary = self._build_param_summary(extracted)
        security_logger.info(
            "PASS: ConstraintGuard verification succeeded - %s",
            param_summary,
            extra={
                "operation_id": operation_id,
                "device": device,
                "extracted_params": {
                    "local_pref": extracted.local_pref,
                    "metric": extracted.metric,
                    "prefix_list_names": extracted.prefix_list_names,
                    "route_map_names": extracted.route_map_names,
                    "directions": list(extracted.directions),
                },
            },
        )
        return True
    
    def _check_no_extra_objects(
        self,
        patch_text: str,
        op_params: Dict[str, Any],
    ) -> List[str]:
        """
        Check for unexpected route-maps or prefix-lists in the patch.
        
        This method extracts all route-map and prefix-list names from the patch
        and compares them against the expected names from op_params. Any names
        found in the patch that are not in the expected set are considered
        unexpected and returned.
        
        Args:
            patch_text: The LLM-generated configuration snippet
            op_params: The operation parameters from PatchOperation (Policy Layer values)
        
        Returns:
            List of unexpected object names found in the patch. Empty list if
            all objects are expected.
        
        Requirements: 7.1, 7.2
        """
        unexpected_objects: List[str] = []
        
        # Build set of expected names from op_params
        expected_names: Set[str] = set()
        
        # Add expected prefix-list name if specified
        if "prefix_list_name" in op_params:
            expected_names.add(op_params["prefix_list_name"])
        
        # Add expected route-map name if specified
        if "route_map_name" in op_params:
            expected_names.add(op_params["route_map_name"])
        
        # Add any additional expected names from expected_names list
        if "expected_names" in op_params:
            for name in op_params["expected_names"]:
                expected_names.add(name)
        
        # Extract all prefix-list names from patch (Requirement 7.1)
        prefix_list_names = [
            m.group(1) for m in self.PREFIX_LIST_PATTERN.finditer(patch_text)
        ]
        
        # Extract all route-map names from definitions only (Requirement 7.1)
        route_map_names = [
            m.group(1) for m in self.ROUTE_MAP_DEF_PATTERN.finditer(patch_text)
        ]
        
        # Get unique names from patch
        patch_names: Set[str] = set(prefix_list_names) | set(route_map_names)
        
        # Find unexpected names (Requirement 7.2)
        # Only check if we have expected names - if no expectations, allow anything
        if expected_names:
            for name in patch_names:
                if name not in expected_names:
                    unexpected_objects.append(name)
        
        return unexpected_objects

    def _check_prefix_isolation(
        self,
        patch_text: str,
    ) -> List[str]:
        """
        Check that route-maps have match prefix-list clauses for prefix isolation.
        
        This method verifies that each route-map definition in the patch contains
        a `match ip address prefix-list` clause. Route-maps without this clause
        are considered to violate prefix isolation because they could match
        unintended traffic.
        
        Args:
            patch_text: The LLM-generated configuration snippet
        
        Returns:
            List of route-map names that violate prefix isolation (i.e., do not
            have a match ip address prefix-list clause). Empty list if all
            route-maps are properly isolated.
        
        Requirements: 8.1, 8.2, 8.3
        """
        violating_route_maps: List[str] = []
        
        # Find all route-map blocks in the patch
        # We need to identify each route-map and check if it has a match prefix-list clause
        
        # First, get all route-map names from definitions
        route_map_names = [
            m.group(1) for m in self.ROUTE_MAP_DEF_PATTERN.finditer(patch_text)
        ]
        
        # Get unique route-map names
        unique_route_maps = set(route_map_names)
        
        # For each route-map, check if there's a match prefix-list clause associated with it
        # We need to look at the content following each route-map definition
        
        # Split the patch into lines for easier processing
        lines = patch_text.split('\n')
        
        # Track which route-maps have match prefix-list clauses
        route_maps_with_match: Set[str] = set()
        
        current_route_map: Optional[str] = None
        
        for line in lines:
            # Check if this line starts a new route-map block
            rm_match = self.ROUTE_MAP_DEF_PATTERN.match(line)
            if rm_match:
                current_route_map = rm_match.group(1)
                continue
            
            # If we're inside a route-map block, check for match prefix-list
            if current_route_map is not None:
                # Check if this line has a match ip address prefix-list clause
                match_pl = self.ROUTE_MAP_MATCH_PREFIX_LIST_PATTERN.search(line)
                if match_pl:
                    route_maps_with_match.add(current_route_map)
                
                # Check if we've exited the route-map block (new route-map or end marker)
                # Route-map blocks typically end with "exit" or "!" or another route-map
                stripped = line.strip()
                if stripped == '!' or stripped.lower() == 'exit':
                    current_route_map = None
        
        # Find route-maps that don't have match prefix-list clauses (Requirement 8.2, 8.3)
        for rm_name in unique_route_maps:
            if rm_name not in route_maps_with_match:
                violating_route_maps.append(rm_name)
        
        return violating_route_maps

    def verify_with_classification(
        self,
        patch_text: str,
        op_params: Dict[str, Any],
        operation_id: Optional[str] = None,
        device: Optional[str] = None,
    ) -> VerificationResult:
        """
        Verify patch and classify any failure.
        
        Failure Classification:
        
        CONTRACT_FAIL (retryable):
        - Empty output
        - Missing markers in LLM response
        - Malformed block structure
        - Missing route-map header line (when route-map expected)
        - Missing required field (local-pref, route-map name, etc.) - ONLY if field is missing
        
        SECURITY_VIOLATION (non-retryable):
        - Prefix isolation match missing (route-map without match clause)
        - Local-pref value mismatch (wrong value, not missing)
        - Wrong direction (in vs out)
        - Wrong neighbor binding
        - Catch-all / permit any
        - Unexpected objects in patch
        
        Args:
            patch_text: The generated configuration snippet
            op_params: Expected parameters from Policy Layer
            operation_id: Optional operation ID for logging
            device: Optional device name for logging
            
        Returns:
            VerificationResult with ok, kind, and reason
        """
        # === CONTRACT_FAIL: Empty output ===
        if not patch_text or not patch_text.strip():
            return VerificationResult(
                ok=False,
                kind=FailureKind.CONTRACT_FAIL,
                reason="empty output",
            )
        
        # Extract parameters and count occurrences
        extracted = self.extract_parameters(patch_text)
        counts = self._count_occurrences(patch_text)
        
        # === CONTRACT_FAIL: Missing required fields ===
        # Check for missing local-preference (CONTRACT_FAIL - retryable)
        if "local_pref" in op_params and counts["local_pref_count"] == 0:
            return VerificationResult(
                ok=False,
                kind=FailureKind.CONTRACT_FAIL,
                reason=f"missing required field: local-preference (expected {op_params['local_pref']})",
                expected={"local_pref": op_params["local_pref"]},
                actual={"local_pref": None},
            )
        
        # Check for missing route-map name (CONTRACT_FAIL - retryable)
        if "route_map_name" in op_params:
            rm_name = op_params["route_map_name"]
            matching_rm_defs = [n for n in counts["route_map_names"] if n == rm_name]
            matching_rm_refs = [n for n in counts.get("route_map_refs", []) if n == rm_name]
            if not matching_rm_defs and not matching_rm_refs:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.CONTRACT_FAIL,
                    reason=f"missing required field: route-map '{rm_name}'",
                    expected={"route_map_name": rm_name},
                    actual={"route_map_names": counts["route_map_names"], "route_map_refs": counts.get("route_map_refs", [])},
                )
        
        # Check for missing prefix-list name (CONTRACT_FAIL - retryable)
        if "prefix_list_name" in op_params:
            pl_name = op_params["prefix_list_name"]
            matching_pl_defs = [n for n in counts["prefix_list_names"] if n == pl_name]
            matching_pl_refs = [n for n in counts["prefix_list_refs"] if n == pl_name]
            if not matching_pl_defs and not matching_pl_refs:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.CONTRACT_FAIL,
                    reason=f"missing required field: prefix-list '{pl_name}'",
                    expected={"prefix_list_name": pl_name},
                    actual={"prefix_list_names": counts["prefix_list_names"], "prefix_list_refs": counts["prefix_list_refs"]},
                )
        
        # === SECURITY_VIOLATION: Value mismatches ===
        # Check for local-preference value mismatch (SECURITY_VIOLATION - non-retryable)
        if "local_pref" in op_params and extracted.local_pref is not None:
            if extracted.local_pref != op_params["local_pref"]:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.SECURITY_VIOLATION,
                    reason=f"local-preference mismatch: expected {op_params['local_pref']}, got {extracted.local_pref}",
                    expected={"local_pref": op_params["local_pref"]},
                    actual={"local_pref": extracted.local_pref},
                )
        
        # Check for metric/cost value mismatch (SECURITY_VIOLATION - non-retryable)
        metric_key = "metric" if "metric" in op_params else ("cost" if "cost" in op_params else None)
        if metric_key is not None and extracted.metric is not None:
            if extracted.metric != op_params[metric_key]:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.SECURITY_VIOLATION,
                    reason=f"{metric_key} mismatch: expected {op_params[metric_key]}, got {extracted.metric}",
                    expected={metric_key: op_params[metric_key]},
                    actual={metric_key: extracted.metric},
                )
        
        # Check for direction mismatch (SECURITY_VIOLATION - non-retryable)
        if "direction" in op_params and extracted.directions:
            expected_dir = op_params["direction"].lower()
            if expected_dir not in extracted.directions:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.SECURITY_VIOLATION,
                    reason=f"direction mismatch: expected {op_params['direction']}, got {list(extracted.directions)}",
                    expected={"direction": op_params["direction"]},
                    actual={"directions": list(extracted.directions)},
                )
        
        # === SECURITY_VIOLATION: Unexpected objects ===
        unexpected_objects = self._check_no_extra_objects(patch_text, op_params)
        if unexpected_objects:
            return VerificationResult(
                ok=False,
                kind=FailureKind.SECURITY_VIOLATION,
                reason=f"unexpected objects in patch: {unexpected_objects}",
                expected={"expected_objects": self._get_expected_names(op_params)},
                actual={"unexpected_objects": unexpected_objects},
            )
        
        # === SECURITY_VIOLATION: Prefix isolation violation ===
        enforce_isolation = op_params.get("enforce_prefix_isolation", True)
        if enforce_isolation:
            violating_route_maps = self._check_prefix_isolation(patch_text)
            if violating_route_maps:
                return VerificationResult(
                    ok=False,
                    kind=FailureKind.SECURITY_VIOLATION,
                    reason=f"prefix isolation violation: route-map(s) {violating_route_maps} missing match clause",
                    expected={"prefix_isolation": "all route-maps must have match ip address prefix-list"},
                    actual={"violating_route_maps": violating_route_maps},
                )
        
        # All checks passed
        security_logger.info(
            "PASS: verify_with_classification succeeded",
            extra={
                "operation_id": operation_id,
                "device": device,
            },
        )
        return VerificationResult(
            ok=True,
            kind=FailureKind.PASS,
            reason="verification passed",
        )

    def _get_expected_names(self, op_params: Dict[str, Any]) -> List[str]:
        """Get list of expected object names from op_params."""
        names = []
        if "prefix_list_name" in op_params:
            names.append(op_params["prefix_list_name"])
        if "route_map_name" in op_params:
            names.append(op_params["route_map_name"])
        if "expected_names" in op_params:
            names.extend(op_params["expected_names"])
        return names

    def _build_param_summary(self, extracted: ExtractionResult) -> str:
        """
        Build a human-readable summary of extracted parameters for logging.
        
        Args:
            extracted: The ExtractionResult containing extracted parameters
        
        Returns:
            A string summary of the parameters
        
        Requirements: 8.1, 8.2
        """
        parts = []
        
        if extracted.local_pref is not None:
            parts.append(f"local_pref={extracted.local_pref}")
        
        if extracted.metric is not None:
            parts.append(f"metric={extracted.metric}")
        
        if extracted.prefix_list_names:
            parts.append(f"prefix_lists={extracted.prefix_list_names}")
        
        if extracted.route_map_names:
            parts.append(f"route_maps={extracted.route_map_names}")
        
        if extracted.directions:
            parts.append(f"directions={list(extracted.directions)}")
        
        return ", ".join(parts) if parts else "no parameters extracted"

    def verify_contract(
        self,
        patch_text: str,
        contract: PatchContract,
    ) -> tuple:
        """
        Verify patch text against contract with three-level classification.
        
        This method performs comprehensive verification of LLM-generated patch text
        against an immutable PatchContract. It enforces fail-closed semantics while
        supporting retry for most violations.
        
        Verification Order (fail-fast):
        1. Dangerous patterns (HARD_STOP - via DangerousCommandDetector)
        2. Scope whitelist (RETRYABLE) - Task B
        3. Object whitelist (RETRYABLE) - Task B
        4. Neighbor binding context (RETRYABLE) - Task C
        5. Empty output (RETRYABLE)
        6. Required slots: presence + uniqueness + equality (RETRYABLE) - Task A
        7. Forbidden patterns (RETRYABLE)
        8. Prefix isolation (RETRYABLE)
        
        Args:
            patch_text: The LLM-generated patch text to verify
            contract: The immutable PatchContract defining correctness criteria
        
        Returns:
            Tuple of (Severity, Optional[ContractDiff]):
            - (Severity.RETRYABLE, None): Verification passed
            - (Severity.RETRYABLE, ContractDiff): Retryable violations found
            - (Severity.HARD_STOP, ContractDiff): Dangerous commands detected
        
        Requirements: 7.2-7.9, 8.1-8.7, 9.4-9.5, 10.2-10.6, 11.3-11.10, 12.2, 12.4
        """
        diff = ContractDiff()
        
        # Step 1: Check for dangerous patterns FIRST (fail-fast) - HARD_STOP
        # Requirements: 12.2, 12.4
        dangerous_result = self._check_dangerous_commands(patch_text, contract)
        if dangerous_result is not None:
            diff.forbidden_found.append(dangerous_result)
            diff.severity = Severity.HARD_STOP
            security_logger.error(
                "HARD_STOP: Dangerous command detected - %s",
                dangerous_result,
                extra={
                    "op_id": contract.op_id,
                    "device": contract.device,
                    "dangerous_command": dangerous_result,
                },
            )
            return Severity.HARD_STOP, diff
        
        # Step 2: Check scope whitelist (Task B) - RETRYABLE
        # Requirements: 9.4, 9.5, 7.3
        if contract.scope_whitelist is not None:
            scope_violations = self._check_scope_whitelist(patch_text, contract.scope_whitelist)
            if scope_violations:
                diff.scope_violations = scope_violations
                diff.severity = Severity.RETRYABLE
                security_logger.warning(
                    "RETRYABLE: Scope whitelist violations - %s",
                    scope_violations,
                    extra={
                        "op_id": contract.op_id,
                        "device": contract.device,
                        "scope_violations": scope_violations,
                    },
                )
                return Severity.RETRYABLE, diff
        
        # Step 3: Check object whitelist (Task B) - RETRYABLE
        # Requirements: 10.2-10.6, 7.3
        if contract.object_whitelist is not None:
            object_violations = self._check_object_whitelist(patch_text, contract.object_whitelist)
            if object_violations:
                diff.object_violations = object_violations
                diff.severity = Severity.RETRYABLE
                security_logger.warning(
                    "RETRYABLE: Object whitelist violations - %s",
                    object_violations,
                    extra={
                        "op_id": contract.op_id,
                        "device": contract.device,
                        "object_violations": object_violations,
                    },
                )
                return Severity.RETRYABLE, diff
        
        # Step 4: Check neighbor binding context (Task C) - RETRYABLE
        # Requirements: 11.3-11.10, 7.3
        if contract.operation_type == "NEIGHBOR_BIND":
            context_violations = self._check_neighbor_binding_context(patch_text, contract)
            if context_violations:
                diff.neighbor_context_violations = context_violations
                diff.severity = Severity.RETRYABLE
                security_logger.warning(
                    "RETRYABLE: Neighbor binding context violations - %s",
                    context_violations,
                    extra={
                        "op_id": contract.op_id,
                        "device": contract.device,
                        "neighbor_context_violations": context_violations,
                    },
                )
                return Severity.RETRYABLE, diff
        
        # Step 5: Check for empty output - RETRYABLE
        if not patch_text or not patch_text.strip():
            diff.missing.append({
                "slot": "patch_content",
                "expected": "non-empty patch text",
            })
            diff.severity = Severity.RETRYABLE
            security_logger.warning(
                "RETRYABLE: Empty patch output",
                extra={
                    "op_id": contract.op_id,
                    "device": contract.device,
                },
            )
            return Severity.RETRYABLE, diff
        
        # Step 6: Check required slots: presence + uniqueness + equality (Task A) - RETRYABLE
        # Requirements: 8.1-8.7, 7.4
        slot_violations = self._check_required_slots(patch_text, contract.required_slots)
        if slot_violations:
            # Categorize slot violations
            for violation in slot_violations:
                category = violation.get("category")
                if category == "missing":
                    diff.missing.append(violation)
                elif category == "multi_match":
                    diff.multi_match.append(violation)
                elif category == "mismatch":
                    diff.mismatch.append(violation)
            
            diff.severity = Severity.RETRYABLE
            security_logger.warning(
                "RETRYABLE: Required slot violations - missing=%s, multi_match=%s, mismatch=%s",
                diff.missing,
                diff.multi_match,
                diff.mismatch,
                extra={
                    "op_id": contract.op_id,
                    "device": contract.device,
                    "missing": diff.missing,
                    "multi_match": diff.multi_match,
                    "mismatch": diff.mismatch,
                },
            )
            return Severity.RETRYABLE, diff
        
        # Step 7: Check forbidden patterns - RETRYABLE
        forbidden_violations = self._check_forbidden_patterns(patch_text, contract.forbidden_patterns)
        if forbidden_violations:
            diff.forbidden_found = forbidden_violations
            diff.severity = Severity.RETRYABLE
            security_logger.warning(
                "RETRYABLE: Forbidden patterns found - %s",
                forbidden_violations,
                extra={
                    "op_id": contract.op_id,
                    "device": contract.device,
                    "forbidden_found": forbidden_violations,
                },
            )
            return Severity.RETRYABLE, diff
        
        # Step 8: Check prefix isolation (if enforced) - RETRYABLE
        if contract.enforce_prefix_isolation:
            violating_route_maps = self._check_prefix_isolation(patch_text)
            if violating_route_maps:
                diff.prefix_isolation_missing = ", ".join(violating_route_maps)
                diff.severity = Severity.RETRYABLE
                security_logger.warning(
                    "RETRYABLE: Prefix isolation violation - route-maps %s missing match clause",
                    violating_route_maps,
                    extra={
                        "op_id": contract.op_id,
                        "device": contract.device,
                        "violating_route_maps": violating_route_maps,
                    },
                )
                return Severity.RETRYABLE, diff
        
        # All checks passed
        security_logger.info(
            "PASS: verify_contract succeeded for op_id=%s, device=%s",
            contract.op_id,
            contract.device,
            extra={
                "op_id": contract.op_id,
                "device": contract.device,
                "operation_type": contract.operation_type,
            },
        )
        return Severity.RETRYABLE, None  # RETRYABLE with None diff means PASS

    def _check_dangerous_commands(
        self,
        patch_text: str,
        contract: PatchContract,
    ) -> Optional[str]:
        """
        Check for dangerous commands using DangerousCommandDetector.
        
        This check is performed FIRST (fail-fast) and triggers HARD_STOP.
        
        Args:
            patch_text: The LLM-generated patch text to check
            contract: The PatchContract (for dangerous_patterns)
        
        Returns:
            None if no dangerous commands found
            String description of the dangerous command if found
        
        Requirements: 12.2, 12.4
        """
        if patch_text is None:
            return None
            
        # Use the global DangerousCommandDetector
        detector = DangerousCommandDetector()
        result = detector.detect(patch_text)
        
        if result is not None:
            description, matched_text, reason = result
            return f"{description}: '{matched_text}' - {reason}"
        
        # Also check contract's dangerous_patterns
        for dangerous in contract.dangerous_patterns:
            if re.search(dangerous.pattern, patch_text, re.IGNORECASE | re.MULTILINE):
                return f"{dangerous.description}: {dangerous.reason}"
        
        return None

    def _check_scope_whitelist(
        self,
        patch_text: str,
        scope_whitelist: ScopeWhitelist,
    ) -> List[Dict[str, Any]]:
        """
        Check patch text against scope whitelist.
        
        Args:
            patch_text: The LLM-generated patch text to check
            scope_whitelist: The ScopeWhitelist to enforce
        
        Returns:
            List of violation dictionaries, empty if no violations
        
        Requirements: 9.4, 9.5
        """
        return scope_whitelist.check_patch(patch_text)

    def _check_object_whitelist(
        self,
        patch_text: str,
        object_whitelist: ObjectWhitelist,
    ) -> List[Dict[str, Any]]:
        """
        Check patch text against object whitelist.
        
        Args:
            patch_text: The LLM-generated patch text to check
            object_whitelist: The ObjectWhitelist to enforce
        
        Returns:
            List of violation dictionaries, empty if no violations
        
        Requirements: 10.2-10.6
        """
        return object_whitelist.check_patch(patch_text)

    def _check_neighbor_binding_context(
        self,
        patch_text: str,
        contract: PatchContract,
    ) -> List[Dict[str, Any]]:
        """
        Check neighbor binding context for frr.conf style patches.
        
        Validates:
        - router bgp <asn> is OPTIONAL (patch-first minimalism)
        - If router bgp appears, ASN must match expected value (Requirement 11.6)
        - Only target neighbor's binding line appears (Requirement 11.7, 11.8)
        - Direction appears exactly once and matches expected (Requirement 11.9, 11.10)
        
        NOTE: router bgp context is NOT required for patch-first minimalism.
        Deterministic rendering outputs only the neighbor binding line.
        The router bgp context is assumed to already exist in the config.
        
        Args:
            patch_text: The LLM-generated patch text to check
            contract: The PatchContract with expected values
        
        Returns:
            List of violation dictionaries, empty if no violations
        
        Requirements: 11.3-11.10 (modified for patch-first minimalism)
        """
        violations: List[Dict[str, Any]] = []
        
        # Check router bgp context (OPTIONAL for patch-first minimalism)
        # If present, validate ASN matches
        router_bgp_pattern = re.compile(r'router\s+bgp\s+(\d+)', re.IGNORECASE)
        router_bgp_matches = router_bgp_pattern.findall(patch_text)
        
        # NOTE: router bgp is OPTIONAL - we don't require it for patch-first minimalism
        # Only validate if it appears
        if len(router_bgp_matches) > 1:
            # Requirement 11.5: router bgp appears more than once
            violations.append({
                "rule": "router_bgp_uniqueness",
                "detail": f"'router bgp' appears {len(router_bgp_matches)} times (expected at most 1)",
            })
        elif len(router_bgp_matches) == 1:
            # Check ASN matches (Requirement 11.6)
            found_asn = int(router_bgp_matches[0])
            if contract.expected_asn is not None and found_asn != contract.expected_asn:
                violations.append({
                    "rule": "asn_mismatch",
                    "detail": f"ASN mismatch: expected {contract.expected_asn}, found {found_asn}",
                })
        
        # Check neighbor bindings (Requirement 11.7, 11.8)
        neighbor_binding_pattern = re.compile(
            r'neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)',
            re.IGNORECASE
        )
        neighbor_bindings = neighbor_binding_pattern.findall(patch_text)
        
        if contract.expected_neighbor is not None:
            # Check for unauthorized neighbor bindings
            for neighbor_ip, rm_name, direction in neighbor_bindings:
                if neighbor_ip != contract.expected_neighbor:
                    # Requirement 11.8: another neighbor's binding line appears
                    violations.append({
                        "rule": "unauthorized_neighbor",
                        "detail": f"Unauthorized neighbor binding: '{neighbor_ip}' (expected only '{contract.expected_neighbor}')",
                    })
        
        # Check direction uniqueness and match (Requirement 11.9, 11.10)
        if contract.expected_direction is not None:
            directions_found = [binding[2].lower() for binding in neighbor_bindings]
            
            if len(directions_found) == 0:
                violations.append({
                    "rule": "direction_missing",
                    "detail": f"Direction is missing (expected '{contract.expected_direction}')",
                })
            elif len(directions_found) > 1:
                # Requirement 11.10: direction appears multiple times
                violations.append({
                    "rule": "direction_uniqueness",
                    "detail": f"Direction appears {len(directions_found)} times: {directions_found} (expected exactly 1)",
                })
            elif directions_found[0] != contract.expected_direction.lower():
                # Requirement 11.10: direction does not match
                violations.append({
                    "rule": "direction_mismatch",
                    "detail": f"Direction mismatch: expected '{contract.expected_direction}', found '{directions_found[0]}'",
                })
        
        return violations

    def _check_required_slots(
        self,
        patch_text: str,
        required_slots: List[RequiredSlot],
    ) -> List[Dict[str, Any]]:
        """
        Check all required slots for presence, uniqueness, and equality.
        
        Args:
            patch_text: The LLM-generated patch text to check
            required_slots: List of RequiredSlot objects to verify
        
        Returns:
            List of violation dictionaries with 'category' field indicating
            the type of violation (missing, multi_match, mismatch)
        
        Requirements: 8.1-8.7
        """
        violations: List[Dict[str, Any]] = []
        
        for slot in required_slots:
            passed, failure_category, details = slot.verify(patch_text)
            
            if not passed and details is not None:
                # Add category to details for proper categorization
                details["category"] = failure_category
                violations.append(details)
        
        return violations

    def _check_forbidden_patterns(
        self,
        patch_text: str,
        forbidden_patterns: List[ForbiddenPattern],
    ) -> List[str]:
        """
        Check for forbidden patterns in patch text.
        
        Args:
            patch_text: The LLM-generated patch text to check
            forbidden_patterns: List of ForbiddenPattern objects to check
        
        Returns:
            List of violation descriptions, empty if no violations
        """
        if patch_text is None:
            return []
            
        violations: List[str] = []
        
        for forbidden in forbidden_patterns:
            if re.search(forbidden.pattern, patch_text, re.IGNORECASE | re.MULTILINE):
                violations.append(f"{forbidden.description}: {forbidden.reason}")
        
        return violations
