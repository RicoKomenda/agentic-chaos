---
name: Experiment or fault proposal
about: Propose a new chaos experiment, fault or probe
labels: experiment
---

**Hypothesis**
What should hold? ("When X fails / is attacked, the agent still Y.")

**Real-world motivation**
Incident, paper, advisory or production story this is based on (links welcome).

**Injection point and fault**
Where does the turbulence enter (`llm.call`, `tool.result`, `memory.read`, `control`, ...) and what does it look like?

**Probe**
How do we observe whether the hypothesis held?

**Risk mapping**
e.g. ASI01, ASI06, LLM01
