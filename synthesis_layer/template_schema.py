"""
Template Schema Definition for Template-RAG System.

Defines the TemplateDefinition dataclass and schema validation for
template manifest.yaml files.

Requirements: 2.1, 2.2, 2.3
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml


class OperationType(str, Enum):
    """
    Supported operation types for templates.
    
    Each operation type corresponds to a specific configuration primitive
    that can be generated using templates.
    """
    PREFIX_LIST = "PREFIX_LIST"
    ROUTE_MAP = "ROUTE_MAP"
    NEIGHBOR_BIND = "NEIGHBOR_BIND"
    OSPF_COST = "OSPF_COST"
    GENERIC = "GENERIC"


class ConfigStyle(str, Enum):
    """
    Supported configuration styles.
    
    Determines the syntax and format of generated configuration.
    """
    FRRCONF = "frrconf"
    CISCO_IOS = "cisco_ios"
    JUNOS = "junos"


@dataclass
class TemplateDefinition:
    """
    Template definition loaded from manifest.yaml.
    
    Attributes:
        id: Unique template identifier
        operation_type: Type of operation (PREFIX_LIST, ROUTE_MAP, NEIGHBOR_BIND, OSPF_COST)
        mechanism: Protocol mechanism (bgp, ospf)
        tags: List of semantic tags for similarity matching
        skeleton: Template skeleton with placeholders (inline)
        skeleton_path: Path to skeleton file (alternative to inline skeleton)
        required_lines: List of required configuration lines (placeholder form)
        forbidden_patterns: List of forbidden regex patterns
        priority: Priority score for deterministic matching (higher = preferred)
        config_style: Configuration style (frrconf, cisco_ios, etc.)
        is_fallback: Whether this is a fallback template
    
    Requirements: 2.1, 2.2, 2.3
    """
    id: str
    operation_type: str
    mechanism: str
    tags: List[str]
    skeleton: str
    skeleton_path: Optional[str] = None
    required_lines: List[str] = field(default_factory=list)
    forbidden_patterns: List[str] = field(default_factory=list)
    priority: int = 0
    config_style: str = "frrconf"
    is_fallback: bool = False
    
    def __post_init__(self):
        """Validate template definition after initialization."""
        self._validate()
    
    def _validate(self) -> None:
        """
        Validate template definition fields.
        
        Raises:
            TemplateSchemaError: If validation fails
        """
        # Validate required fields are non-empty
        if not self.id or not self.id.strip():
            raise TemplateSchemaError("Template 'id' is required and cannot be empty")
        
        if not self.operation_type or not self.operation_type.strip():
            raise TemplateSchemaError(f"Template '{self.id}': 'operation_type' is required")
        
        # Validate operation_type is a known value
        valid_op_types = [e.value for e in OperationType]
        if self.operation_type not in valid_op_types:
            raise TemplateSchemaError(
                f"Template '{self.id}': invalid operation_type '{self.operation_type}'. "
                f"Must be one of: {valid_op_types}"
            )
        
        if not self.mechanism or not self.mechanism.strip():
            raise TemplateSchemaError(f"Template '{self.id}': 'mechanism' is required")
        
        if not isinstance(self.tags, list):
            raise TemplateSchemaError(f"Template '{self.id}': 'tags' must be a list")
        
        # Validate skeleton or skeleton_path is provided
        has_skeleton = self.skeleton and self.skeleton.strip()
        has_skeleton_path = self.skeleton_path and self.skeleton_path.strip()
        
        if not has_skeleton and not has_skeleton_path:
            raise TemplateSchemaError(
                f"Template '{self.id}': either 'skeleton' or 'skeleton_path' is required"
            )
        
        # Validate forbidden_patterns are valid regex
        for pattern in self.forbidden_patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                raise TemplateSchemaError(
                    f"Template '{self.id}': invalid regex in forbidden_patterns: "
                    f"'{pattern}' - {e}"
                )
        
        # Validate priority is non-negative
        if self.priority < 0:
            raise TemplateSchemaError(
                f"Template '{self.id}': 'priority' must be non-negative"
            )
    
    def get_skeleton_content(self, template_dir: Optional[Path] = None) -> str:
        """
        Get the skeleton content, loading from file if necessary.
        
        Args:
            template_dir: Base directory for resolving skeleton_path
        
        Returns:
            The skeleton content as a string
        
        Raises:
            SkeletonNotFoundError: If skeleton_path file doesn't exist
        """
        if self.skeleton and self.skeleton.strip():
            return self.skeleton
        
        if self.skeleton_path and template_dir:
            skeleton_file = template_dir / self.skeleton_path
            if not skeleton_file.exists():
                raise SkeletonNotFoundError(
                    f"Template '{self.id}': skeleton file not found at '{skeleton_file}'"
                )
            return skeleton_file.read_text(encoding="utf-8")
        
        raise TemplateSchemaError(
            f"Template '{self.id}': no skeleton content available"
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TemplateDefinition":
        """
        Create TemplateDefinition from dictionary.
        
        Args:
            data: Dictionary with template fields
        
        Returns:
            TemplateDefinition instance
        
        Raises:
            TemplateSchemaError: If required fields are missing or invalid
        """
        # Check required fields
        required_fields = ["id", "operation_type", "mechanism", "tags"]
        for field_name in required_fields:
            if field_name not in data:
                raise TemplateSchemaError(
                    f"Missing required field '{field_name}' in template definition"
                )
        
        return cls(
            id=data["id"],
            operation_type=data["operation_type"],
            mechanism=data["mechanism"],
            tags=data.get("tags", []),
            skeleton=data.get("skeleton", ""),
            skeleton_path=data.get("skeleton_path"),
            required_lines=data.get("required_lines", []),
            forbidden_patterns=data.get("forbidden_patterns", []),
            priority=data.get("priority", 0),
            config_style=data.get("config_style", "frrconf"),
            is_fallback=data.get("is_fallback", False),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "operation_type": self.operation_type,
            "mechanism": self.mechanism,
            "tags": self.tags,
            "skeleton": self.skeleton,
            "skeleton_path": self.skeleton_path,
            "required_lines": self.required_lines,
            "forbidden_patterns": self.forbidden_patterns,
            "priority": self.priority,
            "config_style": self.config_style,
            "is_fallback": self.is_fallback,
        }


class TemplateSchemaError(Exception):
    """Raised when template schema validation fails."""
    pass


class SkeletonNotFoundError(Exception):
    """Raised when skeleton file is not found."""
    pass


class ManifestNotFoundError(Exception):
    """Raised when manifest.yaml is not found."""
    pass


class InvalidRegexError(Exception):
    """Raised when a forbidden_pattern contains invalid regex."""
    pass


def validate_manifest_schema(manifest_data: Dict[str, Any]) -> List[TemplateDefinition]:
    """
    Validate manifest.yaml schema and return list of TemplateDefinitions.
    
    Args:
        manifest_data: Parsed YAML data from manifest.yaml
    
    Returns:
        List of validated TemplateDefinition objects
    
    Raises:
        TemplateSchemaError: If schema validation fails
    """
    if not isinstance(manifest_data, dict):
        raise TemplateSchemaError("Manifest must be a YAML dictionary")
    
    if "templates" not in manifest_data:
        raise TemplateSchemaError("Manifest must contain 'templates' key")
    
    templates_data = manifest_data["templates"]
    if not isinstance(templates_data, list):
        raise TemplateSchemaError("'templates' must be a list")
    
    templates = []
    seen_ids = set()
    
    for idx, template_data in enumerate(templates_data):
        if not isinstance(template_data, dict):
            raise TemplateSchemaError(
                f"Template at index {idx} must be a dictionary"
            )
        
        template = TemplateDefinition.from_dict(template_data)
        
        # Check for duplicate IDs
        if template.id in seen_ids:
            raise TemplateSchemaError(
                f"Duplicate template id '{template.id}'"
            )
        seen_ids.add(template.id)
        
        templates.append(template)
    
    return templates


def load_manifest(manifest_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Load and parse manifest.yaml file.
    
    Args:
        manifest_path: Path to manifest.yaml
    
    Returns:
        Parsed YAML data
    
    Raises:
        ManifestNotFoundError: If manifest file doesn't exist
        TemplateSchemaError: If YAML parsing fails
    """
    path = Path(manifest_path)
    if not path.exists():
        raise ManifestNotFoundError(f"Manifest not found at '{path}'")
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise TemplateSchemaError(f"Failed to parse manifest YAML: {e}")
