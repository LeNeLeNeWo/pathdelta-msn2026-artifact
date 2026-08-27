"""
Verification Layer - Validation and Assertion Logic

This module provides abstractions for checking and validating system outputs
including syntax validation, BGP/OSPF assertions, and verification logic.

All verification operations should go through this layer, which uses the
Execution Layer for infrastructure operations.
"""

from verification_layer.assertions import (
    parse_bgp_summary_json,
    is_bgp_neighbor_established,
    check_all_neighbors_established,
    parse_ospf_neighbor_json,
    is_ospf_neighbor_full,
)
from verification_layer.verifier import (
    Verifier,
    VerificationResult,
    SteeringVerificationResult,
    FRR_DOCKER_IMAGE,
    DEFAULT_CONVERGENCE_WAIT,
)
from verification_layer.lab_generator import (
    generate_lab_conf,
    KATHARA_FRR_IMAGE,
)
from verification_layer.batfish_validator import (
    BatfishAssertionResult,
    BatfishIntentValidationSpec,
    BatfishSemanticValidator,
    BatfishValidationResult,
    build_batfish_snapshot,
    compose_effective_config_dir,
    validate_rendered_patch_with_batfish,
)

__all__ = [
    # Verifier class
    "Verifier",
    "VerificationResult",
    "SteeringVerificationResult",
    "FRR_DOCKER_IMAGE",
    "DEFAULT_CONVERGENCE_WAIT",
    # Assertions
    "parse_bgp_summary_json",
    "is_bgp_neighbor_established",
    "check_all_neighbors_established",
    "parse_ospf_neighbor_json",
    "is_ospf_neighbor_full",
    # Lab generator
    "generate_lab_conf",
    "KATHARA_FRR_IMAGE",
    # Batfish static semantic validation
    "BatfishAssertionResult",
    "BatfishIntentValidationSpec",
    "BatfishSemanticValidator",
    "BatfishValidationResult",
    "build_batfish_snapshot",
    "compose_effective_config_dir",
    "validate_rendered_patch_with_batfish",
]
