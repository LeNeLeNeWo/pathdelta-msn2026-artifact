"""
Reporter for Synthesis Layer

Calculates footprint metrics and generates synthesis reports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from .models import FootprintVector, PatchPlan, SynthesisReport


class SynthesisReporter:
    """
    Generates synthesis reports with footprint metrics.
    
    Footprint Metrics:
    - lines_changed: Count of new configuration lines
    - objects_touched: Count of modified configuration objects
    - safety_check: Boolean confirming prefix-isolation is present
    """
    
    # Patterns to identify configuration objects
    ROUTE_MAP_PATTERN = re.compile(r'^route-map\s+\S+', re.MULTILINE)
    PREFIX_LIST_PATTERN = re.compile(r'^ip\s+prefix-list\s+\S+', re.MULTILINE)
    INTERFACE_PATTERN = re.compile(r'^interface\s+\S+', re.MULTILINE)
    NEIGHBOR_PATTERN = re.compile(r'^\s*neighbor\s+\S+', re.MULTILINE)
    
    # Pattern to detect prefix-list match (safety check)
    PREFIX_MATCH_PATTERN = re.compile(
        r'match\s+ip\s+address\s+prefix-list\s+\S+',
        re.MULTILINE
    )
    
    # Pattern to detect OSPF interface cost changes
    OSPF_COST_PATTERN = re.compile(
        r'^\s*ip\s+ospf\s+cost\s+\d+',
        re.MULTILINE
    )
    
    def generate_report(
        self,
        plan: PatchPlan,
        rendered_configs: Dict[str, str],
    ) -> SynthesisReport:
        """
        Generate a synthesis report for a patch plan.
        
        Args:
            plan: The PatchPlan that was executed
            rendered_configs: Map of device -> rendered configuration
        
        Returns:
            SynthesisReport with footprint metrics
        """
        # Calculate metrics
        lines_changed = self._count_lines(rendered_configs)
        objects_touched = self._count_objects(rendered_configs)
        safety_check = self._verify_prefix_isolation(plan, rendered_configs)
        
        # Count specific object types
        prefix_lists_created = self._count_prefix_lists(rendered_configs)
        route_maps_modified = self._count_route_maps(rendered_configs)
        interfaces_modified = self._count_interfaces(rendered_configs)
        
        # Collect warnings
        warnings = self._collect_warnings(plan, rendered_configs, safety_check)
        
        return SynthesisReport(
            intent_id=plan.intent_id,
            lines_changed=lines_changed,
            objects_touched=objects_touched,
            safety_check=safety_check,
            devices_affected=list(rendered_configs.keys()),
            warnings=warnings,
            rendered_configs=rendered_configs,
            prefix_lists_created=prefix_lists_created,
            route_maps_modified=route_maps_modified,
            interfaces_modified=interfaces_modified,
        )
    
    def _count_lines(self, rendered_configs: Dict[str, str]) -> int:
        """Count total non-empty, non-comment lines."""
        total = 0
        for config in rendered_configs.values():
            for line in config.split("\n"):
                stripped = line.strip()
                # Count non-empty lines that aren't just comments
                if stripped and not stripped.startswith("!"):
                    total += 1
        return total
    
    def _count_objects(self, rendered_configs: Dict[str, str]) -> int:
        """Count total configuration objects touched."""
        total = 0
        for config in rendered_configs.values():
            # Count route-maps
            total += len(self.ROUTE_MAP_PATTERN.findall(config))
            # Count prefix-lists
            total += len(self.PREFIX_LIST_PATTERN.findall(config))
            # Count interfaces
            total += len(self.INTERFACE_PATTERN.findall(config))
        return total
    
    def _count_prefix_lists(self, rendered_configs: Dict[str, str]) -> int:
        """Count prefix-list entries created."""
        total = 0
        for config in rendered_configs.values():
            total += len(self.PREFIX_LIST_PATTERN.findall(config))
        return total
    
    def _count_route_maps(self, rendered_configs: Dict[str, str]) -> int:
        """Count route-map entries modified."""
        total = 0
        for config in rendered_configs.values():
            total += len(self.ROUTE_MAP_PATTERN.findall(config))
        return total
    
    def _count_interfaces(self, rendered_configs: Dict[str, str]) -> int:
        """Count interfaces modified."""
        total = 0
        for config in rendered_configs.values():
            total += len(self.INTERFACE_PATTERN.findall(config))
        return total
    
    def _verify_prefix_isolation(
        self,
        plan: PatchPlan,
        rendered_configs: Dict[str, str],
    ) -> bool:
        """
        Verify that prefix isolation is enforced.
        
        For BGP: Every route-map sequence must have a prefix-list match.
        For OSPF: Inherently isolated via routing (always True).
        """
        if plan.protocol == "ospf":
            # OSPF is inherently prefix-isolated
            return True
        
        if plan.protocol == "bgp":
            # Check that plan has prefix_isolation_enforced flag
            if not plan.prefix_isolation_enforced:
                return False
            
            # Verify rendered configs have prefix-list matches
            for config in rendered_configs.values():
                # If there are route-maps, they must have prefix-list matches
                route_maps = self.ROUTE_MAP_PATTERN.findall(config)
                if route_maps:
                    prefix_matches = self.PREFIX_MATCH_PATTERN.findall(config)
                    # Should have at least one prefix-list match per route-map
                    if len(prefix_matches) < len(route_maps):
                        return False
            
            return True
        
        # Unknown protocol - conservative default
        return False
    
    def _collect_warnings(
        self,
        plan: PatchPlan,
        rendered_configs: Dict[str, str],
        safety_check: bool,
    ) -> List[str]:
        """Collect any warnings about the synthesis."""
        warnings: List[str] = []
        
        if not safety_check:
            warnings.append(
                "SAFETY WARNING: Prefix isolation not verified in output. "
                "Configuration may affect unintended traffic."
            )
        
        if not rendered_configs:
            warnings.append("No configuration generated - check plan operations.")
        
        # Check for empty device configs
        for device, config in rendered_configs.items():
            if not config.strip() or config.strip() == "!":
                warnings.append(f"Empty configuration generated for device: {device}")
        
        # Check plan metadata for errors
        if plan.metadata.get("error"):
            warnings.append(f"Plan error: {plan.metadata['error']}")
        
        if plan.metadata.get("warning"):
            warnings.append(f"Plan warning: {plan.metadata['warning']}")
        
        return warnings

    def calculate_footprint_vector(
        self,
        plan: PatchPlan,
        rendered_configs: Dict[str, str],
    ) -> FootprintVector:
        """
        Calculate standardized footprint vector for radar charts.
        
        Args:
            plan: The PatchPlan that was executed
            rendered_configs: Map of device -> rendered configuration
        
        Returns:
            FootprintVector with all metrics including ospf_cost_changes
        """
        # metric_devices_touched: count of unique devices in affected_devices
        metric_devices_touched = len(set(plan.affected_devices))
        
        # metric_objects_touched: count of unique route-map and prefix-list names
        metric_objects_touched = self._count_unique_objects(rendered_configs)
        
        # metric_lines_changed: count of non-empty, non-comment lines
        metric_lines_changed = self._count_lines(rendered_configs)
        
        # metric_safety_score: 1.0 if prefix_isolation_enforced, 0.0 otherwise
        metric_safety_score = 1.0 if plan.prefix_isolation_enforced else 0.0
        
        # ospf_cost_changes: count of OSPF interface cost statements
        ospf_cost_changes = self._count_ospf_cost_changes(rendered_configs)
        
        return FootprintVector(
            metric_devices_touched=metric_devices_touched,
            metric_objects_touched=metric_objects_touched,
            metric_lines_changed=metric_lines_changed,
            metric_safety_score=metric_safety_score,
            ospf_cost_changes=ospf_cost_changes,
        )

    def _count_ospf_cost_changes(self, rendered_configs: Dict[str, str]) -> int:
        """
        Count OSPF interface cost statements in rendered configs.
        
        Matches patterns like:
        - ip ospf cost 10
        - ip ospf cost 100
        
        Returns:
            Count of OSPF cost change statements
        """
        total = 0
        for config in rendered_configs.values():
            total += len(self.OSPF_COST_PATTERN.findall(config))
        return total

    def _count_unique_objects(self, rendered_configs: Dict[str, str]) -> int:
        """
        Count unique route-map and prefix-list names across all configs.
        
        Returns:
            Count of unique object names (route-maps + prefix-lists)
        """
        unique_names: Set[str] = set()
        
        # Pattern to extract route-map names
        route_map_name_pattern = re.compile(r'^route-map\s+(\S+)', re.MULTILINE)
        # Pattern to extract prefix-list names
        prefix_list_name_pattern = re.compile(r'^ip\s+prefix-list\s+(\S+)', re.MULTILINE)
        
        for config in rendered_configs.values():
            # Extract route-map names
            for match in route_map_name_pattern.finditer(config):
                unique_names.add(f"route-map:{match.group(1)}")
            
            # Extract prefix-list names
            for match in prefix_list_name_pattern.finditer(config):
                unique_names.add(f"prefix-list:{match.group(1)}")
        
        return len(unique_names)


class AggregateReporter:
    """
    Generates aggregate reports across multiple synthesis operations.
    """
    
    def __init__(self):
        self.reporter = SynthesisReporter()
    
    def generate_aggregate_report(
        self,
        plans: List[PatchPlan],
        rendered_by_plan: Dict[str, Dict[str, str]],
    ) -> Dict[str, Any]:
        """
        Generate an aggregate report for multiple plans.
        
        Args:
            plans: List of PatchPlans
            rendered_by_plan: Map of intent_id -> (device -> config)
        
        Returns:
            Aggregate report dictionary
        """
        individual_reports: List[SynthesisReport] = []
        
        for plan in plans:
            rendered = rendered_by_plan.get(plan.intent_id, {})
            report = self.reporter.generate_report(plan, rendered)
            individual_reports.append(report)
        
        # Aggregate metrics
        total_lines = sum(r.lines_changed for r in individual_reports)
        total_objects = sum(r.objects_touched for r in individual_reports)
        all_safe = all(r.safety_check for r in individual_reports)
        all_devices = set()
        all_warnings: List[str] = []
        
        for report in individual_reports:
            all_devices.update(report.devices_affected)
            all_warnings.extend(report.warnings)
        
        return {
            "total_intents": len(plans),
            "total_lines_changed": total_lines,
            "total_objects_touched": total_objects,
            "all_safety_checks_passed": all_safe,
            "total_devices_affected": len(all_devices),
            "devices": list(all_devices),
            "warnings": all_warnings,
            "individual_reports": [
                {
                    "intent_id": r.intent_id,
                    "lines_changed": r.lines_changed,
                    "objects_touched": r.objects_touched,
                    "safety_check": r.safety_check,
                }
                for r in individual_reports
            ],
        }


def generate_synthesis_report(
    plan: PatchPlan,
    rendered_configs: Dict[str, str],
) -> SynthesisReport:
    """
    Convenience function to generate a synthesis report.
    
    Args:
        plan: The PatchPlan that was executed
        rendered_configs: Map of device -> rendered configuration
    
    Returns:
        SynthesisReport with footprint metrics
    """
    reporter = SynthesisReporter()
    return reporter.generate_report(plan, rendered_configs)
