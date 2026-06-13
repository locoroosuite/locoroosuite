# Security Policy

## Reporting a vulnerability

Email **security@locoroo.net**. Do not file a public issue.

Include:
- Description of the vulnerability
- Steps to reproduce (or a proof of concept)
- Affected versions (if known)
- Your preferred contact method for follow-up

We'll acknowledge receipt within 48 hours and send a detailed response within 5 business days describing the next steps.

## What's in scope

- The LocoRooSuite application (`app/`, `mail-api/`, `packages/locoroosuite-mcp/`)
- Authentication and session management
- Encryption at rest (SQLCipher, DEK wrapping, credential storage)
- API token handling and scope enforcement
- WOPI token generation and validation
- XSS, CSRF, injection, or authentication bypass in the web UI
- Information disclosure through admin interfaces
- MCP server authentication (OAuth JWT, API key validation)

## What's not in scope

- Vulnerabilities in third-party dependencies (report upstream)
- Dovecot, Postfix, Radicale, or Collabora Online (report to those projects)
- Attacks requiring physical access to the server
- Social engineering
- Denial of service

## Disclosure policy

We ask for 90 days to address the issue before public disclosure. If we need more time, we'll communicate a clear timeline. We'll credit you in the fix commit unless you'd prefer to remain anonymous.

We publish security-relevant fixes in the commit log without detailed exploit information until a reasonable patch adoption window has passed.
