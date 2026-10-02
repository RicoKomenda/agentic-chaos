# Security policy

Agentic Chaos is a testing tool. If you find a vulnerability in the tool itself (for example, a way an
experiment file could execute unintended code beyond its declared `entrypoint`, or instrumentation that is
not inert outside experiments), please report it privately via GitHub Security Advisories
("Report a vulnerability" on the repository's Security tab) rather than in a public issue.

Note: experiment files import and run the `entrypoint` they declare. Only run experiment files you trust,
just as you would any other code.

Weaknesses that Agentic Chaos finds in *your* systems are your findings; please handle them according to
your own disclosure process.
