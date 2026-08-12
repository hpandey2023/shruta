from hf_transcript.cli import build_parser


def test_agent_defaults_to_evidence_only():
    args = build_parser().parse_args(["process", "capture.txt", "--workspace", "workspace"])
    assert args.agent == "none"
