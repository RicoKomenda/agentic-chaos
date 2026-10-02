# Governance

Agentic Chaos is an open-source project run by its maintainers ([MAINTAINERS.md](https://github.com/RicoKomenda/agentic-chaos/blob/main/MAINTAINERS.md)) in the open,
on GitHub.

## Roles

- **Contributors**: anyone who opens issues, discussions or pull requests.
- **Maintainers**: review and merge pull requests, cut releases, triage security reports, and steer the roadmap.
  A project lead resolves deadlocks.

## Decisions

- Everyday changes: one maintainer approval and passing CI.
- Changes to the public contract (API in `__all__`, the `agentic-chaos/v1` format, CLI exit codes, report
  fields; see the versioning policy) and new top-level dependencies: an issue describing the change, at least
  one week for comments, and approval by two maintainers once the project has two or more.
- Security-relevant design choices (payloads, default redaction, proxy exposure) follow the same process and
  must keep the project's safety principles: benign canary payloads, inert-by-default instrumentation, and
  no features whose main use is attacking systems the user does not own.

## Becoming a maintainer

Sustained, high-quality contributions (code, reviews, docs, triage) over several months. An existing maintainer
nominates; the current maintainers agree by consensus. Maintainers who are inactive for twelve months move to
emeritus status and can return by asking.

## Security

Vulnerabilities in Agentic Chaos are reported privately (see [SECURITY.md](https://github.com/RicoKomenda/agentic-chaos/blob/main/SECURITY.md)) and handled by the
maintainers before public disclosure.

## Name and trademarks

"Agentic Chaos" is the project's name. The phrase is also used elsewhere in the industry; for example, an AI
platform vendor uses "Agentic Chaos" for a feature of its product. No trademark registration for the name is
known to the maintainers as of October 2026, but this is not legal advice. A trademark clearance search in the
relevant jurisdictions is recommended before registering the name, building a commercial offering on it, or
transferring the project to a foundation. The project does not claim any affiliation with other uses of the
phrase.

## Changes to this document

By pull request, following the process for changes to the public contract.
