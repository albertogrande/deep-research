"""Offline MCP-surface tests: the tool is listed with a usable schema, and the wiring
reaches run_research — no pipeline, no network, no cost."""

from deepresearch import mcp_server
from deepresearch.models import RunRecord
from deepresearch.orchestrator import RunResult


async def test_deep_research_tool_is_listed_with_schema():
    tools = await mcp_server.server.list_tools()
    assert [t.name for t in tools] == ["deep_research"]
    props = tools[0].inputSchema["properties"]
    assert set(props) == {"question", "depth", "max_cost"}
    assert tools[0].inputSchema["required"] == ["question"]
    assert "cited Markdown report" in (tools[0].description or "")


async def test_deep_research_wires_settings_and_returns_report(monkeypatch, tmp_path):
    captured = {}
    report = tmp_path / "report.md"
    report.write_text("# T\n\nParis facts [1].\n", encoding="utf-8")

    async def fake_run_research(question, settings, **kwargs):
        captured["question"] = question
        captured["profile"] = settings.profile
        captured["max_cost"] = settings.max_cost
        record = RunRecord(run_id="r-mcp", query=question, profile=settings.profile, routing="gateway")
        return RunResult(record=record, run_dir=tmp_path, report_path=report)

    monkeypatch.setattr(mcp_server, "run_research", fake_run_research)
    out = await mcp_server.deep_research("What is X?", depth="quick", max_cost=0.5)

    assert captured == {"question": "What is X?", "profile": "quick", "max_cost": 0.5}
    assert out.startswith("# T")
    assert "Paris facts [1]." in out
    assert "run r-mcp" in out  # metadata footer


async def test_deep_research_rejects_unknown_depth(monkeypatch):
    async def exploding(*a, **k):  # pragma: no cover
        raise AssertionError("must not run with a bad depth")

    monkeypatch.setattr(mcp_server, "run_research", exploding)
    out = await mcp_server.deep_research("q", depth="ultra")
    assert out.startswith("error: unknown depth")
