"""
Renderer for Synthesis Layer

Pure Neural Synthesis Mode: Uses LLM-based generation with RAG (Retrieval-Augmented Generation).
When use_neural=True, the renderer REQUIRES successful LLM generation - NO FALLBACK to templates.
This ensures all successful outputs are proven to come from the LLM.

Legacy Mode: When use_neural=False, uses Jinja2 templates for configuration generation.

Deterministic Rendering: For operations that are fully determined by op_params (neighbor binding,
prefix-list entry), we render deterministically without LLM to eliminate flaky failures.
LLM is reserved for style-sensitive route-map blocks only.

Repair Loop: When render_with_repair() is called, the renderer implements a feedback-driven
repair loop that:
1. Generates config using LLM
2. Validates against Policy Layer parameters via ConstraintGuard (FAIL FAST on violations)
3. Checks syntax using Verifier
4. Retries on syntax errors with error feedback (up to max_retries)

Contract Retry: For neural rendering, distinguishes between:
- CONTRACT_FAIL (retryable): missing required field, missing markers, empty output
- SECURITY_VIOLATION (non-retryable): semantic drift, unexpected objects, wrong values
"""
from __future__ import annotations

import json
import logging
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from common.llm_driver import get_driver, DeepSeekDriverError
from .models import PatchOperation, PatchPlan
from .guard import (
    ConstraintGuard,
    SecurityViolationError,
    SynthesisError,
    FailureKind,
    VerificationResult,
    Severity,
    ContractDiff,
    ContractBuilder,
    PatchContract,
    MissingSlotsDiff,
)
from .template_library import TemplateLibrary
from .template_retriever import TemplateRetriever, RetrievalMethod
from .template_schema import TemplateDefinition

if TYPE_CHECKING:
    from .context_analyzer import ContextAnalyzer
    from verification_layer.verifier import Verifier

# Logger for neural rendering operations
logger = logging.getLogger(__name__)


# Template-to-query-type mapping for style retrieval
# Maps template path patterns to configuration element types
TEMPLATE_QUERY_MAP = {
    "bgp/route_map": "route-map",
    "bgp/prefix_list": "prefix-list",
    "bgp/neighbor": "neighbor",
    "ospf/interface": "interface",
    "common/access_list": "access-list",
}

# Neural prompt template for LLM-based configuration generation
# Uses RAG with style examples and strict logic constraints
# Includes patch markers for machine-checkable output (Requirements: 17.1, 17.2, 17.3)
NEURAL_PROMPT_TEMPLATE = """You are a network config expert.

**Style Examples (from current network):**
{examples}

**Task:** Generate a NEW config snippet following the EXACT style above (naming, indentation).

**Strict Logic Constraints (Must Follow):**
{constraints}

**REQUIRED COMMANDS (must include semantically equivalent lines):**
{required_commands}

**CRITICAL FRR SYNTAX RULES:**
1. **Neighbor bindings only:** If the required commands include `neighbor ... route-map ...`, wrap them inside exactly one `router bgp <AS>` block.
2. **Global objects:** `route-map`, `ip prefix-list`, and `interface ... / ip ospf cost ...` belong in global configuration mode, not inside `router bgp`.
3. **Indentation:** Use 1 space for subcommands inside a block (`router bgp`, `route-map`, or `interface`).

**OUTPUT FORMAT:**
You MUST wrap your configuration output between these exact markers:
###BEGIN_PATCH###
<your configuration here>
###END_PATCH###

Output ONLY the configuration code between the markers. Do not include any explanation."""

BASELINE_NEURAL_PROMPT_TEMPLATE = """You are a network config expert.

**Style Examples (may be empty):**
{examples}

**Requested Change:**
- Operation kind: {operation_type}
- Template hint: {template_name}
- Parameters:
{constraints}

**Basic FRR Reminders:**
- `neighbor ... route-map ...` belongs under `router bgp <AS>`.
- `route-map` and `ip prefix-list` are global configuration.
- Use the requested values directly when possible, but infer missing context yourself.

**OUTPUT FORMAT:**
Wrap the configuration between these exact markers:
###BEGIN_PATCH###
<your configuration here>
###END_PATCH###

Output ONLY the configuration code between the markers. Do not include any explanation."""

# Patch markers for machine-checkable output (Requirements: 17.1, 17.2, 17.3)
PATCH_BEGIN_MARKER = "###BEGIN_PATCH###"
PATCH_END_MARKER = "###END_PATCH###"


class OperationType(Enum):
    """Classification of operation rendering mode."""
    DETERMINISTIC = "deterministic"  # No LLM needed - fully determined by op_params
    NEURAL = "neural"  # LLM needed for style-sensitive rendering


def classify_operation(op: PatchOperation) -> OperationType:
    """
    Classify an operation for rendering mode selection.
    
    Deterministic operations (no LLM):
    - neighbor_route_map: neighbor <IP> route-map <RM> in|out
    - prefix_list_entry: ip prefix-list <NAME> [seq <N>] <permit|deny> <PREFIX>
    
    Neural operations (LLM needed):
    - route_map_sequence: route-map blocks with match/set clauses
    
    Args:
        op: The PatchOperation to classify
        
    Returns:
        OperationType.DETERMINISTIC or OperationType.NEURAL
    """
    template = (op.template or "").lower()
    
    # Neighbor binding is always deterministic
    if "neighbor" in template and "route_map" in template:
        return OperationType.DETERMINISTIC
    
    # Also check for neighbor_route_map pattern
    if "neighbor_route_map" in template:
        return OperationType.DETERMINISTIC
    
    # Prefix-list entry is deterministic
    if "prefix_list" in template and ("entry" in template or "prefix_list_entry" in template):
        return OperationType.DETERMINISTIC

    # Also check params for prefix-list operations
    if "prefix_list" in template and "prefix" in op.params and "prefix_list_name" in op.params:
        return OperationType.DETERMINISTIC

    # maximum-paths is a fully determined BGP knob
    if "maximum_paths" in template or "maximum-paths" in template:
        return OperationType.DETERMINISTIC

    # OSPF interface cost is fully determined by planner output
    if "interface_cost" in template:
        return OperationType.DETERMINISTIC

    # Route-map sequences need LLM for style
    return OperationType.NEURAL


class ConfigRenderer:
    """
    Renders PatchPlan operations into configuration snippets.
    
    UNIFIED RENDERING PATH (Must Fix 2):
    ALL rendering goes through the same template-guided flow:
    1. Retrieve template via TemplateRetriever (guaranteed to return template)
    2. Fill skeleton with op_params (RETRYABLE on missing params - Must Fix 3)
    3. Perform Style-RAG: extract similar snippets from c_old
    4. Call LLM with (style_examples, draft_patch, REQUIRED, FORBIDDEN)
    5. Validate via ConstraintGuard.verify_contract
    6. Retry on failure via render_with_repair
    
    NO FALLBACK TO OLD _neural_render - all paths use template-guided rendering.
    
    Pure Neural Synthesis Mode (use_neural=True):
        - REQUIRES context_analyzer to be set
        - Uses LLM-based generation with style-aware RAG
        - NO FALLBACK: Failures propagate as exceptions
        - Proves all successful outputs come from the LLM
    
    Legacy Template Mode (use_neural=False):
        - Uses Jinja2 templates for configuration generation
    """
    
    def __init__(
        self,
        template_dir: Optional[str] = None,
        context_analyzer: Optional["ContextAnalyzer"] = None,
        use_neural: bool = True,
        verifier: Optional["Verifier"] = None,
        no_rag: bool = False,
        no_guard: bool = False,
        template_retriever: Optional[TemplateRetriever] = None,
        max_retries: int = 0, # Default to 0 (No Repair) to match "pass@1" semantics unless opted-in
        pure_llm_baseline: bool = False,
    ):
        """
        Initialize the renderer with optional neural capabilities.
        
        Args:
            template_dir: Path to templates directory. Defaults to 'templates/' in project root.
            context_analyzer: ContextAnalyzer with style_snippets populated for RAG retrieval.
            use_neural: Enable LLM-based generation (default True). When True and
                       context_analyzer is provided, attempts neural rendering first.
            verifier: Optional Verifier instance for syntax checking in repair loop.
                     If None, render_with_repair() will skip syntax verification.
            no_rag: Ablation flag to disable RAG style retrieval (default False).
                   When True, retrieve_style_examples() returns placeholder text.
            no_guard: Ablation flag to disable ConstraintGuard verification (default False).
                     When True, render_with_repair() skips guard.verify() calls.
            template_retriever: TemplateRetriever instance for Template-RAG.
                               If None, creates default retriever with TemplateLibrary.
                               (NO FALLBACK to old rendering - Must Fix 2)
            max_retries: Max attempts for syntax/contract repair (default 0).
        
        Requirements: 2.1, 3.1, 7.1, 7.4, 6.1, 11.4
        """
        if template_dir is None:
            # Default to templates/ relative to project root
            project_root = Path(__file__).parent.parent
            template_dir = str(project_root / "templates")
        
        self.template_dir = template_dir
        self.context_analyzer = context_analyzer
        self.use_neural = use_neural
        self.verifier = verifier
        self.max_retries = max_retries
        self.no_rag = no_rag
        self.no_guard = no_guard
        self.pure_llm_baseline = pure_llm_baseline
        self.guard = ConstraintGuard()
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        
        # MUST FIX 2: Always use template-guided rendering
        # Create default retriever if not provided
        if template_retriever is None:
            try:
                library = TemplateLibrary(template_dir)
                self.template_retriever = TemplateRetriever(library)
                logger.info(f"Created default TemplateRetriever with library from {template_dir}")
            except Exception as e:
                logger.warning(f"Failed to create TemplateRetriever: {e}. Template-guided rendering may be limited.")
                self.template_retriever = None
        else:
            self.template_retriever = template_retriever
    
    def render_plan(self, plan: PatchPlan) -> Dict[str, str]:
        """
        Render all operations in a PatchPlan.
        
        Args:
            plan: The PatchPlan to render
        
        Returns:
            Dict mapping device_name -> rendered configuration snippet
        """
        # Group operations by device
        ops_by_device: Dict[str, List[PatchOperation]] = {}
        for op in plan.operations:
            if op.device not in ops_by_device:
                ops_by_device[op.device] = []
            ops_by_device[op.device].append(op)
        
        # Sort operations by order within each device
        for device in ops_by_device:
            ops_by_device[device].sort(key=lambda x: x.order)
        
        # Render each device's operations
        rendered: Dict[str, str] = {}
        for device, operations in ops_by_device.items():
            device_config = self._render_device_operations(device, operations, plan)
            rendered[device] = device_config
        
        return rendered
    
    def _render_device_operations(
        self,
        device: str,
        operations: List[PatchOperation],
        plan: PatchPlan,
    ) -> str:
        """Render all operations for a single device."""
        lines: List[str] = []
        
        # Add header
        lines.append("!")
        lines.append(f"! PathDelta Configuration Patch for {device}")
        lines.append(f"! Intent: {plan.intent_id}")
        lines.append(f"! Protocol: {plan.protocol}")
        lines.append("!")
        lines.append("")
        
        # Render each operation
        for op in operations:
            try:
                rendered = self._render_operation(op)
                lines.append(rendered)
            except TemplateNotFound as e:
                lines.append(f"! ERROR: Template not found: {op.template}")
                lines.append(f"! Operation: {op.op.value} on {op.device}")
            except (SynthesisError, SecurityViolationError) as e:
                # MUST propagate synthesis and security errors so the repair loop
                # and experiment reporters can accurately detect failures (Requirement 7.4)
                raise e
            except Exception as e:
                lines.append(f"! ERROR rendering operation: {e}")
        
        return "\n".join(lines)
    
    def _render_operation(self, op: PatchOperation, feedback: Optional[str] = None) -> str:
        """
        Render a single operation using neural or template-based approach.
        
        Pure Neural Synthesis Mode (use_neural=True):
            - REQUIRES context_analyzer to be set
            - Calls _neural_render() directly
            - NO FALLBACK: If neural rendering fails, the exception propagates
            - This proves all successful outputs come from the LLM
        
        Legacy Template Mode (use_neural=False):
            - Uses Jinja2 template rendering
        """
        if self.use_neural:
            # Pure Neural Synthesis - NO FALLBACK
            # If context_analyzer is missing, fail loudly
            if self.context_analyzer is None:
                raise RuntimeError(
                    f"Pure Neural Synthesis requires context_analyzer to be set. "
                    f"Cannot render operation for template: {op.template}"
                )

            # Call neural render loop (includes validation and repair if max_retries > 0)
            # If max_retries is 0 (No Repair), we must also disable contract retries to ensure true pass@1
            contract_retries = 2 # Default
            if self.max_retries == 0:
                contract_retries = 0
            
            text, _, _, _ = self.render_with_repair_tracked(
                op, 
                max_retries=self.max_retries,
                max_contract_retries=contract_retries,
                initial_feedback=feedback or "",
            )
            return text
            
            # Legacy code removed:
            # result = self._neural_render(op)
            # if not result or not result.strip(): ...

        
        # Legacy Jinja2 template rendering (only when use_neural=False)
        return self._jinja_render(op)
    
    def _jinja_render(self, op: PatchOperation) -> str:
        """
        Render a single operation using Jinja2 templates.
        
        Args:
            op: The PatchOperation to render
        
        Returns:
            Rendered configuration snippet
        """
        template = self.env.get_template(op.template)
        return template.render(**op.params)
    
    def render_operation(self, op: PatchOperation, feedback: Optional[str] = None) -> str:
        """
        Public method to render a single operation.
        
        Args:
            op: The PatchOperation to render
        
        Returns:
            Rendered configuration snippet
        """
        return self._render_operation(op, feedback)
    
    def _render_neighbor_binding_deterministic(self, op: PatchOperation) -> str:
        """
        Render neighbor binding operation deterministically without LLM.
        
        FRR requires neighbor route-map bindings to appear inside `router bgp`.
        When local_as is available in op.params, emit a minimal BGP block so the
        deterministic path remains syntactically valid without relying on LLM repair.
        
        Args:
            op: PatchOperation with neighbor binding params
            
        Returns:
            Rendered configuration snippet
        """
        params = op.params
        neighbor_ip = params.get("neighbor_address", params.get("neighbor"))
        rm_name = params.get("route_map_name")
        direction = params.get("direction", "in")
        local_as = params.get("local_as")

        if local_as not in (None, ""):
            return (
                f"router bgp {local_as}\n"
                f" neighbor {neighbor_ip} route-map {rm_name} {direction}"
            )

        return f" neighbor {neighbor_ip} route-map {rm_name} {direction}"
    
    def _render_prefix_list_deterministic(self, op: PatchOperation) -> str:
        """
        Render prefix-list entry operation deterministically without LLM.
        
        MANDATORY: If op_params contains seq → output with seq
        MANDATORY: If op_params does NOT contain seq → output without seq
        MANDATORY: Never invent seq if not in op_params
        
        Args:
            op: PatchOperation with prefix-list params
            
        Returns:
            Rendered configuration snippet
        """
        params = op.params
        pl_name = params.get("prefix_list_name")
        prefix = params.get("prefix")
        action = params.get("action", "permit")
        
        # Check if seq is explicitly provided in params
        seq = params.get("sequence_number", params.get("seq"))
        
        if seq is not None:
            # seq is provided - include it
            return f"ip prefix-list {pl_name} seq {seq} {action} {prefix}"
        else:
            # seq is NOT provided - do NOT invent it
            return f"ip prefix-list {pl_name} {action} {prefix}"
    
    def _template_to_query_type(self, template: str) -> str:
        """
        Extract query type from template path for style retrieval.
        
        Maps template paths to configuration element types used for
        retrieving relevant style examples from the knowledge base.
        
        Args:
            template: Template path (e.g., "bgp/route_map_sequence.j2")
        
        Returns:
            Query type string (e.g., "route-map")
        """
        # Check against known mappings
        for pattern, query_type in TEMPLATE_QUERY_MAP.items():
            if pattern in template:
                return query_type
        
        # Default: use template name with underscores replaced by hyphens
        # e.g., "bgp/some_template.j2" -> "some-template"
        template_name = template.split("/")[-1].replace(".j2", "").replace("_", "-")
        return template_name
    
    def retrieve_style_examples(self, query_type: str, k: int = 3) -> str:
        """
        Retrieve relevant style examples from the knowledge base.
        
        Searches the style_snippets stored in the ContextAnalyzer for
        snippets containing the query_type (case-insensitive match).
        
        Args:
            query_type: Configuration element type (e.g., "route-map", "prefix-list")
            k: Maximum number of snippets to return (default 3)
        
        Returns:
            Combined string of matching snippets joined by newlines,
            or empty string if no matches found or no context_analyzer.
            Returns placeholder text if no_rag=True.
        
        Requirements: 2.1, 2.3
        """
        # Ablation: Return placeholder when RAG is disabled
        if self.no_rag:
            logger.warning("ABLATION: Pure Policy Mode - RAG style retrieval DISABLED")
            return """(No style examples - RAG disabled)

[System Requirement]
You are configuring FRRouting (FRR).
IMPORTANT: Any BGP commands (like neighbor, network) MUST be wrapped inside a router bgp <AS_NUMBER> block. Do not output naked BGP commands. If defining route-maps or prefix-lists, place them in global configuration mode."""
        
        # Return empty string if no context_analyzer or no style_snippets
        if self.context_analyzer is None:
            return ""
        
        if not self.context_analyzer.style_snippets:
            return ""
        
        # Filter snippets containing query_type (case-insensitive)
        query_lower = query_type.lower()
        matching_snippets = [
            snippet
            for snippet in self.context_analyzer.style_snippets
            if query_lower in snippet.lower()
        ]
        
        # Return top k matches joined by newlines
        top_k = matching_snippets[:k]
        return "\n".join(top_k)
    
    def _neural_render(self, op: PatchOperation) -> str:
        """
        Render a single operation using LLM-based neural generation.
        
        Uses RAG (Retrieval-Augmented Generation) to generate configuration
        snippets that match the network's style conventions while enforcing
        strict logic constraints from the operation parameters.
        
        Includes patch markers (###BEGIN_PATCH### / ###END_PATCH###) for
        machine-checkable output extraction.
        
        Args:
            op: The PatchOperation to render
        
        Returns:
            Generated configuration snippet (extracted from between markers)
        
        Raises:
            DeepSeekDriverError: If the LLM call fails
            RuntimeError: If patch markers are missing (format_error)
        
        Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6
        """
        # Step A: Get query_type from template name
        query_type = self._template_to_query_type(op.template)
        
        # Step B: Retrieve style examples
        examples = self.retrieve_style_examples(query_type)
        
        # Step C/D: Construct prompt
        if self.pure_llm_baseline:
            prompt = BASELINE_NEURAL_PROMPT_TEMPLATE.format(
                examples=examples or "(No style examples available)",
                operation_type=self._get_operation_type_from_template(op.template),
                template_name=op.template or "unknown",
                constraints=json.dumps(op.params, indent=2),
            )
        else:
            required_commands = self._build_required_commands(op)
            prompt = NEURAL_PROMPT_TEMPLATE.format(
                examples=examples or "(No style examples available)",
                constraints=json.dumps(op.params, indent=2),
                required_commands=required_commands,
            )
        
        # Step E: Call LLM via get_driver().chat_completion()
        driver = get_driver()
        response = driver.chat_completion(
            system_prompt="You are a network configuration expert.",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=2048,
        )
        
        # Step F: Extract patch from between markers (Requirements: 17.2, 17.3)
        patch_text = self._extract_patch_from_markers(response)
        
        return patch_text
    
    def _get_operation_type_from_template(self, template: str) -> str:
        """
        Determine operation type from template name.
        
        Args:
            template: Template name/path
        
        Returns:
            Operation type string (PREFIX_LIST, ROUTE_MAP, NEIGHBOR_BIND, OSPF_COST)
        """
        template_lower = (template or "").lower()
        
        if "prefix_list" in template_lower:
            return "PREFIX_LIST"
        elif "route_map" in template_lower:
            return "ROUTE_MAP"
        elif "neighbor" in template_lower:
            return "NEIGHBOR_BIND"
        elif "ospf" in template_lower or "cost" in template_lower:
            return "OSPF_COST"
        else:
            return "GENERIC"
    
    def _fill_skeleton(
        self,
        skeleton: str,
        params: Dict[str, Any],
        template: TemplateDefinition,
    ) -> Tuple[Optional[str], Optional[MissingSlotsDiff]]:
        """
        Fill template skeleton with operation parameters.
        
        MUST FIX 3: Missing params trigger RETRYABLE, not silent empty string.
        
        Parses skeleton for placeholders using regex: {placeholder_name}
        For each placeholder, checks if key exists in op_params.
        
        Args:
            skeleton: Template skeleton with {placeholder} markers
            params: Operation parameters
            template: Template definition for error context
        
        Returns:
            Tuple of (filled_skeleton, None) if all placeholders filled
            Tuple of (None, MissingSlotsDiff) if any placeholder missing
        
        Requirements: 6.2 (modified for Must Fix 3)
        """
        # Find all placeholders in skeleton
        placeholder_pattern = re.compile(r'\{(\w+)\}')
        placeholders = placeholder_pattern.findall(skeleton)
        
        # Check for missing placeholders
        missing_slots = []
        for placeholder in placeholders:
            if placeholder not in params or params[placeholder] is None:
                missing_slots.append(placeholder)
        
        # If any placeholder is missing, return MissingSlotsDiff
        if missing_slots:
            diff = MissingSlotsDiff(
                severity=Severity.RETRYABLE,
                category="missing_slots",
                template_id=template.id,
                missing_slots=missing_slots,
            )
            return None, diff
        
        # All placeholders present - fill the skeleton
        filled = skeleton
        for placeholder in placeholders:
            value = str(params[placeholder])
            filled = filled.replace(f"{{{placeholder}}}", value)
        
        return filled, None
    
    def _build_template_guided_prompt(
        self,
        draft_patch: str,
        style_examples: str,
        required_lines: List[str],
        forbidden_patterns: List[str],
        operation_type: str,
        missing_slots_diff: Optional[MissingSlotsDiff] = None,
    ) -> str:
        """
        Build LLM prompt for template-guided generation.
        
        If missing_slots_diff is provided, includes structured diff
        requesting LLM to infer missing values while maintaining constraints.
        
        Args:
            draft_patch: Filled skeleton as structural guide
            style_examples: Style examples from Style-RAG
            required_lines: List of required configuration lines
            forbidden_patterns: List of forbidden regex patterns
            missing_slots_diff: Optional MissingSlotsDiff for retry
        
        Returns:
            Formatted prompt string
        
        Requirements: 6.4, 6.5
        """
        lines = [
            "You are a network configuration expert.",
            "",
            "**Style Examples (from current network):**",
            style_examples or "(No style examples available)",
            "",
            "**Structural Template (draft patch):**",
            "```",
            draft_patch,
            "```",
            "",
        ]
        
        # Add REQUIRED LINES section
        if required_lines:
            lines.append("**REQUIRED LINES (must include semantically equivalent lines):**")
            for line in required_lines:
                lines.append(f"- {line}")
            lines.append("")
        
        # Add FORBIDDEN PATTERNS section
        if forbidden_patterns:
            lines.append("**FORBIDDEN PATTERNS (must NOT appear):**")
            for pattern in forbidden_patterns:
                lines.append(f"- {pattern}")
            lines.append("")

        if operation_type == "ROUTE_MAP":
            lines.extend([
                "**OPERATION-SPECIFIC SCOPING RULES:**",
                "- Output ONLY a global `route-map ...` block for this operation.",
                "- The first configuration line MUST start with `route-map `.",
                "- Do NOT emit `router bgp`, `neighbor`, `address-family`, or `ip prefix-list` lines.",
                "",
            ])
        elif operation_type == "NEIGHBOR_BIND":
            lines.extend([
                "**OPERATION-SPECIFIC SCOPING RULES:**",
                "- Output ONLY the neighbor binding for this operation.",
                "- If BGP context is needed, wrap it in exactly one `router bgp <AS>` block.",
                "- Do NOT emit route-map definitions or prefix-list definitions.",
                "",
            ])
        
        # Add missing slots context if present
        if missing_slots_diff:
            lines.append("**MISSING SLOTS (please infer appropriate values):**")
            lines.append(missing_slots_diff.to_prompt_text())
            lines.append("")
        
        # Add instruction
        lines.extend([
            "**Task:** 在不改变 required lines 的前提下润色风格",
            "(Polish the style while preserving the required lines)",
            "",
            "**OUTPUT FORMAT:**",
            "You MUST wrap your configuration output between these exact markers:",
            "###BEGIN_PATCH###",
            "<your configuration here>",
            "###END_PATCH###",
            "",
            "Output ONLY the configuration code between the markers. Do not include any explanation.",
        ])
        
        return "\n".join(lines)
    
    def _template_guided_render(self, op: PatchOperation) -> str:
        """
        Render operation using template-guided approach.
        
        Steps:
        1. Retrieve template (guaranteed to return)
        2. Fill skeleton with op_params (may return MissingSlotsDiff)
        3. Style-RAG for few-shot examples
        4. LLM call with constraints
        
        Args:
            op: The PatchOperation to render
        
        Returns:
            Generated configuration snippet
        
        Raises:
            RuntimeError: If template retriever is not available
            DeepSeekDriverError: If the LLM call fails
        
        Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
        """
        if self.pure_llm_baseline or self.template_retriever is None:
            # Fall back to neural render if no template retriever
            logger.warning("Template-guided rendering disabled, falling back to neural render")
            return self._neural_render(op)
        
        # Step 1: Retrieve template
        operation_type = self._get_operation_type_from_template(op.template)
        mechanism = "bgp" if "bgp" in (op.template or "").lower() else "ospf"
        
        template, retrieval_method = self.template_retriever.retrieve(
            operation_type=operation_type,
            mechanism=mechanism,
            config_style="frrconf",
        )
        
        logger.debug(
            f"Retrieved template {template.id} via {retrieval_method.value} "
            f"for operation {op.template}"
        )
        
        # Step 2: Fill skeleton with op_params
        filled_skeleton, missing_diff = self._fill_skeleton(
            skeleton=template.skeleton,
            params=op.params,
            template=template,
        )
        
        # If missing slots, use the skeleton as-is with placeholders for LLM to infer
        if missing_diff:
            logger.warning(
                f"Missing slots in template {template.id}: {missing_diff.missing_slots}"
            )
            # Use original skeleton as draft, let LLM infer values
            draft_patch = template.skeleton
        else:
            draft_patch = filled_skeleton
        
        # Step 3: Style-RAG for few-shot examples
        query_type = self._template_to_query_type(op.template)
        style_examples = self.retrieve_style_examples(query_type)
        
        # Step 4: Build prompt with template constraints
        prompt = self._build_template_guided_prompt(
            draft_patch=draft_patch,
            style_examples=style_examples,
            required_lines=template.required_lines,
            forbidden_patterns=template.forbidden_patterns,
            operation_type=operation_type,
            missing_slots_diff=missing_diff,
        )
        
        # Step 5: Call LLM
        driver = get_driver()
        response = driver.chat_completion(
            system_prompt="You are a network configuration expert.",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=2048,
        )
        
        # Step 6: Extract patch from between markers
        patch_text = self._extract_patch_from_markers(response)
        
        return patch_text

    def _build_required_commands(self, op: PatchOperation) -> str:
        """
        Build the REQUIRED COMMANDS block for the prompt.
        
        Lists semantically equivalent lines that the LLM must include.
        Uses canonical forms but accepts aliases (e.g., local-pref variants).
        
        CRITICAL: Each operation type has specific REQUIRED COMMANDS:
        - prefix_list_entry: ONLY prefix-list definition (ip prefix-list ... permit <prefix>)
        - route_map_sequence: route-map definition + match prefix-list + set local-pref/cost
        - neighbor_route_map: ONLY neighbor binding (neighbor <IP> route-map <RM> in|out)
        
        Args:
            op: The PatchOperation with params
        
        Returns:
            Formatted REQUIRED COMMANDS string
        
        Requirements: 17.4, 17.5, 17.6, 19.1
        """
        commands: List[str] = []
        params = op.params
        template = op.template or ""
        
        # Determine operation type from template path
        is_prefix_list_op = "prefix_list" in template.lower()
        is_route_map_op = "route_map_sequence" in template.lower()
        is_neighbor_bind_op = "neighbor_route_map" in template.lower() or "neighbor" in template.lower() and "route_map_sequence" not in template.lower()
        
        # === PREFIX-LIST ENTRY OPERATION ===
        # REQUIRED: Only prefix-list definition
        # FORBIDDEN: route-map, neighbor, match ip address prefix-list
        # MANDATORY: Do NOT force seq - only include if in op_params
        if is_prefix_list_op:
            if "prefix" in params and "prefix_list_name" in params:
                pl_name = params.get("prefix_list_name", "<PL_NAME>")
                prefix = params.get("prefix", "<PREFIX>")
                action = params.get("action", "permit")
                # MANDATORY: Only include seq if explicitly provided in params
                seq = params.get("sequence_number", params.get("seq"))
                if seq is not None:
                    commands.append(f"- ip prefix-list {pl_name} seq {seq} {action} {prefix}")
                else:
                    commands.append(f"- ip prefix-list {pl_name} {action} {prefix}")
            commands.append("")
            commands.append("FORBIDDEN (do NOT include):")
            commands.append("- route-map definitions")
            commands.append("- neighbor commands")
            commands.append("- match ip address prefix-list")
            return "\n".join(commands)
        
        # === NEIGHBOR ROUTE-MAP BINDING OPERATION ===
        # REQUIRED: Only neighbor binding statement
        # MANDATORY: Do NOT output `router bgp <AS>` - patch-first minimalism
        # FORBIDDEN: route-map definitions, prefix-list definitions
        if is_neighbor_bind_op:
            neighbor_ip = params.get("neighbor_address", params.get("neighbor", "<IP>"))
            rm_name = params.get("route_map_name", "<RM_NAME>")
            direction = params.get("direction", "in")
            local_as = params.get("local_as") or params.get("asn")

            if local_as not in (None, ""):
                commands.append(f"- router bgp {local_as}")
                commands.append(f"-  neighbor {neighbor_ip} route-map {rm_name} {direction}")
            else:
                commands.append(f"- neighbor {neighbor_ip} route-map {rm_name} {direction}")
            commands.append("")
            commands.append("FORBIDDEN (do NOT include):")
            commands.append("- route-map definitions (route-map ... permit/deny)")
            commands.append("- prefix-list definitions (ip prefix-list ...)")
            commands.append("- match ip address prefix-list")
            return "\n".join(commands)
        
        # === ROUTE-MAP SEQUENCE OPERATION ===
        # REQUIRED: route-map definition + match prefix-list + set local-pref/cost
        if is_route_map_op or "route_map_name" in params:
            rm_name = params.get("route_map_name", "<RM_NAME>")
            seq = params.get("sequence_number", params.get("sequence", params.get("seq", 10)))
            action = params.get("action", "permit")
            commands.append(f"- route-map {rm_name} {action} {seq}")
            
            # Prefix-list match clause (Requirements: 19.1 - REQUIRED for prefix isolation)
            if "prefix_list_name" in params:
                pl_name = params.get("prefix_list_name", "<PL_NAME>")
                commands.append(f"- match ip address prefix-list {pl_name}")
            
            # Local-preference (check both 'local_pref' and 'set_local_pref' keys)
            lp_value = params.get("local_pref") or params.get("set_local_pref")
            if lp_value is not None:
                commands.append(f"- set local-preference {lp_value}")
                commands.append(f"  (also acceptable: 'set local-pref {lp_value}' or 'set local preference {lp_value}')")
            
            # OSPF cost / MED (check both 'cost' and 'set_med' keys)
            cost_value = params.get("cost") or params.get("set_med")
            if cost_value is not None:
                commands.append(f"- set metric {cost_value}")
                commands.append(f"  (also acceptable: 'ip ospf cost {cost_value}')")
            
            commands.append("")
            commands.append("FORBIDDEN (do NOT include):")
            commands.append("- neighbor commands")
            commands.append("- prefix-list definitions (ip prefix-list ...)")
            return "\n".join(commands)
        
        if not commands:
            return "(No specific commands required - follow style examples)"
        
        return "\n".join(commands)

    def _sanitize_patch_for_operation(self, op: PatchOperation, patch_text: str) -> str:
        """
        Normalize known LLM drift patterns before guard/syntax verification.

        Route-map generation occasionally drifts into router-bgp context when style
        examples include nearby neighbor bindings. For route-map operations we keep
        only the route-map block and its subcommands, which preserves the intended
        semantics while removing scope-violating boilerplate.
        """
        if self.pure_llm_baseline:
            return patch_text

        template = (op.template or "").lower()
        if "route_map_sequence" not in template:
            return patch_text

        sanitized = self._sanitize_route_map_patch(patch_text)
        if sanitized != patch_text:
            logger.info(
                "Sanitized route-map patch for %s: removed out-of-scope BGP context",
                op.device,
            )
        return sanitized

    def _sanitize_route_map_patch(self, patch_text: str) -> str:
        """Keep only route-map lines and valid route-map subcommands."""
        sanitized_lines: List[str] = []
        saw_route_map = False

        for raw_line in patch_text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                if sanitized_lines and sanitized_lines[-1] != "":
                    sanitized_lines.append("")
                continue

            lowered = stripped.lower()

            if lowered.startswith("router bgp"):
                continue

            if lowered.startswith("neighbor "):
                continue

            if lowered.startswith("route-map "):
                sanitized_lines.append(stripped)
                saw_route_map = True
                continue

            if saw_route_map and lowered.startswith((
                "match ",
                "set ",
                "description ",
                "call ",
                "continue ",
                "on-match ",
            )):
                sanitized_lines.append(f" {stripped}")
                continue

            if stripped.startswith(("!", "#")):
                sanitized_lines.append(stripped)

        cleaned = "\n".join(sanitized_lines).strip()
        return cleaned or patch_text.strip()
    
    def _extract_patch_from_markers(self, response: str) -> str:
        """
        Extract patch text from between ###BEGIN_PATCH### and ###END_PATCH### markers.
        
        Args:
            response: Raw LLM response
        
        Returns:
            Extracted patch text
        
        Raises:
            RuntimeError: If markers are missing (format_error(marker_missing))
        
        Requirements: 17.2, 17.3, 17.7
        """
        import re
        
        # Guard against None response from LLM
        if response is None:
            raise RuntimeError(
                f"format_error(null_response): LLM returned None instead of a string response. "
                f"This is a synthesis error - retry eligible."
            )
        
        # Try to find patch between markers
        pattern = rf'{re.escape(PATCH_BEGIN_MARKER)}\s*(.*?)\s*{re.escape(PATCH_END_MARKER)}'
        match = re.search(pattern, response, re.DOTALL)
        
        if match:
            return match.group(1).strip()
        
        # Markers not found - this is a format error (syntax_fail, NOT security violation)
        # Requirements: 17.3, 17.7 - treat as syntax error, retry eligible
        logger.warning(f"Patch markers not found in LLM response. Response: {response[:200]}...")
        raise RuntimeError(
            f"format_error(marker_missing): LLM response missing required markers "
            f"({PATCH_BEGIN_MARKER} / {PATCH_END_MARKER}). "
            f"This is a syntax error - retry eligible."
        )

    def render_with_repair(
        self,
        op: PatchOperation,
        max_retries: int = 3,
        max_contract_retries: int = 2,
    ) -> str:
        """
        Render operation with feedback-driven repair loop.
        
        UNIFIED RENDERING PATH (Must Fix 2):
        Uses template-guided rendering when template_retriever is available.
        
        Implements the Generate → Guard → Syntax Check → Retry/Return sequence:
        1. Generate config using template-guided or neural render
        2. Handle MissingSlotsDiff by appending to prompt (Must Fix 3)
        3. Call ConstraintGuard.verify_contract() with Severity classification:
           - HARD_STOP: Raise SecurityViolationError immediately (NO RETRY)
           - RETRYABLE: Append ContractDiff to prompt and retry (up to max_contract_retries)
           (SKIPPED if no_guard=True)
        4. Check syntax using Verifier
        5. If syntax error -> append error to prompt -> retry (up to max_retries)
        6. If success -> return validated config
        
        Args:
            op: The PatchOperation to render
            max_retries: Maximum syntax error retries (default 3)
            max_contract_retries: Maximum contract failure retries (default 2)
                                 Requirement 19.1: Must be >= 2
        
        Returns:
            Validated configuration snippet
        
        Raises:
            SecurityViolationError: If HARD_STOP severity detected (NO RETRY - propagates immediately)
                                   Only raised when no_guard=False
            SynthesisError: If max retries exhausted
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.4, 6.6, 7.2, 7.3, 7.5, 7.7, 7.8, 7.9, 19.1, 19.2
        """
        attempts: List[Dict[str, Any]] = []
        error_context = ""
        contract_diff_context = ""
        missing_slots_context = ""
        contract_retries = 0  # Track contract retry count separately
        
        # Build contract for this operation (immutable during retry)
        contract_builder = ContractBuilder()
        contract = contract_builder.build_contract(op)
        
        # Audit logger for retry tracking (Requirement 7.9)
        audit_logger = logging.getLogger("pathdelta.security.audit")
        
        # Check for missing slots upfront if using template-guided rendering
        if self.template_retriever is not None and not self.pure_llm_baseline:
            operation_type = self._get_operation_type_from_template(op.template)
            mechanism = "bgp" if "bgp" in (op.template or "").lower() else "ospf"
            
            template, _ = self.template_retriever.retrieve(
                operation_type=operation_type,
                mechanism=mechanism,
                config_style="frrconf",
            )
            
            # Check for missing slots
            _, missing_diff = self._fill_skeleton(
                skeleton=template.skeleton,
                params=op.params,
                template=template,
            )
            
            if missing_diff:
                # Must Fix 3: Append MissingSlotsDiff to prompt context
                missing_slots_context = missing_diff.to_prompt_text()
                logger.warning(
                    f"Missing slots detected for template {template.id}: "
                    f"{missing_diff.missing_slots}"
                )
        
        for attempt in range(max_retries + 1):
            # Step A: Generate config using template-guided or neural render
            try:
                if error_context or contract_diff_context or missing_slots_context:
                    # Append error feedback to prompt for retry (Requirement 3.1, 3.2, 7.7)
                    combined_context = ""
                    if missing_slots_context:
                        combined_context = missing_slots_context
                    if contract_diff_context:
                        if combined_context:
                            combined_context += "\n\n" + contract_diff_context
                        else:
                            combined_context = contract_diff_context
                    if error_context:
                        if combined_context:
                            combined_context += "\n\n" + error_context
                        else:
                            combined_context = error_context
                    patch_text = self._neural_render_with_feedback(op, combined_context)
                else:
                    # Use template-guided render if available
                    if self.template_retriever is not None and not self.pure_llm_baseline:
                        patch_text = self._template_guided_render(op)
                    else:
                        patch_text = self._neural_render(op)
            except Exception as e:
                attempts.append({
                    "attempt": attempt + 1,
                    "stage": "generation",
                    "error": str(e),
                })
                logger.warning(f"Generation failed (attempt {attempt + 1}): {e}")
                # Requirement 3.5: Log error and retry count
                audit_logger.info(
                    "Generation attempt failed",
                    extra={
                        "attempt": attempt + 1,
                        "op_id": contract.op_id,
                        "device": op.device,
                        "error": str(e),
                    },
                )
                continue

            patch_text = self._sanitize_patch_for_operation(op, patch_text)
            
            # Step B: ConstraintGuard verification with Severity classification
            # HARD_STOP -> SecurityViolationError (no retry)
            # RETRYABLE -> Append ContractDiff and retry
            # Requirements: 7.7, 7.8
            if self.no_guard:
                # Ablation: Skip ConstraintGuard verification
                logger.warning("ABLATION: Unsafe Mode - ConstraintGuard verification DISABLED")
            else:
                severity, diff = self.guard.verify_contract(
                    patch_text=patch_text,
                    contract=contract,
                )
                
                if diff is not None:
                    # Contract violation detected
                    if severity == Severity.HARD_STOP:
                        # Requirement 7.8: HARD_STOP -> SecurityViolationError immediately
                        audit_logger.error(
                            "HARD_STOP: Dangerous command detected - no retry allowed",
                            extra={
                                "attempt": attempt + 1,
                                "op_id": contract.op_id,
                                "device": op.device,
                                "contract_diff": diff.to_dict(),
                                "severity": "HARD_STOP",
                            },
                        )
                        raise SecurityViolationError(
                            message=f"HARD_STOP: Dangerous command detected - {diff.to_prompt_text()}",
                            expected={"contract": "no dangerous commands"},
                            actual={"violations": diff.to_dict()},
                            operation_id=contract.op_id,
                            device=op.device,
                        )
                    
                    elif severity == Severity.RETRYABLE:
                        # Requirement 19.1: RETRYABLE -> Retry up to max_contract_retries
                        if contract_retries < max_contract_retries:
                            contract_retries += 1
                            attempts.append({
                                "attempt": attempt + 1,
                                "stage": "contract",
                                "error": diff.to_prompt_text(),
                                "patch_text": patch_text,
                                "severity": "RETRYABLE",
                                "contract_retry": contract_retries,
                            })
                            
                            # Requirement 7.9: Audit log for retry
                            audit_logger.warning(
                                "RETRYABLE contract violation - will retry",
                                extra={
                                    "attempt": attempt + 1,
                                    "op_id": contract.op_id,
                                    "device": op.device,
                                    "contract_diff": diff.to_dict(),
                                    "severity": "RETRYABLE",
                                    "contract_retry": contract_retries,
                                    "max_contract_retries": max_contract_retries,
                                },
                            )
                            
                            logger.warning(
                                f"Contract violation (RETRYABLE, retry {contract_retries}/{max_contract_retries}): "
                                f"{diff.to_prompt_text()[:200]}..."
                            )
                            
                            # Build contract diff context for next attempt
                            contract_diff_context = diff.to_prompt_text()
                            continue
                        else:
                            # Max contract retries exhausted - raise SynthesisError
                            audit_logger.error(
                                "Contract retry exhausted",
                                extra={
                                    "op_id": contract.op_id,
                                    "device": op.device,
                                    "contract_retries": contract_retries,
                                    "max_contract_retries": max_contract_retries,
                                    "last_diff": diff.to_dict(),
                                },
                            )
                            raise SynthesisError(
                                message=f"Contract retry exhausted after {max_contract_retries} attempts: {diff.to_prompt_text()[:200]}",
                                attempts=attempts,
                            )
            
            # Step C: Syntax verification (always runs if verifier configured)
            if self.verifier is None:
                # No verifier configured, skip syntax check
                logger.warning("No verifier configured, skipping syntax check")
                return patch_text
            
            is_valid, error_msg, fixed_content = self.verifier.verify_syntax_string(patch_text)
            if fixed_content:
                patch_text = fixed_content
            
            if is_valid:
                # Step E: Success - return validated config (Requirement 7.5)
                logger.info(f"Synthesis succeeded for {op.device} (attempt {attempt + 1})")
                audit_logger.info(
                    "Synthesis succeeded",
                    extra={
                        "attempt": attempt + 1,
                        "op_id": contract.op_id,
                        "device": op.device,
                        "total_attempts": len(attempts) + 1,
                    },
                )
                return patch_text
            
            # Step D: Syntax error - prepare for retry (Requirement 3.1, 3.2, 3.5)
            attempts.append({
                "attempt": attempt + 1,
                "stage": "syntax",
                "error": error_msg,
                "patch_text": patch_text,
            })
            logger.warning(
                f"Syntax error (attempt {attempt + 1}/{max_retries + 1}): {error_msg}"
            )
            
            # Requirement 7.9: Audit log for syntax error
            audit_logger.warning(
                "Syntax error - will retry",
                extra={
                    "attempt": attempt + 1,
                    "op_id": contract.op_id,
                    "device": op.device,
                    "error": error_msg,
                },
            )
            
            # Build error context for next attempt (Requirement 3.1, 3.2)
            error_context = self._build_error_feedback(patch_text, error_msg)
        
        # Requirement 3.3, 3.4: Max retries exhausted - raise SynthesisError
        audit_logger.error(
            "Synthesis failed - max retries exhausted",
            extra={
                "op_id": contract.op_id,
                "device": op.device,
                "total_attempts": len(attempts),
                "attempts": attempts,
            },
        )
        raise SynthesisError(
            message=f"Synthesis failed after {max_retries + 1} attempts",
            attempts=attempts,
        )

    def _neural_render_with_feedback(
        self,
        op: PatchOperation,
        error_context: str,
    ) -> str:
        """
        Render with error feedback appended to prompt.
        
        Uses the same style retrieval as _neural_render but appends
        the previous error context to help the LLM fix the issue.
        
        Args:
            op: The PatchOperation to render
            error_context: Previous error context for feedback
        
        Returns:
            Generated configuration snippet (extracted from between markers)
        
        Requirements: 3.1, 3.2, 17.1, 17.2, 17.3
        """
        # Get query_type and examples as in _neural_render
        query_type = self._template_to_query_type(op.template)
        examples = self.retrieve_style_examples(query_type)
        
        if self.pure_llm_baseline:
            prompt = BASELINE_NEURAL_PROMPT_TEMPLATE.format(
                examples=examples or "(No style examples available)",
                operation_type=self._get_operation_type_from_template(op.template),
                template_name=op.template or "unknown",
                constraints=json.dumps(op.params, indent=2),
            )
        else:
            required_commands = self._build_required_commands(op)
            prompt = NEURAL_PROMPT_TEMPLATE.format(
                examples=examples or "(No style examples available)",
                constraints=json.dumps(op.params, indent=2),
                required_commands=required_commands,
            )
        
        # Append error feedback (Requirement 3.1, 3.2)
        prompt += f"""

**⚠️ CRITICAL: The previous configuration FAILED validation.**

**ERROR REPORT:**
{error_context}

**FIX INSTRUCTIONS:**
1.  **ANALYZE**: Read the error message carefully. Identify WHICH command caused the rejection.
2.  **CORRECT**: Fix the syntax error or constraint violation.
3.  **PRESERVE**: You MUST maintain the original naming convention and indentation style. Do NOT revert to default values unless necessary for the verification.

**OUTPUT:**
Generate the fixed configuration wrapped in ###BEGIN_PATCH### and ###END_PATCH###."""
        
        driver = get_driver()
        response = driver.chat_completion(
            system_prompt="You are a network configuration expert.",
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=2048,
        )
        
        # Extract patch from between markers (Requirements: 17.2, 17.3)
        return self._extract_patch_from_markers(response)

    def _build_error_feedback(self, patch_text: str, error_msg: str) -> str:
        """
        Build error feedback string for retry prompt.
        
        Formats the previous patch_text and error message for inclusion
        in the retry prompt.
        
        Args:
            patch_text: The generated patch text that failed
            error_msg: The error message from syntax verification
        
        Returns:
            Formatted error feedback string
        
        Requirements: 3.1, 3.2
        """
        return f"```\n{patch_text}\n```\n\nError: {error_msg}"

    def _neural_render_with_contract_repair(self, op: PatchOperation) -> str:
        """
        Render with minimal contract repair prompt.
        
        Used after CONTRACT_FAIL to emphasize required commands.
        This is a more focused prompt that strips away style examples
        and emphasizes the exact commands that must be included.
        
        Args:
            op: The PatchOperation to render
            
        Returns:
            Generated configuration snippet (extracted from between markers)
        """
        required_commands = self._build_required_commands(op)
        
        repair_prompt = f"""You MUST output EXACTLY the REQUIRED COMMANDS lines and nothing else.

**REQUIRED COMMANDS (must include semantically equivalent lines):**
{required_commands}

**CRITICAL FRR SYNTAX RULES:**
1. **BGP Commands:** MUST be wrapped inside `router bgp <AS>` block. Never output naked `neighbor` commands.
2. **Route Maps/Prefix Lists:** MUST be defined in global mode (outside router bgp).
3. **Indentation:** Use 1 space for commands inside `router bgp`.

**OUTPUT FORMAT:**
You MUST wrap your configuration output between these exact markers:
###BEGIN_PATCH###
<your configuration here>
###END_PATCH###

Output ONLY the configuration code between the markers. Do not include any explanation."""
        
        driver = get_driver()
        response = driver.chat_completion(
            system_prompt="You are a network configuration expert. Follow instructions exactly.",
            user_prompt=repair_prompt,
            temperature=0.0,
            max_tokens=2048,
        )
        
        return self._extract_patch_from_markers(response)

    def render_with_repair_tracked(
        self,
        op: PatchOperation,
        max_retries: int = 3,
        max_contract_retries: int = 2,
        initial_feedback: str = "",
    ) -> tuple[str, int, List[Dict[str, Any]], bool]:
        """
        Render operation with feedback-driven repair loop, returning retry tracking info.
        
        Supports both deterministic and neural rendering paths:
        - DETERMINISTIC ops: Render without LLM, verify with guard, return llm_used=False
        - NEURAL ops: Render with LLM, verify_contract with Severity, contract retry
        
        Contract retry logic (for NEURAL ops only):
        - RETRYABLE: Retry up to max_contract_retries with ContractDiff feedback
        - HARD_STOP: NO retry, raise SecurityViolationError immediately
        
        Args:
            op: The PatchOperation to render
            max_retries: Maximum syntax error retries (default 3)
            max_contract_retries: Maximum contract failure retries (default 2)
            initial_feedback: External verifier feedback to seed the first retry prompt
        
        Returns:
            Tuple of (config_text, retry_count, attempt_history, llm_used)
            - config_text: Validated configuration snippet
            - retry_count: Number of retries (0 = success on first try)
            - attempt_history: List of attempt details for failed attempts
            - llm_used: True if LLM was used, False for deterministic rendering
        
        Raises:
            SecurityViolationError: If HARD_STOP severity detected (NO RETRY - propagates immediately)
            SynthesisError: If max retries exhausted
        
        Requirements: 7.1, 7.2, 7.3, 7.4, 7.7, 7.8, 7.9
        """
        # Baseline modes force all operations through the LLM path.
        op_type = OperationType.NEURAL if self.pure_llm_baseline else classify_operation(op)
        
        # Build contract for this operation (immutable during retry)
        contract_builder = ContractBuilder()
        contract = contract_builder.build_contract(op)
        
        # Audit logger for retry tracking (Requirement 7.9)
        audit_logger = logging.getLogger("pathdelta.security.audit")
        
        if op_type == OperationType.DETERMINISTIC:
            # === DETERMINISTIC PATH ===
            # Render without LLM, verify with guard, return llm_used=False
            template = (op.template or "").lower()
            
            if "neighbor" in template:
                patch_text = self._render_neighbor_binding_deterministic(op)
            elif "maximum_paths" in template or "maximum-paths" in template:
                patch_text = self._render_maximum_paths_deterministic(op)
            elif "interface_cost" in template:
                patch_text = self._render_ospf_cost_deterministic(op)
            else:
                patch_text = self._render_prefix_list_deterministic(op)
            
            logger.info(f"Deterministic rendering for {op.device}: {op.template}")
            
            # Still verify with contract (unless no_guard)
            if not self.no_guard:
                severity, diff = self.guard.verify_contract(
                    patch_text=patch_text,
                    contract=contract,
                )
                
                if diff is not None:
                    if severity == Severity.HARD_STOP:
                        # This should never happen for deterministic rendering
                        logger.error(
                            f"Deterministic render triggered HARD_STOP on {op.device}: "
                            f"This indicates a bug in the deterministic renderer."
                        )
                        raise SecurityViolationError(
                            message=f"HARD_STOP in deterministic render: {diff.to_prompt_text()}",
                            expected={"contract": "no dangerous commands"},
                            actual={"violations": diff.to_dict()},
                            operation_id=contract.op_id,
                            device=op.device,
                        )
                    else:
                        # RETRYABLE violation in deterministic render - this is a bug
                        logger.error(
                            f"Deterministic render failed contract on {op.device}: "
                            f"This indicates a bug in the deterministic renderer."
                        )
                        raise SecurityViolationError(
                            message=f"Contract violation in deterministic render: {diff.to_prompt_text()}",
                            expected={"contract": "valid deterministic output"},
                            actual={"violations": diff.to_dict()},
                            operation_id=contract.op_id,
                            device=op.device,
                        )
            
            # Return with llm_used=False
            return patch_text, 0, [], False
        
        # === NEURAL PATH ===
        # Render with LLM, verify_contract with Severity, contract retry
        attempts: List[Dict[str, Any]] = []
        error_context = initial_feedback
        contract_diff_context = ""
        contract_retries = 0
        
        for attempt in range(max_retries + 1):
            # Step A: Generate config using LLM
            try:
                if error_context or contract_diff_context:
                    # Append error feedback to prompt for retry (Requirement 7.1, 7.7)
                    combined_context = ""
                    if contract_diff_context:
                        combined_context = contract_diff_context
                    if error_context:
                        if combined_context:
                            combined_context += "\n\n" + error_context
                        else:
                            combined_context = error_context
                    patch_text = self._neural_render_with_feedback(op, combined_context)
                else:
                    patch_text = self._neural_render(op)
            except Exception as e:
                attempts.append({
                    "attempt": attempt + 1,
                    "stage": "generation",
                    "error": str(e),
                })
                logger.warning(f"Generation failed (attempt {attempt + 1}): {e}")
                audit_logger.info(
                    "Generation attempt failed",
                    extra={
                        "attempt": attempt + 1,
                        "op_id": contract.op_id,
                        "device": op.device,
                        "error": str(e),
                    },
                )
                continue

            patch_text = self._sanitize_patch_for_operation(op, patch_text)
            
            # Step B: ConstraintGuard verification with Severity classification
            # HARD_STOP -> SecurityViolationError (no retry)
            # RETRYABLE -> Append ContractDiff and retry (up to max_contract_retries)
            # Requirements: 7.7, 7.8
            if self.no_guard:
                logger.warning("ABLATION: Unsafe Mode - ConstraintGuard verification DISABLED")
            else:
                severity, diff = self.guard.verify_contract(
                    patch_text=patch_text,
                    contract=contract,
                )
                
                if diff is not None:
                    if severity == Severity.HARD_STOP:
                        # Requirement 7.8: HARD_STOP -> SecurityViolationError immediately
                        audit_logger.error(
                            "HARD_STOP: Dangerous command detected - no retry allowed",
                            extra={
                                "attempt": attempt + 1,
                                "op_id": contract.op_id,
                                "device": op.device,
                                "contract_diff": diff.to_dict(),
                                "severity": "HARD_STOP",
                            },
                        )
                        raise SecurityViolationError(
                            message=f"HARD_STOP: Dangerous command detected - {diff.to_prompt_text()}",
                            expected={"contract": "no dangerous commands"},
                            actual={"violations": diff.to_dict()},
                            operation_id=contract.op_id,
                            device=op.device,
                        )
                    
                    elif severity == Severity.RETRYABLE:
                        # Requirement 7.7: RETRYABLE -> Retry up to max_contract_retries
                        if contract_retries < max_contract_retries:
                            contract_retries += 1
                            attempts.append({
                                "attempt": attempt + 1,
                                "stage": "contract",
                                "error": diff.to_prompt_text(),
                                "patch_text": patch_text,
                                "severity": "RETRYABLE",
                            })
                            
                            # Requirement 7.9: Audit log for retry
                            audit_logger.warning(
                                "RETRYABLE contract violation - will retry",
                                extra={
                                    "attempt": attempt + 1,
                                    "op_id": contract.op_id,
                                    "device": op.device,
                                    "contract_diff": diff.to_dict(),
                                    "severity": "RETRYABLE",
                                    "contract_retry": contract_retries,
                                    "max_contract_retries": max_contract_retries,
                                },
                            )
                            
                            logger.warning(
                                f"Contract violation (RETRYABLE, retry {contract_retries}/{max_contract_retries}): "
                                f"{diff.to_prompt_text()[:200]}..."
                            )
                            
                            # Build contract diff context for next attempt
                            contract_diff_context = diff.to_prompt_text()
                            continue
                        else:
                            # Max contract retries exhausted
                            audit_logger.error(
                                "Contract retry exhausted",
                                extra={
                                    "op_id": contract.op_id,
                                    "device": op.device,
                                    "contract_retries": contract_retries,
                                    "max_contract_retries": max_contract_retries,
                                    "last_diff": diff.to_dict(),
                                },
                            )
                            raise SynthesisError(
                                message=f"Contract retry exhausted after {max_contract_retries} attempts: {diff.to_prompt_text()[:200]}",
                                attempts=attempts,
                            )
            
            # Step C: Syntax verification (always runs if verifier configured)
            if self.verifier is None:
                # No verifier configured, skip syntax check
                logger.warning("No verifier configured, skipping syntax check")
                # Return with llm_used=True
                return patch_text, len(attempts), attempts, True
            
            is_valid, error_msg, fixed_content = self.verifier.verify_syntax_string(patch_text)
            if fixed_content:
                patch_text = fixed_content
            
            if is_valid:
                # Success - return validated config with retry tracking
                logger.info(f"Synthesis succeeded for {op.device} (attempt {attempt + 1})")
                audit_logger.info(
                    "Synthesis succeeded",
                    extra={
                        "attempt": attempt + 1,
                        "op_id": contract.op_id,
                        "device": op.device,
                        "total_attempts": len(attempts) + 1,
                        "llm_used": True,
                    },
                )
                return patch_text, len(attempts), attempts, True
            
            # Step D: Syntax error - prepare for retry (Requirement 7.1, 7.2)
            attempts.append({
                "attempt": attempt + 1,
                "stage": "syntax",
                "error": error_msg,
                "patch_text": patch_text,
            })
            logger.warning(
                f"Syntax error (attempt {attempt + 1}/{max_retries + 1}): {error_msg}"
            )
            
            # Requirement 7.9: Audit log for syntax error
            audit_logger.warning(
                "Syntax error - will retry",
                extra={
                    "attempt": attempt + 1,
                    "op_id": contract.op_id,
                    "device": op.device,
                    "error": error_msg,
                },
            )
            
            # Build error context for next attempt
            error_context = self._build_error_feedback(patch_text, error_msg)
        
        # Requirement 7.4: Max retries exhausted - raise SynthesisError with attempt history
        audit_logger.error(
            "Synthesis failed - max retries exhausted",
            extra={
                "op_id": contract.op_id,
                "device": op.device,
                "total_attempts": len(attempts),
                "attempts": attempts,
            },
        )
        raise SynthesisError(
            message=f"Synthesis failed after {max_retries + 1} attempts",
            attempts=attempts,
        )

    def _render_maximum_paths_deterministic(self, op: PatchOperation) -> str:
        """Render `maximum-paths` inside the target BGP process."""
        params = op.params
        asn = params.get("asn") or params.get("local_as")
        max_paths = params.get("maximum_paths", 2)

        if asn in (None, ""):
            return f" maximum-paths {max_paths}"

        return f"router bgp {asn}\n maximum-paths {max_paths}"

    def _render_ospf_cost_deterministic(self, op: PatchOperation) -> str:
        """Render a minimal OSPF interface cost patch without LLM drift."""
        params = op.params
        interface_name = params.get("interface_name", params.get("interface", "eth0"))
        cost = params.get("cost", 10)
        return f"interface {interface_name}\n ip ospf cost {cost}"


class MultiPlanRenderer:
    """
    Renders multiple PatchPlans and aggregates results by device.
    """
    
    def __init__(self, template_dir: Optional[str] = None):
        self.renderer = ConfigRenderer(template_dir)
    
    def render_all(self, plans: List[PatchPlan]) -> Dict[str, str]:
        """
        Render all plans and aggregate by device.
        
        Args:
            plans: List of PatchPlans to render
        
        Returns:
            Dict mapping device_name -> aggregated configuration
        """
        aggregated: Dict[str, List[str]] = {}
        
        for plan in plans:
            rendered = self.renderer.render_plan(plan)
            for device, config in rendered.items():
                if device not in aggregated:
                    aggregated[device] = []
                aggregated[device].append(config)
        
        # Join configs for each device
        return {
            device: "\n".join(configs)
            for device, configs in aggregated.items()
        }


def render_patch_plan(
    plan: PatchPlan,
    template_dir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Convenience function to render a patch plan.
    
    Args:
        plan: The PatchPlan to render
        template_dir: Optional custom template directory
    
    Returns:
        Dict mapping device_name -> rendered configuration
    """
    renderer = ConfigRenderer(template_dir)
    return renderer.render_plan(plan)
