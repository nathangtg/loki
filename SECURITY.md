# Security Policy

LOKI is a research prototype developed as an academic Final Year Project.
Security updates are provided on a best-effort basis.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security vulnerabilities.**

If you discover a security vulnerability in LOKI, please report it responsibly:

1. **Email**: Send a detailed report to **nathangtgmy@gmail.com**
2. **Subject line**: `[SECURITY] LOKI — Brief description`
3. **Include**:
   - A description of the vulnerability
   - Steps to reproduce
   - Potential impact assessment
   - Suggested fix (if any)

### Response Timeline

| Action                   | Target Timeline |
|:-------------------------|:----------------|
| Acknowledgement          | 48 hours        |
| Initial assessment       | 7 days          |
| Fix or mitigation        | 30 days         |
| Public disclosure        | After fix       |

## Security Considerations

LOKI is a **desktop automation system** that controls the user's computer on
their behalf. Users should be aware of the following security considerations:

### Local-First Architecture

- LLM inference runs locally through llama-cpp-python or MLX
- Prompts and screen contents stay on the machine. The only network traffic is
  model downloads (`make models`) and the web search and browsing tasks you ask for
- Session memory and credentials remain on the local machine

### Credential Handling

- Site passwords are read from the macOS Keychain, not stored by LOKI. Only
  usernames and recorded login steps are written to
  `orchestration/data/known_sites.json` and `playbooks.json`
- `orchestration/data/` and `.env` are git-ignored; do not commit them

### Accessibility Permissions

- LOKI requires macOS Accessibility permissions to observe and interact with
  UI elements
- These permissions grant broad control over the desktop — only grant them to
  trusted processes

### Automation Risks

- LOKI executes actions autonomously based on LLM reasoning
- The system may interact with any application or website accessible on the
  user's machine
- Users should review the plan output before executing sensitive operations

### Residual Dependency Advisories

- `diskcache` is a required `llama-cpp-python` dependency with no patched release. LOKI does not configure or read a `LlamaDiskCache`, so untrusted cache-directory contents are never deserialized.
- `nltk` is a transitive `llmlingua` dependency with no patched release for its model-path sandbox bypass. LOKI does not call NLTK model import, export, parser, or tagger persistence APIs and does not accept caller-controlled NLTK paths.

## Scope

The following are **in scope** for security reports:

- Remote code execution vulnerabilities
- Credential leakage or exposure
- Unauthorised data exfiltration
- Privilege escalation beyond granted accessibility permissions
- Server-side vulnerabilities in the orchestration HTTP server

The following are **out of scope**:

- Issues requiring physical access to the machine
- Social engineering attacks
- Denial of service against the local HTTP server
- Vulnerabilities in third-party dependencies (report these upstream)
