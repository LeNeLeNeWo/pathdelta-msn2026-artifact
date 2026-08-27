"""
Template Library for Template-RAG System.

Manages loading, validation, and retrieval of templates from the
template directory and manifest.yaml.

Requirements: 1.1, 1.2, 1.4, 5.1, 12.2
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .template_schema import (
    ManifestNotFoundError,
    SkeletonNotFoundError,
    TemplateDefinition,
    TemplateSchemaError,
    load_manifest,
    validate_manifest_schema,
)


logger = logging.getLogger(__name__)


class TemplateLibrary:
    """
    Template library manager.
    
    Loads templates from manifest.yaml and provides access to template
    definitions including skeleton, required_lines, and forbidden_patterns.
    
    The library guarantees that a fallback template exists for each
    operation_type, ensuring retrieval never fails.
    
    Requirements: 1.1, 1.2, 1.4, 5.1, 12.2
    """
    
    def __init__(self, template_dir: str = "templates/"):
        """
        Initialize template library.
        
        Args:
            template_dir: Path to templates directory containing manifest.yaml
        
        Raises:
            ManifestNotFoundError: If manifest.yaml is not found
            TemplateSchemaError: If manifest validation fails
        """
        self.template_dir = Path(template_dir)
        self.manifest_path = self.template_dir / "manifest.yaml"
        
        # Template storage
        self._templates: Dict[str, TemplateDefinition] = {}
        self._by_operation_type: Dict[str, List[TemplateDefinition]] = {}
        self._fallbacks: Dict[str, TemplateDefinition] = {}
        
        # Load manifest on initialization
        self._load_manifest()
    
    def _load_manifest(self) -> None:
        """
        Load and validate manifest.yaml.
        
        Populates internal template storage and indexes.
        
        Raises:
            ManifestNotFoundError: If manifest.yaml is not found
            TemplateSchemaError: If manifest validation fails
            SkeletonNotFoundError: If a skeleton file is not found
        """
        logger.info(f"Loading template manifest from {self.manifest_path}")
        
        # Load and parse manifest
        manifest_data = load_manifest(self.manifest_path)
        
        # Validate schema and get template definitions
        templates = validate_manifest_schema(manifest_data)
        
        # Clear existing data
        self._templates.clear()
        self._by_operation_type.clear()
        self._fallbacks.clear()
        
        # Process each template
        for template in templates:
            # Validate skeleton file exists if skeleton_path is specified
            if template.skeleton_path:
                skeleton_file = self.template_dir / template.skeleton_path
                if not skeleton_file.exists():
                    raise SkeletonNotFoundError(
                        f"Template '{template.id}': skeleton file not found at "
                        f"'{skeleton_file}'"
                    )
                # Load skeleton content into template
                template.skeleton = skeleton_file.read_text(encoding="utf-8")
            
            # Store template by ID
            self._templates[template.id] = template
            
            # Index by operation_type
            op_type = template.operation_type
            if op_type not in self._by_operation_type:
                self._by_operation_type[op_type] = []
            self._by_operation_type[op_type].append(template)
            
            # Track fallback templates
            if template.is_fallback:
                if op_type in self._fallbacks:
                    logger.warning(
                        f"Multiple fallback templates for operation_type "
                        f"'{op_type}': using '{template.id}' over "
                        f"'{self._fallbacks[op_type].id}'"
                    )
                self._fallbacks[op_type] = template
        
        # Sort templates by priority (descending) within each operation_type
        for op_type in self._by_operation_type:
            self._by_operation_type[op_type].sort(
                key=lambda t: t.priority, reverse=True
            )
        
        logger.info(
            f"Loaded {len(self._templates)} templates, "
            f"{len(self._fallbacks)} fallbacks"
        )
    
    def get_template(self, template_id: str) -> Optional[TemplateDefinition]:
        """
        Get template by ID.
        
        Args:
            template_id: Unique template identifier
        
        Returns:
            TemplateDefinition if found, None otherwise
        """
        return self._templates.get(template_id)
    
    def get_templates_by_operation_type(
        self, 
        operation_type: str
    ) -> List[TemplateDefinition]:
        """
        Get all templates for an operation type.
        
        Templates are returned sorted by priority (highest first).
        
        Args:
            operation_type: Operation type (PREFIX_LIST, ROUTE_MAP, etc.)
        
        Returns:
            List of TemplateDefinition objects, empty if none found
        """
        return self._by_operation_type.get(operation_type, [])
    
    def get_fallback_template(
        self, 
        operation_type: str
    ) -> Optional[TemplateDefinition]:
        """
        Get fallback template for operation type.
        
        Fallback templates are guaranteed to exist for each operation_type
        that has templates defined. They are used when deterministic and
        tag similarity matching both fail.
        
        Args:
            operation_type: Operation type (PREFIX_LIST, ROUTE_MAP, etc.)
        
        Returns:
            TemplateDefinition for fallback, None if no fallback defined
        
        Requirements: 5.1
        """
        return self._fallbacks.get(operation_type)
    
    def reload_manifest(self) -> None:
        """
        Hot-reload manifest.yaml without system restart.
        
        Useful for adding new templates or modifying existing ones
        without restarting the application.
        
        Raises:
            ManifestNotFoundError: If manifest.yaml is not found
            TemplateSchemaError: If manifest validation fails
            SkeletonNotFoundError: If a skeleton file is not found
        
        Requirements: 12.2
        """
        logger.info("Hot-reloading template manifest")
        self._load_manifest()
    
    def list_template_ids(self) -> List[str]:
        """
        List all template IDs.
        
        Returns:
            List of template ID strings
        """
        return list(self._templates.keys())
    
    def list_operation_types(self) -> List[str]:
        """
        List all operation types that have templates.
        
        Returns:
            List of operation type strings
        """
        return list(self._by_operation_type.keys())
    
    def has_fallback(self, operation_type: str) -> bool:
        """
        Check if a fallback template exists for operation type.
        
        Args:
            operation_type: Operation type to check
        
        Returns:
            True if fallback exists, False otherwise
        """
        return operation_type in self._fallbacks
    
    @property
    def template_count(self) -> int:
        """Get total number of templates."""
        return len(self._templates)
    
    @property
    def fallback_count(self) -> int:
        """Get number of fallback templates."""
        return len(self._fallbacks)
