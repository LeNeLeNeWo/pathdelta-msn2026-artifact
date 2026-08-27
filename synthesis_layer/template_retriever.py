"""
Template Retriever for Template-RAG System.

Implements three-stage template retrieval:
1. Deterministic key matching: (operation_type, mechanism, config_style)
2. Tag similarity scoring: Jaccard similarity on tags
3. Generic fallback: Guaranteed fallback template

GUARANTEE: retrieve() NEVER returns None - always falls back to fallback template.

Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 5.2, 5.3, 5.4
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .template_library import TemplateLibrary
from .template_schema import TemplateDefinition


logger = logging.getLogger(__name__)


class RetrievalMethod(Enum):
    """Template retrieval method used."""
    DETERMINISTIC = "deterministic"
    TAG_SIMILARITY = "tag_similarity"
    FALLBACK = "fallback"


@dataclass
class RetrievalStats:
    """
    Statistics for template retrieval.
    
    Tracks retrieval counts by method and operation type for
    generating TEMPLATE_SYSTEM_REPORT.md.
    
    Requirements: 9.1, 9.2, 9.3
    """
    total_retrievals: int = 0
    deterministic_hits: int = 0
    tag_similarity_hits: int = 0
    fallback_hits: int = 0
    
    # Per-operation-type breakdown: {op_type: {method: count}}
    by_operation_type: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    def record(self, operation_type: str, method: RetrievalMethod) -> None:
        """
        Record a retrieval event.
        
        Args:
            operation_type: The operation type that was queried
            method: The retrieval method that succeeded
        """
        self.total_retrievals += 1
        
        if method == RetrievalMethod.DETERMINISTIC:
            self.deterministic_hits += 1
        elif method == RetrievalMethod.TAG_SIMILARITY:
            self.tag_similarity_hits += 1
        elif method == RetrievalMethod.FALLBACK:
            self.fallback_hits += 1
        
        # Update per-operation-type breakdown
        if operation_type not in self.by_operation_type:
            self.by_operation_type[operation_type] = {
                "deterministic": 0,
                "tag_similarity": 0,
                "fallback": 0,
            }
        
        self.by_operation_type[operation_type][method.value] += 1
    
    def to_report(self) -> str:
        """
        Generate TEMPLATE_SYSTEM_REPORT.md content.
        
        Returns:
            Markdown-formatted report string
        """
        lines = [
            "# Template System Report",
            "",
            "## Overall Statistics",
            "",
            f"- **Total Retrievals**: {self.total_retrievals}",
        ]
        
        if self.total_retrievals > 0:
            det_rate = (self.deterministic_hits / self.total_retrievals) * 100
            tag_rate = (self.tag_similarity_hits / self.total_retrievals) * 100
            fb_rate = (self.fallback_hits / self.total_retrievals) * 100
            
            lines.extend([
                f"- **Deterministic Hit Rate**: {det_rate:.1f}% ({self.deterministic_hits} hits)",
                f"- **Tag Similarity Hit Rate**: {tag_rate:.1f}% ({self.tag_similarity_hits} hits)",
                f"- **Fallback Rate**: {fb_rate:.1f}% ({self.fallback_hits} hits)",
            ])
        else:
            lines.extend([
                "- **Deterministic Hit Rate**: N/A (no retrievals)",
                "- **Tag Similarity Hit Rate**: N/A (no retrievals)",
                "- **Fallback Rate**: N/A (no retrievals)",
            ])
        
        lines.extend([
            "",
            "## Per-Operation-Type Breakdown",
            "",
        ])
        
        if self.by_operation_type:
            lines.append("| Operation Type | Deterministic | Tag Similarity | Fallback | Total |")
            lines.append("|----------------|---------------|----------------|----------|-------|")
            
            for op_type, counts in sorted(self.by_operation_type.items()):
                det = counts.get("deterministic", 0)
                tag = counts.get("tag_similarity", 0)
                fb = counts.get("fallback", 0)
                total = det + tag + fb
                lines.append(f"| {op_type} | {det} | {tag} | {fb} | {total} |")
        else:
            lines.append("*No retrievals recorded yet.*")
        
        lines.append("")
        return "\n".join(lines)
    
    def reset(self) -> None:
        """Reset all statistics to zero."""
        self.total_retrievals = 0
        self.deterministic_hits = 0
        self.tag_similarity_hits = 0
        self.fallback_hits = 0
        self.by_operation_type.clear()


class TemplateRetriever:
    """
    Three-stage template retriever.
    
    Retrieval stages:
    1. Deterministic key matching: (operation_type, mechanism, config_style)
    2. Tag similarity scoring: Jaccard similarity on tags
    3. Generic fallback: Guaranteed fallback template
    
    GUARANTEE: retrieve() NEVER returns None - always falls back to fallback template.
    
    Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 5.2, 5.3, 5.4
    """
    
    def __init__(
        self,
        library: TemplateLibrary,
        similarity_threshold: float = 0.3,
    ):
        """
        Initialize retriever.
        
        Args:
            library: TemplateLibrary instance
            similarity_threshold: Minimum similarity score for tag matching (default 0.3)
        """
        self.library = library
        self.similarity_threshold = similarity_threshold
        self._stats = RetrievalStats()
    
    def retrieve(
        self,
        operation_type: str,
        mechanism: str,
        config_style: str = "frrconf",
        query_tags: Optional[List[str]] = None,
    ) -> Tuple[TemplateDefinition, RetrievalMethod]:
        """
        Retrieve best matching template.
        
        Three-stage retrieval process:
        1. Deterministic key matching using (operation_type, mechanism, config_style)
        2. Tag similarity scoring using Jaccard similarity
        3. Fallback to generic template for operation_type
        
        GUARANTEE: This method NEVER returns None. It always returns a valid
        TemplateDefinition, falling back to the generic fallback template if
        no better match is found.
        
        Args:
            operation_type: Operation type (PREFIX_LIST, ROUTE_MAP, etc.)
            mechanism: Protocol mechanism (bgp, ospf)
            config_style: Configuration style (default "frrconf")
            query_tags: Optional tags for similarity matching
        
        Returns:
            Tuple of (template, retrieval_method)
            retrieval_method is one of: DETERMINISTIC, TAG_SIMILARITY, FALLBACK
        
        Requirements: 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 5.2, 5.3
        """
        # Stage 1: Deterministic key matching
        template = self._deterministic_match(operation_type, mechanism, config_style)
        if template is not None:
            logger.debug(
                f"Deterministic match for ({operation_type}, {mechanism}, {config_style}): "
                f"{template.id}"
            )
            self._stats.record(operation_type, RetrievalMethod.DETERMINISTIC)
            return template, RetrievalMethod.DETERMINISTIC
        
        # Stage 2: Tag similarity matching
        if query_tags:
            template = self._tag_similarity_match(operation_type, query_tags)
            if template is not None:
                logger.debug(
                    f"Tag similarity match for {operation_type} with tags {query_tags}: "
                    f"{template.id}"
                )
                self._stats.record(operation_type, RetrievalMethod.TAG_SIMILARITY)
                return template, RetrievalMethod.TAG_SIMILARITY
        
        # Stage 3: Fallback
        template = self.library.get_fallback_template(operation_type)
        if template is not None:
            logger.warning(
                f"Using fallback template for ({operation_type}, {mechanism}, {config_style}): "
                f"{template.id}"
            )
            self._stats.record(operation_type, RetrievalMethod.FALLBACK)
            return template, RetrievalMethod.FALLBACK
        
        # If no fallback exists for this operation_type, try to find ANY fallback
        # This ensures we NEVER return None
        all_op_types = self.library.list_operation_types()
        for op_type in all_op_types:
            fallback = self.library.get_fallback_template(op_type)
            if fallback is not None:
                logger.warning(
                    f"No fallback for {operation_type}, using fallback from {op_type}: "
                    f"{fallback.id}"
                )
                self._stats.record(operation_type, RetrievalMethod.FALLBACK)
                return fallback, RetrievalMethod.FALLBACK
        
        # Last resort: get any template from the library
        all_templates = []
        for op_type in all_op_types:
            all_templates.extend(self.library.get_templates_by_operation_type(op_type))
        
        if all_templates:
            template = all_templates[0]
            logger.warning(
                f"No fallback available, using first available template: {template.id}"
            )
            self._stats.record(operation_type, RetrievalMethod.FALLBACK)
            return template, RetrievalMethod.FALLBACK
        
        # This should never happen if the library is properly configured
        raise RuntimeError(
            f"Template library is empty - cannot retrieve template for {operation_type}"
        )
    
    def _deterministic_match(
        self,
        operation_type: str,
        mechanism: str,
        config_style: str,
    ) -> Optional[TemplateDefinition]:
        """
        Stage 1: Deterministic key matching.
        
        Matches templates by exact (operation_type, mechanism, config_style) tuple.
        If multiple templates match, returns the one with highest priority.
        
        Args:
            operation_type: Operation type to match
            mechanism: Protocol mechanism to match
            config_style: Configuration style to match
        
        Returns:
            Best matching TemplateDefinition, or None if no match
        
        Requirements: 3.1, 3.2, 3.4
        """
        candidates = self.library.get_templates_by_operation_type(operation_type)
        
        # Filter by mechanism and config_style, excluding fallbacks
        matches = [
            t for t in candidates
            if t.mechanism == mechanism
            and t.config_style == config_style
            and not t.is_fallback
        ]
        
        if not matches:
            return None
        
        # Templates are already sorted by priority (descending)
        # Return the highest priority match
        return matches[0]
    
    def _tag_similarity_match(
        self,
        operation_type: str,
        query_tags: List[str],
    ) -> Optional[TemplateDefinition]:
        """
        Stage 2: Tag similarity scoring.
        
        Computes Jaccard similarity between query_tags and each template's tags.
        Returns the template with highest similarity if above threshold.
        
        Args:
            operation_type: Operation type to filter by
            query_tags: Tags to match against
        
        Returns:
            Best matching TemplateDefinition, or None if no match above threshold
        
        Requirements: 4.1, 4.2, 4.3, 4.4
        """
        candidates = self.library.get_templates_by_operation_type(operation_type)
        
        # Exclude fallback templates from similarity matching
        candidates = [t for t in candidates if not t.is_fallback]
        
        if not candidates:
            return None
        
        # Compute similarity scores
        scored = []
        for template in candidates:
            similarity = self._jaccard_similarity(query_tags, template.tags)
            scored.append((template, similarity))
        
        # Sort by similarity (descending), then by priority (descending)
        scored.sort(key=lambda x: (x[1], x[0].priority), reverse=True)
        
        best_template, best_score = scored[0]
        
        if best_score >= self.similarity_threshold:
            logger.debug(
                f"Tag similarity match: {best_template.id} with score {best_score:.3f}"
            )
            return best_template
        
        logger.debug(
            f"Best tag similarity score {best_score:.3f} below threshold "
            f"{self.similarity_threshold}"
        )
        return None
    
    def _jaccard_similarity(
        self,
        tags_a: List[str],
        tags_b: List[str],
    ) -> float:
        """
        Compute Jaccard similarity between two tag sets.
        
        Jaccard similarity = |A ∩ B| / |A ∪ B|
        
        Args:
            tags_a: First tag list
            tags_b: Second tag list
        
        Returns:
            Similarity score between 0.0 and 1.0
            Returns 0.0 if both sets are empty
        
        Requirements: 4.2
        """
        set_a = set(tags_a)
        set_b = set(tags_b)
        
        # Handle empty sets
        if not set_a and not set_b:
            return 0.0
        
        intersection = set_a & set_b
        union = set_a | set_b
        
        if not union:
            return 0.0
        
        return len(intersection) / len(union)
    
    def get_stats(self) -> RetrievalStats:
        """
        Get retrieval statistics for reporting.
        
        Returns:
            RetrievalStats object with current statistics
        """
        return self._stats
    
    def reset_stats(self) -> None:
        """Reset retrieval statistics."""
        self._stats.reset()
