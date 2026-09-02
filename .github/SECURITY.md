# Security Policy

## Supported versions

Only the latest commit on `main` and the currently deployed revision receive security fixes. This is
an independently operated service rather than a versioned commercial product, so older commits and forks are not
supported.

## Report a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private **Report a
vulnerability** form from this repository's Security tab. If the form is unavailable, email
[seongjuice999@gmail.com](mailto:seongjuice999@gmail.com).

Include the affected commit, expected and actual behavior, impact, and the smallest sanitized
reproduction you can provide. Never include a real Gemini key, caller API key, authorization header,
request text containing personal data, Kubernetes Secret, or production response body.

No bug-bounty payment or response-time SLA is offered. Reports will be triaged against the current
code and deployment, and disclosure should be coordinated until a fix or mitigation is available.

## Scope

In scope:

- inbound authentication, per-key rate/concurrency isolation, and cross-caller access;
- secret exposure, unsafe logging, injection, request-boundary bypass, and upstream error leakage;
- container, CI, dependency, Kubernetes, and public endpoint configuration owned by this project.

Google APIs, cloud providers, and other third-party services are not controlled by this project.
Please report integration behavior that creates a vulnerability in this code without testing a
third party directly.
