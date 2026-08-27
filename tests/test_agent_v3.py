from pathlib import Path

from agent_v3.orchestrator import ToolAugmentedAgent


class _Driver:
    def __init__(self, response='{"tools": []}'):
        self.response = response
    def chat_completion(self, *args, **kwargs):
        return self.response


def test_tool_selection_cannot_escape_allowlist():
    agent = ToolAugmentedAgent.__new__(ToolAugmentedAgent)
    agent.driver, agent.trace = _Driver('{"tools":["generic_shell"]}'), []
    agent.toolbox = object()
    try:
        agent._decide_inspection({}, {})
        assert False, "must reject untrusted tool"
    except RuntimeError:
        pass
    assert agent.trace[-1].status == "blocked"


def test_public_tool_surface_has_no_generic_shell():
    from agent_v3.tools import TrustedToolbox
    assert "generic_shell" not in TrustedToolbox.public_tools
    assert "check_patch_contract" in TrustedToolbox.public_tools
    assert "validate_frr" in TrustedToolbox.public_tools
