# Integrations

## Agent frameworks

Tool adapters put a framework's tools under chaos without changing them. Every call passes `tool.call`
(target = the tool's name, with its arguments) and its result `tool.result`. Model calls go through the
provider transports: every framework below accepts a custom HTTP client.

| Framework | Tools | Models |
| --- | --- | --- |
| LangChain / LangGraph | `integrations.langchain.instrument_tools(tools)` - works with `ToolNode` and prebuilt agents | `ChatOpenAI(http_client=httpx2.Client(transport=ChaosTransport()))` (or the httpx variant for older versions) |
| OpenAI Agents SDK | `integrations.openai_agents.instrument_tools(tools)` - `FunctionTool`s are wrapped, hosted tools pass through | `OpenAIChatCompletionsModel(..., openai_client=AsyncOpenAI(http_client=httpx2.AsyncClient(transport=AsyncChaosTransport())))` |
| Pydantic AI | `integrations.pydantic_ai.instrument_tool(tool_or_function)`, or `@chaos.tool` under `@agent.tool_plain` | a provider whose `http_client` uses a chaos transport |
| Anything else | `@chaos.tool`, `@chaos.llm`, `@chaos.memory`, `@chaos.control`, `@chaos.agent`, `@chaos.payment` | `integrations.httpx` / `httpx2` transports |

```python
from langgraph.prebuilt import ToolNode
from agentic_chaos_security.integrations.langchain import instrument_tools

tool_node = ToolNode(instrument_tools([search, send_email]))
```

MCP servers and A2A agents need no adapter: use the [MCP proxy](protocols.md#mcp) and the
[A2A transport](protocols.md#multi-agent-systems-and-a2a).

## pytest

The plugin is registered automatically and does nothing until used.

```python
def test_guardrail_fails_safe(chaos):
    chaos.assert_held("experiments/control-guardrail-outage.yaml", target="my_app.agent:handle")
```

Or collect experiment files as tests:

```toml
[tool.pytest.ini_options]
chaos_experiments = ["experiments/*.yaml"]
```

Options: `--chaos-runs N`, `--chaos-target module:callable`, `--chaos-inconclusive fail|skip`.

## GitHub Actions

```yaml
- uses: RicoKomenda/agentic-chaos@v1
  with:
    experiments: experiments/*.yaml
    target: my_app.agent:handle
    install: pip install -e .
    fail-on: weakness        # weakness | inconclusive | never
```

The action writes a job summary, uploads JUnit/HTML/JSON reports as an artifact, and exposes the exit code
as the `exit-code` output.

## Reports

| Flag | Output |
| --- | --- |
| `--report report.json` | full JSON (add `--traces` for every recorded event) |
| `--junit junit.xml` | one test case per experiment; weaknesses are failures, inconclusive results are errors |
| `--html report.html` | self-contained page with per-probe pass rates and confidence intervals |
| `--markdown summary.md` | a summary table, appended to the file (use `$GITHUB_STEP_SUMMARY`) |
| `--otel` | OpenTelemetry spans (`pip install 'agentic-chaos-security[otel]'`, configured by `OTEL_EXPORTER_OTLP_*`) |

All outputs are redacted (see [safety.md](safety.md#secrets-in-traces-and-reports)).
