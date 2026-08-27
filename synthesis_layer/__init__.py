"""
PathDelta Synthesis Layer

Translates deterministic RolePolicy (IR) into minimized, safe, and deployable
configuration patches.

Core Philosophy:
1. Minimization via Object Reuse: Identify and append to existing objects
2. Safety via Prefix Isolation: All BGP changes guarded by prefix-list matching
3. Extensibility via Templates: All config syntax handled by Jinja2 templates

Main Components:
- context_analyzer: Analyzes existing configs for reuse opportunities
- planner: Generates abstract PatchPlan from RolePolicy + ReuseContext
- renderer: Applies PatchPlan to Jinja2 templates
- reporter: Calculates footprint metrics and safety checks
"""

from .models import (
    DeviceReuseContext,
    InsertionDecision,
    InsertionStrategy,
    ObjectType,
    PatchOperation,
    PatchPlan,
    ReuseContext,
    ReuseStatus,
    ReuseStrategy,
    StepAnalysis,
    SynthesisReport,
)

from .context_analyzer import (
    ContextAnalyzer,
    NamingPattern,
    NamingPatternResult,
    analyze_affected_devices,
    extract_config_names,
    extract_prefix_list_names,
    extract_route_map_names,
    infer_naming_pattern,
    infer_naming_pattern_by_type,
)

from .planner import (
    PatchPlanner,
    create_patch_plan,
    generate_unique_name,
)

from .renderer import (
    ConfigRenderer,
    MultiPlanRenderer,
    render_patch_plan,
)

from .reporter import (
    SynthesisReporter,
    AggregateReporter,
    generate_synthesis_report,
)

from .template_reporter import (
    ExtendedRetrievalStats,
    TemplateUsageRecord,
    generate_report as generate_template_report,
    write_report as write_template_report,
    generate_latex_table,
)

from .guard import (
    SecurityViolationError,
    SynthesisError,
    ExtractionResult,
)


__all__ = [
    # Models
    "DeviceReuseContext",
    "InsertionDecision",
    "InsertionStrategy",
    "ObjectType",
    "PatchOperation",
    "PatchPlan",
    "ReuseContext",
    "ReuseStatus",
    "ReuseStrategy",
    "StepAnalysis",
    "SynthesisReport",
    # Context Analyzer
    "ContextAnalyzer",
    "NamingPattern",
    "NamingPatternResult",
    "analyze_affected_devices",
    "extract_config_names",
    "extract_prefix_list_names",
    "extract_route_map_names",
    "infer_naming_pattern",
    "infer_naming_pattern_by_type",
    # Planner
    "PatchPlanner",
    "create_patch_plan",
    "generate_unique_name",
    # Renderer
    "ConfigRenderer",
    "MultiPlanRenderer",
    "render_patch_plan",
    # Reporter
    "SynthesisReporter",
    "AggregateReporter",
    "generate_synthesis_report",
    # Template Reporter
    "ExtendedRetrievalStats",
    "TemplateUsageRecord",
    "generate_template_report",
    "write_template_report",
    "generate_latex_table",
    # Guard (ConstraintGuard)
    "SecurityViolationError",
    "SynthesisError",
    "ExtractionResult",
]
