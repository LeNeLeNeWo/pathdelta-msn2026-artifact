"""
Context Analyzer for Synthesis Layer

Analyzes existing device configurations to determine object reuse strategy.
Implements the "Minimization via Object Reuse" philosophy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import gcd
from functools import reduce
from statistics import median, mode
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    DeviceReuseContext,
    ObjectType,
    ReuseContext,
    ReuseStatus,
    ReuseStrategy,
    RouteMapHeadroom,
    StepAnalysis,
)


@dataclass
class NamingPattern:
    """
    Structured representation of naming conventions in network configs.
    
    Attributes:
        prefix: Common prefix for object names (e.g., "RM_", "route-map-")
        separator: Character used to separate name components ("_" or "-")
        casing: Case convention ("upper", "lower", or "mixed")
    """
    prefix: str
    separator: str
    casing: str  # "upper", "lower", "mixed"
    
    def to_prompt_string(self) -> str:
        """Format for LLM prompt injection."""
        return f"Enforce naming convention: Prefix='{self.prefix}', Separator='{self.separator}', Case='{self.casing.upper()}'"


# Module-level regex patterns for name extraction
_ROUTE_MAP_NAME_PATTERN = re.compile(
    r'^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)',
    re.MULTILINE
)
_PREFIX_LIST_NAME_PATTERN = re.compile(
    r'^ip\s+prefix-list\s+(\S+)\s+seq\s+(\d+)',
    re.MULTILINE
)


def extract_route_map_names(config_text: str) -> List[str]:
    """
    Extract route-map names from configuration text.
    
    Args:
        config_text: Raw FRR/Cisco configuration text
    
    Returns:
        Deduplicated list of route-map names
    
    Requirements: 5.1
    """
    names: Set[str] = set()
    for match in _ROUTE_MAP_NAME_PATTERN.finditer(config_text):
        names.add(match.group(1))
    return list(names)


def extract_prefix_list_names(config_text: str) -> List[str]:
    """
    Extract prefix-list names from configuration text.
    
    Args:
        config_text: Raw FRR/Cisco configuration text
    
    Returns:
        Deduplicated list of prefix-list names
    
    Requirements: 5.2
    """
    names: Set[str] = set()
    for match in _PREFIX_LIST_NAME_PATTERN.finditer(config_text):
        names.add(match.group(1))
    return list(names)


def extract_config_names(config_text: str) -> List[str]:
    """
    Extract route-map and prefix-list names from configuration text.
    
    Uses existing ROUTE_MAP_PATTERN and PREFIX_LIST_PATTERN regexes to
    extract all object names from the configuration.
    
    Args:
        config_text: Raw FRR/Cisco configuration text
    
    Returns:
        Deduplicated list of all route-map and prefix-list names
    
    Requirements: 1.2
    """
    names: Set[str] = set()
    
    # Extract route-map names
    for match in _ROUTE_MAP_NAME_PATTERN.finditer(config_text):
        names.add(match.group(1))
    
    # Extract prefix-list names
    for match in _PREFIX_LIST_NAME_PATTERN.finditer(config_text):
        names.add(match.group(1))
    
    return list(names)


def _longest_common_prefix(strings: List[str]) -> str:
    """
    Find the longest common prefix among a list of strings.
    
    Args:
        strings: List of strings to analyze
    
    Returns:
        The longest common prefix, or empty string if none
    """
    if not strings:
        return ""
    
    # Start with the first string as the prefix candidate
    prefix = strings[0]
    
    for s in strings[1:]:
        # Reduce prefix until it matches the start of s
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    
    return prefix


def _truncate_prefix_at_separator(prefix: str) -> str:
    """
    Truncate prefix at the last separator character.
    
    This ensures the prefix ends at a logical boundary (after a separator).
    
    Args:
        prefix: The raw common prefix
    
    Returns:
        Prefix truncated at the last separator, or empty if no separator
    """
    # Find the last occurrence of either separator
    last_underscore = prefix.rfind("_")
    last_hyphen = prefix.rfind("-")
    
    # Use the later separator position
    last_sep = max(last_underscore, last_hyphen)
    
    if last_sep == -1:
        # No separator found, return empty prefix
        return ""
    
    # Include the separator in the prefix
    return prefix[:last_sep + 1]


def _detect_separator(names: List[str]) -> str:
    """
    Detect the dominant separator character in names.
    
    Counts occurrences of "_" vs "-" across all names and returns
    the more common one.
    
    Args:
        names: List of object names
    
    Returns:
        "_" or "-" based on which is more common
    """
    underscore_count = sum(name.count("_") for name in names)
    hyphen_count = sum(name.count("-") for name in names)
    
    return "_" if underscore_count >= hyphen_count else "-"


def _detect_casing(names: List[str]) -> str:
    """
    Detect the casing convention in names.
    
    Analyzes the distribution of uppercase vs lowercase letters.
    Uses an 80% threshold to determine the convention.
    
    Args:
        names: List of object names
    
    Returns:
        "upper", "lower", or "mixed"
    """
    # Concatenate all names and filter to letters only
    all_letters = "".join(c for name in names for c in name if c.isalpha())
    
    if not all_letters:
        return "upper"  # Default when no letters
    
    upper_count = sum(1 for c in all_letters if c.isupper())
    lower_count = sum(1 for c in all_letters if c.islower())
    total = upper_count + lower_count
    
    if total == 0:
        return "upper"  # Default
    
    upper_ratio = upper_count / total
    lower_ratio = lower_count / total
    
    if upper_ratio > 0.8:
        return "upper"
    elif lower_ratio > 0.8:
        return "lower"
    else:
        return "mixed"


def infer_naming_pattern(config_text: str) -> NamingPattern:
    """
    Analyze existing configuration to infer naming conventions.
    
    Algorithm:
    1. Extract all route-map and prefix-list names using regex
    2. If < 2 names found, return default pattern
    3. Detect common prefix via longest common prefix algorithm
    4. Detect separator by counting occurrences of "_" vs "-"
    5. Detect casing by analyzing character distribution
    
    Args:
        config_text: Raw FRR/Cisco configuration text
    
    Returns:
        NamingPattern with inferred conventions
    
    Requirements: 1.3, 1.4, 1.5, 1.6
    """
    # Extract names from config
    names = extract_config_names(config_text)
    
    # Edge case: fewer than 2 names, return default pattern
    if len(names) < 2:
        return NamingPattern(prefix="", separator="_", casing="upper")
    
    # Detect common prefix
    raw_prefix = _longest_common_prefix(names)
    prefix = _truncate_prefix_at_separator(raw_prefix)
    
    # Detect separator
    separator = _detect_separator(names)
    
    # Detect casing
    casing = _detect_casing(names)
    
    return NamingPattern(prefix=prefix, separator=separator, casing=casing)


def _infer_pattern_from_names(names: List[str]) -> NamingPattern:
    """
    Infer naming pattern from a list of names.
    
    Helper function that performs the actual pattern inference logic.
    
    Args:
        names: List of object names to analyze
    
    Returns:
        NamingPattern with inferred conventions
    """
    # Edge case: fewer than 2 names, return default pattern
    if len(names) < 2:
        return NamingPattern(prefix="", separator="_", casing="upper")
    
    # Detect common prefix
    raw_prefix = _longest_common_prefix(names)
    prefix = _truncate_prefix_at_separator(raw_prefix)
    
    # Detect separator
    separator = _detect_separator(names)
    
    # Detect casing
    casing = _detect_casing(names)
    
    return NamingPattern(prefix=prefix, separator=separator, casing=casing)


@dataclass
class NamingPatternResult:
    """
    Per-object-type naming pattern inference result.
    
    Attributes:
        route_map: NamingPattern for route-map names
        prefix_list: NamingPattern for prefix-list names
        inferred_from_count: Number of names analyzed
    
    Requirements: 5.5
    """
    route_map: NamingPattern
    prefix_list: NamingPattern
    inferred_from_count: int
    
    def to_json(self) -> Dict[str, Any]:
        """
        Export as JSON for reproducibility artifact.
        
        Returns:
            Dictionary representation suitable for JSON serialization
        
        Requirements: 5.5
        """
        return {
            "route_map": {
                "prefix": self.route_map.prefix,
                "separator": self.route_map.separator,
                "casing": self.route_map.casing,
            },
            "prefix_list": {
                "prefix": self.prefix_list.prefix,
                "separator": self.prefix_list.separator,
                "casing": self.prefix_list.casing,
            },
            "inferred_from_count": self.inferred_from_count,
        }


def infer_naming_pattern_by_type(
    config_text: str,
    object_type: str = "all",
) -> NamingPatternResult:
    """
    Analyze existing configuration to infer naming conventions per object type.
    
    This function provides per-object-type naming inference, allowing different
    naming conventions for route-maps vs prefix-lists. This is important because
    brownfield networks often use different naming conventions for different
    object types (e.g., "RM_" prefix for route-maps, "PL_" for prefix-lists).
    
    Algorithm:
    1. Extract route-map names and prefix-list names separately
    2. Infer naming pattern for each object type independently
    3. Return NamingPatternResult with both patterns
    
    Args:
        config_text: Raw FRR/Cisco configuration text
        object_type: "route_map", "prefix_list", or "all" (default)
    
    Returns:
        NamingPatternResult with inferred conventions for the specified object type(s)
    
    Requirements: 5.1, 5.2, 5.3
    """
    # Extract names by type
    route_map_names = extract_route_map_names(config_text)
    prefix_list_names = extract_prefix_list_names(config_text)
    
    # Infer patterns for each type
    route_map_pattern = _infer_pattern_from_names(route_map_names)
    prefix_list_pattern = _infer_pattern_from_names(prefix_list_names)
    
    # Calculate total names analyzed
    total_count = len(route_map_names) + len(prefix_list_names)
    
    return NamingPatternResult(
        route_map=route_map_pattern,
        prefix_list=prefix_list_pattern,
        inferred_from_count=total_count,
    )


class ContextAnalyzer:
    """
    Analyzes existing configurations to determine reuse opportunities.
    
    Core Philosophy: Identify and append to existing objects instead of
    creating new ones whenever possible, minimizing configuration churn.
    """
    
    # Regex patterns for parsing FRR/Cisco-style configs
    ROUTE_MAP_PATTERN = re.compile(
        r'^route-map\s+(\S+)\s+(permit|deny)\s+(\d+)',
        re.MULTILINE
    )
    PREFIX_LIST_PATTERN = re.compile(
        r'^ip\s+prefix-list\s+(\S+)\s+seq\s+(\d+)',
        re.MULTILINE
    )
    NEIGHBOR_ROUTE_MAP_PATTERN = re.compile(
        r'neighbor\s+(\S+)\s+route-map\s+(\S+)\s+(in|out)',
        re.MULTILINE
    )
    INTERFACE_PATTERN = re.compile(
        r'^interface\s+(\S+)',
        re.MULTILINE
    )
    OSPF_COST_PATTERN = re.compile(
        r'ip\s+ospf\s+cost\s+(\d+)',
        re.MULTILINE
    )
    
    # Pattern for extracting local-pref values from route-map set statements
    LOCAL_PREF_PATTERN = re.compile(
        r'set\s+local-preference\s+(\d+)',
        re.MULTILINE
    )
    
    def __init__(self, sequence_increment: int = 10):
        """
        Initialize the context analyzer.
        
        Args:
            sequence_increment: Increment for new sequence numbers (default 10)
        """
        self.sequence_increment = sequence_increment
        self.style_snippets: List[str] = []
    
    def slice_config(self, config_text: str) -> List[str]:
        """
        Split configuration into meaningful blocks for style extraction.
        
        Args:
            config_text: Raw configuration text (FRR/Cisco format)
        
        Returns:
            List of configuration chunks (style snippets)
        
        Slicing Strategy:
            1. Primary: Split by "!" delimiter (Cisco/FRR convention)
            2. Filter: Remove empty/whitespace-only chunks
        """
        # Split by "!" delimiter
        chunks = config_text.split("!")
        
        # Strip whitespace from each chunk and filter out empty chunks
        chunks = [c.strip() for c in chunks if c.strip()]
        
        # Store for retrieval
        self.style_snippets = chunks
        
        return chunks
    
    def detect_parameter_step(
        self,
        values: List[int],
        min_step: int = 5,
        max_step: int = 100,
        default_step: int = 10,
    ) -> StepAnalysis:
        """
        Detect the step/granularity in a list of parameter values.
        
        Algorithm:
        1. Sort and deduplicate values
        2. Calculate differences between consecutive values
        3. Compute GCD of all differences
        4. Clamp result to [min_step, max_step]
        5. Default to default_step if < 2 values or chaotic
        
        Args:
            values: List of parameter values to analyze
            min_step: Minimum allowed step (default 5)
            max_step: Maximum allowed step (default 100)
            default_step: Default step when pattern cannot be detected (default 10)
        
        Returns:
            StepAnalysis with detected_step, existing_values, baseline_value, is_chaotic
        
        Requirements: 1.1, 1.2, 1.3, 1.4, 1.5
        """
        # Sort and deduplicate
        sorted_values = sorted(set(values))
        
        # Handle edge case: fewer than 2 distinct values
        if len(sorted_values) < 2:
            baseline = sorted_values[0] if sorted_values else 100
            return StepAnalysis(
                detected_step=default_step,
                existing_values=sorted_values,
                baseline_value=baseline,
                is_chaotic=False,
            )
        
        # Calculate differences between consecutive values
        differences = [
            sorted_values[i + 1] - sorted_values[i]
            for i in range(len(sorted_values) - 1)
        ]
        
        # Filter out zero differences (shouldn't happen after dedup, but be safe)
        differences = [d for d in differences if d > 0]
        
        if not differences:
            # All values were the same after dedup
            return StepAnalysis(
                detected_step=default_step,
                existing_values=sorted_values,
                baseline_value=sorted_values[0],
                is_chaotic=False,
            )
        
        # Compute GCD of all differences
        detected_gcd = reduce(gcd, differences)
        
        # Determine if pattern is chaotic (GCD < min_step)
        is_chaotic = detected_gcd < min_step
        
        # Clamp to [min_step, max_step]
        if detected_gcd < min_step:
            # Chaotic pattern fallback
            detected_step = default_step
        elif detected_gcd > max_step:
            # Cap at max_step to prevent excessive gaps
            detected_step = max_step
        else:
            detected_step = detected_gcd
        
        # Calculate baseline value (use median for robustness)
        baseline_value = int(median(sorted_values))
        
        return StepAnalysis(
            detected_step=detected_step,
            existing_values=sorted_values,
            baseline_value=baseline_value,
            is_chaotic=is_chaotic,
        )
    
    def analyze_local_pref_step(self, config_text: str) -> StepAnalysis:
        """
        Extract local-pref values from config text and detect step pattern.
        
        Parses 'set local-preference <value>' statements from route-map
        configurations and analyzes the step/granularity pattern.
        
        Args:
            config_text: Raw configuration text (FRR/Cisco format)
        
        Returns:
            StepAnalysis with detected step pattern for local-pref values
        
        Requirements: 1.6
        """
        # Extract all local-pref values using regex
        values = [
            int(match.group(1))
            for match in self.LOCAL_PREF_PATTERN.finditer(config_text)
        ]
        
        return self.detect_parameter_step(values)
    
    def analyze_ospf_cost_step(self, config_text: str) -> StepAnalysis:
        """
        Extract OSPF cost values from config text and detect step pattern.
        
        Parses 'ip ospf cost <value>' statements from interface
        configurations and analyzes the step/granularity pattern.
        
        Args:
            config_text: Raw configuration text (FRR/Cisco format)
        
        Returns:
            StepAnalysis with detected step pattern for OSPF cost values
        
        Requirements: 1.6
        """
        # Extract all OSPF cost values using regex
        values = [
            int(match.group(1))
            for match in self.OSPF_COST_PATTERN.finditer(config_text)
        ]
        
        return self.detect_parameter_step(values)
    
    def analyze_route_map_headroom(
        self,
        config_text: str,
        route_map_name: str,
    ) -> RouteMapHeadroom:
        """
        Analyze available sequence number headroom for prepending in a route-map.
        
        Parses route-map entries and extracts sequence numbers to determine:
        - Minimum sequence number (for prepend calculation)
        - Available headroom (min_sequence - 1)
        - Catch-all patterns at sequence 10 (risk indicator)
        
        Args:
            config_text: Raw configuration text (FRR/Cisco format)
            route_map_name: Name of the route-map to analyze
        
        Returns:
            RouteMapHeadroom with min_sequence, headroom, and catch-all risk info
        
        Requirements: 4.1, 4.2, 4.4, 4.5
        """
        # Extract all route-map entries for the specified route-map
        existing_sequences: List[int] = []
        has_catchall_risk = False
        
        # Pattern to match route-map entries with their content
        # We need to capture the full entry to detect catch-all patterns
        route_map_entry_pattern = re.compile(
            rf'^route-map\s+{re.escape(route_map_name)}\s+(permit|deny)\s+(\d+)',
            re.MULTILINE
        )
        
        for match in route_map_entry_pattern.finditer(config_text):
            action = match.group(1)  # permit or deny
            seq = int(match.group(2))
            existing_sequences.append(seq)
            
            # Check for catch-all pattern at sequence 10
            # A catch-all is typically "permit" with no match clauses
            if seq == 10 and action == "permit":
                # Look for the content of this route-map entry
                # Find the position of this match and look for match clauses
                entry_start = match.end()
                
                # Find the next route-map entry or end of config section
                next_entry = route_map_entry_pattern.search(config_text, entry_start)
                if next_entry:
                    entry_content = config_text[entry_start:next_entry.start()]
                else:
                    # Look for the next "!" delimiter or end of config
                    next_delimiter = config_text.find("!", entry_start)
                    if next_delimiter != -1:
                        entry_content = config_text[entry_start:next_delimiter]
                    else:
                        entry_content = config_text[entry_start:]
                
                # Check if there are any "match" clauses in the entry content
                # If no match clauses, it's a catch-all (matches everything)
                if not re.search(r'\bmatch\b', entry_content, re.IGNORECASE):
                    has_catchall_risk = True
        
        # Sort sequences
        existing_sequences.sort()
        
        # Calculate min_sequence, max_sequence, and headroom
        if not existing_sequences:
            # No existing entries - unlimited headroom
            return RouteMapHeadroom(
                route_map_name=route_map_name,
                min_sequence=None,
                max_sequence=None,
                headroom=999999,  # Effectively unlimited
                has_catchall_risk=False,
                existing_sequences=[],
            )
        
        min_sequence = min(existing_sequences)
        max_sequence = max(existing_sequences)
        headroom = min_sequence - 1
        
        return RouteMapHeadroom(
            route_map_name=route_map_name,
            min_sequence=min_sequence,
            max_sequence=max_sequence,
            headroom=headroom,
            has_catchall_risk=has_catchall_risk,
            existing_sequences=existing_sequences,
        )
    
    def _is_catchall_prefix_list(self, config_text: str, prefix_list_name: str) -> bool:
        """
        Check if a prefix-list is effectively a catch-all.
        
        Detects:
        - permit 0.0.0.0/0
        - permit 0.0.0.0/0 le 32
        - permit 0.0.0.0/0 ge 0 le 32
        - permit any
        
        Args:
            config_text: Full configuration text containing prefix-list definitions
            prefix_list_name: Name of the prefix-list to check
        
        Returns:
            True if prefix-list is a catch-all, False otherwise
        
        Requirements: 4.3, 4.4
        """
        # Pattern to match prefix-list entries for the specified name
        # Format: ip prefix-list <name> seq <num> <action> <prefix> [le <n>] [ge <n>]
        prefix_list_entry_pattern = re.compile(
            rf'^ip\s+prefix-list\s+{re.escape(prefix_list_name)}\s+seq\s+\d+\s+(permit|deny)\s+(.+)$',
            re.MULTILINE
        )
        
        for match in prefix_list_entry_pattern.finditer(config_text):
            action = match.group(1)
            prefix_spec = match.group(2).strip()
            
            # Only check permit entries (deny entries don't make it a catch-all)
            if action != "permit":
                continue
            
            # Check for "any" keyword
            if prefix_spec.lower() == "any":
                return True
            
            # Check for 0.0.0.0/0 variants
            # Patterns: "0.0.0.0/0", "0.0.0.0/0 le 32", "0.0.0.0/0 ge 0", "0.0.0.0/0 ge 0 le 32"
            catchall_pattern = re.compile(
                r'^0\.0\.0\.0/0(?:\s+(?:le\s+32|ge\s+0|le\s+32\s+ge\s+0|ge\s+0\s+le\s+32))?$',
                re.IGNORECASE
            )
            if catchall_pattern.match(prefix_spec):
                return True
            
            # Also check for ::/0 (IPv6 catch-all)
            ipv6_catchall_pattern = re.compile(
                r'^::/0(?:\s+(?:le\s+128|ge\s+0|le\s+128\s+ge\s+0|ge\s+0\s+le\s+128))?$',
                re.IGNORECASE
            )
            if ipv6_catchall_pattern.match(prefix_spec):
                return True
        
        return False
    
    def extract_top_entry_info(
        self,
        config_text: str,
        route_map_name: str,
    ) -> Optional["TopEntryInfo"]:
        """
        Extract information about the top (lowest sequence) route-map entry.
        
        Used for active shadowing prevention to detect unsafe PREPEND scenarios.
        A top entry is considered unsafe if it has a deny action, has no match
        clauses (catch-all/match-any), or matches a catch-all prefix-list.
        
        Args:
            config_text: Raw configuration text (FRR/Cisco format)
            route_map_name: Name of the route-map to analyze
        
        Returns:
            TopEntryInfo with sequence, action, has_match_clauses, and is_catchall_prefix_list,
            or None if no entries
        
        Requirements: 2.1, 2.2, 2.3, 4.3, 4.4
        """
        from .models import TopEntryInfo
        
        # Pattern to match route-map entries
        route_map_entry_pattern = re.compile(
            rf'^route-map\s+{re.escape(route_map_name)}\s+(permit|deny)\s+(\d+)',
            re.MULTILINE
        )
        
        # Collect all entries with their info
        entries: List[Tuple[int, str, int, int]] = []  # (seq, action, start, end)
        
        matches = list(route_map_entry_pattern.finditer(config_text))
        if not matches:
            return None
        
        for i, match in enumerate(matches):
            action = match.group(1)  # permit or deny
            seq = int(match.group(2))
            entry_start = match.end()
            
            # Find the end of this entry's content
            if i + 1 < len(matches):
                # Next entry starts at the next match
                entry_end = matches[i + 1].start()
            else:
                # Look for the next "!" delimiter or end of config
                next_delimiter = config_text.find("!", entry_start)
                if next_delimiter != -1:
                    entry_end = next_delimiter
                else:
                    entry_end = len(config_text)
            
            entries.append((seq, action, entry_start, entry_end))
        
        if not entries:
            return None
        
        # Find the top entry (lowest sequence number)
        entries.sort(key=lambda x: x[0])
        top_seq, top_action, top_start, top_end = entries[0]
        
        # Extract the content of the top entry
        entry_content = config_text[top_start:top_end]
        
        # Check if there are any "match" clauses in the entry content
        has_match_clauses = bool(re.search(r'\bmatch\b', entry_content, re.IGNORECASE))
        
        # Check if the entry matches a catch-all prefix-list
        # Requirements: 4.3, 4.4
        is_catchall_prefix_list = False
        
        # Look for "match ip address prefix-list <name>" in the entry content
        prefix_list_match_pattern = re.compile(
            r'match\s+ip\s+address\s+prefix-list\s+(\S+)',
            re.IGNORECASE
        )
        prefix_list_match = prefix_list_match_pattern.search(entry_content)
        
        if prefix_list_match:
            prefix_list_name = prefix_list_match.group(1)
            # Check if this prefix-list is a catch-all
            is_catchall_prefix_list = self._is_catchall_prefix_list(config_text, prefix_list_name)
        
        return TopEntryInfo(
            sequence=top_seq,
            action=top_action,
            has_match_clauses=has_match_clauses,
            is_catchall_prefix_list=is_catchall_prefix_list,
        )
    
    def analyze_device(
        self,
        device_name: str,
        config_text: str,
        relevant_neighbors: Optional[List[str]] = None,
        relevant_prefixes: Optional[List[str]] = None,
        relevant_interfaces: Optional[List[str]] = None,
    ) -> DeviceReuseContext:
        """
        Analyze a device's configuration for reuse opportunities.
        
        Args:
            device_name: Name of the device
            config_text: Raw configuration text (FRR or Cisco format)
            relevant_neighbors: BGP neighbors relevant to the intent
            relevant_prefixes: Prefixes relevant to the intent
            relevant_interfaces: Interfaces relevant to the intent (for OSPF)
        
        Returns:
            DeviceReuseContext with reuse decisions for all relevant objects
        """
        context = DeviceReuseContext(device_name=device_name)
        
        # Analyze route-maps for relevant neighbors
        if relevant_neighbors:
            neighbor_route_maps = self._extract_neighbor_route_maps(config_text)
            existing_route_maps = self._extract_route_maps(config_text)
            
            for neighbor in relevant_neighbors:
                context.route_maps[neighbor] = self._analyze_route_map_for_neighbor(
                    neighbor,
                    neighbor_route_maps,
                    existing_route_maps,
                    device_name,
                )
        
        # Analyze prefix-lists for relevant prefixes
        if relevant_prefixes:
            existing_prefix_lists = self._extract_prefix_lists(config_text)
            
            for prefix in relevant_prefixes:
                context.prefix_lists[prefix] = self._analyze_prefix_list(
                    prefix,
                    existing_prefix_lists,
                    device_name,
                )
        
        # Analyze interfaces for OSPF cost changes
        if relevant_interfaces:
            existing_interfaces = self._extract_interfaces(config_text)
            
            for interface in relevant_interfaces:
                context.interfaces[interface] = self._analyze_interface(
                    interface,
                    existing_interfaces,
                    config_text,
                )
        
        return context
    
    def _extract_route_maps(self, config_text: str) -> Dict[str, List[int]]:
        """
        Extract all route-maps and their sequence numbers.
        
        Returns:
            Dict mapping route-map name to list of sequence numbers
        """
        route_maps: Dict[str, List[int]] = {}
        
        for match in self.ROUTE_MAP_PATTERN.finditer(config_text):
            name = match.group(1)
            seq = int(match.group(3))
            
            if name not in route_maps:
                route_maps[name] = []
            route_maps[name].append(seq)
        
        # Sort sequences for each route-map
        for name in route_maps:
            route_maps[name].sort()
        
        return route_maps
    
    def _extract_neighbor_route_maps(
        self, config_text: str
    ) -> Dict[str, Dict[str, str]]:
        """
        Extract neighbor -> route-map mappings.
        
        Returns:
            Dict mapping neighbor IP to {"in": route_map_name, "out": route_map_name}
        """
        neighbor_maps: Dict[str, Dict[str, str]] = {}
        
        for match in self.NEIGHBOR_ROUTE_MAP_PATTERN.finditer(config_text):
            neighbor = match.group(1)
            route_map = match.group(2)
            direction = match.group(3)
            
            if neighbor not in neighbor_maps:
                neighbor_maps[neighbor] = {}
            neighbor_maps[neighbor][direction] = route_map
        
        return neighbor_maps
    
    def _extract_prefix_lists(self, config_text: str) -> Dict[str, List[int]]:
        """
        Extract all prefix-lists and their sequence numbers.
        
        Returns:
            Dict mapping prefix-list name to list of sequence numbers
        """
        prefix_lists: Dict[str, List[int]] = {}
        
        for match in self.PREFIX_LIST_PATTERN.finditer(config_text):
            name = match.group(1)
            seq = int(match.group(2))
            
            if name not in prefix_lists:
                prefix_lists[name] = []
            prefix_lists[name].append(seq)
        
        for name in prefix_lists:
            prefix_lists[name].sort()
        
        return prefix_lists
    
    def _extract_interfaces(self, config_text: str) -> Set[str]:
        """Extract all interface names from config."""
        interfaces: Set[str] = set()
        
        for match in self.INTERFACE_PATTERN.finditer(config_text):
            interfaces.add(match.group(1))
        
        return interfaces
    
    def _analyze_route_map_for_neighbor(
        self,
        neighbor: str,
        neighbor_route_maps: Dict[str, Dict[str, str]],
        existing_route_maps: Dict[str, List[int]],
        device_name: str,
    ) -> ReuseContext:
        """
        Determine reuse strategy for a route-map targeting a specific neighbor.
        
        Logic:
        1. If neighbor already has a route-map applied -> APPEND to it
        2. If no route-map exists -> CREATE new one
        """
        # Check if neighbor has an existing inbound route-map
        if neighbor in neighbor_route_maps and "in" in neighbor_route_maps[neighbor]:
            existing_name = neighbor_route_maps[neighbor]["in"]
            existing_seqs = existing_route_maps.get(existing_name, [])
            next_seq = self._calculate_next_sequence(existing_seqs)
            
            return ReuseContext(
                object_type=ObjectType.ROUTE_MAP,
                status=ReuseStatus.EXISTS,
                strategy=ReuseStrategy.APPEND,
                target_name=existing_name,
                existing_sequences=existing_seqs,
                next_sequence=next_seq,
                metadata={"neighbor": neighbor, "direction": "in"},
            )
        
        # No existing route-map, need to create
        proposed_name = self._generate_route_map_name(device_name, neighbor)
        
        return ReuseContext(
            object_type=ObjectType.ROUTE_MAP,
            status=ReuseStatus.MISSING,
            strategy=ReuseStrategy.CREATE,
            target_name=proposed_name,
            existing_sequences=[],
            next_sequence=10,
            metadata={"neighbor": neighbor, "direction": "in"},
        )
    
    def _analyze_prefix_list(
        self,
        prefix: str,
        existing_prefix_lists: Dict[str, List[int]],
        device_name: str,
    ) -> ReuseContext:
        """
        Determine reuse strategy for a prefix-list.
        
        Logic:
        1. Check if a prefix-list for this exact prefix exists -> APPEND
        2. Otherwise -> CREATE new prefix-list
        """
        # Generate canonical name for this prefix
        canonical_name = self._generate_prefix_list_name(prefix)
        
        if canonical_name in existing_prefix_lists:
            existing_seqs = existing_prefix_lists[canonical_name]
            next_seq = self._calculate_next_sequence(existing_seqs)
            
            return ReuseContext(
                object_type=ObjectType.PREFIX_LIST,
                status=ReuseStatus.EXISTS,
                strategy=ReuseStrategy.APPEND,
                target_name=canonical_name,
                existing_sequences=existing_seqs,
                next_sequence=next_seq,
                metadata={"prefix": prefix},
            )
        
        return ReuseContext(
            object_type=ObjectType.PREFIX_LIST,
            status=ReuseStatus.MISSING,
            strategy=ReuseStrategy.CREATE,
            target_name=canonical_name,
            existing_sequences=[],
            next_sequence=10,
            metadata={"prefix": prefix},
        )
    
    def _analyze_interface(
        self,
        interface: str,
        existing_interfaces: Set[str],
        config_text: str,
    ) -> ReuseContext:
        """
        Determine reuse strategy for an interface (OSPF cost changes).
        
        Logic:
        - Interfaces always exist (we're modifying, not creating)
        - Strategy is always MODIFY
        """
        status = (
            ReuseStatus.EXISTS if interface in existing_interfaces
            else ReuseStatus.MISSING
        )
        
        return ReuseContext(
            object_type=ObjectType.INTERFACE,
            status=status,
            strategy=ReuseStrategy.MODIFY,
            target_name=interface,
            metadata={"interface": interface},
        )
    
    def _calculate_next_sequence(self, existing_sequences: List[int]) -> int:
        """Calculate the next available sequence number."""
        if not existing_sequences:
            return self.sequence_increment
        
        max_seq = max(existing_sequences)
        return max_seq + self.sequence_increment
    
    def _generate_route_map_name(self, device_name: str, neighbor: str) -> str:
        """Generate a canonical route-map name for a neighbor."""
        # Sanitize neighbor IP for use in name
        sanitized = neighbor.replace(".", "_").replace(":", "_")
        return f"RM_PATHDELTA_{sanitized}_IN"
    
    def _generate_prefix_list_name(self, prefix: str) -> str:
        """Generate a canonical prefix-list name for a prefix."""
        # Sanitize prefix for use in name (e.g., "10.0.0.0/8" -> "PL_10_0_0_0_8")
        sanitized = prefix.replace(".", "_").replace("/", "_").replace(":", "_")
        return f"PL_PATHDELTA_{sanitized}"


def analyze_affected_devices(
    configs: Dict[str, str],
    affected_devices: List[str],
    neighbors_by_device: Optional[Dict[str, List[str]]] = None,
    prefixes: Optional[List[str]] = None,
    interfaces_by_device: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, DeviceReuseContext]:
    """
    Convenience function to analyze multiple devices.
    
    Args:
        configs: Map of device_name -> config_text
        affected_devices: List of devices to analyze
        neighbors_by_device: Map of device -> relevant neighbors
        prefixes: Prefixes relevant to all devices
        interfaces_by_device: Map of device -> relevant interfaces
    
    Returns:
        Dict mapping device_name -> DeviceReuseContext
    """
    analyzer = ContextAnalyzer()
    results: Dict[str, DeviceReuseContext] = {}
    
    for device in affected_devices:
        config_text = configs.get(device, "")
        neighbors = (neighbors_by_device or {}).get(device, [])
        interfaces = (interfaces_by_device or {}).get(device, [])
        
        results[device] = analyzer.analyze_device(
            device_name=device,
            config_text=config_text,
            relevant_neighbors=neighbors if neighbors else None,
            relevant_prefixes=prefixes,
            relevant_interfaces=interfaces if interfaces else None,
        )
    
    return results
