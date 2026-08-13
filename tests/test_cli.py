from shruta.cli import build_parser


def test_command_uses_shruta_name():
    assert build_parser().prog == "shruta"


def test_agent_defaults_to_evidence_only():
    args = build_parser().parse_args(["process", "capture.txt", "--workspace", "workspace"])
    assert args.agent == "none"
