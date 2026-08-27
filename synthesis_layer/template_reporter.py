"""
Template System Reporter for Template-RAG System.

Generates TEMPLATE_SYSTEM_REPORT.md with template usage statistics
for analysis and academic paper inclusion.

Requirements: 9.1, 9.2, 9.3, 9.4
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .template_retriever import RetrievalStats, RetrievalMethod


@dataclass
class TemplateUsageRecord:
    """
    Record of template usage for a specific template.
    
    Tracks hit counts and retry statistics for reporting.
    """
    template_id: str
    operation_type: str
    hit_count: int = 0
    retry_count: int = 0
    total_retries: int = 0
    
    @property
    def avg_retry_count(self) -> float:
        """Calculate average retry count per hit."""
        if self.hit_count == 0:
            return 0.0
        return self.total_retries / self.hit_count


@dataclass
class ExtendedRetrievalStats:
    """
    Extended statistics for template retrieval with per-template tracking.
    
    Extends RetrievalStats with template-level usage records for
    detailed reporting per Requirement 9.2.
    """
    base_stats: RetrievalStats = field(default_factory=RetrievalStats)
    
    # Per-template usage: {template_id: TemplateUsageRecord}
    template_usage: Dict[str, TemplateUsageRecord] = field(default_factory=dict)
    
    # Retry tracking: {operation_type: total_retries}
    retry_counts: Dict[str, int] = field(default_factory=dict)
    
    def record_template_hit(
        self,
        template_id: str,
        operation_type: str,
        retries: int = 0,
    ) -> None:
        """
        Record a template hit with optional retry count.
        
        Args:
            template_id: ID of the template that was used
            operation_type: Operation type for the retrieval
            retries: Number of retries needed for this operation
        """
        if template_id not in self.template_usage:
            self.template_usage[template_id] = TemplateUsageRecord(
                template_id=template_id,
                operation_type=operation_type,
            )
        
        record = self.template_usage[template_id]
        record.hit_count += 1
        record.total_retries += retries
        if retries > 0:
            record.retry_count += 1
        
        # Track retries by operation type
        if operation_type not in self.retry_counts:
            self.retry_counts[operation_type] = 0
        self.retry_counts[operation_type] += retries


def generate_report(
    stats: RetrievalStats,
    extended_stats: Optional[ExtendedRetrievalStats] = None,
    title: str = "Template System Report",
) -> str:
    """
    Generate TEMPLATE_SYSTEM_REPORT.md content.
    
    Creates a markdown-formatted report with:
    - Overall statistics (total_operations, hit rates)
    - Per-operation-type breakdown
    - Per-template usage (if extended_stats provided)
    - Formatted for academic paper inclusion
    
    Args:
        stats: RetrievalStats with basic retrieval statistics
        extended_stats: Optional ExtendedRetrievalStats with per-template data
        title: Report title
    
    Returns:
        Markdown-formatted report string
    
    Requirements: 9.1, 9.2, 9.3, 9.4
    """
    lines: List[str] = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Header
    lines.extend([
        f"# {title}",
        "",
        f"*Generated: {timestamp}*",
        "",
    ])
    
    # Overall Statistics Section
    lines.extend([
        "## Overall Statistics",
        "",
    ])
    
    total = stats.total_retrievals
    lines.append(f"| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total Operations | {total} |")
    
    if total > 0:
        det_rate = (stats.deterministic_hits / total) * 100
        tag_rate = (stats.tag_similarity_hits / total) * 100
        fb_rate = (stats.fallback_hits / total) * 100
        
        lines.append(f"| Deterministic Hit Rate | {det_rate:.1f}% ({stats.deterministic_hits}/{total}) |")
        lines.append(f"| Tag Similarity Hit Rate | {tag_rate:.1f}% ({stats.tag_similarity_hits}/{total}) |")
        lines.append(f"| Fallback Rate | {fb_rate:.1f}% ({stats.fallback_hits}/{total}) |")
    else:
        lines.append("| Deterministic Hit Rate | N/A |")
        lines.append("| Tag Similarity Hit Rate | N/A |")
        lines.append("| Fallback Rate | N/A |")
    
    lines.append("")
    
    # Per-Operation-Type Breakdown
    lines.extend([
        "## Per-Operation-Type Breakdown",
        "",
    ])
    
    if stats.by_operation_type:
        lines.append("| Operation Type | Deterministic | Tag Similarity | Fallback | Total | Fallback % |")
        lines.append("|----------------|---------------|----------------|----------|-------|------------|")
        
        for op_type in sorted(stats.by_operation_type.keys()):
            counts = stats.by_operation_type[op_type]
            det = counts.get("deterministic", 0)
            tag = counts.get("tag_similarity", 0)
            fb = counts.get("fallback", 0)
            op_total = det + tag + fb
            fb_pct = (fb / op_total * 100) if op_total > 0 else 0.0
            
            lines.append(f"| {op_type} | {det} | {tag} | {fb} | {op_total} | {fb_pct:.1f}% |")
    else:
        lines.append("*No retrievals recorded yet.*")
    
    lines.append("")
    
    # Per-Template Usage (if extended stats available)
    if extended_stats and extended_stats.template_usage:
        lines.extend([
            "## Per-Template Usage",
            "",
            "| Template ID | Operation Type | Hit Count | Avg Retries |",
            "|-------------|----------------|-----------|-------------|",
        ])
        
        for template_id in sorted(extended_stats.template_usage.keys()):
            record = extended_stats.template_usage[template_id]
            avg_retries = f"{record.avg_retry_count:.2f}"
            lines.append(
                f"| {record.template_id} | {record.operation_type} | "
                f"{record.hit_count} | {avg_retries} |"
            )
        
        lines.append("")
    
    # Summary for Paper
    lines.extend([
        "## Summary",
        "",
    ])
    
    if total > 0:
        det_rate = (stats.deterministic_hits / total) * 100
        fb_rate = (stats.fallback_hits / total) * 100
        
        lines.extend([
            f"The Template-RAG system processed **{total}** template retrieval operations. ",
            f"Deterministic key matching achieved a **{det_rate:.1f}%** hit rate, ",
            f"while fallback templates were used in **{fb_rate:.1f}%** of cases.",
            "",
        ])
        
        if fb_rate > 20:
            lines.extend([
                "> **Note**: Fallback rate exceeds 20%. Consider adding more specific templates ",
                "> for frequently accessed operation types.",
                "",
            ])
    else:
        lines.append("*No operations recorded for analysis.*")
        lines.append("")
    
    return "\n".join(lines)


def write_report(
    stats: RetrievalStats,
    output_path: str = "TEMPLATE_SYSTEM_REPORT.md",
    extended_stats: Optional[ExtendedRetrievalStats] = None,
    title: str = "Template System Report",
) -> str:
    """
    Generate and write TEMPLATE_SYSTEM_REPORT.md to file.
    
    Args:
        stats: RetrievalStats with basic retrieval statistics
        output_path: Path to write the report (default: TEMPLATE_SYSTEM_REPORT.md)
        extended_stats: Optional ExtendedRetrievalStats with per-template data
        title: Report title
    
    Returns:
        Path to the written report file
    
    Requirements: 9.1, 9.4
    """
    report_content = generate_report(stats, extended_stats, title)
    
    # Ensure parent directory exists
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report_content)
    
    return str(output_file.absolute())


def generate_latex_table(stats: RetrievalStats) -> str:
    """
    Generate LaTeX table for academic paper inclusion.
    
    Args:
        stats: RetrievalStats with retrieval statistics
    
    Returns:
        LaTeX-formatted table string
    
    Requirements: 9.4
    """
    lines: List[str] = []
    
    lines.extend([
        r"\begin{table}[h]",
        r"\centering",
        r"\caption{Template-RAG Retrieval Statistics}",
        r"\label{tab:template-rag-stats}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Operation Type & Deterministic & Tag Similarity & Fallback & Total \\",
        r"\midrule",
    ])
    
    if stats.by_operation_type:
        for op_type in sorted(stats.by_operation_type.keys()):
            counts = stats.by_operation_type[op_type]
            det = counts.get("deterministic", 0)
            tag = counts.get("tag_similarity", 0)
            fb = counts.get("fallback", 0)
            op_total = det + tag + fb
            
            # Escape underscores for LaTeX
            op_type_escaped = op_type.replace("_", r"\_")
            lines.append(f"{op_type_escaped} & {det} & {tag} & {fb} & {op_total} \\\\")
    
    # Add totals row
    total = stats.total_retrievals
    lines.extend([
        r"\midrule",
        f"\\textbf{{Total}} & {stats.deterministic_hits} & {stats.tag_similarity_hits} & {stats.fallback_hits} & {total} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])
    
    return "\n".join(lines)
