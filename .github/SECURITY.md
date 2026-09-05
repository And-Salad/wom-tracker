# Reporting a security problem

This is one person's hobby tracker for an Old School RuneScape clan, run as a
single always-on machine. There is no security team and no response time to
promise. What there is: it will be read, and it will be fixed or explained.

Please **do not open a public issue** for anything exploitable. Use GitHub's
private reporting instead - the **Security** tab, then **Report a
vulnerability** - which reaches the maintainer without publishing anything.

## What is worth reporting

The parts of this with a security story worth telling are all in the web app:

- the admin pages, which are behind a password and are the only thing that can
  change what is tracked or write to the database
- the sign-in lockout and the rate limits, which count per visitor using the
  header named by `WOM_TRUSTED_IP_HEADER`
- anything that reads or writes `data/` - it holds the API keys, the database
  and the prompts

A deployment with no admin password set has no admin pages rather than open
ones. That is deliberate, and not a finding.

## What is not

Reports that the app is reachable without authentication: the dashboard is
public on purpose, and shows nothing but Wise Old Man data that is already
public. Findings against a deployment you do not run, or against Wise Old Man
itself - that is not this project.
