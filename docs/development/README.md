# Development documentation

This directory contains current development and operations guidance for the Community
application. Historical implementation reports and notes are maintained in the project archive
outside this repository and are not part of the runtime package or public documentation set.
Current architecture decisions live in [`docs/architecture`](../architecture/README.md).

Browser errors use the application error page: authenticated users retain the normal navigation,
while anonymous users receive the same page without account controls. Validation and conflict
responses stay on their form with the submitted non-secret values escaped; passwords, API keys
and session tokens are never rendered back. Authorization and missing-resource status codes follow
the contracts in `AGENTS.md`, and unexpected web exceptions return HTTP 500 while logging the
exception type and traceback without request payloads.
